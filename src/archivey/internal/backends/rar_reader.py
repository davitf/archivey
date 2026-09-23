"""Native RAR reader backend.

Module split:

- :mod:`.rar_parser` — metadata, offsets, encryption headers, multi-volume merge
- :mod:`.rar_unrar` — spawn RARLAB ``unrar p`` (password on stdin; ``-n./member``)
- this module — ``BaseArchiveReader``: list from the parser; member **data** via unrar

Data-open shapes:

- Solid archive → one ``unrar p`` ALL-pipe + :class:`SolidBlockReader` demux
- Non-solid stored (no encrypt / split) → direct sliced view (no ``unrar``)
- Other non-solid → per-member named ``unrar p -n./…`` opens
- Stream / non-path sources may be materialized to a temp ``.rar`` so ``unrar``
  can open a real path (and resolve sibling volumes)

WinRAR ``-ver`` history members are presented as ``path;n`` (see
:func:`_presented_filename`). Passwords feed three places: header parse, ``unrar``,
and RAR5 ConvertHashToMAC when checksums are tweaked.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import zlib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import BinaryIO, Literal

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import (
    DiagnosticCode,
    DigestContext,
    MemberHeaderRecordContext,
)
from archivey.escaping import quoted
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
    UnsupportedOperationError,
    raw_message_of,
)
from archivey.internal.backends.rar_parser import (
    RAR5_ID,
    RAR_ID,
    DamagedServiceHeader,
    RarArchive,
    RarEncryptionInfo,
    RarMemberInfo,
    _check_rar5_password,
    _decode_name,
    _Rar3Comment,
    convert_blake2sp_to_mac,
    convert_crc_to_mac,
    parse_rar_archive,
    parse_rar_volumes,
    rar5_hash_key,
)
from archivey.internal.backends.rar_unrar import (
    _unrar_glob_demux_ok,
    _unrar_mask_for,
    _unrar_mask_match,
    decompress_rar3_blob,
    open_unrar_p,
    terminate_unrar,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.rar_detect import validate_rar_main_header
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream, RewindWarning
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    ReadOnlyIOStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    skip_forward,
)
from archivey.internal.streams.verify import build_member_verifier
from archivey.internal.volumes import ConcatenatedFile, discover_volume_siblings
from archivey.types import (
    EXTRA_IS_JUNCTION,
    EXTRA_IS_REPARSE_POINT,
    EXTRA_RAR_CREATED_IS_CTIME,
    EXTRA_RAR_EXTRACT_VERSION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    HashAlgorithm,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
    crc32_digest,
)

_STREAM_SINGLE_DISK_COPY_NOTE = (
    "Reading a compressed member will copy the whole archive to disk so "
    "RARLAB unrar or rar can read it."
)
_STREAM_VOLUMES_DISK_COPY_NOTE = (
    "Reading a compressed member will copy every volume to a temp directory "
    "so RARLAB unrar or rar can read them."
)


def _rar_stream_copy_cost_notes(source: ArchiveSource) -> tuple[str, ...]:
    """Open-time caveat when member data needs a filesystem path for ``unrar``.

    A file source, or a joined set of files, gets no note. Both stream shapes get the
    same predictive caveat: the copy happens on the first read ``unrar`` has to serve,
    not at open. Keyed from the source's facts so a mixed set, whose file parts
    ``_materialize_stream_volumes`` copies alongside the streams, is labelled as the
    streams it contains.
    """
    if source.path is not None:
        return ()
    if source.joined is not None:
        if source.volume_paths:
            return ()
        return (_STREAM_VOLUMES_DISK_COPY_NOTE,)
    return (_STREAM_SINGLE_DISK_COPY_NOTE,)


# rarfile / RAR host_os values (parser maps RAR5 Windows→2, Unix→3).
_RAR_HOST_OS_TO_CREATE_SYSTEM: dict[int, CreateSystem] = {
    0: CreateSystem.FAT,
    1: CreateSystem.OS2_HPFS,
    2: CreateSystem.WINDOWS_NTFS,
    3: CreateSystem.UNIX,
    4: CreateSystem.MACINTOSH,
    5: CreateSystem.BEOS,
}

# RAR host_os values used directly below; the same numbers key
# _RAR_HOST_OS_TO_CREATE_SYSTEM above (the parser maps RAR5 Windows->2, Unix->3).
_RAR_HOST_OS_WIN32 = 2
_RAR_HOST_OS_UNIX = 3

_RAR_METHOD_STORED = 0x30
_RAR_METHOD_MAX = 0x35  # RAR M5
_RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x02
_RAR5_XREDIR_WINDOWS_SYMLINK = 2
_RAR5_XREDIR_WINDOWS_JUNCTION = 3
# The two RAR5 redirect types that are Windows reparse points. RAR is the one format
# that records the kind in a header field rather than in the member's data, which is
# why it can flag both while listing and never has to read anything.
_RAR5_XREDIR_REPARSE_POINTS = frozenset(
    {_RAR5_XREDIR_WINDOWS_SYMLINK, _RAR5_XREDIR_WINDOWS_JUNCTION}
)

# Read step for the confirmation pass over a cut-short member's stored bytes
# (``RarReader._confirm_unsettled_plaintext``). Matches the verifier's own drain
# step; the pass is bounded by the member, which is bounded by the source.
_CONFIRM_CHUNK_BYTES = 64 * 1024

# Shared CompressionMethod tuples — many-member listing hits the same method byte
# (typically store / M1–M5) thousands of times; avoid per-member allocations.
# M0 is STORED; M1–M5 are CompressionAlgorithm.RAR with level = method - 0x30.
_STORED_COMPRESSION: tuple[CompressionMethod, ...] = (
    CompressionMethod(algo=CompressionAlgorithm.STORED),
)
_COMPRESSION_BY_METHOD: dict[int, tuple[CompressionMethod, ...]] = {
    _RAR_METHOD_STORED: _STORED_COMPRESSION,
    **{
        method: (
            CompressionMethod(
                algo=CompressionAlgorithm.RAR,
                level=method - _RAR_METHOD_STORED,
            ),
        )
        for method in range(_RAR_METHOD_STORED + 1, _RAR_METHOD_MAX + 1)
    },
}


def _member_stream_size(member: ArchiveMember) -> int:
    """Unpacked size for a RAR payload member.

    RAR headers always store ``file_size`` as ``int``. ``ArchiveMember.size`` is
    optional because other formats have streaming entries; folding ``None`` into
    ``0`` here would treat "unknown length" as "empty". A RAR member without a
    declared size is a programming error.
    """
    size = member.size
    if size is None:
        raise AssertionError("RAR payload members always declare an unpacked size")
    return size


def _presented_filename(info: RarMemberInfo) -> str:
    """Archive path, or WinRAR/``unrar`` ``path;n`` for file-version history."""
    if info.is_file_version_history():
        assert info.file_version is not None
        return f"{info.filename};{info.file_version}"
    return info.filename


def _password_as_str(password: bytes | str | None) -> str | None:
    if password is None or password == b"" or password == "":
        return None
    if isinstance(password, bytes):
        return password.decode("utf-8", errors="surrogateescape")
    return password


def _compression_for(info: RarMemberInfo) -> tuple[CompressionMethod, ...]:
    method = info.compress_type
    if method is None:
        return ()
    cached = _COMPRESSION_BY_METHOD.get(method)
    if cached is not None:
        return cached
    # Outside M0–M5: UNKNOWN with no level. ``level`` is the M1–M5 method-byte
    # offset (1–5), not ``method - 0x30`` for an arbitrary byte.
    return (CompressionMethod(algo=CompressionAlgorithm.UNKNOWN),)


def _crc_is_tweaked(info: RarMemberInfo) -> bool:
    enc = info.file_encryption
    if enc is None:
        return False
    return bool(enc.flags & _RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS)


def _member_hashes(info: RarMemberInfo) -> dict[HashAlgorithm, bytes]:
    """Plaintext digests safe for member verification without a HashKey.

    When ``RAR5_XENC_TWEAKED`` / ``HASHMAC`` (0x02) is set, the stored CRC32 and
    BLAKE2sp are key-tweaked (``ConvertHashToMAC``) and must not be compared to the
    plaintext digest. Those values are stashed in ``member.extra`` and verified via
    forward-transform when a password is available (see
    :meth:`RarReader._tweaked_verify_spec`).

    A **RAR5 redirect** member (symlink, hard link, file copy) surfaces no digest at all.
    It keeps its target in a header field and stores *no data stream*, so its CRC32 field
    covers zero bytes — and ``crc32(b"") == 0``, which RARLAB duly writes. That value is
    correct about nothing: every RAR5 symlink in existence carries ``0x00000000``, so it
    neither describes the member (``size`` is the target's length while the digest covers
    0 bytes) nor distinguishes one link from another. Reporting it would make
    ``member.hashes`` mean something different in RAR than in every other format, against
    the no-surprises rule — and the founding "hashes without decompression" use case
    reads exactly this field.

    **RAR4 is deliberately unaffected**, and the difference is why this keys on the
    redirect rather than on the member type: RAR3/4 store a symlink's target *as the
    member's data*, so ``compress_size`` is the target length and the CRC32 is a real
    digest of it — the same thing ZIP and 7z record. Dropping that would lose a
    meaningful value.
    """
    hashes: dict[HashAlgorithm, bytes] = {}
    if info.file_redir is not None:
        return hashes
    tweaked = _crc_is_tweaked(info)
    if info.crc32 is not None and not tweaked:
        hashes[HashAlgorithm.CRC32] = crc32_digest(info.crc32)
    if info.blake2sp_hash is not None and not tweaked:
        hashes[HashAlgorithm.BLAKE2SP] = info.blake2sp_hash
    return hashes


def _rar_member_extra_and_link(
    info: RarMemberInfo,
) -> tuple[MemberExtra, str | None]:
    """Build ``ArchiveMember.extra`` and the symlink/junction target."""
    extra = MemberExtra()
    link_target: str | None = None
    if info.file_redir is not None:
        link_target = info.file_redir[2]
        if info.file_redir[0] in _RAR5_XREDIR_REPARSE_POINTS:
            extra[EXTRA_IS_REPARSE_POINT] = True
        if info.file_redir[0] == _RAR5_XREDIR_WINDOWS_JUNCTION:
            extra[EXTRA_IS_JUNCTION] = True
    # Pure; re-derived here rather than threaded through the ``_to_member`` split
    # (``is_current`` and the tweaked-digest diagnostic each call the same
    # predicates independently).
    if info.is_file_version_history():
        assert info.file_version is not None
        extra["rar.file_version"] = info.file_version
    if info.extract_version is not None:
        extra[EXTRA_RAR_EXTRACT_VERSION] = info.extract_version
    if _crc_is_tweaked(info):
        # Stored digests are key-tweaked; keep them out of ``hashes`` (see
        # ``_member_hashes``) but expose the raw values for callers / forward-verify.
        if info.crc32 is not None:
            extra["rar.tweaked_crc32"] = info.crc32
        if info.blake2sp_hash is not None:
            extra["rar.tweaked_blake2sp"] = info.blake2sp_hash
    host_os = info.host_os
    # Unix (_RAR_HOST_OS_UNIX): RAR3 Unix, and the parser maps RAR5 Unix→3.
    # That writer's creation slot is st_ctime, not birth; Win32
    # (_RAR_HOST_OS_WIN32) and the other RAR3 hosts store a creation time.
    # Omit the key when there is no created value or host_os is unknown.
    if info.ctime is not None and host_os is not None:
        extra[EXTRA_RAR_CREATED_IS_CTIME] = host_os == _RAR_HOST_OS_UNIX
    return extra, link_target


def _tweaked_hash_key(enc: RarEncryptionInfo, password: str) -> bytes | None:
    """Return HashKey for ``password``, or ``None`` when the password is provably wrong.

    A present PswCheck that rejects ``password`` returns ``None`` so callers skip
    forward-transform verification (a wrong HashKey would false-``CorruptionError``
    good plaintext). When the check is absent or unusable, the HashKey is still
    derived — matching the password ``unrar`` will receive.
    """
    if enc.check_value is not None:
        try:
            _check_rar5_password(enc.check_value, enc.kdf_count, enc.salt, password)
        except EncryptionError:
            return None
    return rar5_hash_key(password, enc.salt, enc.kdf_count)


def _psw_check_usable(enc: RarEncryptionInfo) -> bool:
    """Whether ``enc`` carries a PswCheck that can tell a right password from a wrong one.

    Mirrors the shape test :func:`rar_parser._check_rar5_password` applies before it
    derives a key: twelve bytes whose last four are the SHA-256 prefix of the first
    eight. RAR4 records and a damaged check have none, and a candidate cannot be
    judged before ``unrar`` runs. It does not mirror that function's ``kdf_count``
    bound: a usable check with an out-of-range cost still reaches the check, which
    raises ``CorruptionError`` for the member rather than deriving at that cost.
    """
    check = enc.check_value
    return (
        check is not None
        and len(check) == 12
        and hashlib.sha256(check[:8]).digest()[:4] == check[8:]
    )


class _UnrarOwnedStream(DelegatingStream):
    """Stdout wrapper that terminates the owning ``unrar`` process on close.

    On close it maps ``unrar``'s exit code (RARLAB) to a typed error so a corrupt,
    truncated, or wrong-password member surfaces honestly instead of a silent short
    read. Only a self-exit code maps: when *we* terminate the process (early close /
    teardown) the return code is negative and no error is raised. ``named_member``
    distinguishes a per-member open (``-n`` mask) — where "no files matched" (code 10)
    means the member could not be read — from the solid ALL-pipe, where an empty match
    is not an error.

    ``has_verifiable_hash`` suppresses the corruption/no-match mapping (codes 2/3/10):
    when the member carries a CRC32/BLAKE2sp that archivey verifies itself, that check
    is authoritative, and some legacy archives (e.g. RAR 1.5) make ``unrar`` report a
    spurious CRC error (exit 3) while emitting correct, verified data. A wrong-password
    exit (11) always maps — it means no usable data regardless of any stored hash.

    RAR4 often reports exit 3 (CRC) instead of 11 for a missing/wrong password, with
    empty stdout. After verify's short-before-digest preference that would otherwise
    surface only as ``TruncatedError`` (0 of N) while ``has_verifiable_hash`` suppresses
    the CRC exit. When the member is encrypted and we read zero plaintext bytes, map
    exit 2/3 to ``EncryptionError``.

    Exit mapping runs on the **empty/completing read** when the process has already
    exited (ADR 0014 eager-finalize parity), so ``archive.read()`` / a completing
    ``read()`` see ``EncryptionError`` on the read path rather than only from
    ``close()``. ``close()`` still maps if the empty-read path never ran (early stop).
    """

    # Side-effecting read(); disable passthrough so counting still runs on readinto.
    readinto_passthrough = False
    _SUBCLASS_CLOSES_INNER = True

    def __init__(
        self,
        stdout: BinaryIO,
        proc: subprocess.Popen[bytes],
        *,
        named_member: bool = False,
        has_verifiable_hash: bool = False,
        encrypted: bool = False,
    ) -> None:
        super().__init__(stdout)
        self._proc = proc
        self._named_member = named_member
        self._has_verifiable_hash = has_verifiable_hash
        self._encrypted = encrypted
        self._bytes_read = 0
        self._exit_mapped = False

    def read(self, n: int = -1, /) -> bytes:
        data = super().read(n)
        self._bytes_read += len(data)
        if not data:
            # Completing / EOF read: reap and map exit here so content faults raise on
            # read (not only on close).
            self._map_exit_if_reaped(wait_timeout=1.0)
        return data

    def _raise_for_returncode(self, rc: int) -> None:
        """Map an unrar exit code to an archivey error, or return quietly."""
        # RARLAB unrar exit codes: 11 bad password, 3 CRC/corrupt data, 2 fatal
        # error, 10 no files matched. Codes 0 (success) and 1 (warning) pass; a
        # negative code means we terminated it (early close) — not an error.
        if rc == 11:
            raise EncryptionError("Incorrect RAR password or encrypted member")
        # RAR4 wrong/missing password: often exit 3 + empty stdout, not exit 11.
        # Ambiguity: a genuinely corrupt (not password-related) encrypted member that
        # also yields empty stdout + exit 2/3 is mislabeled EncryptionError here. We
        # accept that bias — for an encrypted member producing zero plaintext, "wrong
        # password" is the far more common cause and the actionable one (a caller
        # cannot distinguish, or make progress, without the correct password anyway).
        # unrar exposes no reliable signal to separate the two; narrow this if one
        # appears.
        if self._encrypted and self._bytes_read == 0 and rc in (2, 3):
            raise EncryptionError("Incorrect RAR password or encrypted member")
        if self._has_verifiable_hash:
            # archivey verifies this member's CRC32/BLAKE2sp itself; that check is
            # authoritative, so ignore unrar's (sometimes spurious) corruption codes.
            return
        if rc in (2, 3):
            raise CorruptionError(
                f"unrar reported a fatal or CRC error (exit {rc}) reading member data"
            )
        if rc == 10 and self._named_member:
            raise CorruptionError(
                "unrar found no matching member (exit 10); the member could not be read"
            )

    def _map_exit_if_reaped(self, *, wait_timeout: float | None) -> None:
        """If unrar has exited (or exits within ``wait_timeout``), map its status once."""
        if self._exit_mapped:
            return
        if self._proc.poll() is None:
            if wait_timeout is None:
                return
            try:
                self._proc.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                return
        self._exit_mapped = True
        self._raise_for_returncode(self._proc.returncode)

    def close(self) -> None:
        if self.closed:
            return
        close_error: BaseException | None = None
        try:
            self._inner.close()
        except BaseException as exc:  # noqa: BLE001 - close must reap unrar even on KeyboardInterrupt
            close_error = exc
        if self._proc.poll() is None:
            terminate_unrar(self._proc)
        else:
            # Drain wait status if the process already exited on EOF.
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                terminate_unrar(self._proc)
        # Mark closed without DelegatingStream closing inner a second time.
        super().close()
        # Early-stop close: map now if the completing-read path never did.
        # (If read already mapped, ``_exit_mapped`` skips a second raise.)
        # Do not let that mapped error replace an exception from inner.close().
        try:
            self._map_exit_if_reaped(wait_timeout=None)
        except BaseException as mapped:  # noqa: BLE001 - chain onto inner.close(), do not replace it
            if close_error is not None:
                raise close_error from mapped
            raise
        if close_error is not None:
            raise close_error


class _UnrarRespawnStream(ReadOnlyIOStream):
    """Seekable view of a named ``unrar p`` pipe.

    The inner handle is a pipe, so a backward seek cannot reposition it. Close it
    and spawn a fresh process on the next ``read()`` that needs bytes; skip to the
    logical offset then. Forward seeks do not drain the pipe until a later
    ``read()`` needs those bytes. Past-end seeks leave the pipe where it is: a
    read with ``pos > size`` is empty, and a read at ``pos == size`` still
    reaches the pipe so fused verify's one-byte overrun probe can see trailing
    output — that boundary read is clamped to one byte.

    ``_pos`` is the logical offset. ``_pipe_pos`` is pipe progress clamped to
    ``_size``: the fused-verify overrun probe's extra byte advances ``_pos``
    but not ``_pipe_pos``, so a no-op ``SEEK_CUR`` after it does not kill the
    process. Re-probing after seeking back to ``_size`` is then not
    byte-exact; that path is unreachable while the first probe raises
    ``CorruptionError`` on any trailing byte (``verify.py`` ``_finish`` /
    ``_verify_reaches_declared``). Respawn is keyed on the pipe, so
    ``seek(0, SEEK_END); seek(0)`` before any read costs nothing.

    ``spawn`` must return a stream that owns the process (typically
    ``_UnrarOwnedStream``, or ``_bounded_member_pipe`` wrapping one), so
    close/respawn reaps it.

    Same restart-on-rewind shape as ``DecompressorStream`` /
    ``Decoder.recreate`` with a one-point index at origin. It does not use
    that engine: ``Decoder`` is a push interface fed compressed bytes, while
    unrar produces plaintext on stdout from a path. ``AesDecryptStream`` is
    a different shape — O(1) CBC restart, no replay — and does not count
    toward extracting a shared restart-and-replay base. A third pull-shaped
    replay producer would be that point.
    """

    def __init__(
        self,
        spawn: Callable[[], BinaryIO],
        inner: BinaryIO,
        *,
        size: int,
    ) -> None:
        super().__init__()
        self._spawn = spawn
        self._inner: BinaryIO | None = inner
        self._size = size
        self._pos = 0
        self._pipe_pos = 0

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return self._pos

    def _close_inner(self) -> None:
        inner = self._inner
        self._inner = None
        # A replacement process always starts at byte 0. Reset even if close
        # raises, so a later read cannot label those bytes with the old offset.
        self._pipe_pos = 0
        if inner is not None:
            inner.close()

    def _ensure(self) -> BinaryIO:
        if self._inner is None:
            self._inner = self._spawn()
        return self._inner

    def _pipe_needed(self, logical: int) -> int:
        return min(logical, self._size)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError(f"invalid whence ({whence})")
        if target < 0:
            raise ValueError(f"negative seek position: {target}")
        needed = self._pipe_needed(target)
        if needed < self._pipe_pos:
            # Close first; set the logical cursor only if close succeeds, so a
            # failed seek leaves tell() unchanged (Python IO). Spawn is lazy —
            # _sync_pipe calls _ensure on the next read. Spawning here would
            # start a process that close-without-read would kill.
            self._close_inner()
        self._pos = target
        return self._pos

    def _sync_pipe(self) -> BinaryIO:
        inner = self._ensure()
        want = self._pipe_needed(self._pos)
        if want > self._pipe_pos:
            try:
                skip_forward(inner, want - self._pipe_pos)
            except EOFError as exc:
                raise TruncatedError(
                    f"unrar output ended after {self._pipe_pos} of {self._size} bytes"
                ) from exc
            self._pipe_pos = want
        return inner

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._pos > self._size:
            return b""
        inner = self._sync_pipe()
        if self._pos < self._size:
            remaining = self._size - self._pos
            if n < 0 or n > remaining:
                n = remaining
        else:
            # At declared size the overrun probe must reach the pipe, but only
            # for one byte — read(-1) here would pull the rest of a corrupt
            # member into memory.
            n = 1 if n < 0 else min(n, 1)
        data = inner.read(n)
        self._pos += len(data)
        # Overrun probe at pos == size can return one extra byte. Count it in
        # _pos (so pos > size reads are empty) but not as pipe progress past
        # _size, or the next seek including SEEK_CUR would kill the process.
        self._pipe_pos = min(self._pipe_pos + len(data), self._size)
        return data

    def close(self) -> None:
        if self.closed:
            return
        close_error: BaseException | None = None
        try:
            self._close_inner()
        except BaseException as exc:  # noqa: BLE001 - mark closed even if inner.close fails
            close_error = exc
        super().close()
        if close_error is not None:
            raise close_error


def _bounded_member_pipe(inner: BinaryIO, *, prefix: int, size: int) -> BinaryIO:
    """Own an ``unrar`` pipe, skip a glob-match prefix, then EOF at ``size``.

    ``unrar -n./name-with-wildcards`` concatenates every matching member with no
    headers. The parsed member list tells us how many unpacked bytes sit before
    the target; after that we must stop, or the fused overrun probe would see the
    next match as extra payload.

    The skip is eager at construction. A seekable wrapper respawns lazily on the
    next ``read()``, so this factory — and the skip — runs then, not inside
    ``seek()``. ``seek(0, SEEK_END); seek(0)`` before any read still costs
    nothing: ``_pipe_pos`` stays 0 and no new pipe is built.

    The bound itself is a non-seekable :class:`SlicingStream` (same shape as the
    7z folder-pipe member slice). ``SharedView`` is the locked re-seek door and
    requires a seekable source; the inner here is a subprocess pipe.
    """
    try:
        assert not is_seekable(inner), (
            "bounded member pipe requires a non-seekable unrar stdout"
        )
        if prefix:
            skip_forward(inner, prefix)
        return SlicingStream(inner, length=size, owns_inner=True)
    except EOFError as exc:
        inner.close()
        raise TruncatedError(
            "unrar pipe ended before the requested glob-matched member"
        ) from exc
    except BaseException:
        inner.close()
        raise


# What a service header's payload would have answered, for the diagnostic that
# reports one whose walk stopped: the payload is then refused, because a header
# nobody finished reading may be hiding the record that says it is ciphertext.
_SERVICE_PAYLOAD_LOST = {
    "CMT": "the archive comment",
    "QO": "the quick-open index",
}


class RarReader(BaseArchiveReader):
    """Reads RAR archives: native metadata parse + RARLAB ``unrar`` for data."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: ArchiveSource,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
        *,
        volume_count: int = 1,
        start_offset: int = 0,
    ) -> None:
        super().__init__(
            ArchiveFormat.RAR,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        del encoding  # RAR names are decoded by the native parser.
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        # The candidate each RAR5 encryption record's PswCheck accepted, keyed by the
        # record's salt, KDF cost and check. RAR writes one salt per archiving run,
        # so this is usually one derivation per archive rather than one per member.
        self._checked_passwords: dict[tuple[bytes, int, bytes], bytes] = {}
        # The tweaked-digest HashKey derived from that candidate, under the same key:
        # a member's digest check is built more than once per open.
        self._hash_keys: dict[tuple[bytes, int, bytes], bytes] = {}
        # The first member whose PswCheck can judge a candidate, found once on first
        # use; ``False`` until looked for, ``None`` when there is none.
        self._archive_check_member: ArchiveMember | None | Literal[False] = False
        self._volume_count = max(source.volume_count, volume_count)
        self._temp_path: Path | None = None
        self._temp_dir: Path | None = None
        self._owned_concat: ConcatenatedFile | None = None
        self._archive_path: Path | None = None
        # Guards the check-then-write in ``_ensure_archive_path``: two concurrent
        # compressed opens used to both see ``None`` and both copy, and close
        # only removed the winner.
        self._materialize_lock = threading.Lock()
        self._volume_paths: list[Path] = []
        # Stream volumes, kept unmaterialized until unrar actually needs files.
        self._stream_volume_items: list[Path | BinaryIO] = []
        self._volume0_parse_origin = 0  # set after sibling discovery when origin > 0
        # Open-time caveat from source shape, not from later materialization
        # (CostReceipt is a static snapshot; see access-mode-and-cost).
        self._cost_notes = _rar_stream_copy_cost_notes(source)

        if not source.seekable():
            raise StreamNotSeekableError(
                "RAR archives require a seekable source: headers and stored member "
                "ranges are addressed by offsets.",
                archive_name=archive_name,
                source_format=ArchiveFormat.RAR,
            )

        # Where the RAR proper starts inside ``source``: detection's payload_offset
        # for a self-extracting file, 0 otherwise. The parser scan skips invalid
        # decoys the same way detection does (then falls back to the first
        # identified candidate if none validate), but pinning volume 1 to that
        # origin still avoids a second scan, and still matters for a CRC-valid
        # decoy that would win as first-VALID. ConcatenatedFile + parser ``tell()``
        # offsets are file-absolute (each volume contributes its full size, stub
        # included), so stored reads must not also shift by ``_origin`` — that is
        # why a discovered multi-volume set zeroes it after copying it to
        # ``_volume0_parse_origin``.
        self._origin = start_offset
        self._shared = self._open_shared_source(source)
        if self._origin and self._volume_set_size() > 1:
            self._volume0_parse_origin = self._origin
            self._origin = 0
        self._archive, self._unrar_password = self._parse_archive()
        # unrar only consults the password when something is actually encrypted, so
        # a spawn for a plain archive is not given one: nothing is decrypted with it,
        # and handing a secret to a subprocess that ignores it buys nothing. This is
        # also what ``get_archive_info`` reports as ``is_encrypted``: one predicate,
        # so what the caller is told and what reaches the subprocess cannot drift.
        #
        # ``info.is_encrypted`` here is the definite answer, not the fail-closed one
        # the member is presented with: a member whose header was cut short says
        # nothing about the archive around it. Reading the presented flag instead
        # would let one damaged member report a wholly plaintext archive as
        # encrypted, hand the caller's password to every ``unrar`` spawn for it, and
        # relabel an ordinary empty read as a wrong password.
        self._archive_has_encryption = self._archive.has_header_encryption or any(
            info.is_encrypted for info in self._archive.members
        )
        if self._archive.is_volume or self._volume_count > 1:
            self._volume_count = max(self._volume_count, self._volume_set_size() or 1)
        self._archive.comment = self._resolve_rar3_comment(self._archive.comment)
        for info in self._archive.members:
            info.comment = self._resolve_rar3_comment(info.comment)
        self._members = [self._to_member(info) for info in self._archive.members]
        # SERVICE headers (``CMT``, ``QO``) are not members, so the walk above never
        # reaches them, and a damaged one would otherwise report nothing under any
        # policy.
        self._emit_service_header_diagnostics()

    def _open_shared_source(self, source: ArchiveSource) -> SharedSource:
        """Build SharedSource, discovering/materializing volumes as needed."""
        wrap = self._seek_handle_wrapper()
        path = source.path
        if path is not None:
            siblings = discover_volume_siblings(path)
            if siblings is not None and len(siblings) > 1:
                # The source reads volume 1 only; ``unrar`` walks the set on disk, and
                # the header walk reads across it through a join this reader builds.
                # That join is the one source-level object a backend still opens and
                # closes itself, because the boundary hands RAR volume 1's path.
                self._volume_paths = siblings
                self._volume_count = len(siblings)
                self._archive_path = siblings[0]
                concat = ConcatenatedFile(siblings)
                self._owned_concat = concat
                return SharedSource(concat, wrap_handle=wrap)
            self._volume_paths = [path]
            self._archive_path = path
            return SharedSource(source, wrap_handle=wrap)

        joined = source.joined
        if isinstance(joined, ConcatenatedFile):
            paths = source.volume_paths
            if paths:
                # Path volumes: prefer real sibling files for unrar.
                self._volume_paths = paths
                self._volume_count = len(paths)
                self._archive_path = paths[0]
                return SharedSource(source, wrap_handle=wrap)
            # Stream volumes: parse from the originals; copy for unrar only when
            # a member actually needs one (_ensure_archive_path).
            items = joined.volume_items
            self._stream_volume_items = items
            self._volume_count = len(items)
            return SharedSource(source, wrap_handle=wrap)

        # Single non-path stream — materialize later when unrar is needed.
        return SharedSource(source, wrap_handle=wrap)

    def _volume_set_size(self) -> int:
        """Volumes in this set, whether or not they are files yet."""
        return max(len(self._volume_paths), len(self._stream_volume_items))

    def _materialize_stream_volumes(self) -> None:
        """Write ordered volumes into a temp dir with ``name.partN.rar`` names.

        Called from :meth:`_ensure_archive_path`, on the first read ``unrar``
        has to serve — not from ``__init__``. Listing a stream-volume set never
        reaches this, so a caller that only lists writes nothing.

        Stream items are copied through a :class:`SharedSource` view rather than
        read directly, so the copy takes the same lock every other read of these
        volumes takes. ``Path`` items are copied by the filesystem: they are not
        shared state. Assignments to ``_archive_path`` / ``_volume_paths`` /
        ``_temp_dir`` are a different lock: the caller
        (:meth:`_ensure_archive_path`) holds ``_materialize_lock`` across this
        method so two concurrent opens cannot each copy and leave the loser's
        directory behind on ``close()``.
        """
        items = self._stream_volume_items
        ranges = self._stream_volume_ranges()
        temp_dir = Path(tempfile.mkdtemp(prefix="archivey-rar-vol-"))
        self._temp_dir = temp_dir
        stem = "archive"
        if self._archive_name:
            stem = Path(self._archive_name).stem or stem
        paths: list[Path] = []
        try:
            for index, item in enumerate(items, start=1):
                dest = temp_dir / f"{stem}.part{index}.rar"
                if isinstance(item, Path):
                    shutil.copy2(item, dest)
                else:
                    start, size = ranges[index - 1]
                    view = self._shared.view(start, size)
                    try:
                        with dest.open("wb") as out:
                            shutil.copyfileobj(view, out, length=1 << 20)
                    finally:
                        view.close()
                paths.append(dest)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._temp_dir = None
            raise
        self._volume_paths = paths
        self._archive_path = paths[0]

    def _stream_volume_ranges(self) -> list[tuple[int, int]]:
        """``(start, size)`` per volume in the concatenated space this reader reads."""
        assert self._source is not None
        joined = self._source.joined
        assert isinstance(joined, ConcatenatedFile), (
            "stream volumes only come from a joined source"
        )
        return joined.volume_ranges

    def _parse_archive(self) -> tuple[RarArchive, str | None]:
        max_members = self._config.listing_limits.max_members

        def parse(password: bytes | None) -> RarArchive:
            if len(self._volume_paths) > 1:
                handles: list[BinaryIO] = []
                try:
                    for index, path in enumerate(self._volume_paths):
                        handle = path.open("rb")
                        if index == 0 and self._volume0_parse_origin:
                            handle.seek(self._volume0_parse_origin)
                        handles.append(handle)
                    return parse_rar_volumes(
                        handles, password=password, max_members=max_members
                    )
                finally:
                    for handle in handles:
                        handle.close()

            if len(self._stream_volume_items) > 1:
                # Stream volumes, not yet copied anywhere. The header walk needs
                # each volume as its own stream positioned at its start, which
                # the concatenation cannot be, so mint one bounded view per
                # volume over the same source. Views are non-owning and take the
                # shared lock, so nothing here touches the caller's streams.
                views: list[BinaryIO] = []
                try:
                    for index, (start, size) in enumerate(self._stream_volume_ranges()):
                        view = self._shared.view(start, size)
                        views.append(view)
                        if index == 0 and self._volume0_parse_origin:
                            view.seek(self._volume0_parse_origin)
                    return parse_rar_volumes(
                        views,
                        password=password,
                        max_members=max_members,
                    )
                finally:
                    for view in views:
                        view.close()

            # Single volume: a path source, or a lone stream. Nothing is copied
            # on this path any more — a stream volume *set* is handled above, from
            # views over the originals, and the copy waits for a member read.
            if self._volume_paths:
                with self._volume_paths[0].open("rb") as handle:
                    handle.seek(self._origin)
                    return parse_rar_archive(
                        handle, password=password, max_members=max_members
                    )

            view = self._shared.view(0)
            try:
                view.seek(self._origin)
                archive = parse_rar_archive(
                    view, password=password, max_members=max_members
                )
                if archive.needs_next_volume or archive.is_volume:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: additional volumes required"
                    )
                return archive
            finally:
                view.close()

        try:
            try:
                archive = parse(None)
            except EncryptionError:
                if not self._passwords.has_passwords():
                    raise
                archive = self._passwords.attempt(None, parse)
            # Incomplete set opened as a lone volume-1 path with no siblings.
            if archive.needs_next_volume and self._volume_set_size() <= 1:
                raise TruncatedError(
                    "Incomplete RAR multi-volume set: end of archive expects "
                    "another volume"
                )
            # _first_candidate_str is the first configured candidate when
            # headers parsed without a password (data-only encryption; a wrong
            # guess is rejected by PswCheck or unrar exit 11). After
            # attempt(), record_success has moved the working password to
            # the front of _known_good, so the same call is the password that
            # worked. core.py mints a fresh _PasswordCandidates per
            # open_archive, so _known_good is not shared across archives.
            return archive, self._first_candidate_str()
        except _PasswordCandidatesExhausted as exc:
            message = (
                exc.last_error.message
                if exc.last_error is not None
                else "Password required to decrypt RAR headers"
            )
            raise EncryptionError(message) from exc

    def _unrar_data_password(self) -> str | None:
        """The password to hand an ``unrar`` spawn, or ``None`` when none is needed.

        ``_first_candidate_str`` returns a configured candidate whenever the caller
        supplied one, encrypted archive or not. Passing that on made every ``unrar``
        spawn carry a password the archive has no use for — which is how a password
        that cannot be handed to ``unrar`` at all (see
        :func:`rar_unrar._password_stdin_bytes`) stopped a plain archive from opening.

        The gate is the whole archive rather than the member being read, and
        deliberately so: on a solid archive a plain member's block can sit behind an
        encrypted one, so ``unrar`` may need the password to reach a member that is
        not itself encrypted. An archive with nothing encrypted anywhere needs it on
        no path at all, which is the case worth cutting.
        """
        return self._unrar_password if self._archive_has_encryption else None

    def _first_candidate_str(self) -> str | None:
        """Password to hand unrar / ConvertHashToMAC, or ``None``.

        Relies on ``_PasswordCandidates.iter_candidates`` yielding
        ``_known_good`` first. After a successful ``attempt()``, that front
        item is the password that worked, not the originally first candidate.
        """
        for password in self._passwords.iter_candidates():
            return _password_as_str(password)
        return None

    def _checked_password(
        self,
        enc: RarEncryptionInfo,
        member: ArchiveMember | None,
        *,
        ask_provider: bool,
    ) -> bytes | None:
        """The candidate ``enc``'s PswCheck accepts, found by trying them in order.

        ``enc`` must pass :func:`_psw_check_usable`. With ``ask_provider`` this is the
        ordinary per-unit attempt (known-good, then the list, then the provider) and
        exhaustion raises ``_PasswordCandidatesExhausted``. Without it, only the
        concrete candidates are tried and ``None`` means none matched: a caller that
        must not prompt (building a digest check for a member nobody read yet) still
        gets the right password when the caller listed it, just not first.
        """
        assert enc.check_value is not None
        check_value = enc.check_value
        key = (enc.salt, enc.kdf_count, check_value)
        found = self._checked_passwords.get(key)
        if found is not None:
            return found

        def check(password: bytes) -> bytes:
            try:
                _check_rar5_password(
                    check_value,
                    enc.kdf_count,
                    enc.salt,
                    _password_as_str(password) or "",
                )
            except (EncryptionError, UnicodeError):
                raise EncryptionError("Wrong password for this RAR member") from None
            return password

        if ask_provider:
            found = self._passwords.attempt(member, check)
        else:
            for password in self._passwords.iter_candidates():
                try:
                    found = check(password)
                except EncryptionError:
                    continue
                break
            if found is None:
                return None
        self._checked_passwords[key] = found
        return found

    def _member_data_password(self, member: ArchiveMember) -> str | None:
        """The password to hand the ``unrar`` spawn that reads ``member``.

        ``unrar`` takes one password and cannot try a list, so a candidate list is
        resolved here. A RAR5 member carries a PswCheck, so its password is picked
        per member, before ``unrar`` runs. A plain member of a solid archive may sit
        behind encrypted ones and needs their password; a plain member of a non-solid
        one needs none worth resolving. Without a usable check (RAR4) nothing can
        judge a candidate before ``unrar`` runs, so ``unrar`` gets the first one.
        """
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)
        enc = raw.file_encryption
        if enc is not None and _psw_check_usable(enc):
            return self._checked_data_password(enc, member)
        if self._archive.is_solid:
            return self._archive_data_password()
        return self._unrar_data_password()

    def _archive_data_password(self) -> str | None:
        """The password for an ``unrar`` spawn that decodes the whole archive.

        Taken from the first member whose PswCheck can judge a candidate; the pass
        spawn and a solid archive's plain members read through it.
        """
        if self._passwords.has_passwords():
            member = self._archive_check_member
            if member is False:
                member = next(
                    (
                        candidate
                        for candidate in self._members
                        if isinstance(candidate._raw, RarMemberInfo)
                        and candidate._raw.file_encryption is not None
                        and _psw_check_usable(candidate._raw.file_encryption)
                    ),
                    None,
                )
                self._archive_check_member = member
            if member is not None:
                raw = member._raw
                assert isinstance(raw, RarMemberInfo)
                assert raw.file_encryption is not None
                return self._checked_data_password(raw.file_encryption, member)
        return self._unrar_data_password()

    def _checked_data_password(
        self, enc: RarEncryptionInfo, member: ArchiveMember
    ) -> str | None:
        if not self._passwords.has_passwords():
            # Nothing to try: unrar reports the missing password itself, as before.
            return None
        try:
            found = self._checked_password(enc, member, ask_provider=True)
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError(
                raw_message_of(exc),
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            ) from exc
        assert found is not None
        return _password_as_str(found)

    def _ensure_archive_path(self) -> Path:
        """Return a filesystem path ``unrar`` can open (materialize streams once).

        Concurrent compressed ``open()`` calls share one copy: the check-then-write
        is under ``_materialize_lock``, and ``_close_archive`` takes the same lock
        so it cannot unlink while a copy is still landing, or miss the loser's
        directory if two copies raced.
        """
        if self._archive_path is not None:
            return self._archive_path
        with self._materialize_lock:
            if self._archive_path is not None:
                return self._archive_path
            if self._stream_volume_items:
                # Stream volumes: unrar needs sibling files on disk, so the whole set
                # is written, not just the volume holding this member. Nothing before
                # this point needed them — the listing was parsed from the originals.
                self._materialize_stream_volumes()
                assert self._archive_path is not None
                return self._archive_path
            # Single stream source: write one temp .rar for unrar.
            fd, name = tempfile.mkstemp(suffix=".rar")
            path = Path(name)
            try:
                with os.fdopen(fd, "wb") as out:
                    # From the origin, so the temp holds the payload alone. A path source
                    # keeps its own path here and `unrar` sees the stub, which it handles
                    # natively; this branch is the stream case, where making the temp a
                    # plain RAR is both smaller and one less thing to rely on.
                    view = self._shared.view(self._origin)
                    try:
                        # Keep the 1 MiB chunk: each SharedView read takes the lock
                        # and seek+reads, so copyfileobj's 64 KiB default is ~16×
                        # the acquisitions. This method already holds the mkstemp
                        # fd, so copyfileobj writes to it rather than opening dest.
                        shutil.copyfileobj(view, out, length=1 << 20)
                    finally:
                        view.close()
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self._temp_path = path
            self._archive_path = path
            return path

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._members

    def _resolve_rar3_comment(self, comment: str | _Rar3Comment | None) -> str | None:
        """Return a parsed old-style comment, dropping unavailable/invalid payloads."""
        if not isinstance(comment, _Rar3Comment):
            return comment
        try:
            unpacked = decompress_rar3_blob(
                extract_version=comment.extract_version,
                compress_type=comment.compress_type,
                packed=comment.packed,
                unpacked_size=comment.unpacked_size,
                flags=comment.flags,
                crc16=comment.crc16,
                password=self._unrar_data_password(),
            )
        except (
            OSError,
            PackageNotInstalledError,
            UnsupportedOperationError,
            subprocess.SubprocessError,
        ):
            # An undecodable comment degrades to None rather than sinking the
            # listing. UnsupportedOperationError belongs here for the same reason:
            # a password unrar cannot be given is a reason to lose the comment, not
            # a reason for open_archive to fail.
            return None
        if unpacked is None or zlib.crc32(unpacked) & 0xFFFF != comment.crc16:
            return None
        return _decode_name(unpacked)

    def _to_member(self, info: RarMemberInfo) -> ArchiveMember:
        member_type = self._member_type(info)
        version_history = info.is_file_version_history()
        presented = _presented_filename(info)
        member_comment = info.comment
        assert not isinstance(member_comment, _Rar3Comment)
        name = normalize_member_name(
            presented,
            member_type,
            backslash_is_separator=True,
        )
        # raw_name keeps archive-stored path bytes (RAR5 has no ``;n`` in header;
        # RAR3 may store ``path;n`` bytes — we do not rewrite them).
        raw_name = (
            info.orig_filename
            if info.orig_filename is not None
            else info.filename.encode("utf-8", errors="surrogateescape")
        )
        extra, link_target = _rar_member_extra_and_link(info)
        host_os = info.host_os
        create_system = (
            _RAR_HOST_OS_TO_CREATE_SYSTEM.get(host_os, CreateSystem.UNKNOWN)
            if host_os is not None
            else CreateSystem.UNKNOWN
        )
        mode: int | None = None
        windows_attrs: int | None = None
        if info.mode is not None:
            # RAR5 stores attr as a vint (hostile values can exceed C unsigned long).
            # Unix: ArchiveMember.mode is the low 12 permission bits (S_IMODE);
            # mask before the C helper so OverflowError cannot abort listing.
            # Win32: FILE_ATTRIBUTE_* is a 32-bit field.
            if host_os == _RAR_HOST_OS_UNIX:
                mode = stat.S_IMODE(info.mode & 0o7777)
            elif host_os == _RAR_HOST_OS_WIN32:
                windows_attrs = info.mode & 0xFFFFFFFF

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=info.mtime,
            accessed=info.atime,
            created=info.ctime,
            mode=mode,
            compression=_compression_for(info),
            # Fails closed: a member whose header stopped before the encryption
            # record could be ruled out is presented as encrypted. Answering
            # "not encrypted" from a header nobody finished reading is a wrong
            # answer rather than a missing one, which is the class this library
            # ranks worst. ``ArchiveInfo.is_encrypted`` deliberately does *not*
            # follow: see ``_archive_has_encryption``.
            is_encrypted=info.is_encrypted or info.encryption_unknown,
            is_current=not version_history,
            create_system=create_system,
            windows_attrs=windows_attrs,
            hashes=_member_hashes(info),
            link_target=link_target,
            comment=member_comment,
            extra=extra,
            _raw=info,
        )
        self._emit_member_diagnostics(info, member, presented)
        return member

    def _emit_member_diagnostics(
        self, info: RarMemberInfo, member: ArchiveMember, presented: str
    ) -> None:
        """Name-normalization, dropped-header-record and tweaked-digest diagnostics.

        All attach onto ``member`` (``attach_to_member=True``) and can raise
        under a strict collector, so this must run before ``_to_member`` returns.
        """
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
        )
        self._emit_header_record_diagnostics(info, member.name, member)
        # Pure; same predicate ``_rar_member_extra_and_link`` uses for extra keys.
        if not _crc_is_tweaked(info) or self._unrar_password is not None:
            return
        # No password → cannot forward-transform; surface as unverifiable digests.
        for algo, present in (
            (HashAlgorithm.CRC32, info.crc32 is not None),
            (HashAlgorithm.BLAKE2SP, info.blake2sp_hash is not None),
        ):
            if not present:
                continue
            self._diagnostics_collector.emit(
                code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                message=(
                    f"Cannot verify tweaked RAR5 {algo} without a password "
                    f"(ConvertHashToMAC); skipping integrity check for it."
                ),
                context=DigestContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    algorithm=algo.value,
                    reason="tweaked_checksum",
                ),
                member=member,
                attach_to_member=True,
                logger=integrity_logger,
            )

    @staticmethod
    def _member_type(info: RarMemberInfo) -> MemberType:
        if info.is_directory:
            return MemberType.DIRECTORY
        if info.is_hardlink_or_copy:
            return MemberType.HARDLINK
        if info.is_symlink:
            return MemberType.SYMLINK
        return MemberType.FILE

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._archive.is_solid:
            # Nonsolid: default lazy per-member named opens (never ALL-pipe demux).
            yield from super()._iter_with_data()
            return

        # Bare ``unrar p`` omits ``-ver`` history from the ALL pipe; pass ``-ver``
        # when any versioned payload FILE is present so demux stays aligned.
        version_control = any(
            isinstance(m._raw, RarMemberInfo)
            and m._raw.is_payload_file()
            and m._raw.is_file_version_history()
            for m in self._members
        )
        solid: SolidBlockReader | None = None

        def _pipe() -> SolidBlockReader:
            """Spawn ``unrar p`` on the first read into the pass, not at pass start.

            A caller that iterates the pass without reading any member — listing a
            solid RAR through ``stream_members``, or an extraction whose selector
            matches nothing — never spawns ``unrar`` and is never asked for a
            password. The stream-source copy lives here too, so that caller also
            writes nothing.
            """
            nonlocal solid
            if solid is None:
                password = self._archive_data_password()
                path = self._ensure_archive_path()
                proc, stdout = open_unrar_p(
                    path,
                    password=password,
                    version_control=version_control,
                )
                # Between Popen and the wrapper taking ownership, a raise would
                # leave the process unowned. Terminate before the wrapper exists;
                # after that, owned.close() reaps the process and the stdout pipe.
                try:
                    owned: BinaryIO = _UnrarOwnedStream(
                        stdout, proc, has_verifiable_hash=True
                    )
                except BaseException:
                    terminate_unrar(proc)
                    raise
                try:
                    # Each payload member in the pipe is verified individually (CRC/BLAKE2sp
                    # and declared length via fused ArchiveStream verify), so the pipe-level
                    # unrar exit code is redundant for corruption and is suppressed here to
                    # avoid legacy-format false positives; wrong-password (11) still maps.
                    owned = self._track_decompressed(owned)
                    solid = SolidBlockReader(owned)
                except BaseException:
                    owned.close()
                    raise
            return solid

        pipe_offset = 0

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            nonlocal pipe_offset
            raw = member._raw
            assert isinstance(raw, RarMemberInfo)
            if not raw.is_payload_file() or not member.is_file:
                return None
            size = _member_stream_size(member)
            # Capture the pipe offset for this member, then advance the running
            # cursor. The pipe itself is spawned, and the skip-decode to this
            # offset run, on the first read; verify is fused into the outer
            # ArchiveStream so a never-opened handle skips verify on close (no
            # solid positioning, and no ``unrar``, for unread members).
            member_offset = pipe_offset
            pipe_offset += size

            hashes, vsize, transforms, verify_member = self._payload_verify_args(member)
            return self._wrap_member_stream(
                None,
                member.name,
                open_fn=lambda: _pipe().open_member(member_offset, size, lazy=True),
                size=member.size,
                track_output=False,
                seekable=False,
                expected_hashes=hashes,
                expected_size=vsize,
                digest_transforms=transforms,
                verify_member=verify_member,
            )

        def _cleanup() -> None:
            if solid is not None:
                solid.close()

        yield from self._drive_pass_streams(
            iter(self._members),
            open_member=_open,
            close_previous=True,
            cleanup=_cleanup,
        )

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, EOFError):
            return TruncatedError("RAR solid stream ended before the requested member")
        return None

    def _tweaked_verify_spec(
        self, info: RarMemberInfo
    ) -> (
        tuple[
            dict[HashAlgorithm, bytes],
            dict[HashAlgorithm, Callable[[bytes], bytes]],
        ]
        | None
    ):
        """Build ``(expected, digest_transforms)`` for tweaked RAR5 checksums.

        Returns ``None`` when checksums are not tweaked, no password is available, or
        the password is provably wrong (PswCheck). Expected values are the *stored*
        (already tweaked) digests; transforms apply ``ConvertHashToMAC`` to the
        plaintext digest before compare.
        """
        if not _crc_is_tweaked(info):
            return None
        enc = info.file_encryption
        if enc is None:
            return None
        if _psw_check_usable(enc):
            # The candidate the check accepts, not the first one: the first may be
            # another member's password, and its HashKey would fail good data.
            # Never prompts; a provider's answer is here once a read asked for it.
            checked = self._checked_password(enc, None, ask_provider=False)
            if checked is None:
                return None
            assert enc.check_value is not None
            cache_key = (enc.salt, enc.kdf_count, enc.check_value)
            hash_key = self._hash_keys.get(cache_key)
            if hash_key is None:
                hash_key = rar5_hash_key(
                    _password_as_str(checked) or "", enc.salt, enc.kdf_count
                )
                self._hash_keys[cache_key] = hash_key
        else:
            password = self._unrar_password
            if password is None:
                return None
            hash_key = _tweaked_hash_key(enc, password)
            if hash_key is None:
                return None
        expected: dict[HashAlgorithm, bytes] = {}
        transforms: dict[HashAlgorithm, Callable[[bytes], bytes]] = {}
        if info.crc32 is not None:
            expected[HashAlgorithm.CRC32] = crc32_digest(info.crc32)
            transforms[HashAlgorithm.CRC32] = lambda digest, hk=hash_key: (
                convert_crc_to_mac(int.from_bytes(digest, "big"), hk).to_bytes(4, "big")
            )
        if info.blake2sp_hash is not None:
            expected[HashAlgorithm.BLAKE2SP] = info.blake2sp_hash
            transforms[HashAlgorithm.BLAKE2SP] = lambda digest, hk=hash_key: (
                convert_blake2sp_to_mac(digest, hk)
            )
        if not expected:
            return None
        return expected, transforms

    def _emit_header_record_diagnostics(
        self,
        info: RarMemberInfo | DamagedServiceHeader,
        name: str,
        member: ArchiveMember | None,
    ) -> None:
        """Report what a RAR5 extra-area walk dropped, and why it stopped.

        ``member`` is ``None`` for a SERVICE header (``CMT``, ``QO``), which is not
        a listed member and so has nothing to attach to. It still comes through
        here: the same leniency applies to its header, and the argument for
        dropping a record rather than refusing the archive is that the diagnostic
        is emitted and ``ARCHIVE_INTEGRITY_CODES`` makes a strict policy refuse.
        A service header that said nothing was the one place that argument did not
        hold.

        The two cases do not get the same words. A member's is about a member that
        was listed; a service header's is about a header that is in no listing, and
        what a reader wants to know is which of the archive's own answers went
        missing with it. Sharing the wording named ``CMT`` as a member the caller
        could then not find, and never mentioned the comment it had withheld. The
        context follows the same split: ``member_name`` is empty and ``member_id``
        is ``None`` for a service header, because there is no member to name.
        """
        member_id = member._member_id if member is not None else None
        attach = member is not None
        context_name = name if member is not None else ""
        for record, record_id, reason in info.skipped_header_records:
            named = record if record_id is None else f"{record} ({record_id})"
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED,
                message=(
                    f"RAR5 extra record {named} is malformed and was dropped "
                    f"({reason}); the member is listed without what it carried."
                    if member is not None
                    else f"RAR5 extra record {named} in this archive's {name} "
                    f"service header is malformed and was dropped ({reason}); "
                    f"the header was read without what it carried."
                ),
                context=MemberHeaderRecordContext(
                    archive_name=self._archive_name,
                    member_name=context_name,
                    member_id=member_id,
                    record=record,
                    record_id=record_id,
                    reason=reason,
                ),
                member=member,
                attach_to_member=attach,
                logger=logger,
            )
        stop_reason = info.header_walk_stop_reason
        if stop_reason is not None:
            # One diagnostic saying the header was abandoned, rather than one per
            # record past the cap — emitting per record is the cost the cap exists
            # to avoid. Without this a caller sees the capped list and cannot tell
            # it is the whole story. ``list_truncated`` is what they read.
            #
            # The reason comes from the walk because four different faults end it
            # and only one of them is the cap. It is also the only thing that
            # explains why the member may be reported encrypted when nothing in
            # its listing says so, so naming a fault that did not happen costs
            # more here than it would on an ordinary skip.
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED,
                message=(
                    f"This RAR5 member's header was not read to the end because "
                    f"{stop_reason}; it is listed from what was read before that."
                    if member is not None
                    else f"This archive's {name} service header was not read to "
                    f"the end because {stop_reason}, so {_SERVICE_PAYLOAD_LOST.get(name, 'its payload')} "
                    f"was not used."
                ),
                context=MemberHeaderRecordContext(
                    archive_name=self._archive_name,
                    member_name=context_name,
                    member_id=member_id,
                    record="",
                    record_id=None,
                    reason=stop_reason,
                    list_truncated=True,
                ),
                member=member,
                attach_to_member=attach,
                logger=logger,
            )

    def _emit_service_header_diagnostics(self) -> None:
        """Report the SERVICE headers (``CMT``, ``QO``) whose walk did not finish.

        Emitted after the members, so the two kinds are not interleaved in file
        order: a strict policy raises at emit time, so it refuses on a member's
        fault first even where the damaged service header came earlier in the file
        — a ``CMT`` sits right after MAIN, so that is the usual layout. Real file
        order would need each header's offset, which ``DamagedServiceHeader``
        deliberately does not keep.

        The parser caps how many it keeps, so the count of the rest is reported
        too: a cap that silently swallowed the remainder would reopen the hole this
        reporting exists to close.
        """
        for damaged in self._archive.damaged_service_headers:
            self._emit_header_record_diagnostics(damaged, damaged.name, None)
        omitted = self._archive.damaged_service_headers_omitted
        if omitted:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED,
                message=(
                    f"{omitted} further RAR5 service header(s) in this archive "
                    f"were not read to the end and are not described "
                    f"individually; an archive with this many damaged service "
                    f"headers is crafted rather than merely damaged."
                ),
                context=MemberHeaderRecordContext(
                    archive_name=self._archive_name,
                    member_name="",
                    member_id=None,
                    record="",
                    record_id=None,
                    reason="too many damaged service headers to describe",
                    # Not ``list_truncated``: that flag marks the diagnostic
                    # reporting that one header's own record list was cut short,
                    # and this one is behind no header at all. What was cut short
                    # here is the list of headers, which the message says.
                    list_truncated=False,
                ),
                logger=logger,
            )

    def _payload_verify_args(
        self, member: ArchiveMember
    ) -> tuple[
        Mapping[HashAlgorithm, bytes] | None,
        int | None,
        Mapping[HashAlgorithm, Callable[[bytes], bytes]] | None,
        ArchiveMember | None,
    ]:
        """Return ``(hashes, size, transforms, member)`` for fused verify, or Nones.

        Verify every member's declared length (and any CRC32/BLAKE2sp) as it is
        read. Tweaked RAR5 digests (HASHMAC) use ConvertHashToMAC transforms when
        a password is available.
        """
        expected: Mapping[HashAlgorithm, bytes] = member.hashes
        transforms: Mapping[HashAlgorithm, Callable[[bytes], bytes]] | None = None
        raw = member._raw
        if isinstance(raw, RarMemberInfo):
            tweaked = self._tweaked_verify_spec(raw)
            if tweaked is not None:
                expected, transforms = tweaked
        if member.size is None and not expected:
            return None, None, None, None
        return expected, member.size, transforms, member

    def _wrap_payload_stream(
        self,
        inner: BinaryIO,
        member: ArchiveMember,
        *,
        track_output: bool = True,
        rewind_warning: RewindWarning | None = None,
    ) -> ArchiveStream:
        hashes, size, transforms, verify_member = self._payload_verify_args(member)
        return self._wrap_member_stream(
            inner,
            member.name,
            size=member.size,
            track_output=track_output,
            expected_hashes=hashes,
            expected_size=size,
            digest_transforms=transforms,
            verify_member=verify_member,
            rewind_warning=rewind_warning,
        )

    def _is_directly_sliceable(self, info: RarMemberInfo) -> bool:
        """Everything the direct read needs except an answer about encryption.

        Split out so ``_open_member`` can tell a member that genuinely needs
        ``unrar`` from one whose bytes are sitting right there and are held back
        only because the header never settled whether they are ciphertext.
        """
        return (
            info.compress_type == _RAR_METHOD_STORED
            and not info.file_solid
            and not info.split_after
            and not info.split_before
            and not info.spanned_volumes
        )

    def _can_direct_read(self, info: RarMemberInfo) -> bool:
        # ``encryption_unknown`` is excluded here rather than refused: slicing the
        # stored bytes and handing them straight back would present ciphertext as
        # plaintext if the unread record was the encryption record, so that member
        # goes through ``_confirm_unsettled_plaintext`` first (see ``_open_member``).
        return (
            self._is_directly_sliceable(info)
            and not info.is_encrypted
            and not info.encryption_unknown
        )

    def _confirm_unsettled_plaintext(
        self, info: RarMemberInfo, member: ArchiveMember
    ) -> None:
        """Prove a cut-short member's stored bytes are plaintext, or raise.

        The member's header stopped before its extra records were read to the end,
        so nothing in it says whether the data is encrypted — and routing to
        ``unrar`` would not settle it either. Measured on unrar 7.00, it reads the
        same damaged header and reaches the same wrong conclusion (``unrar l`` drops
        the encrypted marker); what it actually does is refuse when a digest that
        survived the damage fails against the bytes, and hand them back unverified
        when none survived. Archivey applies the same digest test and refuses the
        second case, which is the maintainer's ruling (see ``format-rar``).

        Which digest survives is the writer's choice, not ours: RAR5 keeps CRC32 in
        the fixed FILE header and BLAKE2sp in the extra area, so a cut area destroys
        one and leaves the other. A surviving digest is a real discriminator because
        an encrypted member's stored digests are key-tweaked (``ConvertHashToMAC``)
        whenever the writer sets that flag, and are the *plaintext* digest when it
        does not — ciphertext matches neither.

        The check runs **before** any byte is handed back, like the ZIP ZipCrypto
        stored path (``_open_stored_confirmed``): both face a member whose framing
        cannot reject wrong bytes incrementally, and a caller that stops reading
        early would otherwise never reach the end-of-stream verdict. The extra pass
        costs one read of an already-damaged member and never touches the happy path.
        """
        hashes, size, transforms, _ = self._payload_verify_args(member)
        # ``member=`` is deliberately omitted: this is a confirmation pass, and any
        # diagnostic it emitted would be attached to the member a second time by the
        # real read that follows.
        verifier = (
            build_member_verifier(
                hashes,
                expected_size=size,
                collector=self._diagnostics_collector,
                archive_name=self._archive_name,
                digest_transforms=transforms,
            )
            if hashes
            else None
        )
        # Two ways to have nothing to go on, and they end the same: the cut took the
        # only digest with it, or the one it left is an algorithm this installation
        # cannot compute. The second matters because such a verifier is dropped
        # silently and would then confirm the member by finding no fault at all.
        if verifier is None or not verifier.expected_algorithms:
            raise CorruptionError(
                "This RAR5 member's header stopped before its extra records were "
                "read to the end, so whether the member is encrypted is unknown, "
                "and no usable checksum survived the damage to tell its stored "
                "bytes from ciphertext. Reading it would risk returning encrypted "
                "bytes as file content.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )
        view = self._direct_view(info)
        try:
            while verifier.read(view, _CONFIRM_CHUNK_BYTES):
                pass
        except TruncatedError as exc:
            # A member whose bytes end short of its declared size is a truncated
            # member, whatever its header said, and relabelling that as a verdict
            # about encryption would name a cause that did not happen. This pass
            # is interpreting one outcome — the digest — and has nothing to add
            # to the others, so they travel as they would on an ordinary read.
            #
            # Stamped because this pass raises from the open path rather than from
            # a member read, which is where the reader's own boundary would have
            # filled these in: without it the same truncation named the archive
            # and the member on an intact header and named neither on a cut-short
            # one.
            self._stamp_error_context(exc, member.name)
            raise
        except CorruptionError as exc:
            # What is left is the digest verdict. The verifier's other
            # ``CorruptionError`` is an over-run, which cannot happen here: the
            # view is bounded to the member's declared size, so the probe past
            # the end reads ``b""`` however much archive follows.
            raise CorruptionError(
                "This RAR5 member's header stopped before its extra records were "
                "read to the end, so whether the member is encrypted is unknown, "
                "and its stored bytes do not match the checksum that survived the "
                "damage: they are either encrypted or corrupt.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            ) from exc
        finally:
            view.close()

    def _direct_view(self, info: RarMemberInfo, length: int | None = None) -> BinaryIO:
        size = info.file_size if length is None else length
        return self._shared.view(info.data_offset, size)

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)
        if raw.file_redir is not None:
            member.link_target = raw.file_redir[2]
            return
        # RAR4: symlink target stored as M0 member data (even when file_solid).
        if (
            raw.compress_type == _RAR_METHOD_STORED
            and not raw.is_encrypted
            and not raw.encryption_unknown
            and raw.file_size > 0
            and not raw.split_before
            and not raw.split_after
        ):
            view = self._shared.view(raw.data_offset, raw.file_size)
            try:
                data = view.read()
            finally:
                view.close()
            member.link_target = data.decode("utf-8", errors="surrogateescape")
            return
        # Encrypted / compressed target without usable direct bytes: leave unset. The
        # reason still has to reach the caller — `SYMLINK_TARGET_UNAVAILABLE` is in
        # `ARCHIVE_INTEGRITY_CODES`, so a strict policy refuses the archive, and a
        # lenient one gets a diagnostic instead of a link whose absence is unexplained.
        #
        # Only the last of these is the archive recording no target; the other three
        # are targets this direct read cannot reach, and the member keeps the
        # per-member failure it had before `LINK_TARGET_UNAVAILABLE` existed. The
        # encrypted one is a limit of reading the bytes straight out of the archive
        # rather than a missing password: this path never decrypts, so a correct
        # password does not change its answer, and claiming one was needed would name
        # a fix that does not work.
        if raw.is_encrypted:
            reason = "target_data_encrypted"
            detail = (
                "its data is encrypted and this reader does not decrypt it in place"
            )
            in_archive = True
        elif raw.split_before or raw.split_after:
            reason = "target_data_split_across_volumes"
            detail = "its data is split across volumes"
            in_archive = True
        elif raw.compress_type != _RAR_METHOD_STORED:
            reason = "target_data_compressed"
            detail = "its data is compressed rather than stored"
            in_archive = True
        else:
            reason = "no_target_data"
            detail = "it carries no data"
            in_archive = False
        self._emit_link_target_unavailable(
            member,
            reason=reason,
            message=(
                f"Cannot read the symlink target of {quoted(member.name)} because {detail}; "
                f"leaving link_target unset."
            ),
            target_in_archive=in_archive,
        )
        return

    def _unrar_glob_prefix(
        self, target: ArchiveMember, presented: str, *, version_control: bool
    ) -> int:
        """Unpacked bytes of earlier payload members that the ``-n`` mask also matches.

        ``unrar`` emits those members concatenated, in archive order, with no
        headers. Zero when the presented name has no glob characters. History
        rows are omitted unless ``version_control`` is set, matching ``unrar``
        (``-ver`` is passed only for a history-row target).

        Matched against :func:`_unrar_mask_for` of the presented name, not the
        name itself: that is the string ``unrar`` was given, and sizing the skip
        against a wider mask would step past bytes the pipe never carried.
        """
        if "*" not in presented and "?" not in presented:
            return 0
        mask = _unrar_mask_for(presented)
        prefix = 0
        for member in self._members:
            raw = member._raw
            if not isinstance(raw, RarMemberInfo) or not raw.is_payload_file():
                continue
            if raw.is_file_version_history() and not version_control:
                continue
            if not _unrar_mask_match(_presented_filename(raw), mask):
                continue
            if member is target:
                return prefix
            prefix += _member_stream_size(member)
        # _open_member is only reached for payload files, so the target is in
        # this walk; identity (``is``) is what makes the skip land on it.
        raise AssertionError(
            "glob target missing from the payload walk; skip uses member identity"
        )

    def _unrar_solid_prefix(self, target: ArchiveMember) -> int:
        """Unpacked bytes of earlier payload members in a solid archive.

        Named ``unrar p`` of a solid member re-decodes this prefix even though
        the pipe only emits the requested member. Zero when the archive is not
        solid — then ``-n`` starts at this member and the member-stream
        ``tell()`` is the whole re-decode cost.

        History rows are counted even without ``-ver``. That is not an oversight
        relative to :meth:`_unrar_glob_prefix`, which skips them unless
        ``version_control``: ``-ver`` controls what unrar *emits*, not what it
        *decodes*, and a solid chain must decompress history to reach later
        members. This value is a ``RewindWarning.min_redecode_bytes`` floor, not
        a pipe offset.
        """
        if not self._archive.is_solid:
            return 0
        prefix = 0
        for member in self._members:
            raw = member._raw
            if not isinstance(raw, RarMemberInfo) or not raw.is_payload_file():
                continue
            if member is target:
                return prefix
            prefix += _member_stream_size(member)
        # Same payload-only walk as glob skip; _open_member is payload-only.
        raise AssertionError(
            "solid prefix target missing from the payload walk; uses member identity"
        )

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)

        if self._can_direct_read(raw):
            inner: BinaryIO = self._direct_view(raw)
            return self._wrap_payload_stream(inner, member)

        if raw.encryption_unknown and self._is_directly_sliceable(raw):
            # This member's bytes are stored and sitting right there; the only thing
            # holding back the slice is that its header stopped before the encryption
            # record, so nothing read so far says whether they are plaintext. A digest
            # that survived the damage can still say, and needs no ``unrar``; when none
            # did, this raises. See ``_confirm_unsettled_plaintext``.
            self._confirm_unsettled_plaintext(raw, member)
            return self._wrap_payload_stream(self._direct_view(raw), member)

        # unrar addresses the member by its presented name (``path`` or ``path;n``) via a
        # ``-n`` include mask (see open_unrar_p); a history row needs ``-ver``. Do not use
        # the normalized ``member.name`` (may differ on separators).
        presented = _presented_filename(raw)
        version_control = raw.is_file_version_history()
        glob_mask = "*" in presented or "?" in presented
        # ``\\`` is a separator to Windows unrar and a literal on Linux; the
        # same ``-n./`` mask therefore matches a different set. Refuse rather
        # than report a valid member truncated (Windows CI on ``a\\b_TGT.txt``).
        if "\\" in presented or (glob_mask and not _unrar_glob_demux_ok(presented)):
            raise UnsupportedFeatureError(
                "RAR member names that contain a backslash or a glob in a "
                "directory component cannot be read through unrar; the "
                "include-mask matcher is only faithful for a glob confined "
                "to the basename.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )
        glob_prefix = self._unrar_glob_prefix(
            member, presented, version_control=version_control
        )
        if glob_prefix and not self._config.rar_allow_glob_member_concatenation:
            # unrar decompresses every earlier match before the target and emits
            # them concatenated. The skip below returns the right bytes, but the
            # decode has already happened and ExtractionLimits do not reach
            # open()/read(). On a nonsolid archive that extra decode is
            # unadvertised and unbounded in the earlier member's size.
            #
            # On a solid archive it is neither. Those members are inside the
            # solid prefix the read pays anyway, so what the mask adds is only
            # the *emit* -- unrar pipes us bytes we then discard. That extra is
            # a bounded transfer cost, roughly 1 ms/MB
            # (dev-docs/formats/rar.md §6), not new work.
            #
            # Refused on both shapes anyway. Maintainer decision (davitf, #372,
            # 2026-09-20): always reject, for internal consistency and so the
            # config flag keeps a single meaning. Two arguments he raised cut
            # the other way and are recorded in rar.md section 7 as a question
            # to revisit: an attacker can simply make the archive solid to
            # sidestep the sharp case, and an out-of-order read of any solid
            # archive already decodes everything ahead of it, glob or not. The
            # names are almost always constructed (davitf, 2026-09-19), with
            # the config flag as the escape hatch.
            # A glob name matching nothing else has `glob_prefix == 0` and
            # never reaches this -- which also means this is **not** a guard
            # against a hostile mask as such: a name built to make a matcher
            # backtrack, with no sibling it can match, has a zero prefix and
            # still goes to unrar. That is bounded separately, by narrowing
            # the mask itself. This bounds the payload, not the match.
            #
            # The predicate is deliberately `_unrar_glob_prefix`'s own answer and
            # not a second walk: which siblings match is decided by the mask
            # actually handed to unrar, which that function owns. Recomputing it
            # from the presented name here would refuse archives that read fine
            # under the mask unrar is given.
            if self._archive.is_solid:
                message = (
                    f"Reading RAR member {quoted(member.name)} is refused: its "
                    "stored name is an unrar include mask that also matches "
                    "earlier members. Names like this are almost always "
                    "constructed. Set "
                    "ArchiveyConfig.rar_allow_glob_member_concatenation=True "
                    "to read it anyway."
                )
            else:
                message = (
                    f"Reading RAR member {quoted(member.name)} would decompress "
                    f"{glob_prefix} bytes of earlier members first: its stored "
                    "name is an unrar include mask that also matches them. Set "
                    "ArchiveyConfig.rar_allow_glob_member_concatenation=True to "
                    "read it anyway."
                )
            raise UnsupportedFeatureError(
                message,
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )

        # Picked before the copy below: a password no candidate satisfies fails here
        # without spooling a stream source to disk.
        data_password = self._member_data_password(member)
        # Prefer our fused digest check (including tweaked ConvertHashToMAC) over
        # unrar's exit code for corruption; wrong-password (11) still maps. After the
        # password: the tweaked check needs the one the member's PswCheck accepted.
        has_hash = bool(member.hashes) or self._tweaked_verify_spec(raw) is not None

        # Only now: every refusal above is decided from the parsed member table and
        # spawns nothing, so a stream source must not be spooled to disk to reach one.
        path = self._ensure_archive_path()

        def _spawn() -> BinaryIO:
            proc, stdout = open_unrar_p(
                path,
                password=data_password,
                member=presented,
                version_control=version_control,
            )
            try:
                owned: BinaryIO = _UnrarOwnedStream(
                    stdout,
                    proc,
                    named_member=True,
                    has_verifiable_hash=has_hash,
                    encrypted=raw.is_encrypted,
                )
            except BaseException:
                terminate_unrar(proc)
                raise
            try:
                tracked = self._track_decompressed(owned)
                if glob_mask:
                    return _bounded_member_pipe(
                        tracked,
                        prefix=glob_prefix,
                        size=_member_stream_size(member),
                    )
                return tracked
            except BaseException:
                # _bounded_member_pipe already closed ``tracked`` (and so ``owned``)
                # if the prefix skip failed; close is idempotent.
                owned.close()
                raise

        # Spawn now so PackageNotInstalledError / a missing stdout pipe surface at
        # open(), and so a spawn-count right after open() is 1 (the live-stream
        # gate's "refused second open does not spawn" pin). Password and
        # corruption still map on the completing read — unrar's exit is only
        # known after the process ends.
        inner = _spawn()
        try:
            rewind: RewindWarning | None = None
            if self._seek_declared():
                inner = _UnrarRespawnStream(
                    _spawn, inner, size=_member_stream_size(member)
                )
                rewind = RewindWarning(
                    codec_name="rar",
                    suggest_install=False,
                    min_redecode_bytes=self._unrar_solid_prefix(member),
                )
            # Folder/pipe output already counted; avoid double-counting at the member wrap.
            # Fused verify in _wrap_payload_stream bounds/checks declared size + digests.
            return self._wrap_payload_stream(
                inner, member, track_output=False, rewind_warning=rewind
            )
        except BaseException:
            inner.close()
            raise

    def _get_archive_info(self) -> ArchiveInfo:
        is_solid = self._archive.is_solid
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.SOLID if is_solid else AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            # RAR solid is one continuous compression context; block count is unknown.
            solid_block_count=None,
            notes=self._cost_notes,
        )
        is_multivolume = (
            self._archive.is_volume
            or self._volume_count > 1
            or self._volume_set_size() > 1
        )
        archive_comment = self._archive.comment
        assert not isinstance(archive_comment, _Rar3Comment)
        info_extra = ArchiveInfoExtra(
            {"rar.volume_count": max(self._volume_count, self._volume_set_size())}
        )
        return ArchiveInfo(
            format=ArchiveFormat.RAR,
            format_version=str(self._archive.version),
            is_solid=is_solid,
            member_count=len(self._members),
            comment=archive_comment,
            is_encrypted=self._archive_has_encryption,
            is_multivolume=is_multivolume,
            cost=cost,
            extra=info_extra,
        )

    def _close_archive(self) -> None:
        with self._materialize_lock:
            self._shared.close()
            if self._owned_concat is not None:
                try:
                    self._owned_concat.close()
                except OSError:
                    pass
                self._owned_concat = None
            if self._temp_path is not None:
                try:
                    self._temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._temp_path = None
            if self._temp_dir is not None:
                # Single-stream copy (_ensure_archive_path) owns _temp_path;
                # stream volumes (_materialize_stream_volumes) own _temp_dir.
                # unlink vs rmtree, so _close_archive unwinds them separately.
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                self._temp_dir = None


class RarReadBackend(ReadBackend):
    """Backend factory for RAR archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.RAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {
        ".rar": ArchiveFormat.RAR,
        ".cbr": ArchiveFormat.RAR,
    }
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, RAR5_ID, ArchiveFormat.RAR),
        MagicSignature(0, RAR_ID, ArchiveFormat.RAR),
    )
    # Both ids, so the scan resolves RAR4 vs RAR5 by which one comes first rather than
    # matching their shared `Rar!\x1a\x07` prefix and re-reading to disambiguate.
    SFX_MAGIC: tuple[MagicSignature, ...] = MAGIC
    SFX_HIT_VALIDATOR = staticmethod(validate_rar_main_header)
    SUPPORTS_PASSWORD = True
    SUPPORTS_STREAMING_NON_SEEKABLE = False
    OPTIONAL_DEPENDENCY = None

    def open_read(
        self,
        source: ArchiveSource,
        format: ArchiveFormat,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
        start_offset: int = 0,
    ) -> RarReader:
        del format
        return RarReader(
            source,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
            start_offset=start_offset,
        )


register_reader(RarReadBackend)

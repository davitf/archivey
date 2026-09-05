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

import io
import os
import shutil
import stat
import subprocess
import tempfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import DiagnosticCode, DigestContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.rar_parser import (
    RAR5_ID,
    RAR_ID,
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
    _unrar_mask_match,
    decompress_rar3_blob,
    open_unrar_p,
    terminate_unrar,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.rar_detect import validate_rar_main_header
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream, RewindWarning
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    ReadOnlyIOStream,
    SharedSource,
    SolidBlockReader,
    is_seekable,
    is_stream,
    skip_forward,
)
from archivey.internal.volumes import ConcatenatedFile, discover_volume_siblings
from archivey.types import (
    EXTRA_IS_JUNCTION,
    EXTRA_RAR_CREATED_IS_CTIME,
    EXTRA_RAR_EXTRACT_VERSION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    HashAlgorithm,
    MagicSignature,
    MemberStreams,
    MemberType,
    crc32_digest,
)

# rarfile / RAR host_os values (parser maps RAR5 Windows→2, Unix→3).
_RAR_HOST_OS_TO_CREATE_SYSTEM: dict[int, CreateSystem] = {
    0: CreateSystem.FAT,
    1: CreateSystem.OS2_HPFS,
    2: CreateSystem.WINDOWS_NTFS,
    3: CreateSystem.UNIX,
    4: CreateSystem.MACINTOSH,
    5: CreateSystem.BEOS,
}

_RAR_METHOD_STORED = 0x30
_RAR_METHOD_MAX = 0x35  # RAR M5
_RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x02
_RAR5_XREDIR_WINDOWS_JUNCTION = 3

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
    # Unusual method byte outside M0–M5: still expose as UNKNOWN.
    level = method - _RAR_METHOD_STORED if method >= _RAR_METHOD_STORED else None
    return (CompressionMethod(algo=CompressionAlgorithm.UNKNOWN, level=level),)


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


def _copy_stream_to_path(source: BinaryIO, dest: Path) -> None:
    pos = source.tell()
    source.seek(0)
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(source, out)
    finally:
        source.seek(pos)


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

    def __init__(
        self,
        stdout: BinaryIO,
        proc: subprocess.Popen[bytes],
        *,
        named_member: bool = False,
        has_verifiable_hash: bool = False,
        encrypted: bool = False,
    ) -> None:
        # Track bytes via read(); disable readinto passthrough so counting is not skipped.
        super().__init__(stdout, readinto_passthrough=False)
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
        # Mark closed without relying on DelegatingStream (already closed inner).
        super(DelegatingStream, self).close()
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

    ``_pos`` is the logical offset; ``_pipe_pos`` is how far the live process
    has actually been read. Respawn is keyed on the pipe, so ``seek(0, SEEK_END);
    seek(0)`` before any read costs nothing.

    ``spawn`` must return a stream that owns the process (typically
    ``_UnrarOwnedStream``, or ``_BoundedMemberPipe`` wrapping one), so
    close/respawn reaps it.
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
        self._pipe_pos += len(data)
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


class _BoundedMemberPipe(DelegatingStream):
    """Own an ``unrar`` pipe, skip a glob-match prefix, then EOF at ``size``.

    ``unrar -n./name-with-wildcards`` concatenates every matching member with no
    headers. The parsed member list tells us how many unpacked bytes sit before
    the target; after that we must stop, or the fused overrun probe would see the
    next match as extra payload.

    The skip is eager at construction. A seekable wrapper respawns lazily on the
    next ``read()``, so this constructor — and the skip — runs then, not inside
    ``seek()``. ``seek(0, SEEK_END); seek(0)`` before any read still costs
    nothing: ``_pipe_pos`` stays 0 and no new pipe is built.
    """

    def __init__(self, inner: BinaryIO, *, prefix: int, size: int) -> None:
        super().__init__(inner, readinto_passthrough=False)
        try:
            if prefix:
                skip_forward(inner, prefix)
        except EOFError as exc:
            inner.close()
            raise TruncatedError(
                "unrar pipe ended before the requested glob-matched member"
            ) from exc
        except BaseException:
            inner.close()
            raise
        self._size = size
        self._pos = 0

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return self._pos

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        remaining = self._size - self._pos
        if remaining <= 0:
            return b""
        if n < 0 or n > remaining:
            n = remaining
        data = super().read(n)
        self._pos += len(data)
        return data


class RarReader(BaseArchiveReader):
    """Reads RAR archives: native metadata parse + RARLAB ``unrar`` for data."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: Path | BinaryIO,
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
        self._volume_count = getattr(source, "volume_count", volume_count)
        self._temp_path: Path | None = None
        self._temp_dir: Path | None = None
        self._owned_concat: ConcatenatedFile | None = None
        self._archive_path: Path | None = None
        self._volume_paths: list[Path] = []

        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "RAR archives require a seekable source: headers and stored member "
                "ranges are addressed by offsets.",
                archive_name=archive_name,
                source_format=ArchiveFormat.RAR,
            )

        # Where the RAR proper starts inside `source`: detection's payload_offset for a
        # self-extracting file, 0 otherwise. The parser would find the same magic by
        # scanning, so this is not what makes SFX work — it makes the parse start at the
        # offset detection already paid for, and it pins the answer: bytes before the
        # origin are not part of this archive, so a stub carrying its own `Rar!\x1a\x07`
        # cannot be picked up instead of the real payload.
        self._origin = start_offset
        self._shared = self._open_shared_source(source)
        if self._origin and len(self._volume_paths) > 1:
            raise UnsupportedFeatureError(
                "A start offset cannot be combined with a multi-volume RAR set: the "
                "offset describes one file, and the volumes are separate ones.",
                archive_name=archive_name,
                source_format=ArchiveFormat.RAR,
            )
        self._archive, self._unrar_password = self._parse_archive()
        if self._archive.is_volume or self._volume_count > 1:
            self._volume_count = max(self._volume_count, len(self._volume_paths) or 1)
        self._archive.comment = self._resolve_rar3_comment(self._archive.comment)
        for info in self._archive.members:
            info.comment = self._resolve_rar3_comment(info.comment)
        self._members = [self._to_member(info) for info in self._archive.members]

    def _open_shared_source(self, source: Path | BinaryIO) -> SharedSource:
        """Build SharedSource, discovering/materializing volumes as needed."""
        wrap = self._seek_handle_wrapper()
        if isinstance(source, Path):
            siblings = discover_volume_siblings(source)
            if siblings is not None and len(siblings) > 1:
                self._volume_paths = siblings
                self._volume_count = len(siblings)
                self._archive_path = siblings[0]
                concat = ConcatenatedFile(siblings)
                self._owned_concat = concat
                return SharedSource(concat, wrap_handle=wrap)
            self._volume_paths = [source]
            self._archive_path = source
            return SharedSource(source, wrap_handle=wrap)

        if isinstance(source, ConcatenatedFile):
            paths = source.volume_paths
            if paths:
                # Path volumes: prefer real sibling files for unrar.
                self._volume_paths = paths
                self._volume_count = len(paths)
                self._archive_path = paths[0]
                return SharedSource(source, wrap_handle=wrap)
            # Stream volumes: materialize for unrar; parse from originals.
            items = source.volume_items
            self._volume_count = len(items)
            self._materialize_stream_volumes(items)
            return SharedSource(source, wrap_handle=wrap)

        # Single non-path stream — materialize later when unrar is needed.
        return SharedSource(source, wrap_handle=wrap)

    def _materialize_stream_volumes(self, items: Sequence[Path | BinaryIO]) -> None:
        """Write ordered volumes into a temp dir with ``name.partN.rar`` names."""
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
                    _copy_stream_to_path(item, dest)
                paths.append(dest)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._temp_dir = None
            raise
        self._volume_paths = paths
        self._archive_path = paths[0]

    def _parse_archive(self) -> tuple[RarArchive, str | None]:
        def parse(password: bytes | None) -> RarArchive:
            if len(self._volume_paths) > 1:
                handles: list[BinaryIO] = []
                try:
                    for path in self._volume_paths:
                        handles.append(path.open("rb"))
                    return parse_rar_volumes(handles, password=password)
                finally:
                    for handle in handles:
                        handle.close()

            # Single volume — may still be a ConcatenatedFile of streams that we
            # already materialized into _volume_paths of length 1, or a lone file.
            if self._volume_paths:
                with self._volume_paths[0].open("rb") as handle:
                    handle.seek(self._origin)
                    return parse_rar_archive(handle, password=password)

            view = self._shared.view(0)
            try:
                view.seek(self._origin)
                archive = parse_rar_archive(view, password=password)
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
                # Incomplete set opened as a lone volume-1 path with no siblings.
                if archive.needs_next_volume and len(self._volume_paths) <= 1:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: end of archive expects "
                        "another volume"
                    )
                # Data-only encryption (no header encrypt): parse succeeds without a
                # password, so this is the unconfirmed first candidate. Safe for unrar
                # and ConvertHashToMAC — a wrong candidate is rejected by the per-file
                # PswCheck (0x01, when present) or by unrar exit 11.
                return archive, self._first_candidate_str()
            except EncryptionError:
                if not self._passwords.has_passwords():
                    raise

                def confirm(password: bytes) -> RarArchive:
                    return parse(password)

                archive = self._passwords.attempt(None, confirm)
                if archive.needs_next_volume and len(self._volume_paths) <= 1:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: end of archive expects "
                        "another volume"
                    )
                return archive, self._first_candidate_str()
        except _PasswordCandidatesExhausted as exc:
            message = (
                exc.last_error.message
                if exc.last_error is not None
                else "Password required to decrypt RAR headers"
            )
            raise EncryptionError(message) from exc

    def _first_candidate_str(self) -> str | None:
        for password in self._passwords.iter_candidates():
            return _password_as_str(password)
        return None

    def _ensure_archive_path(self) -> Path:
        """Return a filesystem path ``unrar`` can open (materialize streams once)."""
        if self._archive_path is not None:
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
                    while True:
                        chunk = view.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
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
                password=self._unrar_password,
            )
        except (OSError, PackageNotInstalledError, subprocess.SubprocessError):
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
        link_target: str | None = None
        extra: dict[str, object] = {}
        tweaked = _crc_is_tweaked(info)
        if info.file_redir is not None:
            link_target = info.file_redir[2]
            if info.file_redir[0] == _RAR5_XREDIR_WINDOWS_JUNCTION:
                extra[EXTRA_IS_JUNCTION] = True
        if version_history:
            assert info.file_version is not None
            extra["rar.file_version"] = info.file_version
        if info.extract_version is not None:
            extra[EXTRA_RAR_EXTRACT_VERSION] = info.extract_version
        if tweaked:
            # Stored digests are key-tweaked; keep them out of ``hashes`` (see
            # ``_member_hashes``) but expose the raw values for callers / forward-verify.
            if info.crc32 is not None:
                extra["rar.tweaked_crc32"] = info.crc32
            if info.blake2sp_hash is not None:
                extra["rar.tweaked_blake2sp"] = info.blake2sp_hash

        host_os = info.host_os
        create_system = (
            _RAR_HOST_OS_TO_CREATE_SYSTEM.get(host_os, CreateSystem.UNKNOWN)
            if host_os is not None
            else CreateSystem.UNKNOWN
        )
        # Unix host_os is 3 (RAR3 Unix, and the parser maps RAR5 Unix→3). That
        # writer's creation slot is st_ctime, not birth; Win32 (2) and the
        # other RAR3 hosts store a creation time. Omit the key when there is
        # no created value or host_os is unknown.
        if info.ctime is not None and host_os is not None:
            extra[EXTRA_RAR_CREATED_IS_CTIME] = host_os == 3

        mode: int | None = None
        windows_attrs: int | None = None
        if info.mode is not None:
            # RAR5 stores attr as a vint (hostile values can exceed C unsigned long).
            # Unix: ArchiveMember.mode is the low 12 permission bits (S_IMODE);
            # mask before the C helper so OverflowError cannot abort listing.
            # Win32: FILE_ATTRIBUTE_* is a 32-bit field.
            if host_os == 3:  # Unix
                mode = stat.S_IMODE(info.mode & 0o7777)
            elif host_os == 2:  # Win32
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
            is_encrypted=info.is_encrypted,
            is_current=not version_history,
            create_system=create_system,
            windows_attrs=windows_attrs,
            hashes=_member_hashes(info),
            link_target=link_target,
            comment=member_comment,
            extra=extra,
            _raw=info,
        )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
        )
        if tweaked and self._unrar_password is None:
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
        return member

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

        path = self._ensure_archive_path()
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
            password.
            """
            nonlocal solid
            if solid is None:
                proc, stdout = open_unrar_p(
                    path,
                    password=self._unrar_password,
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
        password = self._unrar_password
        enc = info.file_encryption
        if password is None or enc is None:
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

    def _can_direct_read(self, info: RarMemberInfo) -> bool:
        return (
            info.compress_type == _RAR_METHOD_STORED
            and not info.is_encrypted
            and not info.file_solid
            and not info.split_after
            and not info.split_before
            and not info.spanned_volumes
        )

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
        # Encrypted / compressed target without usable direct bytes: leave unset.
        return

    def _unrar_glob_prefix(
        self, target: ArchiveMember, presented: str, *, version_control: bool
    ) -> int:
        """Unpacked bytes of earlier payload members that the ``-n`` mask also matches.

        ``unrar`` emits those members concatenated, in archive order, with no
        headers. Zero when the presented name has no glob characters. History
        rows are omitted unless ``version_control`` is set, matching ``unrar``
        (``-ver`` is passed only for a history-row target).
        """
        if "*" not in presented and "?" not in presented:
            return 0
        prefix = 0
        for member in self._members:
            raw = member._raw
            if not isinstance(raw, RarMemberInfo) or not raw.is_payload_file():
                continue
            if raw.is_file_version_history() and not version_control:
                continue
            if not _unrar_mask_match(_presented_filename(raw), presented):
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

        path = self._ensure_archive_path()
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
        # Prefer our fused digest check (including tweaked ConvertHashToMAC) over
        # unrar's exit code for corruption; wrong-password (11) still maps.
        has_hash = bool(member.hashes) or (
            isinstance(raw, RarMemberInfo)
            and self._tweaked_verify_spec(raw) is not None
        )
        glob_prefix = self._unrar_glob_prefix(
            member, presented, version_control=version_control
        )

        def _spawn() -> BinaryIO:
            proc, stdout = open_unrar_p(
                path,
                password=self._unrar_password,
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
                    return _BoundedMemberPipe(
                        tracked,
                        prefix=glob_prefix,
                        size=_member_stream_size(member),
                    )
                return tracked
            except BaseException:
                # BoundedMemberPipe already closed ``tracked`` (and so ``owned``)
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
        )
        any_encrypted = any(m.is_encrypted for m in self._archive.members)
        is_multivolume = (
            self._archive.is_volume
            or self._volume_count > 1
            or len(self._volume_paths) > 1
        )
        archive_comment = self._archive.comment
        assert not isinstance(archive_comment, _Rar3Comment)
        return ArchiveInfo(
            format=ArchiveFormat.RAR,
            format_version=str(self._archive.version),
            is_solid=is_solid,
            member_count=len(self._members),
            comment=archive_comment,
            is_encrypted=self._archive.has_header_encryption or any_encrypted,
            is_multivolume=is_multivolume,
            cost=cost,
            extra={
                "rar.volume_count": max(self._volume_count, len(self._volume_paths))
            },
        )

    def _close_archive(self) -> None:
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
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


class RarReadBackend(ReadBackend):
    """Backend factory for RAR archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.RAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".rar": ArchiveFormat.RAR}
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
        source: Path | BinaryIO,
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

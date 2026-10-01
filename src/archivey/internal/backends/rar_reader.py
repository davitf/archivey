"""Native RAR reader backend.

Module split:

- :mod:`.rar_parser` — metadata, offsets, encryption headers, multi-volume merge
- :mod:`.rar_unrar` — spawn RARLAB ``unrar p`` (password on stdin; ``-n./member``)
- :mod:`.rar_unar` — the refusals and pipe layout when the caller selects ``unar``
  (``ArchiveyConfig.rar_decompressor``); the process itself is
  :mod:`archivey.internal.external.unar`
- this module — ``BaseArchiveReader``: list from the parser; member **data** via unrar,
  or via ``unar`` when selected (same shapes, entries named by index instead of mask)

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
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, NamedTuple

from archivey.config import ArchiveyConfig, RarDecompressor, SpoolLimits
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    DigestContext,
    EncryptedVerificationContext,
    MemberHeaderRecordContext,
    MemberTimestampContext,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    LinkTargetNotFoundError,
    PackageNotInstalledError,
    ReadError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
    raw_message_of,
)
from archivey.internal.backends.rar_detect import validate_rar_main_header
from archivey.internal.backends.rar_parser import (
    RAR5_ID,
    RAR_ID,
    DamagedServiceHeader,
    RarArchive,
    RarEncryptionInfo,
    RarKdfCache,
    RarMemberInfo,
    _check_rar5_password,
    _decode_comment_text,
    _Rar3Comment,
    convert_blake2sp_to_mac,
    convert_crc_to_mac,
    parse_rar_archive,
    parse_rar_volumes,
    rar5_hash_key,
)
from archivey.internal.backends.rar_unar import (
    REFUSE_NON_ASCII_PASSWORD,
    UNAR_PURPOSE,
    UnarRarPolicy,
    unar_dictionary_costs,
    uses_no_dictionary,
)
from archivey.internal.backends.rar_unrar import (
    _unrar_glob_demux_ok,
    decompress_rar3_blob,
    find_rarlab_unrar,
    open_unrar_p,
    unrar_mask_is_usable,
    unrar_mask_keys,
    unrar_mask_selects,
    unrar_mask_view,
    unrar_member_argument,
    unrar_member_refusal,
    unrar_member_view,
    unrar_selection_keys,
)
from archivey.internal.base_reader import (
    MAX_LINK_TARGET_BYTES,
    BaseArchiveReader,
    ReadBackend,
)
from archivey.internal.config import KeyDerivationBudget, check_decoder_memory
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.external.cli import ProcessOutputStream, signal_exit_error
from archivey.internal.external.unar import (
    UnarOutputStream,
    find_unar,
    open_unar_stdout,
    unar_password_supported,
)
from archivey.internal.listing_limits import check_metadata_budget
from archivey.internal.logs import backends as logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import (
    emit_member_name_normalized,
    normalize_member_name,
    resolve_link_target_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
    wrong_password_error,
)
from archivey.internal.password_confirm import UnverifiedPasswordReadWatch
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.spool import SpoolBudget
from archivey.internal.streams.archive_stream import ArchiveStream, RewindWarning
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    skip_forward,
)
from archivey.internal.streams.verify import build_member_verifier
from archivey.internal.volumes import (
    ConcatenatedFile,
    discover_volume_siblings,
    next_old_rar_volume_name,
)
from archivey.terminal import quoted
from archivey.types import (
    EXTRA_IS_FILE_COPY,
    EXTRA_IS_JUNCTION,
    EXTRA_IS_REPARSE_POINT,
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


def _single_disk_copy_note(program: str) -> str:
    return (
        "Reading a compressed member will copy the whole archive to disk so "
        f"{program} can read it."
    )


def _stream_volumes_disk_copy_note(program: str) -> str:
    return (
        "Reading a compressed member will copy every volume to a temp directory "
        f"so {program} can read them."
    )


def _resolve_decompressor(choice: RarDecompressor) -> RarDecompressor:
    """The program ``AUTO`` stands for: ``unrar`` when usable, else ``unar``.

    Neither found resolves to ``UNRAR``, so a data read raises the ``unrar``
    refusal it always has. Probing costs one identification run per binary per
    process; the finders cache the answer.
    """
    if choice is not RarDecompressor.AUTO:
        return choice
    try:
        find_rarlab_unrar()
    except PackageNotInstalledError:
        pass
    else:
        return RarDecompressor.UNRAR
    try:
        find_unar(purpose=UNAR_PURPOSE)
    except PackageNotInstalledError:
        return RarDecompressor.UNRAR
    return RarDecompressor.UNAR


AUTO_CHOSE_UNAR_NOTE = (
    "RAR member data is read with unar because no RARLAB unrar or rar 6.0 or later was "
    "found. unar "
    "reads fewer archives than unrar, and a password is passed on its command line, "
    "where other local users can see it. Install unrar, or set "
    "ArchiveyConfig.rar_decompressor to 'unrar', to avoid both."
)


def _stream_volume_name(stem: str, index: int, *, old_style: bool) -> str:
    """File name of volume ``index`` (1-based) of a set written or linked to disk.

    Used for a stream set copied for either program, and for the set linked into
    ``unar``'s private directory (``RarReader._unar_archive_path``).

    Old-style names run ``.rar``, ``.r00`` … ``.r99``, ``.s00`` … ``.z99`` and on
    past ``z`` (``.{00``, ``.|00`` …), because unrar's next-volume rule just adds one
    to the letter's character code; it reads a set of 1 500 volumes named that way.
    It does not follow ``partN`` names for an old-style set, so those are never used
    for one. ``unar`` stops after volume 901 whatever the names, and is refused for
    a longer set (:data:`_UNAR_MAX_OLD_STYLE_VOLUMES`).
    """
    if not old_style:
        return f"{stem}.part{index}.rar"
    if index == 1:
        return f"{stem}.rar"
    number = index - 2
    return f"{stem}.{chr(ord('r') + number // 100)}{number % 100:02d}"


# unar 1.10 reads an old-style set as far as ``.z99`` and no further: ``lsar``
# reports 901 volumes for a longer set, and the member is then short.
_UNAR_MAX_OLD_STYLE_VOLUMES = 901


_UNRAR_PART_NAME_RE = re.compile(
    r"(?P<head>.*\.part)(?P<num>[0-9]+)(?P<ext>\.rar)", re.I
)


def _unrar_next_volume_name(name: str, *, old_numbering: bool) -> str | None:
    """The name unrar tries for the volume after ``name``, or ``None`` if unsure.

    A subset of unrar's ``NextVolumeName``: an ``.exe`` or ``.sfx`` extension
    counts as ``.rar``; the old scheme goes ``.rar`` -> ``.r00`` -> ``.r01`` ...
    ``.r99`` -> ``.s00``, and the new one increments ``N`` in ``name.partN.rar``.
    Any other shape is ``None``, and the caller then stages the set rather than
    predict unrar's walk. unrar also retries an old-scheme name when a new-scheme
    one is missing; that retry is not modelled, so such a set is staged too.
    """
    stem, dot, ext = name.rpartition(".")
    if not dot:
        name = f"{name}.rar"
    elif ext.lower() in ("", "exe", "sfx"):
        name = f"{stem}.rar"
    if not old_numbering:
        match = _UNRAR_PART_NAME_RE.fullmatch(name)
        if match is None:
            return None
        digits = match["num"]
        number = str(int(digits) + 1).zfill(len(digits))
        return f"{match['head']}{number}{match['ext']}"
    return next_old_rar_volume_name(name)


def _unrar_finds_exactly(
    paths: list[Path], *, is_volume: bool, old_numbering: bool
) -> bool:
    """Whether unrar, given ``paths[0]``, walks exactly ``paths`` by name.

    unrar looks for each next volume by name beside the one before, under the
    scheme volume 1's MAIN header names (``old_numbering``), not under the names
    the files carry: an old-scheme set renamed ``x.part1.rar``, ``x.part2.rar`` is
    continued from ``x.part1.r00``. So each next name is predicted from that flag,
    and must be the next given file; past the last one, no file may answer to the
    next name, or unrar could read it as a further volume. A name the prediction
    does not cover counts as not found, and the set is staged.
    """
    if not is_volume:
        # unrar never looks for another file beside a non-volume archive.
        return len(paths) == 1
    try:
        current = paths[0]
        for index in range(1, len(paths) + 1):
            name = _unrar_next_volume_name(current.name, old_numbering=old_numbering)
            if name is None:
                return False
            candidate = current.parent / name
            if index == len(paths):
                return not os.path.lexists(candidate)
            if not os.path.samefile(candidate, paths[index]):
                return False
            current = candidate
    except OSError:
        return False
    return True


def _link_file(src: Path, dest: Path) -> None:
    """Make ``dest`` name ``src``'s bytes without copying them: a symlink, else a
    hard link. Raises ``OSError`` when neither is possible."""
    try:
        dest.symlink_to(src)
    except (OSError, NotImplementedError):
        os.link(src, dest)


def _stream_copy_refused_note(program: str, reason: str) -> str:
    return (
        f"Reading a compressed member will be refused: {program} reads only files, "
        f"and {reason}. Members archivey reads without {program}, such as stored "
        "members of a non-solid archive, can still be read."
    )


def _rar_stream_copy_cost_notes(
    source: ArchiveSource,
    limits: SpoolLimits,
    copy_size: int | None,
    program: str,
) -> tuple[str, ...]:
    """Open-time caveat when member data needs a filesystem path for the decompressor.

    A file source, or a joined set of files, gets no note here; the one file source that
    is copied, a prefixed file read with ``unar``, is known only after the parse and is
    noted then (see ``RarReader.__init__``). Both stream shapes get the same predictive
    caveat: the copy happens on the first read the decompressor has to serve, not at
    open. Keyed from the source's facts so a mixed set, whose file parts
    ``_materialize_stream_volumes`` copies alongside the streams, is labelled as the
    streams it contains. ``program`` names the decompressor in the note.

    The caveat names the spool limit, so the caller reads the worst case at open.
    When the limit already decides the outcome — ``max_bytes=0``, or ``copy_size``
    (the copy's size, when known at open) over the limit — it says the read will be
    refused rather than promise a copy that cannot happen.
    """
    if source.path is not None:
        return ()
    if source.joined is not None:
        if source.volume_paths:
            return ()
        note = _stream_volumes_disk_copy_note(program)
        what = "every volume"
    else:
        note = _single_disk_copy_note(program)
        what = "the archive"
    return _bounded_copy_notes(
        note, what, "a stream source", limits, copy_size, program
    )


def _bounded_copy_notes(
    note: str,
    what: str,
    source_kind: str,
    limits: SpoolLimits,
    copy_size: int | None,
    program: str,
) -> tuple[str, ...]:
    """``note`` with the spool limit in force, or the refusal the limit decides at open.

    ``what`` names what is copied and ``source_kind`` what it is copied from, in the
    refusal's wording.
    """
    limit = limits.max_bytes
    if limit is None:
        return (f"{note} The copy has no size limit (SpoolLimits.max_bytes=None).",)
    if limit == 0:
        reason = f"SpoolLimits.max_bytes=0 allows no copy of {source_kind}"
        return (_stream_copy_refused_note(program, reason),)
    if copy_size is not None and copy_size > limit:
        reason = (
            f"a copy of {what} would be {copy_size} bytes, over "
            f"SpoolLimits.max_bytes={limit}"
        )
        return (_stream_copy_refused_note(program, reason),)
    return (
        f"{note} An archive over SpoolLimits.max_bytes={limit} is refused instead "
        f"of copied.",
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

# RAR host_os values used directly below; the same numbers key
# _RAR_HOST_OS_TO_CREATE_SYSTEM above (the parser maps RAR5 Windows->2, Unix->3).
_RAR_HOST_OS_WIN32 = 2
_RAR_HOST_OS_UNIX = 3
# Hosts whose creation-time slot is a birth time: MS-DOS, OS/2, Win32, Mac OS, BeOS.
# Listed, not derived from the map above: a host added there is not a birth-time host
# until someone says so, since its slot would otherwise flow into ``created``.
_RAR_BIRTH_TIME_HOSTS = frozenset({0, 1, _RAR_HOST_OS_WIN32, 4, 5})

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


class _DictionaryCost(NamedTuple):
    """What one read costs in dictionary memory, and which header the cost came from.

    ``count`` is compared against ``DecoderLimits.max_decoder_memory``. ``declared``
    is the dictionary behind ``count``, and ``declarer`` the archive index of the
    member whose header declared it. ``declarer`` is -1, and ``declared`` 0, when no
    header is behind the count: a member that uses no dictionary outside a solid
    ``unrar`` walk, or one ahead of every member that does. A count of 0 does not
    imply -1 (an empty compressed member is its own declarer). Both are for the
    refusal message: under ``unrar`` the count can be smaller than ``declared``, and
    in a solid archive, a shared mask or a pass the declarer can be another member.
    Compare two costs with :func:`_larger_cost`, not ``max``.
    """

    count: int
    declared: int
    declarer: int


_NO_DICTIONARY = _DictionaryCost(0, 0, -1)


def _larger_cost(a: _DictionaryCost, b: _DictionaryCost) -> _DictionaryCost:
    """The larger count, with the declaration behind that count."""
    return b if b.count > a.count else a


def _unrar_dictionary_costs(archive: RarArchive) -> list[_DictionaryCost]:
    """The dictionary bytes RARLAB ``unrar`` can touch to decode each member, in order.

    Measured with ``unrar`` 7.00 (``dev-docs/formats/rar.md`` §7). ``unrar`` sizes a
    nonsolid member's window to the smaller of its declared dictionary and its
    unpacked size, and the pages fill only as output is written. A 64 KiB member
    that declares 4 GiB stays near 8 MiB resident. In a solid archive the window
    only grows: ``unrar`` keeps the largest dictionary declared by any member it
    has decoded. It decodes every earlier member, across solid streams too. A
    member that starts a new stream still paid about 300 MiB for the 1 GiB window
    and 300 MB of data ahead of it. So the count for a solid member is the smaller
    of the largest dictionary declared up to and including it and the unpacked
    bytes of those members.

    The unpacked size is a header value, as the dictionary is. When it is too
    small, the reader's own size check stops reading at that size, and ``unrar``
    then blocks on the full pipe. So the bytes written, and so the pages touched,
    stay near the declared size. A stored member, a directory and a redirect use
    no dictionary (:func:`uses_no_dictionary`) and add nothing to the window or
    to the decoded bytes. In a nonsolid archive they count 0. In a solid one they
    count the window built ahead of them, because ``unrar`` decodes every earlier
    member to reach them: measured, a stored 64 KiB member behind a 300 MB member
    declaring 1 GiB took 314 MiB resident. The walk keys on the archive's MAIN
    solid flag, as ``unrar`` does: with it set and the member's own solid flag
    clear, the read still took 314 MiB; with it clear and the member's flag set,
    ``unrar`` decoded nothing ahead (47 MiB, the parent's own). ``rar -s`` writes
    both flags on every member after the first, and the reader slices a stored
    member only when its own flag is clear, so such a member does reach ``unrar``.
    A RAR3 symlink does count: its target is compressed data. That is why this walk
    does not use ``is_payload_file()`` as :meth:`RarReader._solid_prefix` does.
    """
    costs: list[_DictionaryCost] = []
    window = decoded = 0
    declarer = -1
    for index, info in enumerate(archive.members):
        if not archive.is_solid:
            costs.append(
                _NO_DICTIONARY
                if uses_no_dictionary(info)
                else _DictionaryCost(
                    min(info.dictionary_size, info.file_size),
                    info.dictionary_size,
                    index,
                )
            )
            continue
        if not uses_no_dictionary(info):
            if info.dictionary_size > window or declarer < 0:
                window, declarer = info.dictionary_size, index
            decoded += info.file_size
        costs.append(_DictionaryCost(min(window, decoded), window, declarer))
    return costs


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
    """Build ``ArchiveMember.extra`` and the link target (or a file copy's source)."""
    extra = MemberExtra()
    link_target: str | None = None
    if info.file_redir is not None:
        link_target = info.file_redir[2]
        if info.is_file_copy():
            extra[EXTRA_IS_FILE_COPY] = True
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
    return extra, link_target


def _rar_created(info: RarMemberInfo) -> datetime | None:
    """The member's birth time: the creation slot, when ``host_os`` stores one there.

    A Unix writer (RAR3 Unix; the parser maps RAR5 Unix to 3) fills the slot from
    st_ctime, which ``created`` never holds. Win32 and the other RAR3 hosts store a
    birth time. An unknown ``host_os`` says neither, so it gets None too.
    """
    if info.host_os in _RAR_BIRTH_TIME_HOSTS:
        return info.ctime
    return None


def _rar_ctime(info: RarMemberInfo) -> datetime | None:
    """The creation slot when it is not ``created``: a Unix or unknown ``host_os``."""
    if info.host_os in _RAR_BIRTH_TIME_HOSTS:
        return None
    return info.ctime


def _timestamp_field_name(info: RarMemberInfo, slot: str) -> str:
    """The ``ArchiveMember`` field a parser time slot would have filled.

    The creation slot is ``created`` or ``ctime`` by ``host_os``, as in
    :func:`_rar_created`, so the report names the field the caller sees as ``None``.
    """
    if slot == "ctime":
        return "created" if info.host_os in _RAR_BIRTH_TIME_HOSTS else "ctime"
    return {"mtime": "modified", "atime": "accessed"}.get(slot, slot)


def _tweaked_hash_key(
    enc: RarEncryptionInfo, password: str, kdf_cache: RarKdfCache
) -> bytes | None:
    """Return HashKey for ``password``, or ``None`` when the password is provably wrong.

    A present PswCheck that rejects ``password`` returns ``None`` so callers skip
    forward-transform verification (a wrong HashKey would false-``CorruptionError``
    good plaintext). When the check is absent or unusable, the HashKey is still
    derived — matching the password ``unrar`` will receive.
    """
    if enc.check_value is not None:
        try:
            _check_rar5_password(
                enc.check_value,
                enc.kdf_count,
                enc.salt,
                password,
                kdf_cache=kdf_cache,
            )
        except EncryptionError:
            return None
    return rar5_hash_key(password, enc.salt, enc.kdf_count, kdf_cache=kdf_cache)


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


@dataclass(slots=True)
class _UnrarNames:
    """Every payload member's name as ``unrar`` reads it, to size an ``-n`` skip.

    Built once per reader, the first time a member is read through ``unrar``, so
    each later read looks its mask up rather than walking every member.
    """

    # By position in the member list: the name ``unrar_member_view`` gives, or
    # ``None`` for a member that is not a payload file or whose name this host
    # cannot reproduce.
    views: list[str | None]
    # ``id(member)`` to its position.
    positions: dict[int, int]
    # ``unrar_selection_keys`` of each known view, to its payload positions.
    by_key: dict[str, list[int]]
    # Payload positions whose view is ``None``, ascending.
    unknown: list[int]


@contextmanager
def _close_on_error(owned: BinaryIO) -> Iterator[None]:
    """Close ``owned`` if the block raises.

    ``owned`` is the stream built before the block; what the block builds on top of it
    is not closed, so wrap each new outermost stream in its own block.
    """
    try:
        yield
    except BaseException:
        owned.close()
        raise


class _UnrarOwnedStream(ProcessOutputStream):
    """Stdout wrapper that terminates the owning ``unrar`` process on close.

    On close it maps ``unrar``'s exit code (RARLAB) to a typed error so a corrupt,
    truncated, or wrong-password member surfaces honestly instead of a silent short
    read. When *we* terminate the process (early close / teardown) the return code
    is negative and no error is raised. A signal that ends ``unrar`` after it closed
    its output came from elsewhere (or was a crash), and maps to
    ``ResourceLimitError`` / ``ReadError`` (:func:`signal_exit_error`), never to a
    truncation the digest check would otherwise report. ``named_member``
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
        self._named_member = named_member
        self._has_verifiable_hash = has_verifiable_hash
        self._encrypted = encrypted
        self._saw_eof = False
        super().__init__(stdout, proc)

    def _at_eof(self) -> None:
        self._saw_eof = True
        super()._at_eof()

    def tell(self, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        # The inner is a pipe, whose tell() raises ESPIPE. Every byte goes through
        # read() (readinto_passthrough is off), so the count is the position.
        return self._bytes_read

    def _raise_for_returncode(self, rc: int) -> None:
        """Map an unrar exit code to an archivey error, or return quietly."""
        # RARLAB unrar exit codes: 11 bad password, 3 CRC/corrupt data, 2 fatal
        # error, 10 no files matched. Codes 0 (success) and 1 (warning) pass.
        if rc < 0:
            # A signal. Before end of file it is archivey's doing: close stopped a
            # program that was still writing, or the pipe it closed ended it. After
            # end of file something else ended unrar (the out-of-memory killer, an
            # operator) or it crashed; either way the pipe was cut short, and the
            # digest check below would call that a truncated archive.
            if self._saw_eof:
                raise signal_exit_error("unrar", rc)
            return
        if rc == 255 and self._saw_eof:
            # ``USER_BREAK``: unrar catches SIGINT and SIGTERM and exits 255, so a
            # stop from outside arrives as this code rather than as a signal.
            raise ReadError(
                "unrar was stopped from outside (exit 255, user break) while reading "
                "data; the archive may be valid, so try reading it again."
            )
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


class _RespawnStream(ReadOnlyIOStream):
    """Seekable view of a one-member decompressor pipe (``unrar p`` or ``unar``).

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

    ``spawn`` must return a stream that owns the process (``_UnrarOwnedStream``,
    ``_bounded_member_pipe`` wrapping one, or a ``UnarOutputStream``), so
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
        # As io.BytesIO: a relative seek before the start clamps to 0, and only a
        # negative SEEK_SET is the caller's error.
        if whence == io.SEEK_SET:
            if offset < 0:
                raise ValueError(f"Negative seek position {offset}")
            target = offset
        elif whence == io.SEEK_CUR:
            target = max(0, self._pos + offset)
        elif whence == io.SEEK_END:
            target = max(0, self._size + offset)
        else:
            raise ValueError(f"invalid whence ({whence})")
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


def _open_unar_blob_pipe(path: Path) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    """``unar`` for the one-entry archive :func:`decompress_rar3_blob` builds."""
    return open_unar_stdout(path, [0], purpose=UNAR_PURPOSE)


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
        # Applied to RAR 1.5-4 names stored as 8-bit bytes; RAR5 names are UTF-8.
        self._encoding = encoding
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        # The candidate each RAR5 encryption record's PswCheck accepted, keyed by the
        # record's salt, KDF cost and check. RAR writes one salt per archiving run,
        # so this is usually one derivation per archive rather than one per member.
        self._checked_passwords: dict[tuple[bytes, int, bytes], bytes] = {}
        # Every RAR key this reader derives: the header parse, each PswCheck and each
        # tweaked-digest HashKey. An ``-hp`` archive's members repeat the header's
        # salt, so their PswCheck is the header's own derivation.
        self._kdf_cache = RarKdfCache(
            budget=KeyDerivationBudget(self._config.decoder_limits)
        )
        # The first member whose PswCheck can judge a candidate, found once on first
        # use; ``False`` until looked for, ``None`` when there is none.
        self._archive_check_member: ArchiveMember | None | Literal[False] = False
        self._unrar_names_cache: _UnrarNames | None = None
        self._volume_count = max(source.volume_count, volume_count)
        self._temp_path: Path | None = None
        self._temp_dir: Path | None = None
        self._owned_concat: ConcatenatedFile | None = None
        self._archive_path: Path | None = None
        # unar's private directory (``_unar_archive_path``) and the path in it that
        # unar is handed. The directory is removed on close.
        self._unar_dir: Path | None = None
        self._unar_path: Path | None = None
        # Guards the check-then-write in ``_ensure_archive_path``: two concurrent
        # compressed opens used to both see ``None`` and both copy, and close
        # only removed the winner.
        self._materialize_lock = threading.Lock()
        # The one spool allowance for this reader's copies, made on the first copy
        # (``_spool_budget``) and kept, so a refused copy is not retried.
        self._spool: SpoolBudget | None = None
        self._volume_paths: list[Path] = []
        # Stream volumes, kept unmaterialized until unrar actually needs files.
        self._stream_volume_items: list[Path | BinaryIO] = []
        # Volume files (explicit or discovered) that unrar would not find by name from
        # the first one (_choose_unrar_volume_paths), linked into a temp directory on
        # the first read that needs it.
        self._stage_volume_paths = False
        self._volume0_parse_origin = 0  # set after sibling discovery when origin > 0
        # The data program, with ``AUTO`` resolved once for the life of this reader.
        self._decompressor = _resolve_decompressor(self._config.rar_decompressor)

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
        # Open-time caveat from source shape, not from later materialization
        # (CostReceipt is a static snapshot; see access-mode-and-cost).
        self._cost_notes = _rar_stream_copy_cost_notes(
            source,
            self._config.spool_limits,
            self._spool_copy_size(),
            self._decompressor_name(),
        )
        if (
            self._config.rar_decompressor is RarDecompressor.AUTO
            and self._decompressor is RarDecompressor.UNAR
        ):
            self._cost_notes = (AUTO_CHOSE_UNAR_NOTE, *self._cost_notes)
        self._archive, self._unrar_password = self._parse_archive()
        self._choose_unrar_volume_paths()
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
        # Built once from the parse, and only when the caller chose unar: it holds the
        # refusals and the pipe layout, and none of it applies to unrar.
        self._unar_policy: UnarRarPolicy | None = (
            UnarRarPolicy(self._archive)
            if self._decompressor is RarDecompressor.UNAR
            else None
        )
        if (
            self._unar_policy is not None
            and source.path is not None
            and self._volume_set_size() <= 1
            and self._origin + self._archive.sfx_offset > 0
        ):
            # ``_unar_archive_path`` copies a prefixed file from where the RAR starts,
            # within the spool limit; the size is known, so the note can say whether
            # the copy will be refused.
            self._cost_notes = (
                *self._cost_notes,
                *_bounded_copy_notes(
                    _single_disk_copy_note("unar"),
                    "the archive",
                    "a prefixed archive for unar",
                    self._config.spool_limits,
                    self._unar_copy_size(),
                    "unar",
                ),
            )
        self._check_comment_budget()
        self._archive.comment = self._resolve_rar3_comment(self._archive.comment)
        for info in self._archive.members:
            info.comment = self._resolve_rar3_comment(info.comment)
        self._members = [
            self._to_member(info, index)
            for index, info in enumerate(self._archive.members)
        ]
        self._resolve_file_copies()
        # The dictionary memory each member's read costs under the program that will
        # run it, keyed by ``id(member)``, checked against
        # ``DecoderLimits.max_decoder_memory`` before that program starts. The two
        # programs allocate differently, so each has a rule.
        costs = (
            [
                _DictionaryCost(count, count, declarer)
                for count, declarer in unar_dictionary_costs(self._archive)
            ]
            if self._unar_policy is not None
            else _unrar_dictionary_costs(self._archive)
        )
        self._dictionary_costs = {
            id(member): cost for member, cost in zip(self._members, costs, strict=True)
        }
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
                # An explicit list of volume files is used as given, in the order
                # given. Whether unrar may read them in place is decided after the
                # parse (_choose_unrar_volume_paths).
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

    def _choose_unrar_volume_paths(self) -> None:
        """Stage the volume files unless unrar, handed the first, reads exactly them.

        unrar finds later volumes by name beside the first, under the naming scheme
        the MAIN header names, which can differ from the one the files carry or
        the one sibling discovery matched. Unless that walk finds exactly the files
        parsed here, unrar is pointed at links to them under the set's own names
        instead (:meth:`_stage_explicit_volumes`, on the first read that needs
        unrar), so it cannot read a file this reader never parsed, or miss one it
        did.
        """
        if self._archive_path is None or self._stage_volume_paths:
            return
        if _unrar_finds_exactly(
            self._volume_paths,
            is_volume=self._archive.is_volume,
            old_numbering=self._archive.old_volume_naming,
        ):
            return
        self._archive_path = None
        self._stage_volume_paths = True

    def _volume_set_size(self) -> int:
        """Volumes in this set, whether or not they are files yet."""
        return max(len(self._volume_paths), len(self._stream_volume_items))

    def _materialize_stream_volumes(self) -> None:
        """Write ordered volumes into a temp dir under the names the set's own scheme uses.

        ``name.partN.rar``, or ``name.rar``, ``name.r00``, ``name.r01`` … for a RAR
        1.5-2.x set without the new-numbering flag. ``unrar`` and ``unar`` both look
        for the next volume only under the scheme the header names: an old-style set
        written as ``partN`` reads as volume 1 alone.

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
        budget = self._spool_budget("every volume of the stream source")
        # The whole set is one copy, so the limit weighs the total. The joined source
        # already knows every volume's size, so an oversized set is refused here,
        # before the temp directory exists.
        budget.check_total(self._spool_copy_size())
        temp_dir = Path(tempfile.mkdtemp(prefix="archivey-rar-vol-"))
        self._temp_dir = temp_dir
        stem = "archive"
        if self._archive_name:
            stem = Path(self._archive_name).stem or stem
        paths: list[Path] = []
        try:
            for index, item in enumerate(items, start=1):
                dest = temp_dir / _stream_volume_name(
                    stem, index, old_style=self._archive.old_volume_naming
                )
                # A file volume goes through the budget too, not ``shutil.copy2``:
                # its size was read when the set was joined, and a file that grew
                # since then must not carry the copy past the limit.
                if isinstance(item, Path):
                    with item.open("rb") as src, dest.open("wb") as out:
                        budget.copy(src, out)
                else:
                    start, size = ranges[index - 1]
                    view = self._shared.view(start, size)
                    try:
                        with dest.open("wb") as out:
                            budget.copy(view, out)
                    finally:
                        view.close()
                paths.append(dest)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._temp_dir = None
            raise
        self._volume_paths = paths
        self._archive_path = paths[0]

    def _stage_explicit_volumes(self) -> None:
        """Link the parsed volume files into a temp dir under the set's own names.

        The files may sit in different directories, or carry names that unrar's
        rule for this set (:func:`_unrar_finds_exactly`) does not continue, and
        unrar finds each later volume by name beside the one before. Each file is symlinked, or hard-linked where a
        symlink is not allowed (Windows without the privilege), so nothing is
        copied. Where neither works the set is copied, bounded by
        ``SpoolLimits`` like any other copy this reader makes. Called under
        ``_materialize_lock`` from :meth:`_ensure_archive_path`.
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="archivey-rar-vol-"))
        self._temp_dir = temp_dir
        names = [
            temp_dir
            / _stream_volume_name(
                "archive", index, old_style=self._archive.old_volume_naming
            )
            for index in range(1, len(self._volume_paths) + 1)
        ]
        try:
            try:
                for src, dest in zip(self._volume_paths, names):
                    _link_file(src.absolute(), dest)
            except OSError:
                for dest in names:
                    dest.unlink(missing_ok=True)
                budget = self._spool_budget("every volume")
                sizes = [src.stat().st_size for src in self._volume_paths]
                budget.check_total(sum(sizes))
                for src, dest in zip(self._volume_paths, names):
                    with src.open("rb") as handle, dest.open("wb") as out:
                        budget.copy(handle, out)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._temp_dir = None
            raise
        self._archive_path = names[0]

    def _decompressor_name(self) -> str:
        """The data program as the open-time notes name it."""
        if self._decompressor is RarDecompressor.UNAR:
            return "unar"
        return "RARLAB unrar or rar"

    def _spool_budget(self, what: str) -> SpoolBudget:
        """This reader's spool budget, made on first use and kept for its lifetime.

        One budget per reader, not per attempt: a copy that was refused, or failed
        part-way, is not given a fresh allowance by the next read. Called under
        ``_materialize_lock``. ``what`` names the copy in the refusal; the first call
        fixes it. Only a path source's files are ever linked for ``unar``, so the
        link-fallback copy never follows a stream source's copy on one reader.
        """
        if self._spool is None:
            program = (
                "unar" if self._decompressor is RarDecompressor.UNAR else "RARLAB unrar"
            )
            remedy: dict[str, str] = {}
            source = self._source
            if source is None:
                pass
            elif source.path is not None:
                # A file copied for unar: from where a prefixed RAR starts, or because
                # it could not be linked into unar's private directory. unrar reads
                # a path where it is.
                remedy["remedy"] = (
                    "Set ArchiveyConfig.rar_decompressor to 'unrar', which reads an "
                    "archive file in place, or raise the limit (None removes it)."
                )
            elif source.volume_paths and self._decompressor is RarDecompressor.UNAR:
                # unar reads every set from its private directory, so where the
                # caller keeps the files does not matter; unrar can read them there.
                remedy["remedy"] = (
                    "Set ArchiveyConfig.rar_decompressor to 'unrar', which reads "
                    "volumes in place when they sit in one directory under their "
                    "set's names, or raise the limit (None removes it)."
                )
            elif source.volume_paths:
                # Explicit volume files that could not be linked side by side.
                remedy["remedy"] = (
                    "Put the volumes in one directory under their set's names, where "
                    "they are read in place, or raise the limit (None removes it)."
                )
            self._spool = SpoolBudget(
                self._config.spool_limits,
                what=(
                    f"reading this member needs {program}, which reads only files, "
                    f"so {what} must be copied to a temporary location"
                ),
                archive_name=self._archive_name,
                source_format=ArchiveFormat.RAR,
                **remedy,
            )
        return self._spool

    def _unar_copy_size(self) -> int | None:
        """Bytes :meth:`_unar_archive_path` copies from where the RAR starts, if known."""
        size = self._shared.size
        if size is None:
            return None
        return max(size - self._origin - self._archive.sfx_offset, 0)

    def _spool_copy_size(self) -> int | None:
        """Bytes the copy for ``unrar`` would write, or ``None`` when not known.

        Stream volumes copy every volume whole, and the joined source measured each
        one. A single stream copies from the origin to the end of the source.
        """
        if self._stream_volume_items:
            return sum(size for _, size in self._stream_volume_ranges())
        source_size = self._shared.size
        if source_size is None:
            return None
        return max(source_size - self._origin, 0)

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
                        handles,
                        password=password,
                        max_members=max_members,
                        kdf_cache=self._kdf_cache,
                        name_encoding=self._encoding,
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
                        kdf_cache=self._kdf_cache,
                        name_encoding=self._encoding,
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
                        handle,
                        password=password,
                        max_members=max_members,
                        kdf_cache=self._kdf_cache,
                        name_encoding=self._encoding,
                    )

            view = self._shared.view(0)
            try:
                view.seek(self._origin)
                archive = parse_rar_archive(
                    view,
                    password=password,
                    max_members=max_members,
                    kdf_cache=self._kdf_cache,
                    name_encoding=self._encoding,
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
                    kdf_cache=self._kdf_cache,
                )
            except (EncryptionError, UnicodeError):
                raise wrong_password_error(
                    "Wrong password for this RAR member"
                ) from None
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
            if self._stage_volume_paths:
                self._stage_explicit_volumes()
                assert self._archive_path is not None
                return self._archive_path
            # Single stream source: write one temp .rar for unrar.
            budget = self._spool_budget("the whole stream source")
            # Checked before mkstemp, so a refused archive leaves no temp file. With
            # no size known this still refuses a retry after an earlier refusal.
            budget.check_total(self._spool_copy_size())
            path = self._spool_from(self._origin, budget)
            self._temp_path = path
            self._archive_path = path
            return path

    def _spool_from(
        self,
        start: int,
        budget: SpoolBudget,
        *,
        directory: Path | None = None,
    ) -> Path:
        """Copy the source from ``start`` to a new temp ``.rar``; the caller owns it.

        ``budget`` bounds the copy: a stream source's for ``unrar``, and a single
        archive's for ``unar`` (a stream, or a prefixed path). The caller checks the
        known total against it first. ``directory`` is where the file is made, the
        system temp directory if omitted.
        """
        fd, name = tempfile.mkstemp(suffix=".rar", dir=directory)
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                # From ``start``, so the temp holds the RAR alone and a program that
                # does not look past a prefix sees a plain RAR at byte 0. ``unrar``
                # skips a stub itself, so its path source is passed as is and only
                # a stream comes here; ``unar`` also sends a prefixed path source.
                view = self._shared.view(start)
                try:
                    # The budget stops the copy at the limit when the size checked
                    # before was not known, or was wrong. It reads in 1 MiB chunks,
                    # which keeps the SharedView lock acquisitions few.
                    budget.copy(view, out)
                finally:
                    view.close()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def _unar_archive_path(self, member: ArchiveMember | None) -> Path:
        """A path ``unar`` can open, in a directory that holds this archive alone.

        ``unar`` picks a volume set by file name: handed ``report2024.rar`` beside
        ``report2023.rar``, it reads the pair as one set and returns the neighbour's
        data. So ``unar`` never gets the caller's path. It gets a private directory
        holding exactly the volumes archivey found, under the names
        :func:`_stream_volume_name` gives them; a single archive is ``archive.rar``
        there, with no name another file could continue.

        Each volume is linked, not copied (:func:`_link_file`). Where the system
        allows neither link, the volumes that could not be linked are copied through
        the reader's spool budget: their total is checked before the first byte, so a
        limit below it refuses the read and leaves no directory behind. That copy has
        no open-time cost note, because whether a link works is known only when it is
        tried; the notes cover only the copies known at open (a stream source, and a
        prefixed path source).

        A single stream source has no file to link, so it is copied once straight
        into that directory, within the spool budget.

        ``unar`` also does not look for a RAR after a prefix, whether that is an SFX
        stub or anything else; it reports an unknown format and writes nothing. A
        single prefixed archive is therefore copied once into that directory from
        where the RAR starts: the detected origin plus any stub the parser skipped
        past. A prefixed multi-volume set is refused: every volume would need
        copying under its sibling name, and ``unrar`` reads that set in place.
        """
        start = self._origin + self._archive.sfx_offset
        if self._volume_set_size() > 1 and (
            self._volume0_parse_origin or self._archive.sfx_offset
        ):
            raise self._unar_refused(
                member,
                "unar does not find a RAR after a prefix, and a prefixed multi-volume "
                "set is not copied for it. Set ArchiveyConfig.rar_decompressor to "
                "'unrar' to read it with RARLAB unrar.",
            )
        if (
            self._archive.old_volume_naming
            and self._volume_set_size() > _UNAR_MAX_OLD_STYLE_VOLUMES
        ):
            raise self._unar_refused(
                member,
                f"unar reads at most {_UNAR_MAX_OLD_STYLE_VOLUMES} volumes of an "
                "old-style (.rar, .r00 ...) set and reports the rest as a damaged "
                "member. Set ArchiveyConfig.rar_decompressor to 'unrar' to read it "
                "with RARLAB unrar.",
            )
        if self._unar_path is not None:
            return self._unar_path
        if start == 0 and self._stream_volume_items:
            # Stream volumes are already written to a directory of their own, holding
            # the set and nothing else.
            return self._ensure_archive_path()
        # A path source's files are linked. A single stream source has none: it is
        # copied once, straight into the private directory, by the branch below, so
        # no temp copy of it is made first and then copied again.
        volumes = self._volume_paths if start == 0 else []
        with self._materialize_lock:
            if self._unar_path is not None:
                return self._unar_path
            budget = None
            if not volumes:
                # Bounded like any other copy, and checked before the directory
                # exists when the size is known, as it is for a path.
                budget = self._spool_budget("the archive from where the RAR starts")
                budget.check_total(self._unar_copy_size())
            temp_dir = Path(tempfile.mkdtemp(prefix="archivey-unar-"))
            try:
                if budget is not None:
                    target = temp_dir / "archive.rar"
                    self._spool_from(start, budget, directory=temp_dir).replace(target)
                    self._unar_path = target
                else:
                    self._unar_path = self._link_volumes_for_unar(volumes, temp_dir)
            except BaseException:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
            self._unar_dir = temp_dir
            return self._unar_path

    def _link_volumes_for_unar(self, volumes: list[Path], temp_dir: Path) -> Path:
        """Put ``volumes`` in ``temp_dir`` for ``unar``; return volume 1's path there.

        A lone archive is ``archive.rar``; a set takes its own naming scheme. A volume
        the system will not link is copied, within the spool budget (see
        :meth:`_unar_archive_path`).
        """
        if len(volumes) == 1:
            names = ["archive.rar"]
        else:
            old_style = self._archive.old_volume_naming
            names = [
                _stream_volume_name("archive", index, old_style=old_style)
                for index in range(1, len(volumes) + 1)
            ]
        unlinked: list[tuple[Path, Path]] = []
        for volume, name in zip(volumes, names, strict=True):
            try:
                _link_file(volume.absolute(), temp_dir / name)
            except OSError:
                unlinked.append((volume, temp_dir / name))
        if unlinked:
            budget = self._spool_budget(
                "each volume the system would not link into a private directory"
            )
            budget.check_total(sum(volume.stat().st_size for volume, _ in unlinked))
            for volume, dest in unlinked:
                with volume.open("rb") as src, dest.open("wb") as out:
                    budget.copy(src, out)
        return temp_dir / names[0]

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._members
        if self._archive.data_past_end is not None:
            # Terminal damage after the prefix, so the listing keeps what the file
            # holds and reports the rest as missing (``members_report().error``).
            raise TruncatedError(
                self._archive.data_past_end,
                archive_name=self._archive_name,
                source_format=ArchiveFormat.RAR,
            )
        self._emit_end_block_missing()

    def _emit_end_block_missing(self) -> None:
        """Report RAR5 volumes that end without their end-of-archive block.

        RAR5 writers always close a volume with that block, so a walk that reaches
        end of file without it most likely stopped at a cut on a header boundary: the
        bytes list as a shorter, complete-looking archive. Emitted once, after the
        members, the way TAR reports a missing trailer, so the listing still
        completes and a ``RAISE`` disposition refuses it after delivery.
        """
        missing = self._archive.end_block_missing_volumes
        if not missing:
            return
        if self._archive.is_volume or self._volume_count > 1:
            where = "volume(s) " + ", ".join(str(index + 1) for index in missing)
        else:
            where = "the archive"
        self._diagnostics_collector.emit(
            code=DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING,
            message=(
                f"RAR archive may be truncated: {where} ended without the "
                f"end-of-archive block RAR5 writers always write."
            ),
            context=ArchiveEofContext(
                archive_name=self._archive_name,
                format="rar",
                expected_marker="end_of_archive_block",
                expected_bytes=0,
                observed_bytes=0,
                observed_kind="absent",
            ),
            logger=logger,
        )

    def _check_comment_budget(self) -> None:
        """Refuse comments whose sizes, together, exceed the metadata budget.

        Each compressed comment expands to up to 64 KiB, and ``max_members`` alone
        lets one archive carry a million of them. The header declares every
        ``unpacked_size`` up front, so the total is checked before anything is
        decoded, and a refused archive spawns no ``unrar`` at all. The declared size
        is a real bound on the decode, not a trusted attacker number:
        ``decompress_rar3_blob`` reads at most ``unpacked_size + 1`` bytes from
        ``unrar`` and discards a result of any other length.

        This bounds comment **bytes**, not the number of ``unrar`` spawns: comments
        that each declare a few bytes still cost one process apiece. Decoding every
        comment in a single ``unrar`` call is tracked separately.

        Refusing, rather than dropping the remaining comments to ``None`` the way an
        undecodable comment is dropped, is the maintainer's ruling (``review/backlog.md``,
        "#353 F12"): ``max_metadata_bytes`` means retained metadata on every format, and
        an over-budget listing raises on all of them.

        Comments the parser already decoded (stored ones) are weighed in the same
        total. The parser read them in pieces bounded by the file, so what they hold
        is data the archive really carries; this is where their size is judged.
        """
        comments = [self._archive.comment]
        comments.extend(info.comment for info in self._archive.members)
        total = sum(
            comment.unpacked_size if isinstance(comment, _Rar3Comment) else len(comment)
            for comment in comments
            if comment is not None
        )
        check_metadata_budget(
            self._config.listing_limits,
            total,
            detail=f"RAR comments hold or declare {total} bytes",
        )

    def _resolve_rar3_comment(self, comment: str | _Rar3Comment | None) -> str | None:
        """Return a parsed old-style comment, dropping unavailable/invalid payloads."""
        if not isinstance(comment, _Rar3Comment):
            return comment
        # The selected program decodes the comment, ``unar`` included, without the
        # member refusals of ``UnarRarPolicy``: the CRC16 check below catches any wrong
        # or missing output, and a comment that fails it is dropped either way.
        # ``unar`` 1.10.1 decodes the RAR 1.5 comments of ``rar15-comment.rar``
        # correctly, though it returns nothing for that archive's first member.
        try:
            unpacked = decompress_rar3_blob(
                open_pipe=None if self._unar_policy is None else _open_unar_blob_pipe,
                extract_version=comment.extract_version,
                compress_type=comment.compress_type,
                packed=comment.packed,
                unpacked_size=comment.unpacked_size,
                flags=comment.flags,
                crc16=comment.crc16,
            )
        except (
            OSError,
            PackageNotInstalledError,
            subprocess.SubprocessError,
        ):
            # An undecodable comment degrades to None rather than sinking the
            # listing.
            return None
        if unpacked is None or zlib.crc32(unpacked) & 0xFFFF != comment.crc16:
            return None
        return _decode_comment_text(unpacked)

    def _to_member(self, info: RarMemberInfo, index: int) -> ArchiveMember:
        """Type one member. ``index`` is its position in the walk, the id registration
        stamps, so the diagnostics raised here can name it before it has one."""
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
            created=_rar_created(info),
            ctime=_rar_ctime(info),
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
        self._emit_member_diagnostics(info, member, presented, index)
        return member

    def _emit_member_diagnostics(
        self, info: RarMemberInfo, member: ArchiveMember, presented: str, index: int
    ) -> None:
        """Name-normalization, dropped-header-record, invalid-timestamp and
        tweaked-digest diagnostics.

        All attach onto ``member`` (``attach_to_member=True``) and can raise
        under a strict collector, so this must run before ``_to_member`` returns.
        """
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
            member_id=index,
        )
        self._emit_header_record_diagnostics(info, member.name, member, index)
        for issue in info.timestamp_issues:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_TIMESTAMP_INVALID,
                message=issue.message,
                context=MemberTimestampContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    field=_timestamp_field_name(info, issue.field),
                    source=issue.source,
                    value_repr=issue.value_repr,
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
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
                    member_id=index,
                    algorithm=algo.value,
                    reason="tweaked_checksum",
                ),
                member=member,
                attach_to_member=True,
                logger=integrity_logger,
            )

    def _resolve_file_copies(self) -> None:
        """Point each RAR5 file copy's ``link_target_member`` at its source.

        The source is the latest member before the copy whose name the stored target
        names (read as a hard link's target is: archive-root relative), and it must be
        a ``FILE``. ``rar`` always writes the source first, and ``unrar`` copies from a
        file it has already extracted, so only earlier members count; that also rules
        out a copy of itself and any cycle. A source that is itself a copy stands for
        its own source, so a chain collapses to the one member that holds the bytes.
        A copy left unresolved stays listed and raises ``LinkTargetNotFoundError``
        when read (:meth:`_open_file_copy`).
        """
        latest: dict[str, ArchiveMember] = {}
        for member in self._members:
            raw = member._raw
            assert isinstance(raw, RarMemberInfo)
            if raw.is_file_copy() and member.link_target:
                target_name = resolve_link_target_name(
                    member.name, member.link_target, MemberType.HARDLINK
                )
                source = latest.get(target_name) if target_name is not None else None
                if source is not None and source.type is MemberType.FILE:
                    source_raw = source._raw
                    assert isinstance(source_raw, RarMemberInfo)
                    member.link_target_member = (
                        source.link_target_member
                        if source_raw.is_file_copy()
                        else source
                    )
            latest[member.name] = member

    def _open_file_copy(self, member: ArchiveMember) -> ArchiveStream:
        """Serve a RAR5 file copy from its source member.

        A copy carries no data stream: ``unrar p`` emits nothing for it in a full run
        and ``unar`` emits nothing at all, so the bytes are the source's, read and
        verified as the source. The copy's own CRC32 covers zero bytes
        (``_member_hashes``); its declared size must match the source's, or the copy
        would read as a different length than it lists.
        """
        source = member.link_target_member
        if source is None:
            raise LinkTargetNotFoundError(
                "The source of this RAR file copy is not an earlier file member of "
                "the archive",
                archive_name=self._archive_name,
                member_name=member.name,
                link_target=member.link_target,
            )
        if source.size != member.size:
            raise CorruptionError(
                f"This RAR file copy declares {member.size} bytes but its source "
                f"{quoted(source.name)} declares {source.size}.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )
        return self._open_member(source)

    @staticmethod
    def _member_type(info: RarMemberInfo) -> MemberType:
        if info.is_directory:
            return MemberType.DIRECTORY
        if info.is_file_copy():
            # Extracted as an independent file, as ``unrar`` does, not as a link.
            return MemberType.FILE
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
        if self._unar_policy is not None:
            yield from self._iter_solid_with_unar(self._unar_policy)
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
        pass_costs = self._pass_dictionary_costs()

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
                # Each payload member in the pipe is verified individually (CRC/BLAKE2sp
                # and declared length via fused ArchiveStream verify), so the pipe-level
                # unrar exit code is redundant for corruption and is suppressed here to
                # avoid legacy-format false positives; wrong-password (11) still maps,
                # and so does RAR4's wrong-password exit 2/3 with nothing emitted,
                # which is why the pipe is told whether the archive is encrypted.
                owned = _UnrarOwnedStream(
                    stdout,
                    proc,
                    has_verifiable_hash=True,
                    encrypted=self._archive_has_encryption,
                )
                with _close_on_error(owned):
                    tracked = self._track_decompressed(owned)
                with _close_on_error(tracked):
                    solid = SolidBlockReader(tracked)
            return solid

        pipe_offset = 0

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            nonlocal pipe_offset
            raw = member._raw
            assert isinstance(raw, RarMemberInfo)
            if raw.is_file_copy():
                # Not in the pipe; its source's bytes come from a named open.
                return self._lazy_member_stream(member)
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
            cost = pass_costs[id(member)]

            def open_fn() -> BinaryIO:
                # Checked on the first read, as the spawn is: a pass that skips this
                # member is not refused for it.
                self._check_dictionary_memory(member, cost)
                return self._watch_unverified(
                    _pipe().open_member(member_offset, size, lazy=True), member
                )

            hashes, vsize, transforms, verify_member = self._payload_verify_args(member)
            # Registered like the base class's lazy pass streams, so the pass takes
            # the one live-stream slot and is refused beside a live ``open()``.
            return self._register_public_stream(
                self._wrap_member_stream(
                    None,
                    member.name,
                    open_fn=open_fn,
                    size=member.size,
                    track_output=False,
                    seekable=False,
                    expected_hashes=hashes,
                    expected_size=vsize,
                    digest_transforms=transforms,
                    verify_member=verify_member,
                )
            )

        def _cleanup() -> None:
            if solid is not None:
                solid.close()

        yield from self._drive_pass_streams(
            self._listed_members(),
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
            hash_key = rar5_hash_key(
                _password_as_str(checked) or "",
                enc.salt,
                enc.kdf_count,
                kdf_cache=self._kdf_cache,
            )
        else:
            password = self._unrar_password
            if password is None:
                return None
            hash_key = _tweaked_hash_key(enc, password, self._kdf_cache)
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
        member_id: int | None = None,
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
        is ``None`` for a service header, because there is no member to name. For a
        member, ``member_id`` is its position in the walk, the id registration stamps.
        """
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
        # TruncatedError is a CorruptionError subclass: this clause must stay first.
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
        # Never past the packed span: whatever the unpacked size claims, the bytes
        # after ``compress_size`` belong to the next header, not to this member. A
        # stored member that declares more than it packs then ends short, and the
        # size check reports it as truncated.
        size = min(info.file_size, info.compress_size) if length is None else length
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
            # Stored, so the read is the header's own size and cannot amplify; it is
            # still held to the cap every data-stored target is, and an oversized one
            # is refused before any of it is read.
            if raw.file_size > MAX_LINK_TARGET_BYTES:
                self._emit_link_target_too_long(member)
                return
            view = self._shared.view(raw.data_offset, raw.file_size)
            try:
                data = view.read()
            finally:
                view.close()
            # The header's data CRC32 covers these bytes, and the header CRC does not,
            # so this is the only check a damaged target meets. Held to it as ZIP and
            # 7z hold theirs: a mismatch raises, and link finalization lists the link
            # targetless (`_report_damaged_link_target`) while open/extract re-raise.
            # A short read is caught by the same comparison. Encrypted members never
            # reach here, so the CRC is never a RAR5 key-tweaked one.
            if raw.crc32 is not None and zlib.crc32(data) != raw.crc32:
                raise CorruptionError(
                    "The stored symlink target does not match its CRC32 checksum",
                    archive_name=self._archive_name,
                    member_name=member.name,
                    source_format=ArchiveFormat.RAR,
                )
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

    def _unrar_names(self) -> _UnrarNames:
        names = self._unrar_names_cache
        if names is not None:
            return names
        rar5 = self._archive.version == 5
        views: list[str | None] = []
        positions: dict[int, int] = {}
        by_key: dict[str, list[int]] = {}
        unknown: list[int] = []
        for position, member in enumerate(self._members):
            positions[id(member)] = position
            raw = member._raw
            if not isinstance(raw, RarMemberInfo) or not raw.is_payload_file():
                views.append(None)
                continue
            view = unrar_member_view(
                rar5=rar5,
                stored=raw.orig_filename,
                rar3_unicode_name=raw.rar3_unicode_name,
                host_os=raw.host_os,
                file_version=raw.file_version,
            )
            views.append(view)
            if view is None:
                unknown.append(position)
                continue
            for key in unrar_selection_keys(view):
                by_key.setdefault(key, []).append(position)
        names = _UnrarNames(views, positions, by_key, unknown)
        self._unrar_names_cache = names
        return names

    def _unrar_selection(
        self, target: ArchiveMember, mask_view: str, *, version_control: bool
    ) -> tuple[int, bool, _DictionaryCost]:
        """Where ``target``'s bytes start in the ``unrar -n`` pipe, and what the run needs.

        ``unrar p`` emits every payload member the mask selects, concatenated in
        archive order with no headers: a glob, a duplicate name, two names ``unrar``
        cuts or converts to the same text, a mask that names a directory prefix of
        another member. The first value is the unpacked size of the members before
        the target; the second is True when any other member is selected, so the read
        must stop at the target's size. History rows are left out unless
        ``version_control`` is set, as ``unrar`` leaves them out without ``-ver``.

        The third value is the largest :attr:`_dictionary_costs` entry among the
        target and the selected members before it. ``unrar`` decodes each of those
        in full, so in a nonsolid archive each earlier match sizes its own window.
        In a solid archive the target's own count already covers them. A match after
        the target does not count: the read stops at the target's end, and that
        member's window fills only as ``unrar`` writes to the pipe.

        Raises ``UnsupportedFeatureError`` when the answer is not known: the mask
        does not select the target itself, or an earlier member's name cannot be
        read the way ``unrar`` reads it on this host.
        """
        names = self._unrar_names()
        target_position = names.positions[id(target)]
        keys = unrar_mask_keys(mask_view)
        candidates: Iterable[int]
        if keys is None:
            candidates = range(len(self._members))
        else:
            found: set[int] = set()
            for key in keys:
                found.update(names.by_key.get(key, ()))
            candidates = sorted(found)
        prefix = 0
        others = False
        selects_target = False
        cost = self._dictionary_costs[id(target)]
        for position in candidates:
            view = names.views[position]
            if view is None:
                continue
            raw = self._members[position]._raw
            assert isinstance(raw, RarMemberInfo)
            if raw.is_file_version_history() and not version_control:
                continue
            if not unrar_mask_selects(mask_view, view):
                continue
            if position == target_position:
                selects_target = True
                continue
            others = True
            if position < target_position:
                earlier = self._members[position]
                prefix += _member_stream_size(earlier)
                cost = _larger_cost(cost, self._dictionary_costs[id(earlier)])
        if not selects_target:
            raise self._unrar_name_refused(
                target,
                "the name unrar reads for it cannot be given back to unrar as a mask",
            )
        for position in names.unknown:
            raw = self._members[position]._raw
            assert isinstance(raw, RarMemberInfo)
            if raw.is_file_version_history() and not version_control:
                continue
            if position < target_position:
                raise self._unrar_name_refused(
                    target,
                    "an earlier member's name cannot be read the way unrar reads it "
                    "on this system, so the bytes unrar may send before this member "
                    "cannot be sized",
                )
            others = True
        return prefix, others, cost

    def _unrar_name_refused(
        self, member: ArchiveMember, reason: str
    ) -> UnsupportedFeatureError:
        # unar addresses entries by index, so none of these reasons apply to it.
        return UnsupportedFeatureError(
            f"RAR member {quoted(member.name)} cannot be read through unrar: "
            f"{reason}. Set ArchiveyConfig.rar_decompressor to 'unar' to read "
            "it by position instead.",
            archive_name=self._archive_name,
            member_name=member.name,
            source_format=ArchiveFormat.RAR,
        )

    def _solid_prefix(self, target: ArchiveMember) -> int:
        """Unpacked bytes of earlier payload members in a solid archive.

        A named ``unrar p`` or an ``unar -i`` run for a solid member re-decodes this
        prefix even though the pipe only emits the requested member. Zero when the
        archive is not solid — then the run starts at this member and the
        member-stream ``tell()`` is the whole re-decode cost.

        History rows are counted even without ``-ver``. That is not an oversight
        relative to :meth:`_unrar_selection`, which skips them unless
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

    def _check_dictionary_memory(
        self, member: ArchiveMember, cost: _DictionaryCost
    ) -> None:
        """Refuse a read whose decompressor would allocate over ``max_decoder_memory``.

        ``cost`` comes from :attr:`_dictionary_costs`, from :meth:`_unrar_selection`
        for a named ``unrar`` read, or from :meth:`_pass_dictionary_costs` for a
        solid pass. Runs before the program is spawned and before a stream source is
        copied to disk for it.

        The message names the member whose header declared the dictionary, only when
        the member read does not declare that size itself, and says when the count is
        capped below it, so a caller can find both numbers. A declarer that shares the
        read member's name is named by its archive index instead.
        """
        program = "unar" if self._unar_policy is not None else "unrar"
        counted_from = None
        if cost.declarer >= 0:
            source = self._members[cost.declarer]
            raw = member._raw
            # The member read declaring the same size is named in place of the
            # member that happened to declare it first.
            own = source is member or (
                isinstance(raw, RarMemberInfo) and raw.dictionary_size == cost.declared
            )
            if own:
                owner = "its own header"
            elif source.name == member.name:
                owner = (
                    "the header of an earlier entry with the same name "
                    f"(archive index {cost.declarer}, counting from 0)"
                )
            else:
                owner = f"the header of member {quoted(source.name)}"
            if cost.count < cost.declared:
                counted_from = (
                    f"the {cost.declared}-byte dictionary {owner} declares, capped at "
                    "the unpacked bytes the read decodes"
                )
            elif not own:
                counted_from = f"the dictionary {owner} declares"
        check_decoder_memory(
            cost.count,
            limits=self._config.decoder_limits,
            what=f"the RAR dictionary {program} needs for member {quoted(member.name)}",
            counted_from=counted_from,
        )

    def _pass_dictionary_costs(self) -> dict[int, _DictionaryCost]:
        """:attr:`_dictionary_costs` for one process that decodes the archive in order.

        A solid pass runs one process over every member, so a member's read also
        pays for the largest window an earlier member needed. ``unar`` starts a new
        dictionary for each solid stream, but the process's peak is still the
        largest one so far.
        """
        costs: dict[int, _DictionaryCost] = {}
        peak = _NO_DICTIONARY
        for member in self._members:
            peak = _larger_cost(peak, self._dictionary_costs[id(member)])
            costs[id(member)] = peak
        return costs

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)
        if raw.is_file_copy():
            return self._open_file_copy(member)

        if self._can_direct_read(raw) and raw.compress_size != raw.file_size:
            # A plaintext stored member packs exactly its own bytes; only encryption
            # (padding to the AES block) makes the two sizes differ, and encrypted
            # members never take this path. ``unrar`` trusts the packed size here,
            # the size check trusts the unpacked one, and neither is the member.
            raise CorruptionError(
                f"This stored RAR member declares {raw.file_size} bytes but packs "
                f"{raw.compress_size}; a stored member's two sizes must match.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )

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

        if self._unar_policy is not None:
            return self._open_member_with_unar(member, raw, self._unar_policy)

        # unrar addresses the member by name (``path`` or ``path;n``) through a ``-n``
        # include mask (see open_unrar_p); a history row needs ``-ver``. The mask is
        # built from the name as unrar reads it, which is not always the presented
        # ``member.name``: unrar cuts a RAR5 name at its first byte that is not
        # UTF-8, and an 8-bit RAR3 name goes to unrar as its stored bytes (on
        # Windows, as unrar's own OEM reading of them).
        presented = _presented_filename(raw)
        version_control = raw.is_file_version_history()
        names = self._unrar_names()
        view = names.views[names.positions[id(member)]]
        mask_name = unrar_member_argument(
            view,
            raw.orig_filename,
            stored_is_8bit=self._archive.version == 4 and raw.rar3_unicode_name is None,
        )
        refusal = unrar_member_refusal(mask_name)
        if refusal is None and (
            "\0" in presented
            or (raw.orig_filename is not None and b"\0" in raw.orig_filename)
        ):
            # unrar cuts the name at the NUL; the name is refused rather than read
            # through the part before it.
            refusal = unrar_member_refusal("\0")
        mask_view = None if mask_name is None else unrar_mask_view(mask_name)
        if refusal is None and (
            mask_view is None or not unrar_mask_is_usable(mask_view)
        ):
            refusal = (
                "unrar reads its name as empty, or as a path with no name in it"
                if view is not None
                else "its name cannot be read the way unrar reads it on this system"
            )
        # ``mask_name is None`` always comes with a refusal; it is tested here so the
        # checkers narrow it before ``open_unrar_p``, where ``member=None`` means "no
        # ``-n`` mask at all", every member piped.
        if mask_name is None or mask_view is None or refusal is not None:
            raise self._unrar_name_refused(member, refusal or "no mask")
        glob_mask = "?" in mask_view
        # ``\\`` in the mask is a separator to Windows unrar and a literal on
        # POSIX, so the same ``-n./`` mask selects a different set; Windows CI
        # read nothing for ``a\\b_TGT.txt``. On POSIX a RAR5 Windows-host
        # ``\\`` is read as ``_`` (measured in test_rar_unrar_names.py), which
        # the mask already reflects. On Windows every RAR5 ``\\`` becomes ``_``
        # by the unrar source, which is unmeasured there, so a stored backslash
        # is still refused on Windows.
        if (
            "\\" in mask_view
            or (sys.platform == "win32" and "\\" in presented)
            or (glob_mask and not _unrar_glob_demux_ok(mask_view[2:]))
        ):
            raise UnsupportedFeatureError(
                "RAR member names that unrar reads with a backslash, or with a "
                "glob in a directory component, cannot be read through unrar: "
                "Windows unrar treats a backslash as a separator, and a "
                "directory glob selects members archivey cannot size.",
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.RAR,
            )
        glob_prefix, shares_mask, dictionary_cost = self._unrar_selection(
            member, mask_view, version_control=version_control
        )
        if (
            glob_mask
            and glob_prefix
            and not self._config.rar_allow_glob_member_concatenation
        ):
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
            # A mask with no glob that still selects earlier members (a duplicate
            # name, or two names unrar reads the same way) is read by position
            # without this refusal: its name is not an include mask.
            #
            # The predicate is deliberately `_unrar_selection`'s own answer and
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

        # Counts the other members the mask selects too (a duplicate name is enough),
        # because unrar decodes each of them before the target.
        self._check_dictionary_memory(member, dictionary_cost)
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
                member=mask_name,
                version_control=version_control,
            )
            owned = _UnrarOwnedStream(
                stdout,
                proc,
                named_member=True,
                has_verifiable_hash=has_hash,
                encrypted=raw.is_encrypted,
            )
            # _bounded_member_pipe already closed ``tracked`` (and so ``owned``) if the
            # prefix skip failed; close is idempotent.
            with _close_on_error(owned):
                tracked = self._track_decompressed(owned)
                if glob_mask or shares_mask:
                    return _bounded_member_pipe(
                        tracked,
                        prefix=glob_prefix,
                        size=_member_stream_size(member),
                    )
                return tracked

        return self._open_spawned_member(member, _spawn)

    def _open_spawned_member(
        self, member: ArchiveMember, spawn: Callable[[], BinaryIO]
    ) -> ArchiveStream:
        """Wrap one member's decompressor pipe: respawn on rewind, verify, count.

        ``spawn`` starts the process and returns a stream that owns it, already
        counted by ``_track_decompressed``. The same wrapping serves ``unrar`` and
        ``unar``.
        """
        # Spawn now so PackageNotInstalledError / a missing stdout pipe surface at
        # open(), and so a spawn-count right after open() is 1 (the live-stream
        # gate's "refused second open does not spawn" pin). Password and
        # corruption still map on the completing read — the exit status is only
        # known after the process ends.
        inner = spawn()
        try:
            rewind: RewindWarning | None = None
            if self._seek_declared():
                inner = _RespawnStream(spawn, inner, size=_member_stream_size(member))
                rewind = RewindWarning(
                    codec_name="rar",
                    suggest_install=False,
                    min_redecode_bytes=self._solid_prefix(member),
                )
            inner = self._watch_unverified(inner, member)
            # Folder/pipe output already counted; avoid double-counting at the member wrap.
            # Fused verify in _wrap_payload_stream bounds/checks declared size + digests.
            return self._wrap_payload_stream(
                inner, member, track_output=False, rewind_warning=rewind
            )
        except BaseException:
            inner.close()
            raise

    def _watch_unverified(self, stream: BinaryIO, member: ArchiveMember) -> BinaryIO:
        """Wrap ``stream`` to report an abandoned read of a member no check vouched for.

        RAR3/4 file data has no password check value, so ``unrar`` decodes with
        whatever password it is given, and only the CRC at the member's end tells a
        wrong key. Before that it can stream the wrong key's bytes: always for a stored
        member, and for about three wrong passwords in ten on a compressed one
        (unrar 7.00, dev-docs/formats/rar.md §2.2). A caller who reads a prefix and
        closes never reaches the CRC, so the close reports it. A RAR5 record's 64-bit
        PswCheck vouches for the password, and so does a header-encrypted archive's
        header decryption, which the password passed CRC by CRC; neither is watched.

        The test is the parser's ``is_encrypted``, not the member's wider
        ``encrypted`` (which adds ``encryption_unknown``), on purpose. A member whose
        extra-area walk stopped before its encryption record never meets a key: a
        stored one is proved plaintext by ``_confirm_unsettled_plaintext`` or
        refused, and ``unrar`` reads the same damaged header, so it never sees the
        encryption marker and derives no key. There is no wrong-key prefix to report.
        """
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)
        if not raw.is_encrypted or self._archive.has_header_encryption:
            return stream
        enc = raw.file_encryption
        if enc is not None and _psw_check_usable(enc):
            return stream

        def report(reason: str) -> None:
            missed = (
                "gave up its checksum by seeking"
                if reason == "seek"
                else "was closed before its checksum was reached"
            )
            self._diagnostics_collector.emit(
                code=DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED,
                message=(
                    f"Encrypted RAR member {quoted(member.name)} {missed}, and it "
                    f"carries no password check: the bytes read may have been "
                    f"decrypted with a wrong password."
                ),
                context=EncryptedVerificationContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    check="no_password_check",
                    reason=reason,
                ),
                member=member,
                logger=integrity_logger,
            )

        return UnverifiedPasswordReadWatch(
            stream, size=_member_stream_size(member), on_unverified=report
        )

    def _unar_password(
        self, member: ArchiveMember | None, password: str | None
    ) -> str | None:
        """``password`` if ``unar`` can use it; refuse one it cannot."""
        if password is not None and not unar_password_supported(password):
            raise self._unar_refused(member, REFUSE_NON_ASCII_PASSWORD)
        return password

    def _unar_refused(
        self, member: ArchiveMember | None, reason: str
    ) -> UnsupportedFeatureError:
        if member is None:
            return UnsupportedFeatureError(
                f"Cannot read RAR member data: {reason}",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.RAR,
            )
        return UnsupportedFeatureError(
            f"Cannot read RAR member {quoted(member.name)}: {reason}",
            archive_name=self._archive_name,
            member_name=member.name,
            source_format=ArchiveFormat.RAR,
        )

    def _open_member_with_unar(
        self, member: ArchiveMember, raw: RarMemberInfo, policy: UnarRarPolicy
    ) -> ArchiveStream:
        """Serve one member from ``unar -i <index>``.

        The member is named by its entry index, not by its stored name, so none of the
        ``unrar`` include-mask handling applies: no sibling can match, and a glob name
        costs nothing extra. The refusals are the ones ``unar`` needs instead
        (:class:`UnarRarPolicy`), decided from the parse before anything is spooled or
        spawned.
        """
        return self._open_spawned_member(
            member, self._unar_member_spawner(member, raw, policy)
        )

    def _unar_member_spawner(
        self, member: ArchiveMember, raw: RarMemberInfo, policy: UnarRarPolicy
    ) -> Callable[[], BinaryIO]:
        """Check what ``unar -i <index>`` needs for this member; return its spawner."""
        refusal = policy.member_refusal(raw)
        if refusal is not None:
            raise self._unar_refused(member, refusal)
        self._check_dictionary_memory(member, self._dictionary_costs[id(member)])
        # Picked the way the ``unrar`` path picks it, before anything is copied: a
        # RAR5 PswCheck rejects a wrong candidate here, without spawning ``unar``.
        data_password = self._unar_password(member, self._member_data_password(member))
        # The stored CRC32 or BLAKE2sp, when present, is checked; an encrypted RAR5
        # member's tweaked digest needs the password picked above.
        has_digest = bool(member.hashes) or self._tweaked_verify_spec(raw) is not None
        # ``unar`` answers a wrong password with no output and exit 0.
        empty_means_wrong_password = (
            raw.is_encrypted and _member_stream_size(member) > 0
        )
        index = policy.entry_index(raw)
        path = self._unar_archive_path(member)

        def _spawn() -> BinaryIO:
            proc, stdout = open_unar_stdout(
                path, [index], purpose=UNAR_PURPOSE, password=data_password
            )
            owned = UnarOutputStream(
                stdout,
                proc,
                has_verifiable_digest=has_digest,
                empty_means_wrong_password=empty_means_wrong_password,
            )
            with _close_on_error(owned):
                return self._track_decompressed(owned)

        return _spawn

    def _iter_solid_with_unar(
        self, policy: UnarRarPolicy
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """The solid pass over one all-entries ``unar`` run.

        Same shape as the ``unrar`` pass in :meth:`_iter_with_data`: one process, spawned
        on the first read, demultiplexed by :class:`SolidBlockReader`. The offsets come
        from :meth:`UnarRarPolicy.solid_pass_offset` because ``unar`` emits a different
        set of entries than ``unrar p`` (history rows always, RAR3/4 symlink targets
        too), or only the entries the policy names. A refused member raises on its first
        read, so a pass that only lists, or skips it, is not refused.

        A member with no digest to check is not served from this run: it gets its own
        ``unar -i <index>`` run, as :meth:`_open_member_with_unar` would give it. When
        ``unar`` 1.10.1 drops a member from this run (exit 0), the bytes at that
        member's offset are the next members' or stale window bytes, often enough of
        them to pass the size check; only a digest tells them from the member's. A run
        of one entry writes that entry exactly or not at all, which the size check
        catches.
        """
        solid: SolidBlockReader | None = None
        pass_costs = self._pass_dictionary_costs()

        def _pipe() -> SolidBlockReader:
            nonlocal solid
            if solid is None:
                password = self._unar_password(None, self._archive_data_password())
                path = self._unar_archive_path(None)
                proc, stdout = open_unar_stdout(
                    path,
                    policy.solid_pass_indexes,
                    purpose=UNAR_PURPOSE,
                    password=password,
                )
                # Every member read from this pipe is checked against its declared
                # size and its stored CRC32 or BLAKE2sp; a member without one is read
                # by its own run instead (``_open``). unar 1.10.1 drops a compressed
                # RAR5 member whose last packed byte uses 6-8 bits, exit 0; the bytes
                # then read in its place are later members' or stale window bytes,
                # which the digest catches and the size check often does not. The
                # exit status adds nothing. A wrong password gives no output at all,
                # reported as such when a password was needed.
                owned = UnarOutputStream(
                    stdout,
                    proc,
                    has_verifiable_digest=True,
                    empty_means_wrong_password=(
                        self._archive_has_encryption and policy.solid_pass_emits_data()
                    ),
                )
                with _close_on_error(owned):
                    tracked = self._track_decompressed(owned)
                with _close_on_error(tracked):
                    solid = SolidBlockReader(tracked)
            return solid

        def _refuse(member: ArchiveMember, reason: str) -> BinaryIO:
            raise self._unar_refused(member, reason)

        def _read(
            member: ArchiveMember, offset: int, size: int, cost: _DictionaryCost
        ) -> BinaryIO:
            # Checked on the first read, as the spawn is.
            self._check_dictionary_memory(member, cost)
            return self._watch_unverified(
                _pipe().open_member(offset, size, lazy=True), member
            )

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            raw = member._raw
            assert isinstance(raw, RarMemberInfo)
            if raw.is_file_copy():
                # As in the ``unrar`` pass: not in the pipe, read from its source.
                return self._lazy_member_stream(member)
            if not raw.is_payload_file() or not member.is_file:
                return None
            size = _member_stream_size(member)
            refusal = policy.solid_pass_refusal(raw)
            hashes, vsize, transforms, verify_member = self._payload_verify_args(member)
            open_fn: Callable[[], BinaryIO]
            if refusal is not None:
                open_fn = lambda: _refuse(member, refusal)  # noqa: E731
            elif not hashes:
                # No digest would catch misplaced bytes from the shared run (above).
                open_fn = lambda: self._watch_unverified(  # noqa: E731
                    self._unar_member_spawner(member, raw, policy)(), member
                )
            else:
                offset = policy.solid_pass_offset(raw)
                cost = pass_costs[id(member)]
                open_fn = lambda: _read(member, offset, size, cost)  # noqa: E731
            # Registered for the live-stream gate, as in the ``unrar`` pass.
            return self._register_public_stream(
                self._wrap_member_stream(
                    None,
                    member.name,
                    open_fn=open_fn,
                    size=member.size,
                    track_output=False,
                    seekable=False,
                    expected_hashes=hashes,
                    expected_size=vsize,
                    digest_transforms=transforms,
                    verify_member=verify_member,
                )
            )

        def _cleanup() -> None:
            if solid is not None:
                solid.close()

        yield from self._drive_pass_streams(
            self._listed_members(),
            open_member=_open,
            close_previous=True,
            cleanup=_cleanup,
        )

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
            if self._unar_dir is not None:
                # Links, or copies where links are unavailable: removing them leaves
                # the caller's files alone.
                shutil.rmtree(self._unar_dir, ignore_errors=True)
                self._unar_dir = None
                self._unar_path = None
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
    USES_ENCODING = True  # for RAR 1.5-4 names stored as 8-bit bytes
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

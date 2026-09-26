"""RAR policy for the ``unar`` data path: what ``unar`` may read, and where its bytes land.

The process layer is :mod:`archivey.internal.external.unar` and knows nothing about RAR.
This module holds the RAR facts that decide how :mod:`.rar_reader` uses it, all taken
from the native parse before any process starts:

- **Entry indexes.** ``unar -i`` addresses an entry by its position in ``lsar``'s list,
  which for RAR is one entry per FILE header in archive order, a file split across
  volumes counted once. That is the order of :attr:`RarArchive.members`.
- **What an all-entries run emits.** ``unar -o -`` with no index writes each entry's
  unpacked bytes in archive order. That differs from ``unrar p``: every file-version
  history row is included (``unrar`` needs ``-ver``), a RAR3/4 symlink emits its stored
  target, and a directory or a RAR5 redirect emits nothing.
- **Refusals.** The reads ``unar`` 1.10 is known to get wrong, or that would need a
  password on its command line. Each is refused with ``UnsupportedFeatureError`` before
  ``unar`` runs, because ``unar`` reports several of them with exit 0.

Measurements: ``dev-docs/investigations/alternative-rar-decompressors.md`` and
``scripts/exploration/rar_decompressor_matrix.py``.
"""

from __future__ import annotations

from archivey.internal.backends.rar_parser import RarArchive, RarMemberInfo

# RAR3/4 method byte for "stored". Compression versions below 2.0 are decoded by a
# separate RAR 1.5 algorithm, which only matters when the data is compressed.
_METHOD_STORED = 0x30
_FIRST_UNAR_SAFE_EXTRACT_VERSION = 20

UNAR_PURPOSE = (
    "to read RAR member data with ArchiveyConfig.rar_decompressor set to 'unar'"
)

_USE_UNRAR = (
    "Set ArchiveyConfig.rar_decompressor to 'unrar' to read it with RARLAB unrar."
)

REFUSE_ENCRYPTED = (
    "unar is not used for encrypted RAR data: it accepts a password only on its command "
    "line, where other local users can read it, and it answers a wrong password with "
    "success and no data. " + _USE_UNRAR
)
REFUSE_RAR15 = (
    "unar 1.10 returns no data, and reports success, for a member compressed with the "
    "RAR 1.5 algorithm. " + _USE_UNRAR
)
REFUSE_RAR5_SOLID_AFTER_EMPTY = (
    "unar 1.10 crashes, or reports success with no data, on a RAR5 solid archive when a "
    "member with data follows an empty file, a directory or a link. " + _USE_UNRAR
)


# The most entries one solid pass names on the ``unar`` command line. Windows limits a
# command line to 32 767 characters; 4000 indexes of up to six digits plus a space
# stay under that with room for the fixed argv and a long archive path. Only a pass
# with a refused member names entries at all.
MAX_SELECTED_ENTRIES = 4000

REFUSE_TOO_MANY_SELECTED = (
    f"this archive has a member that unar cannot read, so a single unar run must name "
    f"each readable member, and it can name at most {MAX_SELECTED_ENTRIES}. Open this "
    "member on its own instead. " + _USE_UNRAR
)


def unar_entry_index(archive: RarArchive) -> dict[int, int]:
    """``id(member info)`` to its ``unar -i`` index."""
    return {id(info): index for index, info in enumerate(archive.members)}


def _carries_no_solid_data(info: RarMemberInfo) -> bool:
    return (
        info.is_directory
        or info.file_redir is not None
        or info.is_hardlink_or_copy
        or info.file_size == 0
    )


def unar_emitted_size(info: RarMemberInfo) -> int:
    """Bytes ``unar -o -`` writes for this entry in an all-entries run."""
    if info.is_directory or info.file_redir is not None or info.is_hardlink_or_copy:
        return 0
    return info.file_size


def unar_pipe_offsets(archive: RarArchive) -> dict[int, int]:
    """``id(member info)`` to where its bytes start in an all-entries ``unar`` run."""
    offsets: dict[int, int] = {}
    position = 0
    for info in archive.members:
        offsets[id(info)] = position
        position += unar_emitted_size(info)
    return offsets


def _rar5_solid_after_empty(archive: RarArchive) -> set[int]:
    """``id``s of RAR5 solid members that ``unar`` 1.10 does not decode.

    Measured with ``unar`` 1.10.1 (SIGSEGV) and XADMaster 1.10.8 (exit 0, no data): a
    member with data that comes after an empty file or a directory in a RAR5 solid
    archive. An empty entry *last* is harmless, and so is anything before it. Links are
    included on the same grounds as directories (no data in the solid stream), without
    a failing sample: a link before data in a solid RAR5 archive is untested, and this
    refuses rather than guesses. RAR3/4 solid archives decoded correctly in every
    shape measured, so they are not refused; the size and digest check on every member
    is the net there.
    """
    if archive.version != 5 or not archive.is_solid:
        return set()
    refused: set[int] = set()
    seen_empty = False
    for info in archive.members:
        if seen_empty and not _carries_no_solid_data(info):
            refused.add(id(info))
        if _carries_no_solid_data(info):
            seen_empty = True
    return refused


def _needs_password(archive: RarArchive, info: RarMemberInfo) -> bool:
    return archive.has_header_encryption or info.is_encrypted or info.encryption_unknown


class UnarRarPolicy:
    """The refusals and pipe layout for one parsed archive, computed once."""

    def __init__(self, archive: RarArchive) -> None:
        self._archive = archive
        self._index = unar_entry_index(archive)
        self._solid_after_empty = _rar5_solid_after_empty(archive)
        self._any_password = archive.has_header_encryption or any(
            _needs_password(archive, info) for info in archive.members
        )
        self._pass_refusals: dict[int, str] = {}
        self._pass_indexes: list[int] | None = None
        self._pass_offsets: dict[int, int] = {}
        if not self._any_password:
            self._plan_solid_pass()

    def _plan_solid_pass(self) -> None:
        """Choose the one ``unar`` run a solid pass reads, and where each member lands.

        With nothing refused, the run selects every entry and the offsets follow
        :func:`unar_emitted_size`. With a refused member, the run names only the
        readable payload members by index. The refused member must stay out of the run,
        not only out of the demux: ``unar`` 1.10.1 crashes on it, and the crash loses
        output of *earlier* members that ``unar`` had buffered but not yet written.
        """
        payload = [info for info in self._archive.members if info.is_payload_file()]
        for info in payload:
            reason = self.member_refusal(info)
            if reason is not None:
                self._pass_refusals[id(info)] = reason
        if not self._pass_refusals:
            self._pass_offsets = unar_pipe_offsets(self._archive)
            return
        selected: list[RarMemberInfo] = []
        for info in payload:
            if id(info) in self._pass_refusals:
                continue
            if len(selected) == MAX_SELECTED_ENTRIES:
                self._pass_refusals[id(info)] = REFUSE_TOO_MANY_SELECTED
                continue
            selected.append(info)
        position = 0
        for info in selected:
            self._pass_offsets[id(info)] = position
            position += info.file_size
        self._pass_indexes = [self._index[id(info)] for info in selected]

    def entry_index(self, info: RarMemberInfo) -> int:
        return self._index[id(info)]

    def member_refusal(self, info: RarMemberInfo) -> str | None:
        """Why ``unar`` must not read this member on its own, or ``None``."""
        if _needs_password(self._archive, info):
            return REFUSE_ENCRYPTED
        if (
            info.extract_version is not None
            and info.extract_version < _FIRST_UNAR_SAFE_EXTRACT_VERSION
            and info.compress_type != _METHOD_STORED
        ):
            return REFUSE_RAR15
        if id(info) in self._solid_after_empty:
            return REFUSE_RAR5_SOLID_AFTER_EMPTY
        return None

    @property
    def solid_pass_indexes(self) -> list[int] | None:
        """The ``unar -i`` selection for a solid pass; ``None`` selects every entry."""
        return self._pass_indexes

    def solid_pass_refusal(self, info: RarMemberInfo) -> str | None:
        """Why this payload member cannot come out of the solid pass's run, or ``None``.

        One encrypted member anywhere refuses the whole run: ``unar`` would need the
        password to decode the solid stream up to any later member.
        """
        if self._any_password:
            return REFUSE_ENCRYPTED
        return self._pass_refusals.get(id(info))

    def solid_pass_offset(self, info: RarMemberInfo) -> int:
        """Where this readable payload member starts in the solid pass's run."""
        return self._pass_offsets[id(info)]

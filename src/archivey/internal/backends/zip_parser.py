"""Native ZIP structure parser (structure only: no names decoded, no codecs, no crypto).

Layout this parser assumes::

    [ prefix? ]
    [ local file header · data · data descriptor? ] × n   # PK\\x03\\x04
    [ central directory header ] × n                      # PK\\x01\\x02
    [ ZIP64 end record · ZIP64 locator ]?                 # PK\\x06\\x06, PK\\x06\\x07
    [ end of central directory record · comment ]         # PK\\x05\\x06

Call graph: :func:`find_end_record` (from the tail, or past the directory after the
members) → :class:`CentralDirectoryWalk`
(forward over the directory, one :class:`CentralEntry` at a time) →
:func:`read_local_header` (per member, when its data is wanted).

Every read goes through a ``read_at(offset, n)`` callable, so the caller owns the
handle, its position and its lock. The parser returns stored bytes and integers; it
decodes no name or comment and emits no diagnostic. Damage raises ``CorruptionError``
or ``TruncatedError`` (an archive cut short), a valid feature archivey refuses raises
``UnsupportedFeatureError``. The caller stamps archive and member context.

The ZIP64 record checks and the stub offset (``base``) follow stdlib ``zipfile``
(3.13). The end-record search is stdlib's older window, one byte wider than 3.13's
(see ``EOCD_SEARCH_WINDOW``), so a prefixed or commented archive resolves to the record
stdlib reads whenever stdlib finds one. The walk tolerates more than stdlib does: it does not refuse
the archive for an extra field cut short or a version-needed it does not know, and it
yields every entry before a damaged one.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from archivey.exceptions import (
    CorruptionError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.zip_aes import iter_extra_fields
from archivey.internal.backends.zip_detect import (
    LOCAL_HEADER_SIGNATURE,
    LOCAL_HEADER_SIZE,
    ZIP_MULTI_VOLUME_MSG,
)

#: ``read_at(offset, n)``: up to ``n`` bytes from absolute ``offset``; fewer only at
#: the end of the source.
ReadAt = Callable[[int, int], bytes]

EOCD_SIGNATURE = b"PK\x05\x06"
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
CENTRAL_HEADER_SIGNATURE = b"PK\x01\x02"
# Archive extra data record: written in front of a central directory that PKWARE
# Strong Encryption has encrypted (APPNOTE §4.3.11, §7.3).
ARCHIVE_EXTRA_DATA_SIGNATURE = b"PK\x06\x08"

EOCD_SIZE = 22
ZIP64_LOCATOR_SIZE = 20
ZIP64_EOCD_SIZE = 56
CENTRAL_HEADER_SIZE = 46
# How far before the end of the file the end-record search starts: stdlib's older
# window (3.11, early 3.12 releases), ``1 << 16`` plus the record. The comment length
# is a uint16, so this is one byte more than the format needs; 3.13 and later 3.12
# releases shrank stdlib's window to match. Keeping the wider one reads every archive
# either window reads, so one byte of junk after a maximal comment stays readable on
# every Python.
EOCD_SEARCH_WINDOW = (1 << 16) + 22
STRONG_ENCRYPTED_DIRECTORY_MSG = (
    "PKWARE Strong Encryption is not supported (only ZipCrypto and WinZip AES are); "
    "this archive's central directory appears to be encrypted with it"
)

_EOCD = struct.Struct("<4s4H2LH")
_ZIP64_LOCATOR = struct.Struct("<4sLQL")
_ZIP64_EOCD = struct.Struct("<4sQ2H2L4Q")
_CENTRAL_HEADER = struct.Struct("<4s4B4HL2L5H2L")
_LOCAL_HEADER = struct.Struct("<4s2B4HL2L2H")

_ZIP64_EXTRA_TAG = 0x0001
_U32_MAX = 0xFFFF_FFFF
_U64_MAX = 0xFFFF_FFFF_FFFF_FFFF
# Classic end-record disk fields use 0xFFFF to mean "see the ZIP64 record", not disk
# 65535.
_ZIP64_DISK_SENTINEL = 0xFFFF

# The directory is read forward in pieces of this size. It trades memory (one piece
# plus one entry) against the number of reads, each a seek and a read on the shared
# handle; a directory no larger than one piece is read in one read.
_WALK_CHUNK = 1 << 20

# The largest offset a seek takes (a signed 64-bit file offset). A ZIP64 field can
# declare any uint64, and seeking past this raises a raw OverflowError, so an offset
# past it is refused as corrupt before anything seeks to it. It is a structural bound,
# not a policy one: no file holds a header there. Offsets below it need no bound of
# their own, because the reader keeps every member's data before the next local header
# or the central directory (the overlap guard), and a header read past the file's end
# is cut short.
MAX_DATA_OFFSET = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class EndRecord:
    """The end of central directory record, with the ZIP64 record's values when present."""

    #: Absolute position of the classic record's signature.
    eocd_offset: int
    #: A ZIP64 end record supplied the counts, size and offset below.
    zip64: bool
    #: Total entries the record declares (16 bits in a classic record).
    entries_declared: int
    cd_size: int
    #: The directory offset as stored, before ``base``.
    cd_offset: int
    #: Added to every stored offset: the bytes in front of an archive whose writer did
    #: not adjust its offsets for them (a self-extractor stub). stdlib calls it
    #: ``concat``. 0 for an ordinary archive.
    base: int
    #: The archive comment, cut at the end of the file.
    comment: bytes
    #: The comment length the record declares; above ``len(comment)`` when the file
    #: ends first.
    comment_declared: int
    #: Bytes after the record and its declared comment, up to the end of the file. The
    #: reader reports them; the parser only counts them. Junk longer than the search
    #: window hides the record itself, as it does for stdlib.
    trailing: int

    @property
    def cd_start(self) -> int:
        """Absolute position of the first central directory header."""
        return self.cd_offset + self.base


NO_END_RECORD_MSG = (
    "truncated or corrupt ZIP (no end of central directory record found)"
)


def find_end_record(
    read_at: ReadAt,
    file_size: int,
    *,
    members_end: Callable[[], int | None] | None = None,
) -> EndRecord:
    """Locate and read the end record, and the ZIP64 records when there are some.

    The search is stdlib's older one: a comment-less record ending at end of file, then
    the last ``PK\\x05\\x06`` in the final 65 558 bytes. Newer stdlib's window (3.13,
    later 3.12 releases) is one byte shorter; the wider one finds the same record whenever the shorter one finds
    any. A decoy signature earlier in the file, in the comment or in the record's own
    fields therefore cannot make the two disagree.

    When the search finds none (more trailing bytes than the window holds),
    ``members_end`` is called for where the members end, which the caller knows from a
    walk over the local headers (``None`` when the walk found no end), and the record
    is found by stepping over the directory from there
    (:func:`step_over_directory`).

    Raises ``UnsupportedFeatureError`` for a record that names another disk (a spanned
    set) and for an encrypted central directory (PKWARE Strong Encryption), and
    ``CorruptionError`` when no record is found or the records contradict each other.
    The two halves are :func:`locate_end_record` and :func:`read_end_record`.
    """
    found, _stopped = locate_end_record(read_at, file_size, members_end=members_end)
    if found is None:
        raise CorruptionError(NO_END_RECORD_MSG)
    return read_end_record(read_at, file_size, found)


def locate_end_record(
    read_at: ReadAt,
    file_size: int,
    *,
    members_end: Callable[[], int | None] | None = None,
) -> tuple[tuple[int, bytes, bytes] | None, int | None]:
    """``(offset, record, comment)`` of the end record :func:`find_end_record`
    reads, or ``None`` when neither the search nor the step finds one; and where the
    step over the directory stopped (:func:`step_over_directory`), ``None`` when no
    step ran."""
    found = locate_classic_record(read_at, file_size)
    if found is None and members_end is not None:
        directory_at = members_end()
        if directory_at is not None:
            return step_over_directory(read_at, file_size, directory_at)
    return found, None


def read_end_record(
    read_at: ReadAt, file_size: int, found: tuple[int, bytes, bytes]
) -> EndRecord:
    """The end record :func:`locate_end_record` found, with the ZIP64 records."""
    eocd_offset, record, comment = found
    (
        _sig,
        this_disk,
        cd_start_disk,
        _entries_this_disk,
        entries_total,
        cd_size,
        cd_offset,
        comment_declared,
    ) = _EOCD.unpack(record)
    zip64 = _read_zip64_records(read_at, eocd_offset)
    if zip64 is not None:
        # The ZIP64 record's disk fields replace the classic ones, as stdlib's do.
        records_start, this_disk, cd_start_disk, entries_total, cd_size, cd_offset = (
            zip64
        )
    else:
        records_start = eocd_offset
    if disk_field_is_split(this_disk) or disk_field_is_split(cd_start_disk):
        raise UnsupportedFeatureError(ZIP_MULTI_VOLUME_MSG)
    base = records_start - cd_size - cd_offset
    end = EndRecord(
        eocd_offset=eocd_offset,
        zip64=zip64 is not None,
        entries_declared=entries_total,
        cd_size=cd_size,
        cd_offset=cd_offset,
        base=base,
        comment=comment,
        comment_declared=comment_declared,
        trailing=max(file_size - (eocd_offset + EOCD_SIZE + comment_declared), 0),
    )
    if end.cd_start < 0:
        raise CorruptionError("Bad offset for central directory")
    # PKWARE Strong Encryption can encrypt the directory itself; its writer puts an
    # archive extra data record where the directory starts. Checked before the walk,
    # so the refusal does not depend on how the encrypted bytes happen to parse.
    if read_at(end.cd_start, 4) == ARCHIVE_EXTRA_DATA_SIGNATURE:
        raise UnsupportedFeatureError(STRONG_ENCRYPTED_DIRECTORY_MSG)
    return end


def locate_classic_record(
    read_at: ReadAt, file_size: int
) -> tuple[int, bytes, bytes] | None:
    """``(offset, record, comment)`` of the classic end record, or ``None``."""
    if file_size < EOCD_SIZE:
        return None
    tail = read_at(file_size - EOCD_SIZE, EOCD_SIZE)
    if (
        len(tail) == EOCD_SIZE
        and tail[:4] == EOCD_SIGNATURE
        and tail[-2:] == b"\x00\x00"
    ):
        return file_size - EOCD_SIZE, tail, b""
    window_start = max(file_size - EOCD_SEARCH_WINDOW, 0)
    window = read_at(window_start, file_size - window_start)
    idx = window.rfind(EOCD_SIGNATURE)
    if idx < 0:
        return None
    record = window[idx : idx + EOCD_SIZE]
    if len(record) != EOCD_SIZE:
        return None
    (comment_declared,) = struct.unpack_from("<H", record, 20)
    comment = window[idx + EOCD_SIZE : idx + EOCD_SIZE + comment_declared]
    return window_start + idx, record, comment


def step_over_directory(
    read_at: ReadAt, file_size: int, directory_at: int
) -> tuple[tuple[int, bytes, bytes] | None, int]:
    """``(offset, record, comment)`` of the classic end record that follows the
    directory starting at ``directory_at``, or ``None``; and where the step stopped:
    the end record's offset, or that of the bytes it could not step over.

    Steps over what may lie between the members and the end record, in the order
    APPNOTE §4.3.6 gives: the directory's headers, a ZIP64 end record and its
    locator. Each step reads a length field, so only the records' fixed parts are
    read, not searched. ``None`` when anything else comes first, a record comes out of
    that order, or the file ends before the end record. A digital signature record
    (``PK\\x05\\x05``) is not stepped over: the directory walk does not read past
    one either, wherever the end record is found.
    """
    order = (
        CENTRAL_HEADER_SIGNATURE,
        ZIP64_EOCD_SIGNATURE,
        ZIP64_LOCATOR_SIGNATURE,
        EOCD_SIGNATURE,
    )
    buffer = b""
    buffer_at = 0

    def fixed(pos: int, n: int) -> bytes:
        # The directory is read forward in pieces, not one read per header.
        nonlocal buffer, buffer_at
        if not buffer_at <= pos <= pos + n <= buffer_at + len(buffer):
            buffer_at = pos
            buffer = read_at(pos, max(n, min(_WALK_CHUNK, file_size - pos)))
        return buffer[pos - buffer_at : pos - buffer_at + n]

    pos = directory_at
    stage = 0
    while pos + 4 <= file_size:
        sig = fixed(pos, 4)
        if sig not in order or order.index(sig) < stage:
            return None, pos
        stage = order.index(sig)
        if sig == CENTRAL_HEADER_SIGNATURE:
            header = fixed(pos, CENTRAL_HEADER_SIZE)
            if len(header) != CENTRAL_HEADER_SIZE:
                return None, pos
            name_len, extra_len, comment_len = struct.unpack_from("<3H", header, 28)
            pos += CENTRAL_HEADER_SIZE + name_len + extra_len + comment_len
        elif sig == ZIP64_EOCD_SIGNATURE:
            header = fixed(pos, 12)
            if len(header) != 12:
                return None, pos
            pos += 12 + struct.unpack_from("<Q", header, 4)[0]
        elif sig == ZIP64_LOCATOR_SIGNATURE:
            pos += ZIP64_LOCATOR_SIZE
        else:
            record = read_at(pos, EOCD_SIZE)
            if len(record) != EOCD_SIZE:
                return None, pos
            (comment_declared,) = struct.unpack_from("<H", record, 20)
            return (pos, record, read_at(pos + EOCD_SIZE, comment_declared)), pos
    return None, pos


def read_zip64_locator(read_at: ReadAt, eocd_offset: int) -> tuple[int, int] | None:
    """``(locator_offset, record_offset)`` of the ZIP64 locator right before the
    classic record at ``eocd_offset``, or ``None`` when there is none.

    A locator naming another disk is refused as a split set, and one naming a record
    past the place right before it as corruption, before any record is read.
    """
    locator_offset = eocd_offset - ZIP64_LOCATOR_SIZE
    if locator_offset < 0:
        return None
    locator = read_at(locator_offset, ZIP64_LOCATOR_SIZE)
    if len(locator) != ZIP64_LOCATOR_SIZE:
        return None
    sig, disk_with_record, record_offset, disks = _ZIP64_LOCATOR.unpack(locator)
    if sig != ZIP64_LOCATOR_SIGNATURE:
        return None
    if disk_with_record != 0 or disks > 1:
        raise UnsupportedFeatureError(ZIP_MULTI_VOLUME_MSG)
    adjacent = locator_offset - ZIP64_EOCD_SIZE
    if adjacent < 0 or record_offset > adjacent:
        raise CorruptionError("Corrupt ZIP64 end of central directory locator")
    return locator_offset, record_offset


def zip64_record_matches(record: bytes, record_offset: int, extensible: int) -> bool:
    """Whether ``record`` is a whole ZIP64 end record whose directory ends at the
    locator's ``record_offset`` and whose size counts ``extensible`` bytes after it."""
    if len(record) != ZIP64_EOCD_SIZE or not record.startswith(ZIP64_EOCD_SIGNATURE):
        return False
    fields = _ZIP64_EOCD.unpack(record)
    record_size, cd_size, cd_offset = fields[1], fields[8], fields[9]
    return (
        cd_offset + cd_size == record_offset
        and record_size + 12 == ZIP64_EOCD_SIZE + extensible
    )


def _read_zip64_records(
    read_at: ReadAt, eocd_offset: int
) -> tuple[int, int, int, int, int, int] | None:
    """``(record_start, this_disk, cd_disk, entries, cd_size, cd_offset)``, or ``None``.

    ``None`` when no locator sits right before the classic record. ``record_start`` is
    where the ZIP64 end record (with any extensible data) starts, the position the stub
    offset is measured from. The checks are stdlib 3.13's: the locator's record offset
    must agree with the record's own size and directory span; when it does not but a
    record sits right before the locator, prepended data moved everything and that
    record is read instead (with no extensible data). The directory-span check is
    against the locator's offset in both cases, as stdlib's is: with unadjusted offsets
    behind a stub, the stored offsets all omit the stub.
    """
    located = read_zip64_locator(read_at, eocd_offset)
    if located is None:
        return None
    locator_offset, record_offset = located
    adjacent = locator_offset - ZIP64_EOCD_SIZE
    extensible = adjacent - record_offset
    record = read_at(record_offset, ZIP64_EOCD_SIZE)
    if not record.startswith(ZIP64_EOCD_SIGNATURE) and record_offset != adjacent:
        extensible = 0
        record = read_at(adjacent, ZIP64_EOCD_SIZE)
    if len(record) != ZIP64_EOCD_SIZE or not record.startswith(ZIP64_EOCD_SIGNATURE):
        raise CorruptionError("ZIP64 end of central directory record not found")
    if not zip64_record_matches(record, record_offset, extensible):
        raise CorruptionError("Corrupt ZIP64 end of central directory record")
    (
        _sig,
        _record_size,
        _made_by,
        _needed,
        this_disk,
        cd_disk,
        _entries_this_disk,
        entries_total,
        cd_size,
        cd_offset,
    ) = _ZIP64_EOCD.unpack(record)
    return adjacent - extensible, this_disk, cd_disk, entries_total, cd_size, cd_offset


def disk_field_is_split(value: int) -> bool:
    """A classic or ZIP64 end-record disk field that names another disk.

    0xFFFF in a classic field means "see the ZIP64 record", not disk 65535, so it is not
    a split; a naive ``!= 0`` check would refuse legitimate ZIP64 archives. In a ZIP64
    record the field is a uint32, so 0xFFFF is a real disk there; it is tolerated
    anyway, to keep the refusal identical to the stdlib-based reader's.
    """
    return value not in (0, _ZIP64_DISK_SENTINEL)


@dataclass(slots=True)
class CentralEntry:
    """One central directory header, with its ZIP64 extra field applied.

    Read-only by convention, not ``frozen``: a frozen dataclass sets each of its fields
    through ``object.__setattr__``, three times the cost of construction, and the walk
    builds one per member.
    """

    #: Position in the directory, from 0.
    index: int
    #: "Version made by": the low byte is the APPNOTE version, the high byte the host.
    version_made_by: int
    #: "Version needed to extract" as one uint16, as ``zip_detect`` reads it and bounds
    #: it at 10..99. APPNOTE gives its high byte no meaning and writers leave it zero;
    #: stdlib splits it off instead.
    version_needed: int
    flags: int
    method: int
    #: The DOS time and date words as stored (the ZipCrypto check byte under bit 3 is
    #: the time's high byte).
    dos_time: int
    dos_date: int
    crc: int
    compressed_size: int
    file_size: int
    #: Absolute position of the local header: the stored offset plus ``EndRecord.base``.
    header_offset: int
    disk_start: int
    internal_attr: int
    external_attr: int
    #: The stored name bytes, never decoded here.
    name: bytes
    extra: bytes
    comment: bytes

    @property
    def create_system(self) -> int:
        return self.version_made_by >> 8

    @property
    def date_time(self) -> tuple[int, int, int, int, int, int]:
        """The DOS stamp as ``(year, month, day, hour, minute, second)``, unvalidated.

        The same arithmetic as ``zipfile.ZipInfo.date_time``: no range check, so a
        damaged stamp comes back as numbers ``datetime`` may refuse.
        """
        d, t = self.dos_date, self.dos_time
        return (
            (d >> 9) + 1980,
            (d >> 5) & 0xF,
            d & 0x1F,
            t >> 11,
            (t >> 5) & 0x3F,
            (t & 0x1F) * 2,
        )


#: Where the walk found the end record and the directory disagreeing. The reader turns
#: each into a diagnostic once the members are out.
@dataclass(frozen=True, slots=True)
class EntryCountMismatch:
    declared: int
    read: int
    zip64: bool


@dataclass(frozen=True, slots=True)
class CommentCutShort:
    declared: int
    available: int


@dataclass(frozen=True, slots=True)
class EntryOverrun:
    """An entry whose name, extra field or comment runs past the directory's end."""

    index: int
    field: Literal["name", "extra field", "comment"]
    #: Where the entry ends, counted from the directory's start.
    entry_end: int
    cd_size: int


WalkFinding = EntryCountMismatch | CommentCutShort | EntryOverrun


class CentralDirectoryWalk:
    """Iterate the central directory forward, one :class:`CentralEntry` at a time.

    Reads ``end.cd_size`` bytes from the directory's start in pieces, as stdlib does in
    one read, and parses an entry only when the iteration asks for it. An entry whose
    name, extra field or comment runs past ``cd_size`` keeps the bytes before that
    point and ends the walk, as in stdlib; it is reported in :attr:`findings`.

    Damage raises ``CorruptionError`` after every entry before it has been yielded: a
    header without its signature, a fixed header that crosses the declared end of the
    directory, a ZIP64 extra field missing a value the header defers to it. The
    directory always ends where the end records start (``find_end_record`` derives its
    start from that), so a file cut inside it has lost its end record and never gets
    here.

    Each ``iter()`` walks again from the start. :attr:`findings` is complete once an
    iteration has ended without raising, and is reset when the next one starts.
    """

    def __init__(self, read_at: ReadAt, end: EndRecord) -> None:
        self._read_at = read_at
        self._end = end
        self.findings: list[WalkFinding] = []
        self.entries_read: int | None = None

    def __iter__(self) -> Iterator[CentralEntry]:
        end = self._end
        self.findings = []
        self.entries_read = None
        start = end.cd_start
        cd_size = end.cd_size
        base = end.base
        read_at = self._read_at
        data = b""  # directory bytes from data_start, never past cd_size
        data_start = 0  # directory-relative offset of data[0]

        def ensure(pos: int, want_end: int) -> None:
            """Hold the directory bytes up to ``want_end`` (or ``cd_size``), from ``pos``."""
            nonlocal data, data_start
            have = data_start + len(data)
            if want_end <= have or have >= cd_size:
                return
            # Drop what the walk has passed, then read forward in large pieces.
            pieces = [data[pos - data_start :]]
            data_start = pos
            target = min(max(want_end, have + _WALK_CHUNK), cd_size)
            while have < target:
                piece = read_at(start + have, target - have)
                if not piece:
                    break
                pieces.append(piece)
                have += len(piece)
            data = b"".join(pieces)

        pos = 0
        index = 0
        while pos < cd_size:
            ensure(pos, pos + CENTRAL_HEADER_SIZE)
            off = pos - data_start
            if len(data) - off < CENTRAL_HEADER_SIZE:
                raise CorruptionError(
                    f"Truncated central directory: entry #{index} starts "
                    f"{cd_size - pos} bytes before the directory's declared end"
                )
            fields = _unpack_central(data, off, index)
            name_len, extra_len, comment_len = fields[12:15]
            name_at = pos + CENTRAL_HEADER_SIZE
            extra_at = name_at + name_len
            comment_at = extra_at + extra_len
            entry_end = comment_at + comment_len
            ensure(pos, entry_end)
            # Slices stop at the end of what was read, which never passes cd_size.
            at = name_at - data_start
            name = data[at : at + name_len]
            at += name_len
            extra = data[at : at + extra_len]
            at += extra_len
            comment = data[at : at + comment_len]
            if entry_end > cd_size:
                if extra_at > cd_size:
                    field: Literal["name", "extra field", "comment"] = "name"
                elif comment_at > cd_size:
                    field = "extra field"
                else:
                    field = "comment"
                self.findings.append(EntryOverrun(index, field, entry_end, cd_size))

            yield _central_entry(fields, name, extra, comment, index=index, base=base)
            index += 1
            pos = entry_end

        self.entries_read = index
        # A classic record counts in 16 bits. Old 7-Zip versions stored the low 16
        # bits of a larger count there without writing ZIP64; current 7-Zip
        # (ZipIn.cpp) accepts that with a "16-bit overflow for number of files in
        # headers" note rather than a Headers Error, so the comparison is modulo
        # 65536 too.
        if end.entries_declared != (index if end.zip64 else index & 0xFFFF):
            self.findings.append(
                EntryCountMismatch(end.entries_declared, index, end.zip64)
            )
        if end.comment_declared > len(end.comment):
            self.findings.append(
                CommentCutShort(end.comment_declared, len(end.comment))
            )


#: The fixed header's fields as ``_CENTRAL_HEADER`` unpacks them: signature, made-by
#: version and host, needed version and host, flags, method, DOS time and date, CRC,
#: both sizes, the three variable lengths, disk, both attribute fields, header offset.
_CentralFields = tuple[
    bytes,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
]


def _unpack_central(buffer: bytes, offset: int, index: int) -> _CentralFields:
    """Unpack the fixed 46 bytes of central directory header ``index`` at ``offset``.

    A tuple, not an object: the walk unpacks one per member. ``CorruptionError`` when
    the signature is not there.
    """
    fields = _CENTRAL_HEADER.unpack_from(buffer, offset)
    if fields[0] != CENTRAL_HEADER_SIGNATURE:
        raise CorruptionError(
            f"Bad magic number for central directory (entry #{index})"
        )
    return fields


def _central_entry(
    fields: _CentralFields,
    name: bytes,
    extra: bytes,
    comment: bytes,
    *,
    index: int,
    base: int,
) -> CentralEntry:
    """Build the entry: apply the ZIP64 extra field, then ``base`` to the offset."""
    (
        _signature,
        made_version,
        made_system,
        needed_version,
        needed_system,
        flags,
        method,
        dos_time,
        dos_date,
        crc,
        compressed_size,
        file_size,
        _name_len,
        _extra_len,
        _comment_len,
        disk_start,
        internal_attr,
        external_attr,
        header_offset,
    ) = fields
    compressed_size, file_size, header_offset, disk_start = _apply_zip64_extra(
        extra,
        compressed_size=compressed_size,
        file_size=file_size,
        header_offset=header_offset,
        disk_start=disk_start,
        index=index,
    )
    return CentralEntry(
        index,
        (made_system << 8) | made_version,
        (needed_system << 8) | needed_version,
        flags,
        method,
        dos_time,
        dos_date,
        crc,
        compressed_size,
        file_size,
        header_offset + base,
        disk_start,
        internal_attr,
        external_attr,
        name,
        extra,
        comment,
    )


def _apply_zip64_extra(
    extra: bytes,
    *,
    compressed_size: int,
    file_size: int,
    header_offset: int,
    disk_start: int,
    index: int,
) -> tuple[int, int, int, int]:
    """The four values after the ZIP64 extra field (``0x0001``) replaces the deferred ones.

    APPNOTE 4.5.3: the field holds, in this order, only the values whose header field
    is all ones: uncompressed size, compressed size, local header offset, disk number.
    As in stdlib, every ``0x0001`` field in the blob is applied in turn: a value still
    all ones after one field (a 64-bit all-ones uncompressed size counts) is read again
    from the next. A deferred size or offset a field does not hold is
    ``CorruptionError``. The disk number is the exception: stdlib never reads it and
    nothing consumes it yet, so when no field holds it, it stays ``0xFFFF`` ("not
    known") rather than refusing an archive stdlib opens; it is read once, from the
    first field with four bytes left after the other values (its first four bytes when
    it is the only deferred value, per APPNOTE). A field elsewhere in the blob that is
    cut short is left alone: it says nothing about these four.
    """
    if not (
        file_size == _U32_MAX
        or compressed_size == _U32_MAX
        or header_offset == _U32_MAX
        or disk_start == 0xFFFF
    ):
        return compressed_size, file_size, header_offset, disk_start
    disk_deferred = disk_start == 0xFFFF
    for field in iter_extra_fields(extra):
        if field.tag != _ZIP64_EXTRA_TAG:
            continue
        data = field.data
        cursor = 0

        def take(width: int, what: str) -> int:
            nonlocal cursor
            if cursor + width > len(data):
                raise CorruptionError(
                    f"Corrupt ZIP64 extra field in central directory entry "
                    f"#{index}: {what} not found"
                )
            value = int.from_bytes(data[cursor : cursor + width], "little")
            cursor += width
            return value

        if file_size in (_U32_MAX, _U64_MAX):
            file_size = take(8, "uncompressed size")
        if compressed_size == _U32_MAX:
            compressed_size = take(8, "compressed size")
        if header_offset == _U32_MAX:
            header_offset = take(8, "local header offset")
        if disk_deferred and cursor + 4 <= len(data):
            disk_start = take(4, "disk number")
            disk_deferred = False
    return compressed_size, file_size, header_offset, disk_start


@dataclass(frozen=True, slots=True)
class LocalHeader:
    """A local file header: the copy of the member's fields in front of its data."""

    #: One uint16, as ``CentralEntry.version_needed``.
    version_needed: int
    flags: int
    method: int
    dos_time: int
    dos_date: int
    #: 0 with general-purpose bit 3, which defers it to a data descriptor.
    crc: int
    compressed_size: int
    file_size: int
    name: bytes
    extra: bytes
    #: Absolute position of the member's first data byte.
    data_start: int


def read_local_header(
    read_at: ReadAt, header_offset: int, *, zip64_sizes: bool = False
) -> LocalHeader:
    """Parse the local header at absolute ``header_offset``.

    Reads the fixed 30 bytes, then the name and extra field. Name and extra lengths are
    uint16, so 65 535 is legal and they need no cap of their own; offsets past
    :data:`MAX_DATA_OFFSET` are refused. The caller decides what a disagreement with the
    central directory means. ``zip64_sizes`` is :func:`local_header_from`'s.
    """
    if not 0 <= header_offset <= MAX_DATA_OFFSET:
        raise CorruptionError(f"Absurd local-header offset: {header_offset}")
    fixed = read_at(header_offset, LOCAL_HEADER_SIZE)
    if len(fixed) == LOCAL_HEADER_SIZE and fixed[:4] != LOCAL_HEADER_SIGNATURE:
        raise CorruptionError("Bad magic number for file header")
    if len(fixed) != LOCAL_HEADER_SIZE:
        if fixed and not LOCAL_HEADER_SIGNATURE.startswith(fixed[:4]):
            raise CorruptionError("Bad magic number for file header")
        raise TruncatedError("Truncated local file header")
    variable_len = local_header_variable_length(fixed)
    variable = read_at(header_offset + LOCAL_HEADER_SIZE, variable_len)
    if len(variable) != variable_len:
        raise TruncatedError("Truncated local file header")
    data_start = header_offset + LOCAL_HEADER_SIZE + variable_len
    if data_start > MAX_DATA_OFFSET:
        raise CorruptionError(f"Absurd local-header data offset: {data_start}")
    return local_header_from(fixed, variable, data_start, zip64_sizes=zip64_sizes)


def local_header_variable_length(fixed: bytes) -> int:
    """How many name and extra-field bytes follow the fixed local header ``fixed``."""
    name_len, extra_len = struct.unpack_from("<HH", fixed, LOCAL_HEADER_SIZE - 4)
    return name_len + extra_len


def local_header_from(
    fixed: bytes, variable: bytes, data_start: int, *, zip64_sizes: bool = False
) -> LocalHeader:
    """The :class:`LocalHeader` for the fixed 30 bytes and the name and extra field.

    Both walks read the bytes their own way and call this. The sizes are the stored
    32-bit fields unless ``zip64_sizes``, which applies a ZIP64 extra field's sizes
    (:func:`_local_sizes`): the forward walk has no central entry to take them from.
    """
    (
        _sig,
        needed_version,
        needed_system,
        flags,
        method,
        dos_time,
        dos_date,
        crc,
        compressed_size,
        file_size,
        name_len,
        _extra_len,
    ) = _LOCAL_HEADER.unpack(fixed)
    extra = variable[name_len:]
    if zip64_sizes:
        compressed_size, file_size = _local_sizes(extra, compressed_size, file_size)
    return LocalHeader(
        version_needed=(needed_system << 8) | needed_version,
        flags=flags,
        method=method,
        dos_time=dos_time,
        dos_date=dos_date,
        crc=crc,
        compressed_size=compressed_size,
        file_size=file_size,
        name=variable[:name_len],
        extra=extra,
        data_start=data_start,
    )


def _local_sizes(extra: bytes, compressed_size: int, file_size: int) -> tuple[int, int]:
    """The two sizes after a local ZIP64 extra field replaces the deferred ones.

    In a local header the field holds both sizes, uncompressed first, whichever is
    deferred (APPNOTE 4.5.3). A field shorter than that holds the deferred ones in the
    central directory's order. A deferred size no field holds stays all ones.
    """
    if compressed_size != _U32_MAX and file_size != _U32_MAX:
        return compressed_size, file_size
    for field in iter_extra_fields(extra):
        if field.tag != _ZIP64_EXTRA_TAG:
            continue
        data = field.data
        if len(data) >= 16:
            if file_size == _U32_MAX:
                file_size = int.from_bytes(data[:8], "little")
            if compressed_size == _U32_MAX:
                compressed_size = int.from_bytes(data[8:16], "little")
        else:
            cursor = 0
            if file_size == _U32_MAX and len(data) >= 8:
                file_size = int.from_bytes(data[:8], "little")
                cursor = 8
            if compressed_size == _U32_MAX and len(data) >= cursor + 8:
                compressed_size = int.from_bytes(data[cursor : cursor + 8], "little")
        break
    return compressed_size, file_size

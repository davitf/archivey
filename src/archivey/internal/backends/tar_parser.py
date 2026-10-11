"""Native TAR header parser and header walker (structure only: no member mapping).

Reads TAR headers one at a time from a byte stream and returns small data objects.
``tar_reader.py`` still parses headers with stdlib ``tarfile``; nothing reads through
this module yet. Its tests compare it with ``tarfile`` and GNU tar.

On-disk layout this parser assumes::

    [ 512-byte header ]  v7, POSIX ustar ("ustar\\0" "00") or old GNU ("ustar  \\0")
    [ data, padded to 512 ]          only for types that carry data
    ...
    [ zero block ] [ zero block ]    the end-of-archive marker

    Extended headers come before the member they describe, in any order:
      x / X   PAX records for the next member      g  PAX records for every later member
      L       GNU long name                        K  GNU long link name
    Each is a header block plus its data, which holds the records or the name.

Every header encoding ``tarfile`` reads is read here: base-256 numbers, the ustar
``prefix``, PAX records (per-member and global) and the four GNU sparse encodings (old
GNU ``S`` with extension blocks, PAX 0.0, 0.1 and 1.0).

The walk is a loop, not a recursion, and allocates nothing whose size comes from a
header field before the caller's budget allows it: an extended header is charged
before it is read, and a sparse map is charged by its entry count before its entries
are parsed. Strings stay ``bytes``; decoding happens in the reader, where the caller's
``encoding=`` is known.

Where GNU tar 1.35 and ``tarfile`` read a malformed archive differently, this parser
follows GNU tar, the format's official tool (design rule DR-6), unless a comment says
otherwise.
"""

from __future__ import annotations

import errno
import re
from array import array
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ResourceLimitError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.streams.streamtools import read_within_reach
from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.terminal import quoted

BLOCKSIZE = 512

# A size or offset past this is no file's size: seeking there fails (OverflowError,
# EINVAL) on every stream archivey reads from.
MAX_OFFSET = 2**63 - 1

# What a sparse map entry is charged against ``max_metadata_bytes``, which counts
# header text, not allocator bytes: 24 is the width of an entry in its fixed-width
# encoding (two 12-byte numbers in an old GNU header), whatever encoding the map came
# in. The archive-reading spec sets this weight. A SparseMap holds 16 bytes per entry.
SPARSE_ENTRY_BYTES = 24

# The most digits one number of a PAX sparse 1.0 map may have. GNU tar reads each into
# a buffer sized for the largest ``uintmax_t`` (20 digits) and refuses a longer one.
# Structural, not a policy limit: without it a number with no newline after it would
# grow one buffer for as long as the archive lasts.
SPARSE_NUMBER_DIGITS = 20

# The read step for data whose size a header declares: an extended header, or the data
# area a forward-only walk reads through. A declared size is attacker-chosen, so it is
# never one read.
READ_STEP = 64 * 1024

POSIX_MAGIC = b"ustar\x00"
GNU_MAGIC = b"ustar  \x00"

# Typeflags.
SPARSE_TYPE = b"S"
LINK_TYPES = frozenset((b"1", b"2"))
PAX_MEMBER_TYPES = frozenset((b"x", b"X"))  # ``X`` is Solaris's spelling of ``x``
PAX_GLOBAL_TYPE = b"g"
LONG_NAME_TYPE = b"L"
LONG_LINK_TYPE = b"K"
EXTENDED_TYPES = PAX_MEMBER_TYPES | {PAX_GLOBAL_TYPE, LONG_NAME_TYPE, LONG_LINK_TYPE}
# Types whose size field does not announce a data area. ``tarfile`` skips no data
# after these; GNU tar skips none after a directory and fails on the others with
# "Skipping to next header". Data declared on one of them is read as the next header,
# as both tools read it. Every other typeflag, including the regular types and ones
# neither tool knows (``V``, ``M``, ``N``, ``D`` ...), has its data skipped by size.
NO_DATA_TYPES = frozenset((b"1", b"2", b"3", b"4", b"5", b"6"))


class HeaderFormat(Enum):
    """Which header layout a block uses, from its magic field."""

    V7 = "v7"
    USTAR = "ustar"
    GNU = "gnu"


class NameSource(Enum):
    """Which record a member's name or link name came from."""

    HEADER = "header"  # the ustar/v7/GNU header block (with the ustar prefix)
    GNU_LONG = "gnu_long"  # a GNU ``L`` or ``K`` header
    PAX = "pax"  # a PAX ``path``/``linkpath`` or ``GNU.sparse.name`` record


class SparseFormat(Enum):
    OLD_GNU = "old_gnu"
    PAX_0_0 = "0.0"
    PAX_0_1 = "0.1"
    PAX_1_0 = "1.0"


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True, slots=True)
class HeaderBlock:
    """One decoded 512-byte header block. Strings are the stored bytes, cut at the
    first NUL; ``name`` already has the ustar ``prefix`` joined to it."""

    offset: int
    format: HeaderFormat
    name: bytes
    mode: int
    uid: int
    gid: int
    size: int
    mtime: int
    typeflag: bytes
    linkname: bytes
    uname: bytes
    gname: bytes
    devmajor: int
    devminor: int
    # Old GNU ``S`` only: the four in-header map slots, whether extension blocks
    # follow, and the logical size.
    sparse_slots: tuple[tuple[int, int], ...] = ()
    sparse_extended: bool = False
    sparse_realsize: int = 0


@dataclass(frozen=True, slots=True)
class ZeroBlock:
    """A block of 512 NUL bytes: the first block of an end-of-archive marker."""

    offset: int


@dataclass(frozen=True, slots=True)
class RejectedBlock:
    """A full block that is not a valid header. ``reason`` names what failed."""

    offset: int
    reason: str


class _BadNumber(ValueError):
    pass


def _cut_nul(field: bytes) -> bytes:
    end = field.find(b"\x00")
    return field if end < 0 else field[:end]


_OCTAL = re.compile(rb" *([0-7]*)[ \x00]*")


def parse_number(field: bytes) -> int:
    """A numeric header field: octal or GNU base-256.

    Octal may have leading spaces and ends at a space or NUL, as GNU tar and every
    writer store it; an empty field is 0. Base-256 is flagged by the first byte:
    ``0x80`` positive, ``0xFF`` negative (two's complement over the field).

    The base-256 form is ``tarfile``'s, on purpose: GNU tar takes any first byte with
    the high bit set and counts its low seven bits in the value, so a field starting
    with ``0x81`` is a number to GNU tar and :class:`_BadNumber` here. Such a value
    is at least 2**56 in magnitude, past any id, mode, size or time a real archive
    holds, and every answer stays what ``tarfile`` gave.
    """
    if not field:
        return 0
    first = field[0]
    if first == 0x80 or first == 0xFF:
        value = int.from_bytes(field[1:], "big")
        if first == 0xFF:
            value -= 1 << (8 * (len(field) - 1))
        return value
    match = _OCTAL.fullmatch(field)
    if match is None:
        raise _BadNumber(field)
    digits = match.group(1)
    return int(digits, 8) if digits else 0


def _checksums(block: bytes) -> tuple[int, int]:
    """The unsigned and signed sums of ``block`` with its checksum field as spaces.

    Old Sun and other writers summed signed bytes; GNU tar and ``tarfile`` accept
    either sum.
    """
    unsigned = sum(block[:148]) + 8 * 0x20 + sum(block[156:])
    high = sum(1 for b in block[:148] if b >= 0x80) + sum(
        1 for b in block[156:] if b >= 0x80
    )
    return unsigned, unsigned - 256 * high


_FIELDS = (
    ("mode", 100, 108),
    ("uid", 108, 116),
    ("gid", 116, 124),
    ("size", 124, 136),
    ("mtime", 136, 148),
    ("devmajor", 329, 337),
    ("devminor", 337, 345),
)


def parse_header_block(
    block: bytes, offset: int
) -> HeaderBlock | ZeroBlock | RejectedBlock:
    """Decode one full 512-byte block.

    No exception for an outcome the walk expects: a zero block ends the walk and a
    rejected block is classified by the caller, which knows whether it came first.
    """
    assert len(block) == BLOCKSIZE
    if block.count(0) == BLOCKSIZE:
        return ZeroBlock(offset)
    try:
        stored_sum = parse_number(block[148:156])
    except _BadNumber:
        return RejectedBlock(offset, "the checksum field is not a number")
    if stored_sum not in _checksums(block):
        return RejectedBlock(offset, "bad header checksum")
    values: dict[str, int] = {}
    for field, start, end in _FIELDS:
        try:
            values[field] = parse_number(block[start:end])
        except _BadNumber:
            return RejectedBlock(offset, f"the {field} field is not a number")
    if values["size"] < 0:
        return RejectedBlock(offset, f"negative size {values['size']}")
    if values["size"] > MAX_OFFSET:
        return RejectedBlock(offset, f"size {values['size']} is past any file's size")
    typeflag = block[156:157]
    magic = block[257:265]
    name = _cut_nul(block[0:100])
    sparse_slots: tuple[tuple[int, int], ...] = ()
    sparse_extended = False
    sparse_realsize = 0
    if magic == GNU_MAGIC:
        header_format = HeaderFormat.GNU
    elif magic[:6] == POSIX_MAGIC:
        header_format = HeaderFormat.USTAR
    else:
        header_format = HeaderFormat.V7
    if typeflag == SPARSE_TYPE:
        # The old GNU sparse slots. Read whatever the magic says, as tarfile reads
        # them; GNU tar writes them only in old GNU headers.
        try:
            sparse_slots = _sparse_slots(block, 386, 4)
            sparse_realsize = parse_number(block[483:495])
        except _BadNumber:
            return RejectedBlock(
                offset,
                "an old GNU sparse field is not a number or past any file's size",
            )
        sparse_extended = block[482] != 0
    elif header_format is HeaderFormat.USTAR:
        # GNU tar joins the prefix for any "ustar\0" magic, whatever the version
        # bytes, and never for old GNU or v7 headers, whose bytes 345.. hold other
        # fields or nothing. (tarfile joins it for every header but GNU types.)
        prefix = _cut_nul(block[345:500])
        if prefix:
            name = prefix + b"/" + name
    return HeaderBlock(
        offset=offset,
        format=header_format,
        name=name,
        typeflag=typeflag,
        linkname=_cut_nul(block[157:257]),
        uname=_cut_nul(block[265:297]),
        gname=_cut_nul(block[297:329]),
        sparse_slots=sparse_slots,
        sparse_extended=sparse_extended,
        sparse_realsize=sparse_realsize,
        **values,
    )


def _sparse_slots(block: bytes, start: int, count: int) -> tuple[tuple[int, int], ...]:
    """Old GNU map slots: ``count`` (offset, numbytes) pairs of 12-byte numbers.

    The list ends at the first slot whose numbytes field is empty, as GNU tar reads
    it (``oldgnu_add_sparse``); a real final entry ``(realsize, 0)`` is stored as
    digits, so it is kept.
    """
    slots = []
    for i in range(count):
        pos = start + 24 * i
        if block[pos + 12] == 0:
            break
        offset = parse_number(block[pos : pos + 12])
        length = parse_number(block[pos + 12 : pos + 24])
        # A base-256 field reaches 2**95 either side of zero. A negative entry that
        # fits is kept, for validate_sparse_map to name with its offset.
        if not all(-MAX_OFFSET - 1 <= n <= MAX_OFFSET for n in (offset, length)):
            raise _BadNumber(block[pos : pos + 24])
        slots.append((offset, length))
    return tuple(slots)


# ------------------------------------------------------------------------ PAX records


_PAX_LENGTH = re.compile(rb"([0-9]{1,20}) ")


@dataclass(frozen=True, slots=True)
class PaxValue:
    """One PAX record's value, with whether its block declared ``hdrcharset=BINARY``
    (then a name value has no declared encoding; otherwise it is UTF-8)."""

    value: bytes
    binary: bool


def parse_pax_records(
    data: bytes, *, binary_default: bool
) -> list[tuple[bytes, PaxValue]]:
    """Parse ``"<length> <key>=<value>\\n"`` records, in order.

    ``length`` is decimal and counts the whole record, itself and the newline
    included; it must land on the newline. Parsing stops at a NUL byte where a record
    would start, which is the block padding. ``hdrcharset`` in the same block applies
    to every name value in it (POSIX); ``binary_default`` is the global one in force.
    Raises :class:`CorruptionError` on a record that does not parse.
    """
    raw: list[tuple[bytes, bytes]] = []
    binary = binary_default
    pos = 0
    end = len(data)
    while pos < end and data[pos] != 0:
        match = _PAX_LENGTH.match(data, pos)
        if match is None:
            raise CorruptionError(
                f"TAR PAX header has a malformed record at byte {pos}"
            )
        length = int(match.group(1))
        record_end = pos + length
        # The shortest record is "5 x=\n".
        if length < 5 or record_end > end or data[record_end - 1] != 0x0A:
            raise CorruptionError(
                f"TAR PAX header has a record at byte {pos} whose length {length} "
                f"does not end on a newline"
            )
        key, equals, value = data[match.end() : record_end - 1].partition(b"=")
        if not key or not equals:
            raise CorruptionError(
                f"TAR PAX header has a record at byte {pos} with no key"
            )
        if key == b"hdrcharset":
            binary = value == b"BINARY"
        raw.append((key, value))
        pos = record_end
    return [(key, PaxValue(value, binary)) for key, value in raw]


def _pax_int(records: Mapping[bytes, PaxValue], key: bytes) -> int | None:
    entry = records.get(key)
    if entry is None or not entry.value:
        return None
    text = entry.value
    if not text.isdigit():
        raise CorruptionError(
            f"TAR PAX record {key.decode()} is not a number: {text[:40]!r}"
        )
    return int(text)


# ------------------------------------------------------------------------ sparse maps


@dataclass(slots=True)
class SparseMap:
    """A sparse member's chunks: ``offsets[i]`` is where chunk ``i`` sits in the
    logical file, ``lengths[i]`` how many stored bytes it holds. Chunks are stored
    one after another in the data area, in this order."""

    offsets: array[int]
    lengths: array[int]

    def __len__(self) -> int:
        return len(self.offsets)

    @classmethod
    def from_pairs(cls, pairs: list[int]) -> SparseMap:
        assert len(pairs) % 2 == 0, "a sparse map is (offset, length) pairs"
        try:
            return cls(array("q", pairs[0::2]), array("q", pairs[1::2]))
        except OverflowError:
            # The parsers refuse such a number with a better message first.
            raise CorruptionError(
                "TAR sparse map has an entry past any file's size"
            ) from None


Charge = Callable[[int, str], None]
"""``charge(nbytes, what)``: count ``nbytes`` of retained header text against the
member's budget, raising :class:`ResourceLimitError` when it is spent. ``what`` names
the cost for the message."""


def _map_number(item: bytes, name: str, what: str) -> int:
    """One decimal number of a PAX sparse map. ``name`` is already quoted."""
    if not item.isdigit() or len(item) > SPARSE_NUMBER_DIGITS:
        raise CorruptionError(
            f"TAR sparse map of {name} ({what}) has a bad number: {item[:40]!r}"
        )
    value = int(item)
    if value > MAX_OFFSET:
        raise CorruptionError(
            f"TAR sparse map of {name} ({what}) has {value}, past any file's size"
        )
    return value


def _parse_map_numbers(text: bytes, name: str, *, what: str) -> list[int]:
    return [_map_number(item, name, what) for item in text.split(b",")]


def sparse_map_0_1(value: bytes, name: str, charge: Charge) -> SparseMap:
    """PAX sparse 0.1: ``GNU.sparse.map`` is ``offset,numbytes,offset,numbytes...``.

    The entry count is charged from the comma count before any number is parsed.
    """
    commas = value.count(b",")
    if commas % 2 == 0:
        raise CorruptionError(
            f"TAR sparse map of {name} (GNU.sparse.map) has an odd count of numbers"
        )
    charge(((commas + 1) // 2) * SPARSE_ENTRY_BYTES, "sparse map")
    return SparseMap.from_pairs(_parse_map_numbers(value, name, what="GNU.sparse.map"))


def sparse_map_0_0(
    records: list[tuple[bytes, PaxValue]], name: str, charge: Charge
) -> SparseMap:
    """PAX sparse 0.0: repeated ``GNU.sparse.offset`` / ``GNU.sparse.numbytes``
    records, in order. Charged from the record count before parsing."""
    offsets = [v.value for k, v in records if k == b"GNU.sparse.offset"]
    lengths = [v.value for k, v in records if k == b"GNU.sparse.numbytes"]
    if len(offsets) != len(lengths):
        raise CorruptionError(
            f"TAR sparse map of {name} (GNU.sparse.offset/numbytes) has "
            f"{len(offsets)} offsets and {len(lengths)} lengths"
        )
    charge(len(offsets) * SPARSE_ENTRY_BYTES, "sparse map")
    pairs: list[int] = []
    for offset, length in zip(offsets, lengths, strict=True):
        # One number per record: a comma is not a separator here.
        pairs.append(_map_number(offset, name, "GNU.sparse.offset"))
        pairs.append(_map_number(length, name, "GNU.sparse.numbytes"))
    return SparseMap.from_pairs(pairs)


def read_sparse_map_1_0(
    read: Callable[[int], bytes], stored_size: int, name: str, charge: Charge
) -> tuple[SparseMap, int]:
    """PAX sparse 1.0: the map is decimal lines at the start of the data area — the
    entry count, then an offset and a length per entry — padded to a whole block.

    ``read(n)`` reads the data area from its start. Returns the map and how many bytes
    of the data area it takes (whole blocks); the chunks start there. The count is
    charged before the entries are read.
    """
    buffer = bytearray()
    consumed = 0
    numbers: list[int] = []
    wanted: int | None = None

    def next_number() -> int:
        nonlocal consumed
        while True:
            newline = buffer.find(b"\n")
            if newline >= 0:
                item = bytes(buffer[:newline])
                del buffer[: newline + 1]
                return _map_number(item, name, "1.0")
            if len(buffer) > SPARSE_NUMBER_DIGITS:
                raise CorruptionError(
                    f"TAR sparse map of {name} (1.0) has a number longer than "
                    f"{SPARSE_NUMBER_DIGITS} digits"
                )
            if consumed + BLOCKSIZE > stored_size:
                raise CorruptionError(
                    f"TAR sparse map of {name} (1.0) runs past the member's data"
                )
            block = read(BLOCKSIZE)
            if len(block) < BLOCKSIZE:
                raise TruncatedError(
                    f"TAR archive is truncated inside the sparse map of {name}"
                )
            consumed += BLOCKSIZE
            buffer.extend(block)

    wanted = next_number()
    charge(wanted * SPARSE_ENTRY_BYTES, "sparse map")
    for _ in range(2 * wanted):
        numbers.append(next_number())
    return SparseMap.from_pairs(numbers), consumed


def validate_sparse_map(
    sparse: SparseMap, size: int, stored: int, name: str
) -> CorruptionError | UnsupportedFeatureError | None:
    """The error opening this sparse member must raise, or ``None``.

    ``size`` is the logical size, ``stored`` the bytes the data area holds for the
    chunks (the 1.0 map's own blocks excluded). ``name`` goes into the message as it
    is, so the caller passes it through ``quoted()``. Damage is :class:`CorruptionError`: a
    negative entry, a chunk past the logical size, or chunks that do not add up to
    exactly the stored bytes (more would read the next header as data; fewer leaves
    bytes inside the member that nothing names, DR-3). A map whose chunks are out of
    order or overlap is valid data archivey will not serve: ``tarfile`` stitched such
    chunks into the wrong bytes, and serving them in logical order in a forward pass
    would buffer up to the logical size. It is :class:`UnsupportedFeatureError`, and
    only when the map has no damage. An empty entry is exempt from the order check:
    GNU tar ends a map with ``(realsize, 0)`` when the file ends in a hole.
    """
    if size > MAX_OFFSET:
        return CorruptionError(
            f"TAR sparse member {name} has a logical size of {size} bytes, "
            "past any file's size"
        )
    total = 0
    previous_end = 0
    unordered: UnsupportedFeatureError | None = None
    for offset, length in zip(sparse.offsets, sparse.lengths, strict=True):
        if offset < 0 or length < 0:
            return CorruptionError(
                f"TAR sparse map of {name} has a negative entry "
                f"(offset {offset}, {length} bytes)"
            )
        if offset + length > size:
            return CorruptionError(
                f"TAR sparse map of {name} has a chunk at offset {offset} "
                f"({length} bytes) that ends past the member's size of {size} bytes"
            )
        if length:
            if offset < previous_end and unordered is None:
                unordered = UnsupportedFeatureError(
                    f"TAR sparse map of {name} is out of order or overlapping: a chunk "
                    f"at offset {offset} starts before the previous chunk ends at "
                    f"{previous_end}"
                )
            previous_end = max(previous_end, offset + length)
        total += length
    if total != stored:
        return CorruptionError(
            f"TAR sparse map of {name} accounts for {total} bytes of data, but the "
            f"member stores {stored}"
        )
    return unordered


# --------------------------------------------------------------------------- entries


@dataclass(slots=True)
class TarEntry:
    """One member, after its extended headers are applied.

    ``data_offset`` is where its stored bytes start in the stream and ``stored_size``
    how many there are (exact, before block padding). For a sparse member these are
    the chunks alone: an old GNU map's extension blocks and a PAX 1.0 map come before
    ``data_offset``. ``size`` is the logical size: the same as ``stored_size`` except
    for a sparse member.
    """

    header: HeaderBlock
    header_offset: int  # where the first header of the member's chain starts
    data_offset: int
    stored_size: int
    size: int
    name: bytes
    name_source: NameSource
    name_binary: bool  # a PAX name under hdrcharset=BINARY: no declared encoding
    linkname: bytes | None
    linkname_source: NameSource | None
    linkname_binary: bool
    uname: bytes
    uname_pax: PaxValue | None
    gname: bytes
    gname_pax: PaxValue | None
    uid: int
    gid: int
    # The PAX records in force: the global records, then the member's own on top.
    # Shared, read-only, between members that have none of their own.
    pax: Mapping[bytes, PaxValue]
    has_own_pax: bool
    # An AREGTYPE (NUL) entry whose final name ends in "/": a directory, whose
    # declared data is skipped, as GNU tar reads it.
    old_style_directory: bool
    # Set only for a member type that carries data: sparse records on a link,
    # device, FIFO or directory leave both None.
    sparse_format: SparseFormat | None = None
    sparse: SparseMap | None = None

    @property
    def typeflag(self) -> bytes:
        return self.header.typeflag

    @property
    def data_end(self) -> int:
        """Where the next header starts."""
        return self.data_offset + _round_up(self.stored_size)


def _round_up(n: int) -> int:
    return (n + BLOCKSIZE - 1) // BLOCKSIZE * BLOCKSIZE


def carries_data(typeflag: bytes) -> bool:
    return typeflag not in NO_DATA_TYPES


# --------------------------------------------------------------------------- walker


class TarEndKind(Enum):
    """Why a walk stopped."""

    ZERO_BLOCK = "zero_block"  # the first block of an end-of-archive marker
    REJECTED = "rejected"  # a full block, or a chain of headers, that does not parse
    ABSENT = "absent"  # the stream ended exactly where a header should start
    SHORT = "short"  # the stream ended inside a header block


@dataclass(frozen=True, slots=True)
class TarEnd:
    """Why a walk stopped, and where. ``reason`` says why a header was rejected, or
    names the extended header that a zero block left with no member;
    ``observed_bytes`` is how much of a short block the stream held."""

    kind: TarEndKind
    offset: int
    reason: str = ""
    observed_bytes: int = 0


Budget = tuple[int, int] | None
"""``(bytes left, cap)`` of ``max_metadata_bytes`` for one member's headers, or
``None`` for no limit."""


class _Charger:
    __slots__ = ("cap", "left", "name")

    def __init__(self, budget: Budget) -> None:
        self.left, self.cap = budget if budget is not None else (-1, -1)
        self.name = "a TAR member"

    def __call__(self, nbytes: int, what: str) -> None:
        if self.cap < 0:
            return
        if nbytes > self.left:
            raise ResourceLimitError(
                f"Listing limit reached: max_metadata_bytes={self.cap} ({what} of "
                f"{self.name} weighs {nbytes} bytes, {max(self.left, 0)} left)"
            )
        self.left -= nbytes


class TarWalker:
    """Walks the headers of one TAR byte stream, one member per :meth:`next_entry`.

    ``seekable`` walks skip a member's data with a seek; forward-only walks read
    through it in steps, so the cost is the bytes the archive holds, not the size a
    header declares. The walker owns the stream position between calls: a caller
    that reads member data on a seekable stream may move it freely (each call seeks
    back), and on a forward-only stream reads through :meth:`open_data`, whose reads
    the walker counts.
    """

    def __init__(self, stream: BinaryIO, *, seekable: bool) -> None:
        self._stream = stream
        self._seekable = seekable
        self._pos = 0  # where the next header starts, once the current data is skipped
        self._stream_pos = 0  # forward-only: bytes consumed from the stream so far
        self._global: dict[bytes, PaxValue] = {}
        self._global_view: Mapping[bytes, PaxValue] = _ReadOnly({})
        self._global_binary = False
        # The end of the last member's data area, while it is still to be checked
        # (see _check_data_present).
        self._unchecked_data_end: int | None = None
        self.end: TarEnd | None = None
        # The first error next_entry raised: the walk is over, and later calls
        # raise it again.
        self._failed: ArchiveyError | None = None

    # The stream ---------------------------------------------------------------

    def _seek(self, offset: int) -> None:
        if offset > MAX_OFFSET:
            raise CorruptionError(
                f"TAR archive asks for offset {offset}, past any file's size"
            )
        if self._seekable:
            try:
                self._stream.seek(offset)
            except (OverflowError, ValueError) as exc:
                raise CorruptionError(
                    f"TAR archive asks for offset {offset}, which the source cannot "
                    "seek to"
                ) from exc
            except OSError as exc:
                if exc.errno in _SEEK_RANGE_ERRNOS:
                    raise TruncatedError(
                        f"TAR archive is truncated: it ends before offset {offset}"
                    ) from exc
                raise
            self._stream_pos = offset
            return
        self._discard(offset - self._stream_pos)

    def _discard(self, count: int) -> None:
        assert count >= 0, "a forward-only walk never goes back"
        while count:
            want = min(count, READ_STEP)
            got = len(self._stream.read(want))
            self._stream_pos += got
            count -= got
            if got < want:
                raise TruncatedError(
                    f"TAR archive is truncated: a member's data runs to offset "
                    f"{self._stream_pos + count}, but the archive ends at "
                    f"{self._stream_pos}"
                )

    def _read(self, size: int) -> bytes:
        data = read_within_reach(self._stream, size, remaining=None, step=READ_STEP)
        self._stream_pos += len(data)
        return data

    def _read_block(self, offset: int) -> bytes:
        self._seek(offset)
        return self._read(BLOCKSIZE)

    def _check_data_present(self, end: int) -> None:
        """On a seekable stream, raise unless the byte before ``end`` exists.

        A seek past the end succeeds, so a member whose data area the archive cuts
        short would otherwise read as a clean end. ``tarfile`` makes the same check.
        """
        if not self._seekable:
            return
        self._seek(end - 1)
        if not self._read(1):
            raise TruncatedError(
                f"TAR archive is truncated: a member's data runs to offset {end}, "
                "past the end of the archive"
            )

    @property
    def position(self) -> int:
        """Where the next header starts (the end of the last member's data area)."""
        return self._pos

    def open_data(self, entry: TarEntry) -> BinaryIO:
        """A forward-only stream over ``entry``'s data area (``stored_size`` bytes),
        good until the next :meth:`next_entry`. Only for a forward-only walk."""
        assert not self._seekable
        return _ForwardSlice(self, entry.data_offset, entry.stored_size)

    # The walk -----------------------------------------------------------------

    def next_entry(self, budget: Budget = None) -> TarEntry | TarEnd:
        """Parse the next member's headers and return it, or the end of the walk.

        ``budget`` bounds this member's extended headers and sparse map together.
        Raises :class:`CorruptionError`, :class:`TruncatedError` or
        :class:`ResourceLimitError` for damage inside a member's headers; a block that
        is not a header at all ends the walk as a :class:`TarEnd` for the caller to
        classify. After an error, every later call raises the same error.
        """
        if self._failed is not None:
            raise self._failed
        if self.end is not None:
            return self.end
        try:
            return self._next_entry(budget)
        except ArchiveyError as exc:
            self._failed = exc
            raise

    def _next_entry(self, budget: Budget) -> TarEntry | TarEnd:
        charge = _Charger(budget)
        start = self._pos
        offset = start
        if self._unchecked_data_end is not None:
            self._check_data_present(self._unchecked_data_end)
            self._unchecked_data_end = None
        own: dict[bytes, PaxValue] = {}
        own_records: list[tuple[bytes, PaxValue]] = []
        long_name: bytes | None = None
        long_link: bytes | None = None
        # Where the last x or L header of this member's chain starts, once there is
        # one: the chain must end in a member header. A global header describes no
        # member, so it starts no chain.
        extended_at: int | None = None
        while True:
            block = self._read_block(offset)
            if len(block) < BLOCKSIZE:
                if extended_at is not None:
                    raise TruncatedError(
                        "TAR archive is truncated after the extended header at "
                        f"offset {extended_at}"
                    )
                kind = TarEndKind.SHORT if block else TarEndKind.ABSENT
                self.end = TarEnd(kind, offset, observed_bytes=len(block))
                return self.end
            parsed = parse_header_block(block, offset)
            if isinstance(parsed, ZeroBlock | RejectedBlock):
                if isinstance(parsed, ZeroBlock):
                    # GNU tar 1.35 lists an archive whose last x or L header comes
                    # right before the end marker cleanly; the reason names the
                    # unused header so the reader can report it.
                    reason = (
                        ""
                        if extended_at is None
                        else f"the extended header at offset {extended_at} "
                        "describes no member"
                    )
                    self.end = TarEnd(TarEndKind.ZERO_BLOCK, offset, reason=reason)
                elif extended_at is not None:
                    return self._reject(
                        offset,
                        f"the extended header at offset {extended_at} is followed "
                        f"by a block that is no header ({parsed.reason})",
                    )
                else:
                    self.end = TarEnd(TarEndKind.REJECTED, offset, reason=parsed.reason)
                return self.end
            if parsed.typeflag in EXTENDED_TYPES:
                header_at = offset
                if parsed.typeflag != PAX_GLOBAL_TYPE:
                    extended_at = offset
                charge(parsed.size, "an extended header")
                data = self._read(parsed.size)
                if len(data) < parsed.size:
                    raise TruncatedError(
                        "TAR archive is truncated inside an extended header at "
                        f"offset {header_at}"
                    )
                offset += BLOCKSIZE + _round_up(parsed.size)
                if parsed.typeflag == LONG_NAME_TYPE:
                    long_name = _cut_nul(data)
                elif parsed.typeflag == LONG_LINK_TYPE:
                    long_link = _cut_nul(data)
                else:
                    try:
                        records = parse_pax_records(
                            data, binary_default=self._global_binary
                        )
                    except CorruptionError as exc:
                        # Records that do not parse make a header that does not parse,
                        # which the reader classifies like any rejected block.
                        return self._reject(header_at, str(exc))
                    if parsed.typeflag == PAX_GLOBAL_TYPE:
                        self._apply_global(records)
                    else:
                        own_records.extend(records)
                        own.update(records)
                continue
            try:
                entry = self._resolve(
                    parsed,
                    start,
                    offset,
                    own,
                    own_records,
                    long_name,
                    long_link,
                    charge,
                )
            except _RejectedHeader as exc:
                return self._reject(offset, exc.reason)
            self._pos = entry.data_end
            if entry.stored_size:
                self._unchecked_data_end = entry.data_end
            return entry

    def _reject(self, offset: int, reason: str) -> TarEnd:
        self.end = TarEnd(TarEndKind.REJECTED, offset, reason=reason)
        return self.end

    def _apply_global(self, records: list[tuple[bytes, PaxValue]]) -> None:
        for key, value in records:
            if key == b"hdrcharset":
                self._global_binary = value.value == b"BINARY"
            if value.value:
                self._global[key] = value
            else:
                # POSIX: an empty value in a global header removes the keyword.
                self._global.pop(key, None)
        self._global_view = _ReadOnly(self._global)

    def _resolve(
        self,
        header: HeaderBlock,
        start: int,
        offset: int,
        own: dict[bytes, PaxValue],
        own_records: list[tuple[bytes, PaxValue]],
        long_name: bytes | None,
        long_link: bytes | None,
        charge: _Charger,
    ) -> TarEntry:
        if own:
            merged: Mapping[bytes, PaxValue] = _ReadOnly({**self._global, **own})
        else:
            merged = self._global_view

        def pax(key: bytes) -> PaxValue | None:
            value = merged.get(key)
            # An empty value in a member's own header cancels the keyword for it.
            return value if value is not None and value.value else None

        name, name_source, name_binary = header.name, NameSource.HEADER, False
        if long_name is not None:
            name, name_source = long_name, NameSource.GNU_LONG
        # PAX wins over a GNU long name, whichever came first, as GNU tar applies it.
        # GNU.sparse.name is the real name of a PAX sparse member (whose path is a
        # GNUSparseFile.N placeholder), so it wins over path.
        for key in (b"path", b"GNU.sparse.name"):
            value = pax(key)
            if value is not None:
                name, name_source, name_binary = (
                    value.value,
                    NameSource.PAX,
                    value.binary,
                )
        # Quoted once here; messages escape it once more when they are built.
        charger_name = quoted(name.decode("utf-8", "surrogateescape"))
        charge.name = charger_name

        typeflag = header.typeflag
        linkname: bytes | None = None
        linkname_source: NameSource | None = None
        linkname_binary = False
        if typeflag in LINK_TYPES:
            linkname, linkname_source = header.linkname, NameSource.HEADER
            if long_link is not None:
                linkname, linkname_source = long_link, NameSource.GNU_LONG
            value = pax(b"linkpath")
            if value is not None:
                linkname, linkname_source, linkname_binary = (
                    value.value,
                    NameSource.PAX,
                    value.binary,
                )

        stored_size = header.size
        pax_size = _pax_int(merged, b"size") if pax(b"size") is not None else None
        if pax_size is not None:
            if pax_size > MAX_OFFSET:
                raise CorruptionError(
                    f"TAR PAX size {pax_size} of {charger_name} is past any file's size"
                )
            stored_size = pax_size

        uid, gid = header.uid, header.gid
        for key in (b"uid", b"gid"):
            value = pax(key)
            if value is not None and value.value.isdigit():
                # A PAX id that is not a number is ignored, keeping the header's.
                if key == b"uid":
                    uid = int(value.value)
                else:
                    gid = int(value.value)

        data_offset = offset + BLOCKSIZE
        size = stored_size
        sparse_format: SparseFormat | None = None
        sparse: SparseMap | None = None
        if typeflag == SPARSE_TYPE:  # always carries data
            sparse_format = SparseFormat.OLD_GNU
            sparse, data_offset = self._old_gnu_map(
                header, data_offset, charger_name, charge
            )
            size = header.sparse_realsize
        elif not carries_data(typeflag):
            # Sparse records on a link, device, FIFO or directory describe no data
            # area: the member is not sparse.
            pass
        # Each encoding is chosen by the presence of the records that define its map,
        # not by pax(), which drops an empty value: a sparse record whose value is
        # empty is damage, and reading the member as a plain file would serve its
        # compacted data as the content. Only the member's own records choose: a map
        # describes one member's data area, so a global sparse record makes no member
        # sparse, as GNU tar 1.35 reads it. The values still read the global defaults.
        elif b"GNU.sparse.map" in own:
            sparse_format = SparseFormat.PAX_0_1
            sparse = sparse_map_0_1(
                merged[b"GNU.sparse.map"].value, charger_name, charge
            )
            size = _pax_int(merged, b"GNU.sparse.size") or 0
        elif b"GNU.sparse.size" in own or any(
            key == b"GNU.sparse.offset" for key, _ in own_records
        ):
            sparse_format = SparseFormat.PAX_0_0
            sparse = sparse_map_0_0(own_records, charger_name, charge)
            size = _pax_int(merged, b"GNU.sparse.size") or 0
        elif b"GNU.sparse.major" in own or b"GNU.sparse.realsize" in own:
            # GNU tar 1.35 reads any major version of 1 or more as 1.0, whatever the
            # minor (measured with 1.1, 1.5, 2.0 and 9.9), and refuses a major of 0,
            # an empty one, or one that is not a number when no 0.x map came with
            # it. A realsize with no major is refused too: it declares a 1.0
            # member's logical size, and GNU tar fails on it. (A minor alone is
            # read as a plain file, as GNU tar reads it.) Read as a plain file, the
            # member would serve its map blocks as content.
            major = merged.get(b"GNU.sparse.major")
            if major is None or not (major.value.isdigit() and int(major.value) >= 1):
                version = (
                    "(none)"
                    if major is None
                    else major.value[:20].decode("ascii", "replace")
                )
                raise CorruptionError(
                    f"TAR member {charger_name} has GNU sparse major version "
                    f"{version} and no sparse map"
                )
            sparse_format = SparseFormat.PAX_1_0
            size = _pax_int(merged, b"GNU.sparse.realsize") or 0
            # The map is the first blocks of the data area. It is read here, as
            # tarfile and GNU tar read it, so a bad map fails the listing as in the
            # other encodings and its entries count against this member's budget.
            self._seek(data_offset)
            sparse, used = read_sparse_map_1_0(
                self._read, stored_size, charger_name, charge
            )
            data_offset += used
            stored_size -= used

        old_style_directory = typeflag == b"\x00" and name.endswith(b"/")
        if not carries_data(typeflag):
            # The size field of a link, device, FIFO or directory announces no data
            # area: the next header follows this one.
            stored_size = 0
        return TarEntry(
            header=header,
            header_offset=start,
            data_offset=data_offset,
            stored_size=stored_size,
            size=size,
            name=name,
            name_source=name_source,
            name_binary=name_binary,
            linkname=linkname,
            linkname_source=linkname_source,
            linkname_binary=linkname_binary,
            uname=header.uname,
            uname_pax=pax(b"uname"),
            gname=header.gname,
            gname_pax=pax(b"gname"),
            uid=uid,
            gid=gid,
            pax=merged,
            has_own_pax=bool(own),
            old_style_directory=old_style_directory,
            sparse_format=sparse_format,
            sparse=sparse,
        )

    def _old_gnu_map(
        self, header: HeaderBlock, data_offset: int, name: str, charge: _Charger
    ) -> tuple[SparseMap, int]:
        """Read an old GNU map: the header's slots, then 21-slot extension blocks
        while each says another follows. Each block is charged before it is kept."""
        pairs: list[int] = []
        charge(len(header.sparse_slots) * SPARSE_ENTRY_BYTES, "sparse map")
        for slot in header.sparse_slots:
            pairs.extend(slot)
        extended = header.sparse_extended
        while extended:
            block = self._read_block(data_offset)
            if len(block) < BLOCKSIZE:
                raise TruncatedError(
                    f"TAR archive is truncated inside the sparse map of {name}"
                )
            try:
                slots = _sparse_slots(block, 0, 21)
            except _BadNumber:
                # tarfile rejects the whole header here, and so does the walk.
                raise _RejectedHeader(
                    f"the sparse map of {name} has an extension block with a bad number"
                ) from None
            charge(len(slots) * SPARSE_ENTRY_BYTES, "sparse map")
            for slot in slots:
                pairs.extend(slot)
            extended = block[504] != 0
            data_offset += BLOCKSIZE
        return SparseMap.from_pairs(pairs), data_offset


# What a filesystem answers to a seek past the offsets it supports (ext4 for a
# base-256 size of 2**62): the archive is shorter than that offset, as GNU tar reports.
_SEEK_RANGE_ERRNOS = frozenset((errno.EINVAL, errno.EOVERFLOW))


class _RejectedHeader(Exception):
    """A member's headers do not parse: the walk ends as at a rejected block."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _ReadOnly(dict[bytes, PaxValue]):
    """The PAX records in force for a member, shared by members with none of their
    own. A plain ``dict`` on purpose: one is built per member with its own records,
    and a ``MappingProxyType`` would add a layer to every lookup. The ``Mapping``
    annotation on :attr:`TarEntry.pax` is what keeps callers from writing to it;
    the walker never writes to one once it is made."""

    __slots__ = ()


class _ForwardSlice(ReadOnlyIOStream):
    """Forward-only reads of one member's data area, counted by its walker."""

    def __init__(self, walker: TarWalker, start: int, length: int) -> None:
        super().__init__()
        self._walker = walker
        self._start = start
        self._length = length
        self._done = 0

    def read(self, size: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("read from a closed TAR member stream")
        walker = self._walker
        if walker._stream_pos != self._start + self._done:
            raise ValueError(
                "TAR member data is no longer readable: the walk has moved on"
            )
        left = self._length - self._done
        want = left if size is None or size < 0 else min(size, left)
        data = walker._read(want)
        self._done += len(data)
        if len(data) < want:
            raise TruncatedError(
                "TAR archive is truncated inside a member's data at offset "
                f"{walker._stream_pos}"
            )
        return data

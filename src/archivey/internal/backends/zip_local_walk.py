"""Forward walk over a ZIP's local headers: the parser half of a read with no seek.

A ZIP can be read start to end from a pipe, a socket or a remote object read once::

    [ stub? ]
    [ local file header · data · data descriptor? ] × n   # PK\\x03\\x04, PK\\x07\\x08
    [ bytes before the directory? ]                       # an APK signing block
    [ central directory header ] × n                      # PK\\x01\\x02
    [ ZIP64 end record · ZIP64 locator ]?                 # PK\\x06\\x06, PK\\x06\\x07
    [ end of central directory record · comment ]         # PK\\x05\\x06

:class:`LocalHeaderWalk` reads that layout from a stream it only reads, never seeks.
It counts the bytes consumed, which is the offset every local entry is matched by
against the directory. Call graph: :meth:`LocalHeaderWalk.next_entry` (one
:class:`LocalEntry` per member, in file order) → :meth:`LocalHeaderWalk.member_data`
(the member's compressed bytes, through a reader that knows where they end) →
:meth:`MemberData.settle` (the data descriptor, when bit 3 defers the sizes to one) →
after the last member, :meth:`LocalHeaderWalk.directory` (the central entries, parsed by
the same code as ``CentralDirectoryWalk``, then the end records).

Where a member's data ends (``openspec/changes/native-zip-reader/design.md``):

- Bit 3 clear: the local header's ``compressed_size`` bytes.
- Bit 3 and STORED: a scan for ``PK\\x07\\x08`` followed by a CRC and a compressed size
  that match the bytes since the data start, then by a ZIP signature (libarchive reads
  it the same way). Under encryption the CRC covers the plaintext, so only the size
  and the signature after the descriptor are matched.
- Bit 3 and any other method: the codec's own end. The walk cannot see it, so the
  caller decodes and tells :meth:`MemberData.settle` how many compressed bytes the
  codec used; the bytes its layers read past that are put back in front of the stream.

Like ``zip_parser``, this module decodes no name, runs no codec and emits no
diagnostic. Damage raises ``CorruptionError``, a stream that ends early
``TruncatedError``, a valid shape a forward read cannot follow
``UnsupportedFeatureError``. The caller stamps archive and member context.
"""

from __future__ import annotations

import struct
import zlib
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, NoReturn

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
    validate_zip_local_header,
)
from archivey.internal.backends.zip_parser import (
    _EOCD,
    _ZIP64_EOCD,
    _ZIP64_LOCATOR,
    ARCHIVE_EXTRA_DATA_SIGNATURE,
    CENTRAL_HEADER_SIGNATURE,
    CENTRAL_HEADER_SIZE,
    EOCD_SIGNATURE,
    EOCD_SIZE,
    ZIP64_EOCD_SIGNATURE,
    ZIP64_EOCD_SIZE,
    ZIP64_LOCATOR_SIGNATURE,
    ZIP64_LOCATOR_SIZE,
    CentralEntry,
    EndRecord,
    LocalHeader,
    _central_entry,
    _unpack_central,
    disk_field_is_split,
    local_header_from,
    local_header_variable_length,
)
from archivey.internal.sfx import SFX_MAX, VALIDATOR_PEEK_MAX, HitOutcome
from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.internal.streams.streamtools.binaryio import read_exact

DATA_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"
# The digital signature record, between the directory and the end records (APPNOTE
# 4.3.13): signature, a uint16 size, then that many bytes.
DIGITAL_SIGNATURE_SIGNATURE = b"PK\x05\x05"

_FLAG_DATA_DESCRIPTOR = 0x08
_METHOD_STORED = 0
_ZIP64_EXTRA_TAG = 0x0001
_U32_MAX = 0xFFFF_FFFF

# What may follow a member's data descriptor: the next member, the directory, the end
# records when the directory is empty or missing, or the record PKWARE Strong Encryption
# writes in front of an encrypted directory. Bytes before the directory that are
# neither (an APK signing block) are not a descriptor's successor.
_AFTER_MEMBER = frozenset(
    {
        LOCAL_HEADER_SIGNATURE,
        CENTRAL_HEADER_SIGNATURE,
        ZIP64_EOCD_SIGNATURE,
        EOCD_SIGNATURE,
        ARCHIVE_EXTRA_DATA_SIGNATURE,
    }
)
# Where next_entry stops: the directory or an end record follows the members.
_MEMBERS_OVER = frozenset(
    {CENTRAL_HEADER_SIGNATURE, ZIP64_EOCD_SIGNATURE, EOCD_SIGNATURE}
)
# What may open a stream without a stub in front: the same set as after a member.
_OPENS_STREAM = _AFTER_MEMBER
# The longest data descriptor, plus the signature after it: signature, CRC, two 8-byte
# sizes, next signature.
_DESCRIPTOR_LOOKAHEAD = 4 + 4 + 8 + 8 + 4
# Forward reads are made in pieces of this size.
_READ_CHUNK = 1 << 16
# How much of what a codec member handed out is kept to put back. A codec member's
# layers read ahead by at most a few of their read sizes: DecompressorStream reads up
# to 1 MiB at once and a BufferedReader in front of it a little more.
_CODEC_RETAIN = 4 << 20
# The end record and its comment, the longest tail a stream can end with after the
# directory: kept by the STORED scan to tell a descriptor without its signature (a
# whole archive follows) from a stream cut inside the member.
_TAIL_KEEP = (1 << 16) + EOCD_SIZE


class ForwardSource:
    """A stream read forward only, with bytes put back in front of it.

    :attr:`offset` is the position of the next byte :meth:`read` returns, counted from
    the start of the stream (``offset`` at construction): reading moves it forward,
    :meth:`unread` moves it back.
    """

    def __init__(self, stream: BinaryIO, *, offset: int = 0) -> None:
        self._stream = stream
        self._pending = bytearray()
        self.offset = offset

    def read(self, n: int) -> bytes:
        """Up to ``n`` bytes; fewer only at the end of the stream."""
        if n <= 0:
            return b""
        if self._pending:
            out = bytes(self._pending[:n])
            del self._pending[:n]
            if len(out) < n:
                out += read_exact(self._stream, n - len(out))
        else:
            out = read_exact(self._stream, n)
        self.offset += len(out)
        return out

    def unread(self, data: bytes | bytearray | memoryview) -> None:
        """Put ``data``, the bytes most recently read, back in front of the stream."""
        if data:
            self._pending[0:0] = data
            self.offset -= len(data)

    def peek(self, n: int) -> bytes:
        """Up to ``n`` bytes, left in front of the stream."""
        data = self.read(n)
        self.unread(data)
        return data

    def skip(self, n: int) -> int:
        """Read past ``n`` bytes; returns how many there were before the end."""
        skipped = 0
        while skipped < n:
            got = len(self.read(min(n - skipped, _READ_CHUNK)))
            if not got:
                break
            skipped += got
        return skipped


@dataclass(frozen=True, slots=True)
class LocalEntry:
    """One local header met by the forward walk."""

    #: Position in file order, from 0.
    index: int
    #: Stream offset of the header's signature: what a central entry's offset names.
    offset: int
    #: The header. ``data_start`` is a stream offset, and the sizes have the ZIP64
    #: extra field applied.
    header: LocalHeader
    #: The header carries a ZIP64 extra field, so a data descriptor's sizes are 8
    #: bytes wide (APPNOTE 4.3.9.2).
    zip64: bool

    @property
    def deferred(self) -> bool:
        """Bit 3: the CRC and sizes are in a data descriptor after the data."""
        return bool(self.header.flags & _FLAG_DATA_DESCRIPTOR)


@dataclass(frozen=True, slots=True)
class DataDescriptor:
    """The CRC and sizes written after a bit-3 member's data."""

    crc: int
    #: The full compressed size: a 4-byte shape matched it modulo 2**32.
    compressed_size: int
    #: The full uncompressed size when the caller supplied it to match against;
    #: otherwise as stored, so modulo 2**32 for a 4-byte shape.
    file_size: int
    #: The bytes it occupies, the signature included when it has one.
    length: int


def _has_zip64_extra(extra: bytes) -> bool:
    return any(field.tag == _ZIP64_EXTRA_TAG for field in iter_extra_fields(extra))


def match_descriptor(
    buffer: bytes | bytearray,
    pos: int,
    *,
    compressed_size: int,
    crc: int | None,
    file_size: int | None,
    zip64: bool,
    at_end: bool,
    signed_only: bool = False,
) -> DataDescriptor | None:
    """The data descriptor at ``buffer[pos:]``, or ``None`` when none fits there.

    Four shapes are tried, signature or none, 4- or 8-byte sizes, those the local
    header promises first (8-byte when it has a ZIP64 extra field; Java writes 8-byte
    sizes for a member past 4 GiB without one). A shape fits when its compressed size
    is ``compressed_size`` (modulo 2**32 for 4-byte sizes), its CRC is ``crc`` and its
    uncompressed size ``file_size`` when those are known, and a ZIP signature follows
    it, or the end of the input when ``at_end``. ``buffer`` must hold the
    longest shape and the signature after it (``_DESCRIPTOR_LOOKAHEAD`` bytes) unless
    it ends there. ``signed_only`` drops the shapes without a signature: a scan finds
    a descriptor by its signature.
    """
    widths = (8, 4) if zip64 else (4, 8)
    for width in widths:
        for signed in (True,) if signed_only else (True, False):
            at = pos
            if signed:
                if buffer[at : at + 4] != DATA_DESCRIPTOR_SIGNATURE:
                    continue
                at += 4
            body_len = 4 + 2 * width
            if len(buffer) < at + body_len:
                continue
            got_crc = int.from_bytes(buffer[at : at + 4], "little")
            got_csize = int.from_bytes(buffer[at + 4 : at + 4 + width], "little")
            got_usize = int.from_bytes(buffer[at + 4 + width : at + body_len], "little")
            mask = (1 << (8 * width)) - 1
            if got_csize != compressed_size & mask:
                continue
            if crc is not None and got_crc != crc:
                continue
            if file_size is not None and got_usize != file_size & mask:
                continue
            end = at + body_len
            following = bytes(buffer[end : end + 4])
            if following not in _AFTER_MEMBER and not (at_end and not following):
                continue
            # A 4-byte shape matched modulo 2**32: give back the full sizes the caller
            # supplied. An uncompressed size it did not supply stays as stored.
            return DataDescriptor(
                crc=got_crc,
                compressed_size=compressed_size if width == 4 else got_csize,
                file_size=file_size
                if width == 4 and file_size is not None
                else got_usize,
                length=end - pos,
            )
    return None


class MemberData(ReadOnlyIOStream):
    """One member's compressed bytes, read forward from the walk's stream.

    Read it as far as the caller needs, then call :meth:`settle` exactly once: it
    reads past what is left of the data and the data descriptor, and leaves the stream
    at the next header.
    """

    def __init__(self, walk: LocalHeaderWalk, entry: LocalEntry) -> None:
        super().__init__()
        self._walk = walk
        self._source = walk.source
        self.entry = entry
        #: Bytes handed out so far.
        self.consumed = 0
        self._settled = False

    def read(self, n: int = -1, /) -> bytes:
        if self._settled:
            raise ValueError("member data read after settle()")
        if n < 0:
            chunks = []
            while chunk := self._read_some(_READ_CHUNK):
                chunks.append(chunk)
            return b"".join(chunks)
        return self._read_some(n)

    def _read_some(self, size: int) -> bytes:
        raise NotImplementedError

    def settle(
        self,
        used: int | None = None,
        *,
        crc: int | None = None,
        file_size: int | None = None,
    ) -> DataDescriptor | None:
        """Finish the member: skip its unread data, read its data descriptor.

        ``used`` is how many compressed bytes the codec consumed. A member whose end
        only its codec knows (:class:`_CodecData`) needs it; on the others, whose end
        the walk knows, it is optional and checked against that end: a sized member
        refuses one past its stated size, a scanned one any other than its length. ``crc`` and
        ``file_size``, when the caller has computed them, pick the descriptor among
        the shapes that fit, so only a codec member reads them: a sized member has no
        descriptor and a scanned one matched its own. A descriptor that disagrees
        with them is the caller's to judge. Returns ``None`` for a member with no
        descriptor.
        """
        if self._settled:
            raise ValueError("settle() called twice")
        self._settled = True
        descriptor = self._finish(used, crc=crc, file_size=file_size)
        self._walk._member_settled()
        return descriptor

    def _finish(
        self, used: int | None, *, crc: int | None, file_size: int | None
    ) -> DataDescriptor | None:
        raise NotImplementedError

    def _check_used(self, used: int | None) -> None:
        """Refuse a ``used`` that is not where the walk found the data's end."""
        if used is not None and used != self.consumed:
            raise ValueError(
                f"settle(used={used}) on member #{self.entry.index}, whose data "
                f"is {self.consumed} bytes"
            )


class _SizedData(MemberData):
    """Data whose size the local header states."""

    def __init__(self, walk: LocalHeaderWalk, entry: LocalEntry) -> None:
        super().__init__(walk, entry)
        self._size = entry.header.compressed_size

    def _read_some(self, size: int) -> bytes:
        want = min(size, self._size - self.consumed)
        if want <= 0:
            return b""
        data = self._source.read(want)
        self.consumed += len(data)
        if len(data) < want:
            raise TruncatedError(
                f"ZIP stream ends inside member #{self.entry.index}'s data "
                f"({self.consumed} of {self._size} bytes)"
            )
        return data

    def _finish(
        self, used: int | None, *, crc: int | None, file_size: int | None
    ) -> DataDescriptor | None:
        left = self._size - self.consumed
        if left > 0:
            skipped = self._source.skip(left)
            self.consumed += skipped
            if skipped < left:
                raise TruncatedError(
                    f"ZIP stream ends inside member #{self.entry.index}'s data "
                    f"({self.consumed} of {self._size} bytes)"
                )
        # The codec may stop short of the stated size (padding after its end), but
        # never past it.
        if used is not None and used > self._size:
            raise ValueError(
                f"settle(used={used}) on member #{self.entry.index}, whose data "
                f"is {self._size} bytes"
            )
        return None


class _CodecData(MemberData):
    """Bit-3 data whose end only the codec finds.

    Hands out the stream as it comes, keeping the last :data:`_CODEC_RETAIN` bytes
    so the ones the caller's layers read past the codec's end can be put back. Only
    that many can be: an unsized read would run to the end of the archive, so it is
    refused, and the caller reads in pieces.
    """

    def __init__(self, walk: LocalHeaderWalk, entry: LocalEntry) -> None:
        super().__init__(walk, entry)
        self._kept: deque[bytes] = deque()
        self._kept_len = 0

    def read(self, n: int = -1, /) -> bytes:
        if n < 0:
            raise ValueError(
                "a codec member's data has no known end, and only the last "
                f"{_CODEC_RETAIN} bytes read can be put back; read it in pieces"
            )
        return super().read(n)

    def readall(self) -> bytes:
        return self.read(-1)

    def _read_some(self, size: int) -> bytes:
        data = self._source.read(size)
        if data:
            self.consumed += len(data)
            self._kept.append(data)
            self._kept_len += len(data)
            while self._kept and self._kept_len - len(self._kept[0]) >= _CODEC_RETAIN:
                self._kept_len -= len(self._kept.popleft())
        return data

    def _finish(
        self, used: int | None, *, crc: int | None, file_size: int | None
    ) -> DataDescriptor | None:
        if used is None:
            raise ValueError("settle() needs `used` for a member its codec ends")
        excess = self.consumed - used
        if excess < 0:
            raise ValueError(
                f"settle(used={used}) after only {self.consumed} bytes were read"
            )
        if excess > self._kept_len:
            raise ValueError(
                f"{excess} bytes were read past the codec's end, more than the "
                f"{self._kept_len} kept to put back"
            )
        if excess:
            tail = b"".join(self._kept)[-excess:]
            self._source.unread(tail)
        self._kept.clear()
        self._kept_len = 0
        return self._walk._read_descriptor(
            self.entry, compressed_size=used, crc=crc, file_size=file_size
        )


class _ScannedData(MemberData):
    """Bit-3 STORED data, ended by a scan for its data descriptor.

    ``check_crc`` is false for an encrypted member, whose descriptor CRC covers the
    plaintext: the scan then matches the compressed size and the signature after the
    descriptor only.
    """

    def __init__(
        self, walk: LocalHeaderWalk, entry: LocalEntry, *, check_crc: bool
    ) -> None:
        super().__init__(walk, entry)
        self._check_crc = check_crc
        self._buf = bytearray()
        self._crc = 0  # of the bytes handed out
        self._eof = False
        #: Where the data ends in ``_buf``, once the descriptor is found.
        self._end_at: int | None = None
        self._descriptor: DataDescriptor | None = None
        # Where the next candidate search starts in ``_buf``.
        self._search_from = 0
        # The bytes read from the stream most recently, for the end-of-stream verdict.
        self._tail = bytearray()

    def _pull(self) -> bool:
        data = self._source.read(_READ_CHUNK)
        if not data:
            self._eof = True
            return False
        self._buf += data
        self._tail += data
        if len(self._tail) > _TAIL_KEEP:
            del self._tail[: len(self._tail) - _TAIL_KEEP]
        return True

    def _find_end(self) -> None:
        """Look for the descriptor in what ``_buf`` holds; set ``_end_at`` on a match.

        Candidates are judged in order, each only once all the bytes a descriptor
        and the signature after it can take are in (or the stream has ended). The
        CRC runs on from one candidate to the next, so a buffer full of planted
        signatures costs one pass, and it is computed only for a candidate whose
        compressed size already fits.
        """
        buf = self._buf
        crc = self._crc  # of the bytes handed out, then buf[:crc_at]
        crc_at = 0
        pos = self._search_from
        while True:
            pos = buf.find(DATA_DESCRIPTOR_SIGNATURE, pos)
            if pos < 0:
                # A signature can still start in the last three bytes.
                self._search_from = max(len(buf) - 3, 0)
                return
            if not self._eof and len(buf) - pos < _DESCRIPTOR_LOOKAHEAD:
                self._search_from = pos
                return
            length = self.consumed + pos
            if self._size_fits(buf, pos, length):
                expect_crc = None
                if self._check_crc:
                    crc = zlib.crc32(buf[crc_at:pos], crc)
                    crc_at = pos
                    expect_crc = crc
                descriptor = match_descriptor(
                    buf,
                    pos,
                    compressed_size=length,
                    crc=expect_crc,
                    # STORED: both sizes are the data's length, unless encryption
                    # added its header to the compressed one.
                    file_size=length if self._check_crc else None,
                    zip64=self.entry.zip64,
                    at_end=self._eof,
                    signed_only=True,
                )
                if descriptor is not None:
                    self._end_at = pos
                    self._descriptor = descriptor
                    return
            pos += 1

    @staticmethod
    def _size_fits(buf: bytearray, pos: int, length: int) -> bool:
        """A signed descriptor at ``pos`` could state ``length``: the cheap test that
        comes before a CRC."""
        at = pos + 8  # past the signature and the CRC
        return any(
            int.from_bytes(buf[at : at + width], "little")
            == length & ((1 << (8 * width)) - 1)
            for width in (4, 8)
        )

    def _read_some(self, size: int) -> bytes:
        while True:
            if self._end_at is None:
                self._find_end()
            if self._end_at is not None:
                avail = self._end_at
            elif self._eof:
                self._raise_no_descriptor()
            else:
                # The bytes before the search point start no descriptor.
                avail = self._search_from
            if avail > 0 or self._end_at is not None:
                take = min(size, avail)
                out = bytes(self._buf[:take])
                del self._buf[:take]
                self._crc = zlib.crc32(out, self._crc)
                self.consumed += take
                self._search_from = max(self._search_from - take, 0)
                if self._end_at is not None:
                    self._end_at -= take
                return out
            self._pull()

    def _raise_no_descriptor(self) -> NoReturn:
        name = f"member #{self.entry.index}"
        if EOCD_SIGNATURE in self._tail:
            raise UnsupportedFeatureError(
                f"ZIP STORED {name} defers its sizes to a data descriptor that has no "
                "signature (or none matches its data), so a forward read cannot find "
                "where its data ends; read the archive from a seekable source"
            )
        raise TruncatedError(
            f"ZIP stream ends inside STORED {name}'s data, before its data descriptor"
        )

    def _finish(
        self, used: int | None, *, crc: int | None, file_size: int | None
    ) -> DataDescriptor | None:
        while self._read_some(_READ_CHUNK):
            pass
        descriptor = self._descriptor
        assert descriptor is not None
        rest = self._buf[descriptor.length :]
        self._source.unread(rest)
        self._buf.clear()
        self._check_used(used)
        return descriptor


class LocalHeaderWalk:
    """Read a ZIP forward: local entries, their data, then the directory.

    ``stream`` is only read. ``offset`` is its position in the archive file, so the
    entry offsets compare with the directory's. ``find_start`` scans for the first
    local header when the stream starts with no ZIP record :meth:`next_entry`
    handles (a self-extractor stub nobody skipped), within :data:`~archivey.internal.sfx.SFX_MAX`
    bytes, with the detector's local-header validator.
    """

    def __init__(
        self, stream: BinaryIO, *, offset: int = 0, find_start: bool = True
    ) -> None:
        self.source = ForwardSource(stream, offset=offset)
        self._find_start = find_start
        self._started = False
        self._index = 0
        # The entry :meth:`next_entry` returned last, until its data is settled.
        self._unsettled: LocalEntry | None = None
        self._open: MemberData | None = None
        #: Stream offset of the first local header, once found.
        self.first_offset: int | None = None
        #: Bytes that were neither a header nor a descriptor between the last member
        #: and the directory: ``(offset, length)``. The end record decides whether the
        #: directory accounts for them.
        self.gap: tuple[int, int] | None = None
        #: Stream offset of the first central header, once the directory is reached.
        self.cd_start: int | None = None
        #: The end record, once :meth:`directory` has read it. Its ``base`` and
        #: ``eocd_offset`` are stream positions; ``trailing`` is ``None`` (not counted).
        self.end: EndRecord | None = None
        #: Central entries :meth:`directory` read.
        self.entries_read = 0

    # -- members -------------------------------------------------------------

    def next_entry(self) -> LocalEntry | None:
        """The next local entry, or ``None`` once the directory or end records follow.

        The previous member's data must have been settled. Raises ``TruncatedError``
        when the stream ends where a header should be, ``CorruptionError`` for bytes
        that are no ZIP structure and lead to no directory.
        """
        if self._unsettled is not None:
            raise ValueError("settle the previous member's data first")
        if not self._started:
            self._started = True
            self._start()
        sig = self.source.peek(4)
        if sig == LOCAL_HEADER_SIGNATURE:
            entry = self._read_local_header()
            if self.first_offset is None:
                self.first_offset = entry.offset
            self._unsettled = entry
            return entry
        if sig in _MEMBERS_OVER:
            return None
        if sig == ARCHIVE_EXTRA_DATA_SIGNATURE:
            raise UnsupportedFeatureError(
                "PKWARE Strong Encryption is not supported (only ZipCrypto and WinZip "
                "AES are); this archive's central directory appears to be encrypted "
                "with it"
            )
        if not sig:
            raise TruncatedError(
                f"ZIP stream ends after {self._index} members, before the central "
                "directory"
            )
        if len(sig) < 4:
            raise TruncatedError(
                f"ZIP stream ends {len(sig)} bytes {self._where()}, before the "
                "central directory"
            )
        self._skip_to_directory()
        return None

    def _where(self) -> str:
        """Where the walk is, for a message: after which member, if any."""
        if self._index == 0:
            return "before the first local header"
        return f"after member #{self._index - 1}"

    def _start(self) -> None:
        # Anything next_entry knows how to handle opens the stream: a local header, the
        # directory or end records of an archive whose members are gone, or the Strong
        # Encryption record it names. Only other bytes are a stub to scan past.
        sig = self.source.peek(4)
        if sig in _OPENS_STREAM or not self._find_start:
            return
        if not self._scan_for_local_header():
            raise CorruptionError(
                f"no ZIP local header at the start of the stream or within its first "
                f"{SFX_MAX} bytes"
            )

    def _scan_for_local_header(self) -> bool:
        """Move to the first local header the detector's validator accepts.

        A candidate counts only when its signature lies wholly inside the first
        :data:`SFX_MAX` bytes, as ``sfx.scan_for_magic`` requires; one near the end
        is judged on its whole header, so up to :data:`VALIDATOR_PEEK_MAX` more are
        read. This is not ``scan_for_magic`` itself because the walk needs every
        byte it read back, to put the ones after the header found in front of the
        stream, and ``scan_for_magic`` does not hand them back.
        Every byte read is kept, so the ones after the header found can be put back.
        Rejected candidates are not capped, as for
        the seekable ZIP read (``sfx.MAX_VALIDATED_CANDIDATES`` covers RAR and 7z):
        the window bounds the work, and the validator reads only the header.
        """
        window = bytearray()
        searched = 0
        while True:
            pos = window.find(LOCAL_HEADER_SIGNATURE, searched, SFX_MAX)
            if pos >= 0:

                def peek_more(n: int, _pos: int = pos) -> bytes:
                    while (
                        len(window) < _pos + n
                        and len(window) < SFX_MAX + VALIDATOR_PEEK_MAX
                    ):
                        more = self.source.read(_READ_CHUNK)
                        if not more:
                            break
                        window.extend(more)
                    return bytes(window[_pos : _pos + n])

                if validate_zip_local_header(peek_more, None) is HitOutcome.VALID:
                    self.source.unread(window[pos:])
                    return True
                searched = pos + 1
                continue
            searched = max(len(window) - len(LOCAL_HEADER_SIGNATURE) + 1, 0)
            if len(window) >= SFX_MAX:
                return False
            more = self.source.read(min(_READ_CHUNK, SFX_MAX - len(window)))
            if not more:
                return False
            window.extend(more)

    def _read_local_header(self) -> LocalEntry:
        offset = self.source.offset
        fixed = self.source.read(LOCAL_HEADER_SIZE)
        if len(fixed) != LOCAL_HEADER_SIZE:
            raise TruncatedError(f"ZIP stream ends inside local header #{self._index}")
        variable_len = local_header_variable_length(fixed)
        variable = self.source.read(variable_len)
        if len(variable) != variable_len:
            raise TruncatedError(f"ZIP stream ends inside local header #{self._index}")
        header = local_header_from(
            fixed, variable, self.source.offset, zip64_sizes=True
        )
        entry = LocalEntry(
            index=self._index,
            offset=offset,
            header=header,
            zip64=_has_zip64_extra(header.extra),
        )
        if not entry.deferred and header.compressed_size == _U32_MAX:
            raise CorruptionError(
                f"ZIP local header #{self._index} defers its compressed size to a "
                "ZIP64 extra field that does not hold it"
            )
        self._index += 1
        return entry

    def member_data(self, entry: LocalEntry, *, encrypted: bool = False) -> MemberData:
        """The reader for ``entry``'s compressed data (for an encrypted member, its
        ciphertext, encryption header included).

        Must be called once for each entry :meth:`next_entry` returns, and settled
        before the next one.
        """
        if entry is not self._unsettled or self._open is not None:
            raise ValueError(
                "member_data() is for the entry next_entry() returned last"
            )
        data: MemberData
        if not entry.deferred:
            data = _SizedData(self, entry)
        elif entry.header.method == _METHOD_STORED:
            data = _ScannedData(self, entry, check_crc=not encrypted)
        else:
            data = _CodecData(self, entry)
        self._open = data
        return data

    def _member_settled(self) -> None:
        self._open = None
        self._unsettled = None

    def _read_descriptor(
        self,
        entry: LocalEntry,
        *,
        compressed_size: int,
        crc: int | None,
        file_size: int | None,
    ) -> DataDescriptor:
        buffer = self.source.read(_DESCRIPTOR_LOOKAHEAD)
        at_end = len(buffer) < _DESCRIPTOR_LOOKAHEAD
        descriptor = match_descriptor(
            buffer,
            0,
            compressed_size=compressed_size,
            crc=crc,
            file_size=file_size,
            zip64=entry.zip64,
            at_end=at_end,
        )
        if descriptor is None and (crc is not None or file_size is not None):
            # The structure is there but the values disagree with the decoded data:
            # the caller judges that, so find the descriptor by its size alone.
            descriptor = match_descriptor(
                buffer,
                0,
                compressed_size=compressed_size,
                crc=None,
                file_size=None,
                zip64=entry.zip64,
                at_end=at_end,
            )
        if descriptor is None:
            self.source.unread(buffer)
            if at_end:
                raise TruncatedError(
                    f"ZIP stream ends before member #{entry.index}'s data descriptor"
                )
            raise CorruptionError(
                f"no data descriptor after member #{entry.index}'s data "
                f"({compressed_size} bytes)"
            )
        self.source.unread(buffer[descriptor.length :])
        return descriptor

    def _skip_to_directory(self) -> None:
        """Read past bytes that are no header, up to the central directory.

        An APK signing block sits there. The skip stops at the first directory or
        end-record signature, so a gap holding one is not read past; and a gap after a
        last member with bit 3 never gets here, because its descriptor needs a ZIP
        signature after it. The end record later confirms where the directory starts;
        until then the bytes are only recorded in :attr:`gap`.
        """
        start = self.source.offset
        window = bytearray()
        while True:
            found = [
                pos
                for pos in (
                    window.find(CENTRAL_HEADER_SIGNATURE),
                    window.find(EOCD_SIGNATURE),
                )
                if pos >= 0
            ]
            if found:
                self.source.unread(window[min(found) :])
                self.gap = (start, self.source.offset - start)
                return
            # Keep the bytes a signature split across two reads could start in.
            keep = window[-3:]
            more = self.source.read(_READ_CHUNK)
            if not more:
                raise CorruptionError(
                    f"bytes at stream offset {start} {self._where()} are not a ZIP "
                    "header, and no central directory follows them"
                )
            window = keep + more

    # -- directory -----------------------------------------------------------

    def directory(self) -> Iterator[CentralEntry]:
        """Yield the central entries, then read the end records into :attr:`end`.

        Call once :meth:`next_entry` has returned ``None``. The entries' offsets are
        as stored (``base`` 0): the stub offset is known only from the end record.
        """
        if self._unsettled is not None:
            raise ValueError("settle the previous member's data first")
        self.cd_start = self.source.offset
        index = 0
        while self.source.peek(4) == CENTRAL_HEADER_SIGNATURE:
            fixed = self.source.read(CENTRAL_HEADER_SIZE)
            if len(fixed) != CENTRAL_HEADER_SIZE:
                raise TruncatedError(
                    f"ZIP stream ends inside central directory entry #{index}"
                )
            fields = _unpack_central(fixed, 0, index)
            name_len, extra_len, comment_len = fields[12:15]
            variable = self.source.read(name_len + extra_len + comment_len)
            if len(variable) != name_len + extra_len + comment_len:
                raise TruncatedError(
                    f"ZIP stream ends inside central directory entry #{index}"
                )
            name = variable[:name_len]
            extra = variable[name_len : name_len + extra_len]
            comment = variable[name_len + extra_len :]
            yield _central_entry(fields, name, extra, comment, index=index, base=0)
            index += 1
            self.entries_read = index
        self.entries_read = index
        self.end = self._read_end_records()

    def _read_end_records(self) -> EndRecord:
        sig = self.source.peek(4)
        if sig == DIGITAL_SIGNATURE_SIGNATURE:
            head = self.source.read(6)
            if len(head) != 6:
                raise TruncatedError("ZIP stream ends inside the digital signature")
            (size,) = struct.unpack_from("<H", head, 4)
            if self.source.skip(size) != size:
                raise TruncatedError("ZIP stream ends inside the digital signature")
            sig = self.source.peek(4)
        zip64: tuple[int, int, int, int, int] | None = None
        if sig == ZIP64_EOCD_SIGNATURE:
            zip64 = self._read_zip64_records()
            sig = self.source.peek(4)
        if sig != EOCD_SIGNATURE:
            if len(sig) < 4:
                raise TruncatedError(
                    "ZIP stream ends after the central directory, before the end of "
                    "central directory record"
                )
            raise CorruptionError(
                f"bytes at stream offset {self.source.offset} after the central "
                "directory are not an end of central directory record"
            )
        eocd_offset = self.source.offset
        record = self.source.read(EOCD_SIZE)
        if len(record) != EOCD_SIZE:
            raise TruncatedError(
                "ZIP stream ends inside the end of central directory record"
            )
        (
            _sig,
            this_disk,
            cd_start_disk,
            _entries_this_disk,
            entries_total,
            declared_cd_size,
            cd_offset,
            comment_declared,
        ) = _EOCD.unpack(record)
        if zip64 is not None:
            this_disk, cd_start_disk, entries_total, declared_cd_size, cd_offset = zip64
        if disk_field_is_split(this_disk) or disk_field_is_split(cd_start_disk):
            raise UnsupportedFeatureError(ZIP_MULTI_VOLUME_MSG)
        comment = self.source.read(comment_declared)
        assert self.cd_start is not None
        return EndRecord(
            eocd_offset=eocd_offset,
            zip64=zip64 is not None,
            entries_declared=entries_total,
            cd_size=declared_cd_size,
            cd_offset=cd_offset,
            base=self.cd_start - cd_offset,
            comment=comment,
            comment_declared=comment_declared,
            trailing=None,
        )

    def _read_zip64_records(self) -> tuple[int, int, int, int, int]:
        """``(this_disk, cd_disk, entries, cd_size, cd_offset)`` from the ZIP64 end
        record, then the locator after it read past."""
        record = self.source.read(ZIP64_EOCD_SIZE)
        if len(record) != ZIP64_EOCD_SIZE:
            raise TruncatedError("ZIP stream ends inside the ZIP64 end record")
        (
            _sig,
            record_size,
            _made_by,
            _needed,
            this_disk,
            cd_disk,
            _entries_this_disk,
            entries_total,
            cd_size,
            cd_offset,
        ) = _ZIP64_EOCD.unpack(record)
        extensible = record_size + 12 - ZIP64_EOCD_SIZE
        if extensible < 0:
            raise CorruptionError("Corrupt ZIP64 end of central directory record")
        if self.source.skip(extensible) != extensible:
            raise TruncatedError("ZIP stream ends inside the ZIP64 end record")
        locator = self.source.read(ZIP64_LOCATOR_SIZE)
        if len(locator) != ZIP64_LOCATOR_SIZE:
            raise TruncatedError("ZIP stream ends inside the ZIP64 locator")
        sig, disk_with_record, _record_offset, disks = _ZIP64_LOCATOR.unpack(locator)
        if sig != ZIP64_LOCATOR_SIGNATURE:
            raise CorruptionError("ZIP64 end record without its locator")
        if disk_with_record != 0 or disks > 1:
            raise UnsupportedFeatureError(ZIP_MULTI_VOLUME_MSG)
        return this_disk, cd_disk, entries_total, cd_size, cd_offset


__all__ = [
    "DATA_DESCRIPTOR_SIGNATURE",
    "DataDescriptor",
    "ForwardSource",
    "LocalEntry",
    "LocalHeaderWalk",
    "MemberData",
    "match_descriptor",
]

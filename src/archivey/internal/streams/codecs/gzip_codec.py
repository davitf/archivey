"""gzip: the codec, and the checks it adds around rapidgzip (the ISIZE truncation
backstop, the first header's CRC, the multi-member probe).
"""

from __future__ import annotations

import gzip
import io
import zlib
from collections.abc import Callable
from dataclasses import replace
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
)
from archivey.internal.config import StreamConfig
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    MetadataContext,
    _peeking,
    _source_tail,
)
from archivey.internal.streams.codecs.deflate_decoder import GzipDecompressorStream
from archivey.internal.streams.codecs.deflate_family_codec import _DeflateFamilyCodec
from archivey.internal.streams.codecs.stdlib_takeover import (
    _OutputChecksum,
    _seek_reached_end,
    _SourceViews,
    _StdlibOnAcceleratorError,
)
from archivey.internal.streams.decompressor_stream import gzip_error
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import DelegatingStream
from archivey.internal.timestamps import unix32_to_datetime
from archivey.types import (
    ArchiveFormat,
    ArchiveMember,
    MagicSignature,
    StreamFormat,
)


def _gzip_isize_and_length(source: CodecSource) -> tuple[int | None, int | None]:
    """Capture ``(source_byte_length, ISIZE_trailer)`` for the truncation backstop in one pass.

    Preserves the **tri-state** the path-reopen backstop relied on, which a bare ``int | None``
    would collapse:

    - ``(length, isize)`` — a readable seekable source ≥ 18 bytes: compare ``isize``.
    - ``(length, None)`` with ``length < 18`` — too short for a complete gzip member: the
      backstop must **raise** on a non-empty soft EOF (an incomplete member).
    - ``(None, None)`` — non-seekable / unreadable: the backstop cannot verify, so it must
      **return** without raising (never invent a truncation it cannot prove).

    ISIZE is uncompressed size mod 2**32 and covers only the *last* member of a multi-member
    file; callers needing a hard bound still prefer a container-declared size. Restores the
    source position for a caller-owned stream.
    """
    length, trailer = _source_tail(source, size=4, min_length=18)
    if trailer is None:
        return length, None
    return length, int.from_bytes(trailer, "little")


def _gzip_header_refused(source: CodecSource) -> bool:
    """Whether zlib refuses the first gzip member's header, which rapidgzip accepts.

    rapidgzip 0.16 ignores a header CRC (FHCRC) that does not match and the reserved
    FLG bits that RFC 1952 says must make a decoder fail; zlib's gzip window, which
    the standard-library path uses, raises "header crc mismatch" and "unknown header
    flags set". The header is fed to zlib until the first byte of output, the end of
    the member, or an error. Any error counts, so a first member that zlib cannot
    start is left to the engine whose verdict the accelerator must keep. Restores the
    source position for a caller-owned stream. Only the first member's header is
    checked: the accelerator does not say where later members start.
    """

    def refused(view: BinaryIO) -> bool:
        decoder = zlib.decompressobj(31)
        while chunk := view.read(1 << 16):
            try:
                if decoder.decompress(chunk, 1) or decoder.eof:
                    return False
            except zlib.error:
                return True
        return False

    with _peeking(source) as f:
        f.seek(0)
        return refused(f)


def _gzip_isize_from_source(source: CodecSource) -> int | None:
    """The gzip ISIZE trailer when cheaply readable, else ``None`` (see the tri-state helper)."""
    return _gzip_isize_and_length(source)[1]


def _config_with_gzip_isize(source: CodecSource, config: StreamConfig) -> StreamConfig:
    """Mark gzip ISIZE as available for AUTO / the ISIZE truncation backstop.

    Does **not** set ``expected_decompressed_size`` from ISIZE: that field is an exact
    bound for ``VerifyingStream``, but ISIZE is mod 2**32 and multi-member gzip's
    trailer covers only the last member.
    """
    if config.gzip_isize_backstop or config.expected_decompressed_size is not None:
        return config
    if _gzip_isize_from_source(source) is None:
        return config
    return replace(config, gzip_isize_backstop=True)


def _stdlib_gzip(source: CodecSource, config: StreamConfig) -> BinaryIO:
    return GzipDecompressorStream(
        source,
        collector=config.collector,
        report_trailing_data=config.report_trailing_data,
    )


# How far before the earliest place a gzip trailer could start the backstop still looks
# for the output's CRC-32 (``_GzipTruncationCheckStream._member_ends_the_file``). The
# numbers stated for that lookup follow from it: with ``end`` the end of the non-zero data,
# the range is ``[end - 8 - 64, end + 12)``, so "72 bytes before" the end; a copy more than
# 64 - 8 = 56 bytes past the end of the real trailer is not excluded; and the first read is
# the last 64 + 32 = 96 bytes, which hold the range when padding is 24 bytes or fewer. The
# docstring, ``dev-docs/formats/gzip.md`` §2.3 and §5, and the ``compressed-streams`` and
# ``seekable-decompressor-streams`` specs state these numbers; change them together.
_TRAILER_LOOKBACK = 64


class _GzipTruncationCheckStream(DelegatingStream):
    """Backstop truncation detection for the rapidgzip accelerator (any seekable source).

    Upstream rapidgzip treats many incomplete streams as soft EOF (by design): ``read()``
    may return empty or a short/full prefix with no exception. This wrapper:

    1. On EOF with **zero** bytes delivered — fully switch ``_inner`` to the stdlib
       gzip-window :func:`GzipDecompressorStream` *before* returning empty success, so
       truncation is loud and any recoverable prefix is streamed (valid empty gzip still
       succeeds with zero bytes). Switching the inner keeps ``tell``/``seek``/`seekable`
       honest (ADR 0014: content faults raise from reads, never ``close()``).
    2. On EOF after **non-empty** delivery — check that rapidgzip decoded to the end of
       the source (its compressed position), then look for the member's trailer: the CRC-32
       of the output, kept as it goes by, and its length (mod 2**32), as the eight bytes
       that end the file but for zero padding (single-member). A forged copy of those
       eight bytes is not one (:meth:`_member_ends_the_file` says how far that holds).
       When a seek short of the end skipped output and a read then meets the end, there
       is no CRC-32 of that output, and the length is compared to the ISIZE read at
       open, the file's last four bytes, instead. On a mismatch, a file with a further
       member that zlib confirms (:func:`gzip_has_additional_member`) is taken as
       multi-member and nothing is raised: the trailer is only the last member's size
       (a per-member ISIZE sum is deferred). A decode that stopped short of the end, or
       any other mismatch, hands the read to the standard library.

       The compressed position is what tells a cut member followed by a complete one
       from a multi-member file: the trailer is then the last member's, and the further
       member is real, but rapidgzip stopped at the cut and never decoded it (found by
       the accelerator fuzz targets). It also keeps a forged ISIZE that matches the
       bytes delivered before a soft end from passing.

    ISIZE and the source length are **captured up front** (``isize`` / ``source_len``) so no
    per-read reopen is needed and the tri-state is preserved: ``source_len < 18`` ⇒ a
    non-empty soft EOF is an incomplete member, handed to the standard library like a
    mismatch; a value ⇒ compare; ``source_len is None`` (unreadable) ⇒ return without
    raising. ``views`` gives the multi-member scan and the stdlib fallback their own access
    to the source (:class:`_SourceViews`), so neither disturbs the live accelerator's
    cursor.

    Both checks run once, on the first read or seek that meets the end of rapidgzip's
    output, and are spent after it: no later read or seek runs them again. A seek short
    of the end leaves them armed. The length they compare is the position at the end,
    not a count of the bytes delivered: a read that meets the end is at that length
    whatever seeks came before (rapidgzip clamps a seek past the end to the end, and
    ``_StdlibSeekContract`` above keeps the caller's position).

    A seek that stops at the end of rapidgzip's output (a seek to the end, or one that
    rapidgzip clamped) runs the checks there before it returns a position, so that
    ``seek(0, SEEK_END)`` on a cut file does not return rapidgzip's short length with no
    error. It first reads the output from the CRC-32 frontier to the end
    (:meth:`_read_through`), so the trailer check finds the trailer by its CRC-32, not
    by the four-byte ISIZE comparison that a forged trailer can pass. A size query on a
    stream not yet read so pays for receiving and checksumming the whole output, the
    price of a size that has been checked. When the standard library takes over, at the
    check or during that read, it seeks to the caller's target: it raises as it does
    with the accelerator off, or holds the caller's position, so a read after a seek
    past the end of an empty rapidgzip output does not return the bytes at offset 0.
    After a takeover the standard library owns the end, and this check does not run.

    An ISIZE mismatch hands the read to the standard library
    (:meth:`_StdlibOnAcceleratorError.switch_to_stdlib`), which gives the verdict and keeps
    it, as does the stdlib engine the empty-EOF arm switches to. The switch always installs
    that engine: positioning it at the delivered offset defers any content fault to the
    next read (``DecompressorStream.seek``), so a later read cannot see a clean EOF.
    """

    # Side-effecting read() (byte-total + EOF truncation check); disable
    # passthrough so readinto does not skip it.
    readinto_passthrough = False

    def __init__(
        self,
        inner: _StdlibOnAcceleratorError,
        *,
        views: _SourceViews,
        isize: int | None,
        source_len: int | None,
        open_stdlib: Callable[[CodecSource], BinaryIO],
    ) -> None:
        super().__init__(inner)
        # The same object as ``_inner`` until the empty-EOF arm replaces that, typed:
        # the ISIZE check hands over through it.
        self._takeover = inner
        self._views = views
        self._isize = isize
        self._source_len = source_len
        self._open_stdlib = open_stdlib
        # The position in the output: where the read that meets the end of rapidgzip's
        # output is, that output's length. It equals ``self._inner.tell()`` (read adds
        # what the inner returned, seek stores what the inner's seek returned) and is
        # kept here because that tell() can be a round trip to the rapidgzip child
        # (``_StdlibSeekContract``). The ISIZE comparison depends on the two agreeing.
        self._pos = 0
        # CRC-32 of the output from offset 0 up to its frontier; ``_trailer_hands_over``
        # needs it whole to find where the member's trailer is.
        self._crc = _OutputChecksum(zlib.crc32, 0)
        self._checked = False
        self._verify = True

    def _count(self, data: bytes) -> None:
        """Advance the position past ``data`` that rapidgzip returned, and fold it into
        the CRC-32 of the output while that prefix is still unbroken."""
        if not self._checked:
            self._crc.feed(self._pos, data)
        self._pos += len(data)

    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""  # an explicit read(0) is not EOF; it must not trip the check
        data = self._inner.read(size)
        if data:
            self._count(data)
            if size < 0 and self._verify and not self._checked:
                # Completing read (read/-1): observe soft EOF now and run ISIZE
                # before returning. Callers that do only ``s.read(); s.close()`` must
                # still get TruncatedError from the read — never from close (ADR 0014).
                while True:
                    nxt = self._inner.read(1)
                    if not nxt:
                        break
                    more = nxt + self._inner.read(-1)
                    self._count(more)
                    data += more
                if self._settle_end():
                    more = self._inner.read(-1)
                    self._pos += len(more)
                    data += more
            return data
        if self._settle_end():
            data = self._inner.read(size)
            self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        # A seek short of the end leaves the checks armed. A seek that stops at the end
        # of rapidgzip's output runs them there (class docstring).
        if whence == io.SEEK_CUR:
            # _StdlibSeekContract above resolves a relative seek. A caller that drives
            # this layer directly does not, and _seek_reached_end needs an absolute
            # target, so resolve it here too, as _ZlibAdlerCheckStream.seek does.
            offset, whence = self._pos + offset, io.SEEK_SET
        self._pos = super().seek(offset, whence)
        if not _seek_reached_end(offset, whence, self._pos):
            return self._pos
        read_through = (
            self._verify and not self._checked and not self._takeover.switched
        )
        if read_through:
            try:
                self._read_through()
            except ArchiveyError:
                if not self._takeover.switched:
                    raise
                # The standard library took over on the way and met the fault. Its
                # seek below gives the verdict a seek gets with the accelerator off: a
                # seek past the end holds the target and the next read raises.
        if self._settle_end() or (read_through and self._takeover.switched):
            # The standard library decodes now: it seeks to the caller's place, or
            # raises as it does with the accelerator off. The second operand covers a
            # handover during _read_through, not at the check: _settle_end() then
            # returns False, as the trailer check defers to a stream already switched.
            self._pos = super().seek(offset, whence)
        return self._pos

    def _read_through(self) -> None:
        """Read rapidgzip's output from the CRC-32 frontier to its end, so that the
        trailer check finds the trailer by its CRC-32 and does not compare the length
        alone with the file's last four bytes (:meth:`_trailer_hands_over`).

        A cut or damaged stream can hand the read to the standard library on the way;
        it then raises as it does with the accelerator off, or reads on to its own end.
        """
        if self._crc.frontier >= self._pos:
            return
        self._pos = self._inner.seek(self._crc.frontier)
        while data := self._inner.read(1 << 20):
            self._count(data)

    def nearest_resume_offset(self, target: int) -> int | None:
        # Sits on the decompressed chain (accelerator, then maybe stdlib fallback).
        return ask_resume_offset(self._inner, target)

    def _settle_end(self) -> bool:
        """Run the end checks, once, at the end of rapidgzip's output (at ``_pos``);
        return whether the standard library decodes from now on, so that the caller's
        read or seek goes to it.

        An end at offset 0 is the empty-EOF arm (:meth:`_begin_stdlib_fallback`), any
        other end the trailer check (:meth:`_trailer_hands_over`)."""
        if not self._verify or self._checked:
            return False
        self._checked = True
        if self._pos == 0:
            self._begin_stdlib_fallback()
            return True
        return self._trailer_hands_over()

    def _begin_stdlib_fallback(self) -> None:
        """Replace rapidgzip with the stdlib gzip engine after a silent empty EOF.

        Retargets the same :class:`GzipDecompressorStream` used when rapidgzip is OFF
        (#183), so large bounded ``read(n)`` recovers a prefix without a byte-at-a-time
        ``GzipFile`` workaround. ``read()`` / ``readall`` still raise without returning
        bytes (same contract as accelerator-off). The stdlib engine owns truncation
        after the switch — disarm the ISIZE backstop so faults stay on its read path.
        """
        self._replace_inner(self._open_stdlib(self._views.for_stdlib()))
        self._verify = False

    def _trailer_hands_over(self) -> bool:
        """Check the trailer at the end of the data; return whether the
        standard-library decoder took over to decide (no trailer found, or after a
        seek short of the end the ISIZE does not match; below), at the position
        delivered.

        The inner half of :meth:`_settle_end`, which guards it (armed, not yet run)
        and runs it once: call that, not this.
        """
        if self._takeover.switched:
            # The standard-library decoder finished the read; it owns truncation, and
            # bytes appended to the file are for it to report.
            return False
        if self._source_len is None:
            # Source length unreadable (non-seekable / I/O error at capture): cannot verify,
            # so never invent a truncation we can't prove.
            return False
        # Below 18 bytes no gzip member is complete, so the delivered bytes are a
        # truncation. So is a decode that stopped short of the end of the source: the
        # trailer there is not this output's. Otherwise no trailer found (or, after a
        # seek, an ISIZE mismatch) is one, unless this is a concatenated multi-member
        # gzip (then the trailer is only the last member's). A confirmed further member
        # => do not raise (a cut whose decode still reaches the end, with zlib
        # confirming a later member, can pass; the per-member ISIZE sum is deferred).
        if self._source_len >= 18 and self._decoded_to_the_end():
            if self._isize is None:
                return False  # length known but ISIZE unread (should not happen here)
            if self._crc.frontier == self._pos:
                # Every byte went through the CRC-32, so the trailer can be found
                # rather than assumed to be the file's last four bytes.
                if self._member_ends_the_file():
                    return False
            elif self._pos % (1 << 32) == self._isize:
                # A seek short of the end skipped output, then a read met the end, so
                # there is no CRC-32 to find the trailer with; the last four bytes of
                # the file stand in. A seek that meets the end reads through instead.
                return False
            if self._has_additional_gzip_member():
                return False
        # No trailer of this output ends the file: it was cut, its ISIZE is wrong, or
        # something was appended, and rapidgzip reads past all of these without a word.
        # The standard-library decoder tells them apart, and keeps its verdict on later
        # reads: it carries on from here, and raises the truncation or reports the bytes.
        self._takeover.switch_to_stdlib()
        return True

    def _member_ends_the_file(self) -> bool:
        """Whether the file ends with this output's own trailer: its CRC-32 and the
        length mod 2**32, then nothing but zero padding.

        The trailer starts where the deflate stream ends, which rapidgzip does not
        report, and zero padding looks like the length's own high zero bytes. What is
        known is that the real trailer starts with the output's CRC-32, because rapidgzip
        compares it there. A trailer that ends the non-zero data starts at most 8 bytes
        before its end. So the CRC-32 is looked for from ``_TRAILER_LOOKBACK`` bytes
        before that to 12 bytes past the end of the non-zero data, and the file ends with
        the trailer only when the CRC-32 occurs there once, followed by the length and
        then by nothing but zeros.

        A forged copy of the eight bytes is turned down wherever it sits, because the
        real CRC-32 is then a second occurrence in that range. Bytes appended after the
        real trailer, a copy that overlaps it, and a copy that reaches back into the
        compressed data (a stored block ends with the payload's own bytes) all leave the
        real CRC-32 within range. If the real trailer starts in the zeros after the copy,
        its CRC-32 is zero, and the four zeros right after the copy are a second
        occurrence. A forger sets the CRC-32 at will with four chosen payload bytes, so
        nothing here leans on it being unlikely to repeat. Not excluded: a copy more than
        56 bytes after the end of the real trailer. That needs rapidgzip to read past
        those bytes, which are not a further member, without an error, and rapidgzip
        0.16 raises on ten or more, which hands the read to the standard library.

        A real trailer turned down goes to the standard library, which finds nothing
        wrong. That takes the CRC-32 occurring a second time by chance, about one file
        in 2**25, or a CRC-32 of zero with zero padding after the trailer.

        The file is read from the end: the last 96 bytes, which hold the whole range in
        any file with 24 bytes of padding or fewer, and more only to get back over
        padding.
        """
        length = self._source_len
        assert length is not None
        crc = self._crc.value.to_bytes(4, "little")
        size = (self._pos % (1 << 32)).to_bytes(4, "little")
        try:
            with self._views.view() as f:
                base = max(0, length - (_TRAILER_LOOKBACK + 32))
                f.seek(base)
                tail = f.read(length - base)
                stripped = tail.rstrip(b"\0")
                end = base + len(stripped)
                if not stripped:
                    end, step = base, 1 << 16
                    while end > 0:
                        start = max(0, end - step)
                        f.seek(start)
                        stripped = f.read(end - start).rstrip(b"\0")
                        if stripped:
                            end = start + len(stripped)
                            break
                        end = start
                # A trailer that ends the data starts 8 bytes before ``end`` at the
                # earliest and at ``end`` at the latest.
                first = max(0, end - 8 - _TRAILER_LOOKBACK)
                last = min(length, end + 12)
                if base <= first and last <= base + len(tail):
                    window = tail[first - base : last - base]
                else:
                    f.seek(first)
                    window = f.read(last - first)
        except OSError:
            return True  # cannot look -> do not invent a mismatch
        at = window.find(crc)
        if at < 0 or window.find(crc, at + 1) >= 0:
            return False  # absent, or a second occurrence: one of them is forged
        return window[at + 4 : at + 8] == size and first + at + 8 >= end

    def _decoded_to_the_end(self) -> bool:
        """Whether rapidgzip's decode reached the end of the source; ``True`` when it
        cannot say, which leaves the checks after it as they were."""
        position = getattr(self._takeover.accelerator, "compressed_position", None)
        if position is None or self._source_len is None:
            return True
        end = position()
        return end is None or end >= self._source_len

    def _has_additional_gzip_member(self) -> bool:
        # Closed via the context manager: a real fd close for a path source, a no-op
        # mark for a shared view (see _SourceViews).
        try:
            with self._views.view() as f:
                return gzip_has_additional_member(f)
        except OSError:
            return True  # cannot rule out a second member -> do not raise


# The bounds of ``gzip_has_additional_member``'s probe. They are not ``ArchiveyConfig``
# limits because they are structural, not policy: running out of any of them only hands
# the read to the standard library, which gives the verdict at the cost of a second
# decode. It never turns a valid file into an error the caller would have to accept.
#
# How much of a candidate member the probe decodes before it takes the candidate for a
# real member: compressed bytes read, and output produced. Random bytes after a chance
# ``1f 8b 08`` fail long before either (see that function).
_MEMBER_PROBE_INPUT = 1 << 16
_MEMBER_PROBE_OUTPUT = 1 << 20
# The compressed bytes all candidates together may decode: this much, plus one byte per
# sixteen scanned. A file that spends it is not taken for multi-member.
_MEMBER_PROBE_BUDGET = 1 << 20
# The compressed bytes one probe read takes. The budget is tested between reads, so this
# is what every candidate that passes the header check costs at least (about 256
# candidates per MiB of budget) and how far one probe can overspend the budget.
_MEMBER_PROBE_PIECE = 1 << 12


def gzip_has_additional_member(stream: BinaryIO) -> bool:
    """Whether ``stream`` contains a gzip member after the one starting at offset 0.

    A member starts with ``1f 8b 08``, and those three bytes turn up by chance in a
    compressed body about once per 16 MiB, so a match is only a candidate. A candidate
    with reserved ``FLG`` bits set is skipped; any other counts when zlib's gzip decoder
    takes it for a member (:func:`_gzip_member_at`).

    The decodes share a budget (``_MEMBER_PROBE_BUDGET``), so a file crafted with a
    candidate every few bytes, each decoding a long way before it fails, costs a bounded
    share of the scan. A scan that spends it answers ``False``: the caller then hands the
    read to the standard library, which gives the verdict at the cost of a second decode.

    Scans in fixed-size blocks (never reads the whole file into memory), carrying a small
    overlap so a header split across a block boundary is still found. Starts one byte in so
    this member's own header at offset 0 is not matched.

    **Side effect:** seeks the stream. Callers that need the prior position MUST restore
    it themselves (e.g. ``tell``/``seek`` around the call). Path-source callers typically
    open a fresh handle owned only for this scan.
    """
    magic = b"\x1f\x8b\x08"
    block = 1 << 20
    offset = 1  # of buf[0] in the stream
    budget = _MEMBER_PROBE_BUDGET
    stream.seek(offset)
    buf = b""
    while chunk := stream.read(block):
        budget += len(chunk) // 16
        buf += chunk
        found = buf.find(magic)
        while found >= 0:
            # FLG, when this block holds it: reserved bits make zlib refuse the header.
            if found + 3 >= len(buf) or not buf[found + 3] & 0xE0:
                member, fed = _gzip_member_at(stream, offset + found, budget)
                budget -= fed
                if member is not None:
                    return member
                if budget <= 0:
                    return False
            found = buf.find(magic, found + 1)
        keep = min(len(magic) - 1, len(buf))
        offset += len(buf) - keep
        buf = buf[len(buf) - keep :]
        stream.seek(offset + keep)
    return False


def _gzip_member_at(
    stream: BinaryIO, offset: int, budget: int
) -> tuple[bool | None, int]:
    """Whether a gzip member starts at ``offset`` of ``stream``, as far as a short decode
    can tell, and the compressed bytes read: ``True`` for a member, ``None`` for a
    candidate that is not one, ``False`` when ``budget`` ran out first. Seeks the stream.

    zlib's gzip decoder checks the header (method, reserved flags, header CRC) and
    decodes from it. The candidate counts when the decode reaches the member's end, where
    zlib checks the CRC-32 and ISIZE, or decodes ``_MEMBER_PROBE_INPUT`` compressed bytes
    or ``_MEMBER_PROBE_OUTPUT`` of output without an error. Random bytes after a chance
    match do neither: most fail the header, and DEFLATE decoded from random bits meets an
    invalid code or a distance past the output within a few hundred symbols. A candidate
    the source ends inside does not count: a real member cut there is a truncation, which
    the standard-library engine then reports.
    """
    stream.seek(offset)
    decoder = zlib.decompressobj(31)
    fed = produced = 0
    try:
        while fed < _MEMBER_PROBE_INPUT:
            if fed >= budget:
                return False, fed
            pending = stream.read(min(_MEMBER_PROBE_PIECE, _MEMBER_PROBE_INPUT - fed))
            if not pending:
                return None, fed
            fed += len(pending)
            while pending:
                # Ask for no more than the output bound leaves, so the probe never
                # produces more than _MEMBER_PROBE_OUTPUT in total.
                produced += len(
                    decoder.decompress(pending, _MEMBER_PROBE_OUTPUT - produced)
                )
                if decoder.eof or produced >= _MEMBER_PROBE_OUTPUT:
                    return True, fed
                pending = decoder.unconsumed_tail
    except zlib.error:
        return None, fed
    return True, fed


# How many gzip header bytes to peek for cheap metadata (FNAME/mtime). Longer stored names
# beyond this are simply not surfaced.
_GZIP_HEADER_PEEK = 512


class GzipCodec(_DeflateFamilyCodec):
    codec = Codec.GZIP
    stream_format = StreamFormat.GZIP
    magic = (MagicSignature(0, b"\x1f\x8b", ArchiveFormat.GZ),)
    # gzip is only a standalone stream format, never a 7z or ZIP coder, so no container
    # declares a pack size to clip it to and no AES pad follows its data.
    _bounds_source = False

    def prepare_config(self, source: CodecSource, config: StreamConfig) -> StreamConfig:
        # gzip ISIZE makes truncation checkable → allows rapidgzip AUTO with the ISIZE
        # backstop. Do not promote ISIZE into expected_decompressed_size (mod 2**32 /
        # multi-member).
        return _config_with_gzip_isize(source, config)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        # Prefer a container-declared size; otherwise note a readable ISIZE so AUTO can
        # still select rapidgzip with the dedicated ISIZE backstop (not VerifyingStream).
        return super().open(source, params, _config_with_gzip_isize(source, config))

    def _open_stdlib(self, source: CodecSource, config: StreamConfig) -> BinaryIO:
        # Stdlib path: gzip-window DecompressorStream (not gzip.GzipFile). CRC/ISIZE
        # outcomes come from zlib's gzip window; multi-member chaining matches GzipFile
        # (NUL padding, a further member). O(n) rewind with a warning.
        return _stdlib_gzip(source, config)

    def _open_accelerated(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO | None:
        if _gzip_header_refused(source):
            # The standard-library engine raises zlib's own error on the first
            # read, as it does with the accelerator off.
            return None
        return super()._open_accelerated(source, params, config)

    def _end_check(
        self,
        source: CodecSource,
        config: StreamConfig,
        accel_source: CodecSource,
        views: _SourceViews,
    ) -> Callable[[_StdlibOnAcceleratorError], BinaryIO] | None:
        # A declared size does not replace this check: the VerifyingStream outside
        # checks only the length, and rapidgzip ends a cut member with no error, so a
        # size equal to the output before the cut would pass (the raw DEFLATE case the
        # accelerator fuzz targets found). No container declares a gzip size today
        # (gzip is never a ZIP or 7z coder), so this only keeps the check from
        # depending on that staying true.
        # Truncation backstop for **any** seekable source (path or caller-owned stream):
        # empty→stdlib fallback + single-member ISIZE, with the multi-member scan on an
        # independent view: the check stands down only for a further member zlib's gzip
        # decoder confirms, and a scan that spends its budget hands the read to the
        # standard library (the per-member ISIZE sum is deferred). Capture the ISIZE
        # tri-state up front so no per-read reopen is needed and `size < 18` truncation
        # is preserved.
        source_len, isize = _gzip_isize_and_length(source)
        return lambda stream: _GzipTruncationCheckStream(
            stream,
            views=views,
            isize=isize,
            source_len=source_len,
            open_stdlib=lambda fallback: self._open_stdlib(fallback, config),
        )

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, gzip.BadGzipFile):
            return CorruptionError(f"Error reading gzip stream: {exc!r}")
        if isinstance(exc, zlib.error):
            # Corruption inside the deflate body (a valid gzip header, then bad data) is
            # raised by zlib's gzip window as a raw zlib.error. zlib does not flag
            # truncation distinctly here (a short stream surfaces as TruncatedError via
            # the decompressor engine), so any zlib.error at this point is corruption,
            # and a failed CRC-32/ISIZE check a whole-stream one; a member header gzip
            # refuses as unsupported stays unsupported (:func:`gzip_error`).
            return gzip_error(exc)
        if isinstance(exc, EOFError):
            return TruncatedError(f"gzip stream is truncated: {exc!r}")
        return None

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        """Surface gzip's stored filename (FNAME) and mtime.

        RFC 1952 specifies the FNAME field as ISO-8859-1 (Latin-1), so the decoded value in
        ``extra`` uses that encoding; ``raw_name`` keeps the verbatim stored bytes.

        **The trailer CRC-32 is deliberately never put in** ``member.hashes``. Maintainer
        decision (PR 441); the reasoning, so it is not re-litigated:

        - A digest is worth having for two things: skipping a decompression because an
          equal file was already processed (it must be known *before* the read), or
          verifying a read (it must come from somewhere the decoder does not already
          check).
        - The trailer CRC covers only the **last** member. It equals the digest of the
          whole content only when the file holds exactly one member, and nothing in the
          header says so.
        - Proving single-memberness at open means scanning the whole compressed file.
          That was the old behaviour: O(file size) on every open (a full download for a
          remote source), and on large files the 3-byte member magic ``1f 8b 08``
          matches by chance (about once per 16 MiB), so the scan usually concluded
          "multi-member" and dropped the CRC anyway.
        - Adding the CRC after a full read was implemented and removed: by then the
          decoder has already checked every member's CRC and raised on a mismatch, so
          the digest serves neither purpose. It also could not be made uniform, since
          the rapidgzip accelerator hides member boundaries.

        A caller that wants a digest of a gzip's content computes one while reading.
        """
        header = ctx.peek_header(_GZIP_HEADER_PEEK)
        if len(header) < 10 or header[:2] != b"\x1f\x8b":
            return
        flg = header[3]
        mtime = int.from_bytes(header[4:8], "little")
        if mtime != 0:
            member.modified = unix32_to_datetime(mtime)

        pos = 10
        if flg & 0x04:  # FEXTRA: 2-byte length + data
            if pos + 2 <= len(header):
                xlen = int.from_bytes(header[pos : pos + 2], "little")
                pos += 2 + xlen
            else:
                pos = len(header) + 1  # stop optional-field walk
        if flg & 0x08 and pos <= len(header):
            # FNAME: null-terminated stored filename (Latin-1 per RFC 1952)
            end = header.find(b"\x00", pos)
            if end != -1:
                name_bytes = header[pos:end]
                member.raw_name = name_bytes
                member.extra["gzip.original_filename"] = name_bytes.decode("latin-1")

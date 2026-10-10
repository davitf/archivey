"""zlib and raw DEFLATE: the codecs, and the checks they add around rapidgzip (the
Adler-32 check, the format gate on the first bytes).
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Callable
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    TruncatedError,
)
from archivey.internal.config import StreamConfig
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    ProbeChargeDecode,
    ProbeReadAt,
    _restoring_position,
    _source_tail,
    _stream_prefix,
)
from archivey.internal.streams.codecs.deflate_decoder import ZlibDecompressorStream
from archivey.internal.streams.codecs.deflate_family_codec import _DeflateFamilyCodec
from archivey.internal.streams.codecs.deflate_resume import stream_end
from archivey.internal.streams.codecs.stdlib_takeover import (
    _DRAIN_CHUNK,
    _drain_into,
    _OutputChecksum,
    _SourceViews,
    _StdlibOnAcceleratorError,
)
from archivey.internal.streams.decompressor_stream import (
    _StreamChecksumError,
    input_after_end_error,
    zlib_error,
)
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import DelegatingStream
from archivey.types import StreamFormat


def _rapidgzip_may_read_as_another_format(source: CodecSource) -> bool:
    """Whether rapidgzip may take raw DEFLATE ``source`` for gzip, zlib or bzip2.

    rapidgzip 0.16 is told no format: it looks at the first bytes and tries gzip, zlib,
    bzip2 and then raw DEFLATE (``determineFileTypeAndOffset``), so a raw DEFLATE source
    that starts like one of the others is decoded as that format, where the standard
    library raises at the first block. The test is wider than rapidgzip's own: every
    header it takes for zlib passes :func:`_zlib_header_plausible`, every gzip one
    starts ``1f 8b``, and every bzip2 one is ``BZh`` and a digit from 1 to 9.
    """
    prefix = _stream_prefix(source, 4)
    return (
        prefix[:2] == b"\x1f\x8b"
        or _zlib_header_plausible(prefix[:2])
        or (len(prefix) == 4 and prefix[:3] == b"BZh" and prefix[3] in b"123456789")
    )


def _rapidgzip_reads_as_zlib(source: CodecSource) -> bool:
    """Whether rapidgzip takes zlib ``source`` for a zlib stream.

    Its zlib header check is :func:`_zlib_header_plausible` without a preset
    dictionary (``FDICT``): a header with one is not zlib to rapidgzip, which then
    decodes the source as raw DEFLATE from its first byte. The standard library reads
    the header and raises for the missing dictionary.
    """
    prefix = _stream_prefix(source, 2)
    return _zlib_header_plausible(prefix) and not prefix[1] & 0x20


def _stdlib_zlib(
    source: CodecSource, config: StreamConfig, *, wbits: int = zlib.MAX_WBITS
) -> BinaryIO:
    return ZlibDecompressorStream(
        source,
        wbits=wbits,
        collector=config.collector,
        report_trailing_data=config.report_trailing_data,
        refuse_input_after_end=config.refuse_input_after_end,
    )


def _zlib_adler_trailer(source: CodecSource) -> int | None:
    """The last four bytes of a zlib ``source`` as its Adler-32 trailer, or ``None``.

    ``None`` when the source is shorter than a complete zlib stream (a two-byte header and
    the four-byte trailer) or cannot be read; :class:`_ZlibAdlerCheckStream` then goes
    straight to its standard-library confirmation. Restores a stream source's position.
    """
    trailer = _source_tail(source, size=4, min_length=6)[1]
    if trailer is None:
        return None
    return int.from_bytes(trailer, "big")


class _ZlibStoppedShort(Exception):
    """rapidgzip's output of a zlib stream ended where the standard library's goes on:
    it has more output, or meets the end of the source inside the stream (a cut
    stream). Control flow inside :class:`_ZlibAdlerCheckStream`; never reaches a
    caller."""


class _ZlibFirstStreamEnds(Exception):
    """The standard library's decode of the source ends its first zlib stream at
    ``end``, before the output rapidgzip delivered, and no byte past ``end`` has reached
    the caller. Control flow inside :class:`_ZlibAdlerCheckStream`; never reaches a
    caller."""

    def __init__(self, end: int) -> None:
        super().__init__(end)
        self.end = end


class _ZlibAdlerCheckStream(DelegatingStream):
    """Check a zlib stream's Adler-32 after rapidgzip, which does not check it.

    rapidgzip decodes a zlib stream with a damaged body or a wrong Adler-32 without an
    error: it can return the whole stream, or a shorter one, as good data. The standard
    library raises on the same input, and an accelerator must not change whether a
    damaged source raises. This wrapper keeps an Adler-32 of the output from offset 0 up
    to a frontier, and when the frontier reaches the end compares it to the trailer (the
    last four bytes of the compressed source, read before the child starts).

    A seek does not forfeit the check. A seek back stays behind the frontier; a seek
    forward past it reads the bytes in between, so the Adler-32 still covers them. A
    reader that skips member data by seeking (the TAR reader over ``tar.zz``) therefore
    still gets the check when it reaches the end. rapidgzip decodes the skipped bytes to
    seek past them anyway; what the read-through adds is their transfer from the child
    and one ``zlib.adler32`` pass. For a reader that only skips, such as a listing, that
    transfer is the whole cost: the full decompressed stream, once, where a plain seek
    moved no output. It is paid at most once per stream, and only under an explicit
    ``ON``. Nothing is checked on a stream that is never read to its end, and neither
    does the standard library check one: it verifies the trailer only when it consumes
    the end of the stream, so the parity holds at both ends.

    A mismatch has two causes: damage, or several zlib streams one after another
    (rapidgzip decodes all of them, so the trailer is only the last stream's). The
    wrapper tells them apart by decoding the source again with the standard library,
    which reads only the first stream and reports what follows it as trailing data.
    When that first stream ends before the output rapidgzip delivered, and no byte past
    its end has reached the caller yet, the read is handed to the standard library at
    the end of the first stream (:class:`_ZlibFirstStreamEnds`): a completing
    ``read()`` returns only the bytes before it, and a seek to the end lands there, as
    with the accelerator off. The cases that reach the end that way are a completing
    ``read()``, which the readers' ``read`` of a whole member uses, and a seek past the
    frontier. A reader that took the rest in bounded reads already has bytes from past
    the first stream when the end shows up, which cannot be taken back; there the decode
    goes on, one zlib stream after another, until it has as many bytes as rapidgzip
    delivered, and that output is accepted. Every decode must succeed and reproduce the
    delivered length and Adler-32; otherwise the read or seek that reached the end raises
    :class:`_StreamChecksumError`. The second decode runs only on a mismatch.

    A third cause is a cut stream: rapidgzip reads a zlib stream cut inside its body
    as a shorter whole one, with no error, and can stop before output the standard
    library still decodes. When that decode agrees with every byte delivered and then
    goes on, or runs out of source, the read is handed to the standard library
    (``switch_to_stdlib`` on the ``_StdlibOnAcceleratorError`` inside) at the position
    delivered so far. It delivers what it delivers with the accelerator off, and
    raises its own :class:`TruncatedError`, with one exception: when the container
    declared a size, the ``VerifyingStream`` that ``_wrap_accelerated_length`` puts
    outside this class owns the read that reaches that size. Its probe past the size
    meets the cut, and a failed verifying event withholds that read's chunk (see
    ``MemberVerifier.read`` in ``verify.py``), so up to one chunk fewer arrives than
    with the accelerator off. The error type is the same.

    As in :class:`_GzipTruncationCheckStream`, the check runs on the call that reaches
    the end (ADR 0014: never from ``close()``), and a verdict once raised is raised again
    at every later end of data.
    """

    readinto_passthrough = False

    def __init__(
        self,
        inner: _StdlibOnAcceleratorError,
        *,
        views: _SourceViews,
        trailer: int | None,
    ) -> None:
        super().__init__(inner)
        # The same object as ``_inner``, typed: the cut-stream handover calls it.
        self._takeover = inner
        self._views = views
        self._trailer = trailer
        self._pos = 0
        # The end of the furthest bytes read() has returned. A seek's read-through
        # moves the frontier, not this.
        self._returned = 0
        # Output bytes [0, frontier) are covered by the Adler-32 (1 for the empty string).
        self._sum = _OutputChecksum(zlib.adler32, 1)
        self._checked = False
        self._verdict: _StreamChecksumError | None = None

    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""
        start = self._pos
        data = self._inner.read(size)
        if data:
            self._count(data)
            if size < 0:
                # A completing read: reach the end now, so the check raises from this
                # read rather than leave the caller to find it on a later one.
                buf = bytearray(data)
                _drain_into(self._inner, buf, self._count)
                tail = self._at_end(size, start)
                # Where the standard library took over: _at_end put _pos there, then
                # moved it on by the tail it read. With no handover the tail is empty
                # and this is the end of data. Bytes of this read past that point (a
                # second zlib stream) are dropped.
                handover = self._pos - len(tail)
                del buf[handover - start :]
                buf += tail
                data = bytes(buf)
        else:
            data = self._at_end(size, start)
        self._returned = max(self._returned, start + len(data))
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if whence == io.SEEK_CUR:
            # Absolute before any read-through moves the position it is relative to.
            offset, whence = self._pos + offset, io.SEEK_SET
        if not self._checked:
            if whence == io.SEEK_END:
                self._read_through(None)
            elif offset > self._sum.frontier:
                self._read_through(offset)
        self._pos = self._inner.seek(offset, whence)
        return self._pos

    def tell(self, /) -> int:
        return self._pos

    def nearest_resume_offset(self, target: int) -> int | None:
        return ask_resume_offset(self._inner, target)

    def _count(self, data: bytes) -> None:
        end = self._pos + len(data)
        if not self._checked:
            self._sum.feed(self._pos, data)
        self._pos = end

    def _read_through(self, target: int | None) -> None:
        """Advance the frontier to ``target`` (``None``: the end) by reading."""
        self._pos = self._inner.seek(self._sum.frontier)
        while target is None or self._pos < target:
            want = (
                _DRAIN_CHUNK
                if target is None
                else min(_DRAIN_CHUNK, target - self._pos)
            )
            data = self._inner.read(want)
            if not data:
                # The rest of a cut stream, if any, is the read's after this seek.
                self._at_end(0)
                return
            self._count(data)

    def _at_end(self, size: int, start: int = 0) -> bytes:
        """Check the Adler-32 at the end of rapidgzip's output, and return what follows
        it: nothing, or after a handover, the standard library's read of ``size``
        (``0``: none yet) from the position reached.

        ``start`` is where the read that reached the end started (a seek passes 0, and
        repositions afterwards). A handover at the end of the first zlib stream puts
        ``_pos`` there, or at ``start`` when that is later, and a completing read drops
        its bytes past that point."""
        if self._verdict is not None:
            raise self._verdict.with_traceback(None)
        if self._checked or self._pos < self._sum.frontier:
            return b""
        self._checked = True
        if self._trailer == self._sum.value:
            return b""
        try:
            self._confirm_with_stdlib()
        except _ZlibStoppedShort:
            return self._hand_to_stdlib(size)
        except _ZlibFirstStreamEnds as first:
            self._pos = max(first.end, start)
            return self._hand_to_stdlib(size, at=self._pos)
        except _StreamChecksumError as exc:
            self._verdict = exc
            raise
        return b""

    def _hand_to_stdlib(self, size: int, at: int | None = None) -> bytes:
        self._takeover.switch_to_stdlib(at)
        if size == 0:
            return b""
        data = self._inner.read(size)
        self._pos += len(data)
        return data

    def _confirm_with_stdlib(self) -> None:
        """Decode the source again with the standard library, up to the output
        rapidgzip delivered, and compare.

        Raises :class:`_StreamChecksumError` when the two disagree on those bytes, and
        :class:`_ZlibStoppedShort` when they agree and the standard library has more
        to say past them: more output, or the end of the source inside the stream.
        Raises :class:`_ZlibFirstStreamEnds` when the first zlib stream ends before
        those bytes do and the caller has none of the bytes past its end.
        """
        produced = 0
        adler = 1
        decoder = zlib.decompressobj()
        pending = b""
        stopped_short = False
        first = True
        try:
            with self._views.view() as f:
                while produced <= self._sum.frontier:
                    if decoder.eof:
                        if produced == self._sum.frontier:
                            break
                        if first and self._returned <= produced:
                            # zlib checked this stream's Adler-32. Without the
                            # accelerator the read ends here, and the standard
                            # library reports the rest as trailing data.
                            raise _ZlibFirstStreamEnds(produced)
                        first = False
                        # The next of several zlib streams one after another.
                        pending, decoder = decoder.unused_data, zlib.decompressobj()
                        continue
                    if not pending:
                        pending = f.read(1 << 16)
                        if not pending:
                            stopped_short = True
                            break
                    # Bounded output per call: a damaged body can expand without limit.
                    out = decoder.decompress(pending, 1 << 20)
                    pending = decoder.unconsumed_tail
                    # Only the bytes rapidgzip delivered are compared.
                    adler = zlib.adler32(out[: self._sum.frontier - produced], adler)
                    produced += len(out)
        except zlib.error as exc:
            raise _StreamChecksumError(f"Error reading zlib stream: {exc!r}") from exc
        if adler == self._sum.value and (
            stopped_short or produced > self._sum.frontier
        ):
            raise _ZlibStoppedShort
        if produced != self._sum.frontier or adler != self._sum.value:
            raise _StreamChecksumError(
                "zlib stream is damaged: the data does not match its Adler-32 "
                "(the rapidgzip accelerator does not check it)"
            )


class _DeflateEndCheckStream(DelegatingStream):
    """Check that a raw DEFLATE stream rapidgzip ends without an error reached its end.

    rapidgzip ends a raw DEFLATE stream cut after a whole block, or inside its last
    one, with no error: it returns the output so far, then ``b""`` (found by the
    accelerator fuzz targets). The standard library raises ``TruncatedError`` there,
    since the stream never reached a final block. A declared size does not catch it:
    when the size equals the output before the cut, the ``VerifyingStream`` outside
    sees a complete member.

    So when rapidgzip's output ends, this wrapper decodes the end of the stream again
    with zlib, from the newest resume point at or before that offset
    (:func:`~archivey.internal.streams.codecs.deflate_resume.stream_end`), or from the start
    when there is none. Raw DEFLATE has no checksum, so the resumed decode is a full
    answer:

    - zlib reaches a final block: the stream is whole. That block can end before the
      offset, when rapidgzip read on into a second stream after it, within the size the
      container declared (past that size, ``limit`` of ``_StdlibOnAcceleratorError``
      hands over). The ``compressed-streams`` spec accepts that difference: the
      declared size and CRC decide.
    - zlib does not reach a final block (a cut or damaged stream): the read goes to the
      standard library (``switch_to_stdlib`` on the ``_StdlibOnAcceleratorError``
      inside), which gives the verdict it gives with the accelerator off.
    - With ``refuse_input_after_end`` (a ZIP member), zlib follows on through the
      streams rapidgzip read, to the one that ends at the offset, and any byte of the
      source after that one, a zero too, raises ``CorruptionError``, as the standard
      library does. A second stream rapidgzip read whole still reads here (the
      ``compressed-streams`` exception above), where the standard library refuses it:
      telling it apart would need a decode from the start, since the resume point can
      lie after the first stream's end.

    The check costs a decode of the output between the resume point and the end. The
    child keeps its first point only once 4 MiB of output has been delivered
    (``_MIN_QUERY_SPACING`` in ``rapidgzip_child.py``), so a stream with less output
    than that is decoded again whole, by zlib in one thread: under ``ON`` such a member
    pays a whole standard-library decode on top of rapidgzip's (measured on 2 MiB:
    57 ms against 45 ms without the check, and 11 ms for zlib alone, since starting
    the child already costs more than that). ``AUTO`` engages only from
    16 MiB of compressed input (``RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE``), where the
    decode is the stretch after the last point: measured on an 82 MiB stream, about
    2 MiB and 10 ms. A point at the end itself cannot be had instead: its window is
    the 32 KiB of output before the point, and the stream keeps only the 32 KiB
    before the end.

    The view is not guarded against ``OSError`` as the gzip member scan is: raw DEFLATE
    is container-only, so the view is a sibling of the handle the decode itself reads,
    and an error from it is the caller's source failing, which reaches the caller.

    The check runs once, on the read that meets the end, as in
    :class:`_GzipTruncationCheckStream` (ADR 0014: never from ``close()``); a
    completing ``read()`` reaches the end itself so that it raises. A seek does not
    disarm it. After a takeover the standard library owns the end. With a declared
    size, the ``VerifyingStream`` probe past that size is the read that meets the end,
    and a failed verifying event withholds its chunk (as for zlib, see
    :class:`_ZlibAdlerCheckStream`): the error type is the same as with the
    accelerator off, and up to one chunk fewer arrives.
    """

    readinto_passthrough = False

    def __init__(
        self,
        inner: _StdlibOnAcceleratorError,
        *,
        views: _SourceViews,
        refuse_input_after_end: bool = False,
    ) -> None:
        super().__init__(inner)
        # The same object as ``_inner``, typed: the handover calls it.
        self._takeover = inner
        self._views = views
        self._refuse_input_after_end = refuse_input_after_end
        self._checked = False

    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""
        data = self._inner.read(size)
        if data and size >= 0:
            return data
        if not data:
            return self._at_end(size)
        # A completing read: reach the end now, so the check raises from this read.
        buf = bytearray(data)
        _drain_into(self._inner, buf)
        buf += self._at_end(size)
        return bytes(buf)

    def nearest_resume_offset(self, target: int) -> int | None:
        return ask_resume_offset(self._inner, target)

    def _at_end(self, size: int) -> bytes:
        """Check the end of rapidgzip's output; return what a read of ``size`` there
        gets: nothing, or after a handover, the standard library's read."""
        if self._checked or self._takeover.switched:
            return b""
        self._checked = True
        end = self._takeover.position
        resume_point = getattr(self._takeover.accelerator, "resume_point", None)
        point = resume_point(end) if resume_point is not None else None
        with self._views.view() as f:
            found, input_after = stream_end(
                f, point, end, check_input_after=self._refuse_input_after_end
            )
        if found is not None:
            if input_after:
                raise input_after_end_error("deflate")
            return b""
        self._takeover.switch_to_stdlib()
        return self._inner.read(size)


class _ZlibErrorCodec(_DeflateFamilyCodec):
    """Shared zlib/deflate error taxonomy for raw deflate and zlib-wrapped deflate."""

    # The stream's name in messages.
    _label: str

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, zlib.error):
            # A zlib stream's Adler-32 failing is a whole-stream checksum.
            return zlib_error(exc, self._label)
        if isinstance(exc, EOFError):
            return TruncatedError(f"{self._label} stream is truncated: {exc!r}")
        return None


class DeflateCodec(_ZlibErrorCodec):
    codec = Codec.DEFLATE
    _label = "deflate"
    _empty_to_stdlib = True

    def _open_stdlib(self, source: CodecSource, config: StreamConfig) -> BinaryIO:
        # Stdlib raw deflate; a backward seek re-decodes from the start (see rewind_warning).
        # Under rapidgzip it is also the takeover's decoder, so bytes after the stream
        # that rapidgzip fails on are refused there too (refuse_input_after_end).
        return _stdlib_zlib(source, config, wbits=-15)

    def _open_accelerated(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO | None:
        if _rapidgzip_may_read_as_another_format(source):
            # rapidgzip would decode a gzip, zlib or bzip2 stream here; the standard
            # library reads it as raw DEFLATE, as it does with the accelerator off. No
            # encoder starts raw DEFLATE like zlib: that first byte is a stored block
            # with nonzero padding bits. ``BZh`` and a digit is a possible start, so a
            # real stream that has it decodes without the accelerator.
            return None
        return super()._open_accelerated(source, params, config)

    def _end_check(
        self,
        source: CodecSource,
        config: StreamConfig,
        accel_source: CodecSource,
        views: _SourceViews,
    ) -> Callable[[_StdlibOnAcceleratorError], BinaryIO] | None:
        refuse = config.refuse_input_after_end
        return lambda stream: _DeflateEndCheckStream(
            stream, views=views, refuse_input_after_end=refuse
        )

    def _accelerated_limit(
        self, params: CodecParams, config: StreamConfig
    ) -> int | None:
        # zlib ends the member at the stream's final block; rapidgzip reads on into
        # whatever follows. The standard library decides both a data error and output
        # past the declared size (_StdlibOnAcceleratorError). ZIP declares the size
        # as the member's; a 7z coder declares it as its unpack size.
        if config.expected_decompressed_size is not None:
            return config.expected_decompressed_size
        return params.unpack_size


def _zlib_header_plausible(prefix: bytes) -> bool:
    """The RFC 1950 CMF/FLG grammar — the zlib probe's cheap gate before a decode.

    zlib's 2-byte header is not a true magic (the same prefix begins many raw-deflate
    streams and can occur in arbitrary data), so it only decides whether the decode that
    actually confirms a zlib stream is worth attempting. The grammar is *stated* rather
    than enumerated: an allow-list of common headers reads as data, and the four-entry
    one this replaced silently excluded six of the seven legal window sizes — a reader
    could not tell a deliberate exclusion from an oversight.

    ``FDICT`` is accepted: a preset dictionary is valid zlib, and whether archivey holds
    that dictionary is the decode's answer, not the header's.

    66 of the 65 536 ``(CMF, FLG)`` pairs satisfy this, against 4 before — a wider
    fail-fast gate, taken deliberately in the false-negative direction (a real stream
    archivey cannot open is a hard failure; a probe false positive is a graded one).
    """
    if len(prefix) < 2:
        return False
    cmf, flg = prefix[0], prefix[1]
    if cmf & 0x0F != 8:  # CM: deflate is the only compression method zlib defines
        return False
    if cmf >> 4 > 7:  # CINFO: window sizes 512 B – 32 KiB
        return False
    return (cmf * 256 + flg) % 31 == 0  # FCHECK


class ZlibCodec(_ZlibErrorCodec):
    codec = Codec.ZLIB
    _label = "zlib"
    stream_format = StreamFormat.ZLIB
    # No exact magic: zlib's 2-byte header is too unspecific, so it is recognized by a content
    # probe that gates on that header before decoding.

    def _open_stdlib(self, source: CodecSource, config: StreamConfig) -> BinaryIO:
        # Stdlib zlib; a backward seek re-decodes from the start (see rewind_warning).
        return _stdlib_zlib(source, config)

    def _open_accelerated(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO | None:
        if not _rapidgzip_reads_as_zlib(source):
            # Not a zlib header rapidgzip accepts: it would decode a gzip member, a
            # bzip2 stream or raw DEFLATE, and the standard library raises at the
            # first read, as it does with the accelerator off.
            return None
        return super()._open_accelerated(source, params, config)

    def _end_check(
        self,
        source: CodecSource,
        config: StreamConfig,
        accel_source: CodecSource,
        views: _SourceViews,
    ) -> Callable[[_StdlibOnAcceleratorError], BinaryIO] | None:
        # Read through the view, which can move the caller's stream under it; put
        # that back, since an AUTO open whose child cannot start decodes from it.
        with _restoring_position(source):
            trailer = _zlib_adler_trailer(accel_source)
        return lambda stream: _ZlibAdlerCheckStream(
            stream, views=views, trailer=trailer
        )

    def content_probe(
        self,
        prefix: bytes,
        *,
        source_length: int | None = None,
        read_at: ProbeReadAt | None = None,
        charge_decode: ProbeChargeDecode | None = None,
    ) -> bool:
        """Recognize a zlib stream: an RFC 1950 CMF/FLG header (fail-fast) that then decodes."""
        return _zlib_header_plausible(prefix) and self._decodes_sample(
            prefix, source_length=source_length
        )

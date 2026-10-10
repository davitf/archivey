"""Decoders for gzip, zlib and raw DEFLATE on the standard library's ``zlib``."""

from __future__ import annotations

import os
import zlib
from typing import BinaryIO

from archivey.exceptions import TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs.deflate_resume import (
    DeflateResume,
    DeflateResumeDecoder,
)
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    Decoder,
    DecompressorStream,
    SeekPoint,
    gzip_error,
)

_GZIP_MAGIC = b"\x1f\x8b"
_GZIP_WBITS = 16 + zlib.MAX_WBITS


class ZlibDecoder(BaseDecoder):
    """Inflate a raw-deflate or zlib-wrapped stream via ``zlib.decompressobj``."""

    def __init__(self, wbits: int = -15) -> None:
        self._wbits = wbits
        self._decomp = zlib.decompressobj(wbits)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder:
        del inner
        if isinstance(point.state, DeflateResume):
            return DeflateResumeDecoder(
                point.state, self, corruption=None, truncated="File is truncated"
            )
        return ZlibDecoder(self._wbits)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if self._decomp.eof:
            # Past the end; the stream stops reading once these hold a non-zero byte.
            self._past_end(chunk)
            return DecodeOut(b"")
        # unconsumed_tail holds input not yet consumed under a prior max_length cap;
        # prepend it exactly once (mirrors gzip._GzipReader).
        data = self._decomp.unconsumed_tail + chunk
        if not data:
            return DecodeOut(b"")
        if max_length < 0:
            out = self._decomp.decompress(data)
        else:
            out = self._decomp.decompress(data, max_length)
        if self._decomp.eof:
            self._past_end(self._decomp.unused_data)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        if self._decomp.unconsumed_tail:
            out = self._decomp.decompress(self._decomp.unconsumed_tail)
            leftover = out + self._decomp.flush()
        else:
            leftover = self._decomp.flush()
        if not self._decomp.eof:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(leftover)

    @property
    def finished(self) -> bool:
        return self._decomp.eof

    @property
    def needs_input(self) -> bool:
        return not self._decomp.unconsumed_tail


class GzipDecoder(BaseDecoder):
    """gzip-window inflate with GzipFile-parity multi-member chaining.

    Uses ``wbits=16+MAX_WBITS`` so zlib validates CRC/ISIZE. After each member,
    strips leading NUL padding from ``unused_data`` / retained input, then:
    empty → clean EOF; ``1f 8b`` → new ``decompressobj`` and continue; anything
    else (trailing junk, or a partial magic at true EOF) ends the data there and sets
    :attr:`trailing_bytes`, which the stream reports. ``gzip.GzipFile`` raises on the
    same bytes; archivey reads the members and reports what follows them.
    Cross-``feed`` NUL runs and a lone trailing ``1f`` are retained until the next
    header (or ``flush``) resolves them.

    Mid-member ``max_length`` remainder stays in ``decompressobj.unconsumed_tail``
    (same as :class:`ZlibDecoder`); ``_retained`` is only for post-member bytes.
    """

    def __init__(self) -> None:
        self._decomp = zlib.decompressobj(_GZIP_WBITS)
        # Post-member bytes not yet resolved (NUL padding / next magic / junk).
        # Never store unconsumed_tail here — that lives on the decompressobj.
        self._retained = b""
        self._between_members = False
        self._finished = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder:
        del inner
        if isinstance(point.state, DeflateResume):
            return DeflateResumeDecoder(
                point.state,
                self,
                corruption=gzip_error,
                truncated="gzip stream is truncated",
            )
        return GzipDecoder()

    def _arm_trailing_junk(self, data: bytes) -> None:
        """End the data at ``data``: bytes after a member that start no further member.

        ``data`` has its NUL padding stripped already and runs to the end of what has
        been fed; the stream reports it from :attr:`trailing_bytes`.
        """
        self._past_end(data)
        self._retained = b""
        self._between_members = False
        self._finished = True

    def _resolve_between(self, data: bytes) -> bytes:
        """Strip NULs; start next member, retain partial magic, arm junk, or wait."""
        i = 0
        while i < len(data) and data[i] == 0:
            i += 1
        data = data[i:]
        if not data:
            self._between_members = True
            self._retained = b""
            return b""
        if data.startswith(_GZIP_MAGIC):
            self._decomp = zlib.decompressobj(_GZIP_WBITS)
            self._between_members = False
            self._retained = b""
            return data
        if data == b"\x1f":
            self._between_members = True
            self._retained = data
            return b""
        self._arm_trailing_junk(data)
        return b""

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if self._finished or self._pending_error is not None:
            return DecodeOut(b"")

        if self._between_members:
            data = self._retained + chunk
            self._retained = b""
        else:
            data = self._decomp.unconsumed_tail + chunk

        # Output pieces, joined once at the end: one ``decompress`` call is the common
        # case, and ``b"".join`` hands a lone piece back without copying it.
        output: list[bytes] = []
        produced_total = 0
        while True:
            if self._pending_error is not None or self._finished:
                break
            if max_length >= 0 and produced_total >= max_length:
                if self._between_members and data:
                    self._retained = data
                break

            if self._between_members:
                data = self._resolve_between(data)
                if self._pending_error is not None or self._finished:
                    break
                if self._retained or not data:
                    # Partial magic retained, or only NULs/empty — need more input.
                    break
                continue

            if not data:
                break

            limit = max_length - produced_total if max_length >= 0 else -1
            if limit == 0:
                break
            try:
                if limit < 0:
                    produced = self._decomp.decompress(data)
                else:
                    produced = self._decomp.decompress(data, limit)
            except zlib.error as e:
                # Corrupt deflate body (bad CRC/data check inside a member). Raise
                # CorruptionError here so a raw GzipDecompressorStream is consistent
                # with flush() and does not leak zlib.error (GzipCodec.translate maps
                # it too, but the decoder must stand on its own).
                # A failed CRC-32/ISIZE check is a _StreamChecksumError.
                raise gzip_error(e) from e
            if produced:
                output.append(produced)
                produced_total += len(produced)

            if self._decomp.eof:
                data = self._decomp.unused_data
                self._between_members = True
                continue

            # More compressed input remains under a max_length cap — leave it in
            # unconsumed_tail for the next feed (do not copy into _retained).
            data = self._decomp.unconsumed_tail
            if data and produced and (max_length < 0 or produced_total < max_length):
                continue
            break

        return DecodeOut(b"".join(output))

    def flush(self) -> DecodeOut:
        if self._finished:
            return DecodeOut(b"")
        out = bytearray()
        # Drain mid-member unconsumed_tail / continue member chaining with no new input.
        drained = self.feed(b"")
        out.extend(drained.data)
        if self._pending_error is not None or self._finished:
            return DecodeOut(bytes(out))

        if self._between_members:
            data = self._retained
            self._retained = b""
            i = 0
            while i < len(data) and data[i] == 0:
                i += 1
            data = data[i:]
            if not data:
                self._finished = True
                return DecodeOut(bytes(out))
            if data.startswith(_GZIP_MAGIC):
                self._decomp = zlib.decompressobj(_GZIP_WBITS)
                self._between_members = False
                try:
                    produced = self._decomp.decompress(data)
                    out.extend(produced)
                    if self._decomp.unconsumed_tail:
                        out.extend(
                            self._decomp.decompress(self._decomp.unconsumed_tail)
                        )
                    if not self._decomp.eof:
                        out.extend(self._decomp.flush())
                except zlib.error as e:
                    raise gzip_error(e) from e
                if not self._decomp.eof:
                    self._pending_error = TruncatedError("gzip stream is truncated")
                    return DecodeOut(bytes(out))
                trailing = self._decomp.unused_data
                j = 0
                while j < len(trailing) and trailing[j] == 0:
                    j += 1
                trailing = trailing[j:]
                if trailing:
                    self._arm_trailing_junk(trailing)
                    return DecodeOut(bytes(out))
                self._finished = True
                return DecodeOut(bytes(out))
            self._arm_trailing_junk(data)
            return DecodeOut(bytes(out))

        # Mid-member compressed EOF.
        try:
            if self._decomp.unconsumed_tail:
                out.extend(self._decomp.decompress(self._decomp.unconsumed_tail))
            out.extend(self._decomp.flush())
        except zlib.error as e:
            raise gzip_error(e) from e
        if not self._decomp.eof:
            self._pending_error = TruncatedError("gzip stream is truncated")
        else:
            # Completed final member exactly at EOF.
            trailing = self._decomp.unused_data
            j = 0
            while j < len(trailing) and trailing[j] == 0:
                j += 1
            trailing = trailing[j:]
            if trailing == b"\x1f" or (
                trailing and not trailing.startswith(_GZIP_MAGIC)
            ):
                self._arm_trailing_junk(trailing)
            elif trailing.startswith(_GZIP_MAGIC):
                self._pending_error = TruncatedError("gzip stream is truncated")
            else:
                self._finished = True
        return DecodeOut(bytes(out))

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def needs_input(self) -> bool:
        if self._pending_error is not None or self._finished:
            return True
        if self._decomp.unconsumed_tail:
            return False
        # Full next-member prefix retained — drain without reading more.
        if self._retained.startswith(_GZIP_MAGIC):
            return False
        return True


def ZlibDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    wbits: int = -15,
    *,
    collector: DiagnosticCollector | None = None,
    report_trailing_data: bool = False,
    exact_input: bool = False,
) -> DecompressorStream:
    """Inflate a raw-deflate or zlib-wrapped stream (forward-only)."""
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: ZlibDecoder(wbits),
        collector=collector,
        codec_name="zlib" if wbits > 0 else "deflate",
        report_trailing_data=report_trailing_data,
        exact_input=exact_input,
    )


def GzipDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    report_trailing_data: bool = False,
) -> DecompressorStream:
    """Inflate a gzip stream with multi-member chaining (forward-only; O(n) rewind)."""
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: GzipDecoder(),
        collector=collector,
        codec_name="gzip",
        report_trailing_data=report_trailing_data,
    )

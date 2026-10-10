"""The liblzma family: xz, lzip, ``.lzma`` (LZMA Alone), and raw LZMA1/LZMA2 for
containers.
"""

from __future__ import annotations

import functools
import lzma
import os
from collections.abc import Callable
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.config import (
    DecoderLimits,
    StreamConfig,
    check_decoder_memory,
    exceeds_decoder_memory,
)
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import RewindWarning
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    MetadataContext,
    ProbeReadAt,
    StreamCodec,
)
from archivey.internal.streams.codecs.framed_decoder import FramedDecompressorStream
from archivey.internal.streams.codecs.lzip_decoder import LzipDecompressorStream
from archivey.internal.streams.codecs.xz_decoder import (
    XzDecompressorStream,
    lzma_error_to_archivey,
)
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    ReadOnlyIOStream,
    is_seekable,
    read_exact,
)
from archivey.internal.streams.streamtools.slice import SlicingStream
from archivey.types import (
    ArchiveFormat,
    ArchiveMember,
    HashAlgorithm,
    MagicSignature,
    StreamFormat,
    crc32_digest,
)

_ALONE_HEADER_SIZE = 13
# Alone header marks unknown uncompressed size with all-ones uint64.
_ALONE_UNKNOWN_SIZE = (1 << 64) - 1


class _LzmaErrorCodec(StreamCodec):
    """Shared LZMA/XZ error taxonomy for the lzma-family codecs (xz, lzip, raw LZMA)."""

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, lzma.LZMAError):
            return lzma_error_to_archivey(exc, "Error reading LZMA/XZ stream")
        if isinstance(exc, EOFError):
            return TruncatedError(f"LZMA/XZ stream is truncated: {exc!r}")
        return None


class _SizedLzmaCodec(_LzmaErrorCodec):
    """xz / lzip: surface the decompressed size recorded in the stream index/trailer."""

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        member.size = ctx.probe_decompressed_size()


class XzCodec(_SizedLzmaCodec):
    codec = Codec.XZ
    stream_format = StreamFormat.XZ
    magic = (MagicSignature(0, b"\xfd7zXZ\x00", ArchiveFormat.XZ),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        return XzDecompressorStream(
            source,
            collector=config.collector,
            seekable=config.seekable,
            decoder_limits=config.decoder_limits,
            report_trailing_data=config.report_trailing_data,
        )


class LzipCodec(_SizedLzmaCodec):
    codec = Codec.LZIP
    stream_format = StreamFormat.LZIP
    magic = (MagicSignature(0, b"LZIP", ArchiveFormat.LZIP),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        return LzipDecompressorStream(
            source,
            collector=config.collector,
            seekable=config.seekable,
            decoder_limits=config.decoder_limits,
            report_trailing_data=config.report_trailing_data,
        )

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        """Surface decompressed size and whole-member CRC-32 from one seekable index scan.

        ``probe_lzip_index`` returns both values from a single backward trailer walk.
        The CRC is the combine of every per-member trailer CRC-32 with that member's
        uncompressed ``data_size`` (single-member degenerates to the trailer CRC).
        """
        summary = ctx.probe_lzip_index()
        if summary is None:
            return
        member.size, crc32 = summary
        hashes = dict(member.hashes)
        hashes[HashAlgorithm.CRC32] = crc32_digest(crc32)
        member.hashes = hashes


def _alone_props_plausible(props: int) -> bool:
    """Whether ``props`` encodes a valid Alone ``(lc, lp, pb)`` triple.

    The format's range, props = (pb * 5 + lp) * 9 + lc with lc <= 8, lp <= 4, pb <= 4:
    225 of the 256 values. Detection admits all of them, since a header grammar accepts
    the format's full legal range and the decode decides.
    """
    return props <= (4 * 5 + 4) * 9 + 8


def _alone_props_liblzma_decodes(props: int) -> bool:
    """Whether liblzma decodes a stream with these properties: 75 of the 256 values.

    liblzma also requires lc + lp <= 4 (``LZMA_LCLP_MAX``). The next-stream check uses
    this, because a header liblzma refuses there fails the whole read.
    """
    lc = props % 9
    lp = props // 9 % 5
    return _alone_props_plausible(props) and lc + lp <= 4


def _alone_header_plausible(prefix: bytes) -> bool:
    """Cheap Alone header gate before a decode probe.

    The 13-byte header is a properties byte, a 32-bit dictionary size, and a 64-bit
    uncompressed size (all-ones meaning "unknown"). Two of the three are checked:

    - **Properties** must encode a legal ``(lc, lp, pb)`` triple.
    - **Uncompressed size** must not be exactly zero. Any value but the all-ones
      sentinel is the stream's exact output length, so zero declares a stream carrying
      no payload — nothing archivey could open. That is the rule the probe already
      applies after decoding (``require_output``), stated at the header because the
      bounded probe cannot reach it: 18 zero bytes are a *valid, complete, empty* Alone
      stream (13-byte header plus a five-byte range-coder init, which must begin with a
      zero), so zero-filled padding decodes cleanly to nothing, and the bounded read
      then runs off the end of the trailing zeros and reports truncation — which is a
      match. It refuses nothing that was ever detected: an empty stream is not claimed
      with or without this gate, because ``require_output`` already declines an empty
      decode, and a ``.lzma`` name still opens one through the extension.
    - **Dictionary size** is deliberately *not* gated, at any value. Every 32-bit value
      is legal and the LZMA specification requires decoders to round one below 4 KiB up
      to 4 KiB, so a stream whose field is zero still decodes — rejecting it was a false
      negative on real input, and it is independent of the size field above (a
      dictionary-zeroed stream of 700 bytes decodes fine). That rejection also served as
      an ordering workaround, keeping a zero-filled ISO system area from decoding as an
      empty Alone stream before far-magic ISO detection ran; far magic now runs before
      the content probes and answers that source itself (``format-detection``).
    """
    if len(prefix) < _ALONE_HEADER_SIZE or not _alone_props_plausible(prefix[0]):
        return False
    return int.from_bytes(prefix[5:13], "little") != 0


# A zero run this long, starting in the first ``_ALONE_ZERO_RUN_SPAN`` bytes of the
# range-coder data, means the bytes are not an encoder's output.
_ALONE_ZERO_RUN = 16
_ALONE_ZERO_RUN_SPAN = 32


def _alone_payload_has_zero_run(prefix: bytes) -> bool:
    """Whether a zero run near the start of the range-coder data rules out a real stream.

    A range coder fed zeros decodes zero literals without error, so any header that
    passes the gate and is followed by zeros decodes as a valid stream of zeros: a few
    hundred zero bytes give the probe its 4 KiB of output. A zero byte and one to nine
    random bytes before the run do too, in up to a third of cases (measured over 16 000
    random heads). ID3-tagged MP3s and OLE files have this shape, and they were claimed
    and read as a member of zeros, with no error.

    The two encoders measured never write a run like this. The longest zero run
    measured anywhere in a payload: 3 bytes from liblzma (``FORMAT_ALONE``, presets 0-9, plain and extreme)
    and 7 from the LZMA SDK encoder (7-Zip 23.01, levels 1-9, varied ``lc``/``lp``/
    ``pb``, dictionary, ``a=0``/``a=1``, with and without an end marker), on zeros,
    ``A``, ``ff``, ``ab`` and ``abc`` runs, random data, text and mixtures, and every
    input of 1-64 zero bytes (``-mx=9``). The 7-byte run is a two-zero-byte input with
    no end marker: two zero literals are all zero bits, so the whole payload is the
    range-coder init plus a zero flush. Both encoders code the third byte of a run as a
    match, and a match writes a one bit; that argument holds for any LZMA1 encoder, but
    other ``.lzma`` writers (XZ for Java, the SDK's ``lzma`` tool, ``lzma-rs``) were
    not run. A 16-byte run is over twice the longest one measured, and a run starting
    anywhere in the first 32 bytes is caught, where the latest start seen to reach 4 KiB
    of output over random heads was byte 10. The span bounds accidental collisions, not
    crafted input: a head of 32 or more bytes built to keep the range coder decoding
    before a zero run passes this rule (threat-model O10).
    """
    payload = prefix[_ALONE_HEADER_SIZE:]
    window = payload[: _ALONE_ZERO_RUN_SPAN + _ALONE_ZERO_RUN - 1]
    return bytes(_ALONE_ZERO_RUN) in window


def _peek_alone_header(source: CodecSource) -> tuple[CodecSource, bytes]:
    """Read an Alone stream's 13-byte header without consuming it from ``source``.

    ``lzma.LZMAFile`` takes no ``memlimit``, so the dictionary size has to be read
    before it is built. Returns the source to decode from, which is ``source``
    itself unless it could neither seek nor peek, in which case it is wrapped in an
    :class:`~archivey.internal.source.ArchiveSource` that replays the header — nothing
    is lost, since such a source could not have been rewound anyway.

    A path source is read twice, once here for the header and once by ``LZMAFile``,
    so the checked header and the decoded one come from two opens of the file. That
    is the same concurrent-open shape the single-file reader uses for every path
    source; whoever can swap the file between the two can as easily swap in a whole
    archive whose declared dictionary is under the cap.
    """
    if isinstance(source, (str, os.PathLike)):
        with open(os.fspath(source), "rb") as f:
            return source, read_exact(f, _ALONE_HEADER_SIZE)
    if isinstance(source, ArchiveSource) and not source.seekable():
        return source, source.peek(_ALONE_HEADER_SIZE)
    if is_seekable(source):
        pos = source.tell()
        try:
            return source, read_exact(source, _ALONE_HEADER_SIZE)
        finally:
            source.seek(pos)
    # Non-seekable, so its length is never a fact and nothing here clamps: this only
    # replays the header.
    replay = ArchiveSource.for_stream(source)
    return replay, replay.peek(_ALONE_HEADER_SIZE)


def _starts_alone_stream(data: bytes, *, limits: DecoderLimits) -> bool | None:
    """Whether the bytes after an Alone stream start another one, as ``lzma`` reads it.

    ``lzma.LZMAFile`` reads a second Alone stream after the first, so a concatenated
    ``.lzma`` is one payload. Alone has no magic to tell it by, so this checks what the
    header must hold: a properties byte liblzma decodes, and a zero first byte of
    range-coder data (byte 13), which every LZMA encoder writes. Text and most binary
    junk fail one of the two and are trailing data; junk that passes both (about one
    random tail in 870) is decoded as a stream and fails the read with
    ``CorruptionError``. A stream whose dictionary is over ``max_decoder_memory`` is
    refused as the first one is, rather than read as trailing data.
    """
    if len(data) <= _ALONE_HEADER_SIZE:
        return None
    if not _alone_props_liblzma_decodes(data[0]) or data[_ALONE_HEADER_SIZE] != 0:
        return False
    check_decoder_memory(
        int.from_bytes(data[1:5], "little"),
        limits=limits,
        what="LZMA Alone dictionary size",
    )
    return True


class _RefusedAloneStream(ReadOnlyIOStream):
    """What ``.lzma`` opens as when its dictionary is over the cap: every read refuses.

    The refusal is raised on read rather than on open, where xz and lzip raise theirs
    and where ``LZMAFile`` would have raised a corrupt header. It matters because
    ``.lzma`` has no magic: detection claims it by content probe alone, and a
    probe-only claim's read errors are stamped ``format_unconfirmed``
    (``error-handling``). The single-file reader opens a codec stream eagerly at
    ``open_archive``, before that provenance is attached, so a refusal raised on open
    would reach the caller unstamped — telling them to raise the cap for a file the
    probe may have misread, measured on an OLE header whose bytes 1-4 read as 2.7 GiB.
    Nothing is decoded, so no decoder is ever built.
    """

    def __init__(self, declared: int, limits: DecoderLimits) -> None:
        super().__init__()
        self._declared = declared
        self._limits = limits

    def read(self, n: int = -1, /) -> bytes:
        check_decoder_memory(
            self._declared, limits=self._limits, what="LZMA Alone dictionary size"
        )
        raise AssertionError("unreachable: the declared size is over the cap")

    # Not seekable, and ``seek``/``tell`` are ``RawIOBase``'s raising defaults: a
    # stream with no data has no position or size to report, and answering 0 would
    # let a caller that sizes a member with ``seek(0, SEEK_END)`` read it as empty
    # without ever meeting the refusal.


class LzmaAloneCodec(_LzmaErrorCodec):
    """Legacy LZMA Alone (``.lzma``) — framed standalone stream, not raw FORMAT_RAW."""

    codec = Codec.LZMA_ALONE
    stream_format = StreamFormat.LZMA_ALONE
    # No exact magic: the properties byte is too weak; recognition is by content probe.

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        source, header = _peek_alone_header(source)
        # A shorter header is left for liblzma to call truncated.
        if len(header) == _ALONE_HEADER_SIZE:
            declared = int.from_bytes(header[1:5], "little")
            if exceeds_decoder_memory(declared, config.decoder_limits):
                return _RefusedAloneStream(declared, config.decoder_limits)
        # A rewind re-decompresses from the start; the outer ArchiveStream warns (see
        # rewind_warning).
        return FramedDecompressorStream(
            source,
            lambda: lzma.LZMADecompressor(format=lzma.FORMAT_ALONE),
            codec_name="lzma",
            magic=functools.partial(_starts_alone_stream, limits=config.decoder_limits),
            collector=config.collector,
            report_trailing_data=config.report_trailing_data,
        )

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("lzma")

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        header = ctx.peek_header(_ALONE_HEADER_SIZE)
        if len(header) < _ALONE_HEADER_SIZE:
            return
        size = int.from_bytes(header[5:13], "little")
        if size != _ALONE_UNKNOWN_SIZE:
            member.size = size

    def content_probe(
        self,
        prefix: bytes,
        *,
        source_length: int | None = None,
        read_at: ProbeReadAt | None = None,
    ) -> bool:
        """Recognize LZMA Alone: plausible 13-byte header that then yields decode output.

        A source no longer than the 13-byte header carries no range-coder payload and
        cannot be an Alone stream — the whole of the measured real-world false-positive
        set. When ``source_length`` is unknown the check is skipped.

        A zero run at the start of the range-coder data is refused before any decode
        (``_alone_payload_has_zero_run``): it decodes cleanly, but no measured encoder writes it.

        Completeness and the bounded decode share ``_decodes_sample``; Alone additionally
        requires a positive output length (an empty successful read is not a claim).
        """
        if not _alone_header_plausible(prefix) or not self.available:
            return False
        if _alone_payload_has_zero_run(prefix):
            return False
        if source_length is not None and source_length <= _ALONE_HEADER_SIZE:
            return False
        return self._decodes_sample(
            prefix, source_length=source_length, require_output=True
        )


LZMA_DICTIONARY_FILTERS: dict[int, str] = {
    lzma.FILTER_LZMA1: "LZMA",
    lzma.FILTER_LZMA2: "LZMA2",
}

# stdlib exposes no public decoder for a raw LZMA1/LZMA2 property blob → filter dict;
# zipfile and py7zr rely on the same private helper. Bind once at import, loudly.
_raw_decode_filter_properties = getattr(lzma, "_decode_filter_properties", None)
if _raw_decode_filter_properties is None:  # pragma: no cover
    raise ImportError(
        "This Python's `lzma` module no longer exposes `_decode_filter_properties`, which "
        "archivey needs to decode raw LZMA properties (ZIP method 14, 7z LZMA/LZMA2 "
        "coders). Please report this to archivey (with your Python version)."
    )
_decode_filter_properties: Callable[[int, bytes], dict] = _raw_decode_filter_properties

# liblzma's ``LZMA_LCLP_MAX``: the largest lc + lp its LZMA1 decoder takes.
_LIBLZMA_LCLP_MAX = 4


def decode_lzma_filter_properties(filter_id: int, props: bytes, *, what: str) -> dict:
    """liblzma filter dict for a raw LZMA1/LZMA2 property blob.

    7-Zip accepts LZMA1 ``lc + lp`` up to 12 (``-mm=LZMA:lc=8`` writes it, in 7z and in
    ZIP method 14); liblzma decodes at most 4. Such a properties byte is well formed,
    so it raises ``UnsupportedFeatureError`` naming ``what`` (the container's coder).
    Any other blob liblzma refuses propagates as liblzma's own error, for the caller
    to report as corruption in its own terms.
    """
    try:
        return _decode_filter_properties(filter_id, props)
    except (lzma.LZMAError, ValueError) as exc:
        if filter_id == lzma.FILTER_LZMA1 and len(props) == 5 and props[0] < 9 * 5 * 5:
            lc, lp = props[0] % 9, props[0] // 9 % 5
            if lc + lp > _LIBLZMA_LCLP_MAX:
                raise UnsupportedFeatureError(
                    f"{what} with lc={lc}, lp={lp} is not supported: "
                    f"liblzma decodes lc + lp up to {_LIBLZMA_LCLP_MAX}"
                ) from exc
        raise


class _RawLzmaCodec(_LzmaErrorCodec):
    """Raw LZMA1/LZMA2 (FORMAT_RAW + properties); container-only (no standalone stream)."""

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if params.filters is None:
            raise ValueError(
                "raw LZMA decoding requires filter properties (CodecParams.filters)"
            )
        # The 7z coder properties and the ZIP method-14 header both arrive here as
        # decoded filter dicts, so this one check covers both containers. A filter
        # with no ``dict_size`` (7z LZMA without properties) gets liblzma's preset
        # default, which the archive did not choose.
        for spec in params.filters:
            if spec.get("id") in LZMA_DICTIONARY_FILTERS and "dict_size" in spec:
                check_decoder_memory(
                    spec["dict_size"],
                    limits=config.decoder_limits,
                    what=f"{LZMA_DICTIONARY_FILTERS[spec['id']]} dictionary size",
                )
        filters = params.filters
        # The coder's input span. A 7z AES stage decrypts whole blocks, so its output
        # runs up to 15 pad bytes past the next coder's input; ``pack_size`` (the AES
        # coder's unpack size) is the span 7-Zip reads, and the pad is not in it.
        # The slice does not clamp to its source's own size: reading on lets a
        # truncated source raise its own error (the AES stage's mid-block one).
        if params.pack_size is not None and not isinstance(source, (str, os.PathLike)):
            source = SlicingStream(
                source,
                length=params.pack_size,
                owns_inner=False,
                probe_source_size=False,
            )
        # One stream, as 7-Zip reads it: the data ends at the end marker, and any
        # byte of the span after it (a zero too) is "Data Error" there and
        # CorruptionError here. ``LZMAFile`` would instead have started a second raw
        # stream on those bytes and delivered it as content.
        decoded: BinaryIO = FramedDecompressorStream(
            source,
            lambda: lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters),
            codec_name="lzma",
            magic=_refuse_data_after_lzma_end,
            zero_padding=False,
            collector=config.collector,
        )
        if params.unpack_size is None:
            return decoded
        # A raw LZMA1 stream written without an end-of-stream marker (7-Zip's
        # default in 7z, ZIP method 14 with general-purpose bit 1 clear) ends where
        # its known output size says. liblzma cannot tell that from the input, so
        # reading on would ask for input past the end and fail as truncated; stop at
        # the size instead, and look for an end marker there (_LzmaEndAtSize).
        capped = SlicingStream(decoded, length=params.unpack_size, owns_inner=True)
        return _LzmaEndAtSize(capped, decoded=decoded, size=params.unpack_size)


class LzmaDataAfterEndError(CorruptionError):
    """Input in a raw LZMA coder's span after its end marker (7-Zip: "Data Error").

    A class of its own so a probe past a coder's declared size, which discards a
    decoder error there as not being surplus output, can still let this one through.
    """


def _refuse_data_after_lzma_end(data: bytes) -> bool:
    raise LzmaDataAfterEndError(
        f"LZMA stream has {len(data)}+ bytes of input after its end marker"
    )


class _LzmaEndAtSize(DelegatingStream):
    """A raw LZMA stream capped at its declared size, checked for an end marker there.

    Output stops at ``size``. When it gets there, one more byte of output is asked
    of the decoder, once. A stream with an end marker right after its data then
    reaches it without output, and input in the span after the marker raises
    :class:`LzmaDataAfterEndError`, as with no size. Anything else is a stream
    without an end marker, which this cannot tell from data past the size: a
    decoder error, a truncation (the usual case: the input ends with the data) or a
    decoded byte are all dropped, as before the check. That keeps 7-Zip's
    marker-less LZMA1 reading clean; probing it as surplus would fail valid
    archives.
    """

    readinto_passthrough = False

    def __init__(self, capped: BinaryIO, *, decoded: BinaryIO, size: int) -> None:
        # ``capped`` is ``decoded`` sliced at ``size``; this owns it (the default),
        # and it owns ``decoded``.
        super().__init__(capped)
        self._decoded = decoded
        self._size = size
        self._checked = False

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        if not self._checked and self._inner.tell() >= self._size:
            self._checked = True
            try:
                self._decoded.read(1)
            except LzmaDataAfterEndError:
                raise
            except (ArchiveyError, lzma.LZMAError, EOFError):
                pass
        return data

    def nearest_resume_offset(self, target: int) -> int | None:
        # The slice starts at the decoder's 0, so the offsets are the codec's.
        return ask_resume_offset(self._inner, target)


class LzmaCodec(_RawLzmaCodec):
    codec = Codec.LZMA


class Lzma2Codec(_RawLzmaCodec):
    codec = Codec.LZMA2

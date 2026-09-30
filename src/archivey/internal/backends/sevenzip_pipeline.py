"""7z folder decode pipeline — registry-driven trees of linear coder chains.

A *folder* is a coder graph over one or more packed streams. This module accepts a
**tree of linear chains**: every coder has one output, every coder but BCJ2 has one
input, and each input is either a pack stream or bound to one coder's output. A plain
folder is a single chain over pack stream 0; a BCJ2 folder is a BCJ2 coder fed by four
chains, one per pack stream.

Decode order within a chain (packed → unpacked)::

    AES? → (COPY skipped) → SINGLE codecs | LZMA-family run

``MethodKind.LZMA_FAMILY`` means “participates in a liblzma / BCJ staging run”,
not “is LZMA”: Delta and BCJ are batched with LZMA1/2 here.

- LZMA2 then Delta/BCJ (decode order) → one stdlib ``lzma`` raw filter chain
- LZMA1 then Delta → one capped chain; a BCJ after LZMA1 is its own stage
  (BPO-21872 truncation)
- A Delta/BCJ decoded before any LZMA1/LZMA2 → one filter stage per coder (a liblzma
  raw chain must end in LZMA1/LZMA2 in encode order, so that codec decodes first)
- Several LZMA1/LZMA2 coders in one run → one chain each
- BCJ2 (``0x0303011B``) → the source of its chain: four branch chains, each capped at
  its declared size, feed :class:`~archivey.internal.streams.bcj2.Bcj2DecoderStream`

Two phases: :func:`plan_folder` resolves stages (pure — no I/O); then
:func:`open_folder_pipeline` / :func:`_execute_stage` fold stages onto the packed
sources. Encoded-header decode and the convenience :func:`parse_sevenzip_archive`
also live here (parser stays structure-only). The encoded header stays linear-only:
no writer puts BCJ2 there.
"""

from __future__ import annotations

import io
import lzma
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from archivey.config import ListingLimits
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_aes import SevenZipKeyCache
from archivey.internal.backends.sevenzip_methods import (
    METHOD_DELTA,
    METHOD_LZMA,
    METHOD_LZMA2,
    MethodKind,
    _method_hex,
    is_bcj,
    is_lzma_family,
    lookup,
    require,
)
from archivey.internal.backends.sevenzip_parser import (
    MAX_NEXT_HEADER_SIZE,
    EncodedHeader,
    HeaderBlock,
    PlainHeader,
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFolder,
    empty_archive,
    encoded_folder_slices,
    folder_is_encrypted,
    materialize_archive,
    parse_header_block,
    read_signature_and_next_header,
)
from archivey.internal.config import (
    DEFAULT_STREAM_CONFIG,
    StreamConfig,
    check_decoder_memory,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.bcj2 import Bcj2DecoderStream
from archivey.internal.streams.codecs import (
    LZMA_DICTIONARY_FILTERS,
    Codec,
    CodecParams,
    open_codec_stream,
    parse_ppmd_var_h_properties,
)
from archivey.internal.streams.crypto import open_aes_decrypt_stream
from archivey.internal.streams.decompress import FilterStream
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import SlicingStream, read_exact
from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.binaryio import (
    read_blocking,
    readinto_via_read,
    try_readinto,
)
from archivey.internal.streams.zstd_framing import (
    MAX_FRAME_HEADER_SIZE,
    frame_window_size,
)

# Omitting max_members on the archive-level entry point means the ListingLimits
# default, as in sevenzip_parser and rar_parser. None is the explicit UNLIMITED opt-out.
_DEFAULT_MAX_MEMBERS = ListingLimits().max_members

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

# stdlib exposes no public decoder for a raw LZMA1/LZMA2 property blob → filter dict;
# py7zr relies on the same private `lzma._decode_filter_properties`. Bind once at import.
_raw_decode_filter_properties = getattr(lzma, "_decode_filter_properties", None)
if _raw_decode_filter_properties is None:  # pragma: no cover
    raise ImportError(
        "This Python's `lzma` module no longer exposes `_decode_filter_properties`, which "
        "archivey's native 7z reader needs to decode raw LZMA1/LZMA2 coder properties. "
        "Please report this to archivey (with your Python version)."
    )
_decode_filter_properties: Callable[[int, bytes], dict] = _raw_decode_filter_properties

# The most filters one liblzma chain holds (``LZMA_FILTERS_MAX`` in lzma/filter.h).
_LIBLZMA_MAX_FILTERS = 4


# A folder's coder chain is decoded in two phases: `plan_folder` resolves it into an
# ordered list of these concrete stages (pure — no streams opened), then
# `_execute_stage` / `open_folder_pipeline` fold each stage onto the packed source.
# Keeping the branchy LZMA1+BCJ staging in the planner makes the decode flow
# inspectable and keeps I/O out of the decision.


@dataclass
class _AesStage:
    """AES-256 decryption of the stream."""

    coder: SevenZipCoder


@dataclass
class _CodecStage:
    """A single self-contained codec (Deflate, BZip2, Zstd, PPMd, …).

    ``unpack_size`` is the coder's output length from the folder header. It is passed
    through for codecs that need a bound (PPMd7 has no end mark). The codecs in
    :data:`_CODEC_LABELS` end their own stream, so their output is checked against
    this size instead (:class:`_DecodedPastSizeCheck`).

    ``pack_size`` is the coder's *input* length — the output of the preceding coder in
    its chain, or a BCJ2 source's declared output (``None`` when this coder consumes
    the packed slice directly, whose length ``open_codec_stream`` recovers from the
    sized source). PPMd uses it
    to gate post-eof recovery on full pack delivery. Deflate/zlib/bzip2 rapidgzip
    uses it to bound the input so AES pad bytes are not a concatenated member or
    trailing-garbage stderr.
    """

    codec: Codec
    properties: bytes | None
    unpack_size: int | None = None
    pack_size: int | None = None


@dataclass
class _LzmaChainStage:
    """One liblzma raw-filter chain (LZMA1/LZMA2 with any Delta/BCJ filters).

    ``cap_size`` bounds the decoded output with a ``SlicingStream``; it is set for
    every LZMA1 chain, because 7-Zip writes LZMA1 without an end marker and a reader
    past the declared size (a BCJ look-ahead, BPO-21872, or a following codec) would
    otherwise ask for input that is not there. ``None`` means no cap. The following
    ``_FilterStage`` must close that slice (``owns_inner=True``) — DecompressorStream
    does not close a passed-in stream by default.

    ``end_check_size`` is set for an LZMA2 chain instead: the declared output size,
    past which a decoded byte is corruption (:class:`_DecodedPastSizeCheck`).
    """

    codec: Codec
    filters: list[dict]
    cap_size: int | None
    end_check_size: int | None = None


@dataclass
class _FilterStage:
    """One filter-only coder staged on its own: a BCJ (LZMA1+BCJ, or no LZMA at all)
    or a Delta with no LZMA1/LZMA2 in its run.

    ``lzma_filter`` is the liblzma filter dict with its options (BCJ
    ``start_offset``, Delta ``dist``); :class:`FilterDecoder` runs it outside the
    folder's main chain, over its own LZMA2 framing.
    """

    lzma_filter: dict
    unpack_size: int


@dataclass
class _CopyStage:
    """A COPY coder reading a pack stream: output past its declared size is refused.

    A COPY coder anywhere else passes its input through unchanged, and needs no stage:
    the planner has already checked that its size equals its input coder's.
    """

    unpack_size: int


_Stage = _AesStage | _CodecStage | _LzmaChainStage | _FilterStage | _CopyStage


@dataclass
class _Bcj2Stage:
    """A BCJ2 coder: the source of a chain, fed by four chains of its own.

    ``branches`` are ``main``, ``call``, ``jump`` and ``rc``, in the coder's input order.
    """

    branches: list[_Chain]
    unpack_size: int


@dataclass
class _Chain:
    """Linear stages over one source: a pack stream, or a BCJ2 coder's output.

    ``source`` is the pack stream's position in the folder's ``packed_indices``
    (0 for a linear folder), or the :class:`_Bcj2Stage` whose output the stages read.
    ``stages`` are in decode order.
    """

    source: int | _Bcj2Stage
    stages: list[_Stage]
    # The declared size of the chain's output: its top coder's unpack size. ``None``
    # for a bare pack stream, whose view is already sized.
    unpack_size: int | None = None

    def has_bcj2(self) -> bool:
        return isinstance(self.source, _Bcj2Stage)

    def pack_count(self) -> int:
        """How many pack streams this chain reads, counting every BCJ2 branch."""
        if isinstance(self.source, _Bcj2Stage):
            return sum(branch.pack_count() for branch in self.source.branches)
        return 1


def plan_folder(folder: SevenZipFolder) -> _Chain:
    """Resolve a folder's coder graph into decode stages, opening no streams.

    The graph must be a tree of linear chains. Every coder has one output; the only
    coder with more than one input is BCJ2, with four. Each input is either a pack
    stream or bound to exactly one coder's output. The root is the coder whose output
    no bind pair consumes, and the plan is read from the root down (design D1 of the
    ``sevenzip-bcj2-decode`` change).

    A graph that cannot be a valid folder (no coders, a coder with no input or no
    output, a cycle, an input that is neither packed nor bound) is
    :class:`CorruptionError`. A valid shape this planner does not run (a
    coder with several outputs, a multi-input coder other than BCJ2) is
    :class:`UnsupportedFeatureError`. A linear folder plans to one chain over pack
    stream 0, as it always has.
    """
    coders = folder.coders
    if not coders:
        raise CorruptionError("7z folder has no coders")
    for coder in coders:
        if coder.num_out_streams == 0:
            raise CorruptionError(
                f"7z coder {_method_hex(coder.method)} has no out-stream"
            )
        if coder.num_in_streams == 0:
            raise CorruptionError(
                f"7z coder {_method_hex(coder.method)} has no in-stream"
            )
        if coder.num_out_streams != 1:
            raise UnsupportedFeatureError(
                "7z folders with multi-output coders are not supported"
            )
        method = lookup(coder.method)
        is_bcj2 = method is not None and method.kind is MethodKind.BCJ2
        if coder.num_in_streams != 1 and not (is_bcj2 and coder.num_in_streams == 4):
            raise UnsupportedFeatureError(
                f"7z coder {_method_hex(coder.method)} with "
                f"{coder.num_in_streams} inputs is not supported"
            )
    # With one output per coder, out-stream ``i`` is coder ``i``'s output.
    in_base: list[int] = []
    total_in = 0
    for coder in coders:
        in_base.append(total_in)
        total_in += coder.num_in_streams
    bound: dict[int, int] = {}
    for in_index, out_index in folder.bind_pairs:
        if in_index in bound or not 0 <= out_index < len(coders):
            raise CorruptionError("7z folder has an invalid coder bind pair")
        bound[in_index] = out_index
    if len(set(bound.values())) != len(bound):
        raise CorruptionError("7z folder binds one coder output to two inputs")
    roots = [i for i in range(len(coders)) if i not in set(bound.values())]
    if not roots:
        raise CorruptionError("7z folder coder graph has a cycle and no output")
    if len(roots) > 1:
        raise UnsupportedFeatureError(
            "7z folders with several outputs are not supported"
        )
    if len(folder.unpack_sizes) < len(coders):
        raise CorruptionError("7z folder has fewer unpack sizes than coders")

    visited: set[int] = set()

    def input_chain(in_index: int) -> _Chain:
        if in_index in folder.packed_indices:
            if in_index in bound:
                raise CorruptionError("7z folder coder input is both packed and bound")
            return _Chain(folder.packed_indices.index(in_index), [])
        if in_index not in bound:
            raise CorruptionError("7z folder coder input is neither packed nor bound")
        return chain_ending_at(bound[in_index])

    def chain_ending_at(top: int) -> _Chain:
        run: list[int] = []  # top first; reversed into decode order below
        coder_index = top
        while True:
            if coder_index in visited:
                raise CorruptionError("7z folder coder graph has a cycle")
            visited.add(coder_index)
            # Four inputs means BCJ2: the check at the top refused every other coder
            # with more than one input. Relaxing that check must change this test too.
            if coders[coder_index].num_in_streams == 4:
                base = in_base[coder_index]
                source: int | _Bcj2Stage = _Bcj2Stage(
                    [input_chain(base + k) for k in range(4)],
                    folder.unpack_sizes[coder_index],
                )
                break
            run.append(coder_index)
            in_index = in_base[coder_index]
            if in_index in folder.packed_indices or in_index not in bound:
                source = input_chain(in_index).source
                break
            coder_index = bound[in_index]
        run.reverse()
        return _Chain(source, _plan_run(folder, run, source), folder.unpack_sizes[top])

    chain = chain_ending_at(roots[0])
    if len(visited) != len(coders):
        # Every output but the root's is bound, so a coder the root never reaches
        # feeds a loop of coders.
        raise CorruptionError("7z folder coder graph has a cycle")
    return chain


def _plan_run(
    folder: SevenZipFolder, run: list[int], source: int | _Bcj2Stage
) -> list[_Stage]:
    """Plan one linear run of 1-in coders, given as coder indices in decode order.

    "The preceding coder" is the previous coder *in this run*, not in the folder's
    coder list: in a BCJ2 folder the previous coder in the list is usually a sibling
    branch's. The first coder's input is the source: a pack stream, whose length the
    sized view carries, or a BCJ2 stage, whose output size the folder declares.
    """
    coders = folder.coders
    source_size = source.unpack_size if isinstance(source, _Bcj2Stage) else None
    _check_size_preserving_coders(folder, run, source_size)
    stages: list[_Stage] = []
    position = 0
    while position < len(run):
        index = run[position]
        method = require(coders[index].method)
        if method.kind is MethodKind.COPY:
            if position == 0 and source_size is None:
                # Over a pack stream, whose length the folder does not carry: the
                # check is on the output, as for a codec.
                stages.append(_CopyStage(folder.unpack_sizes[index]))
            position += 1
            continue
        if method.kind is MethodKind.BCJ2:
            # Only reachable with a BCJ2 coder declared with one input.
            raise UnsupportedFeatureError("7z BCJ2 coder must have four inputs")
        if method.kind is MethodKind.AES:
            stages.append(_AesStage(coders[index]))
            position += 1
            continue
        if method.kind is MethodKind.SINGLE:
            assert method.codec is not None
            input_size = (
                folder.unpack_sizes[run[position - 1]] if position > 0 else source_size
            )
            stages.append(
                _CodecStage(
                    method.codec,
                    coders[index].properties,
                    unpack_size=folder.unpack_sizes[index],
                    pack_size=input_size,
                )
            )
            position += 1
            continue
        # LZMA_FAMILY (LZMA1/LZMA2/Delta/BCJ): batch the contiguous run, then plan it.
        lzma_run: list[SevenZipCoder] = []
        sizes: list[int] = []
        while position < len(run) and is_lzma_family(coders[run[position]].method):
            lzma_run.append(coders[run[position]])
            sizes.append(folder.unpack_sizes[run[position]])
            position += 1
        stages.extend(_plan_lzma_family(lzma_run, sizes))
    return stages


def _check_size_preserving_coders(
    folder: SevenZipFolder, run: list[int], source_size: int | None
) -> None:
    """Refuse a COPY, Delta or BCJ coder whose size differs from its input's.

    These coders output exactly as many bytes as they read, so in a valid folder each
    one's unpack size equals its input's: the preceding coder's unpack size, or a BCJ2
    source's. (An AES coder declares its output without the block padding, so this
    holds after AES too.) 7-Zip decodes every coder to its own size and reports a
    mismatch as a data error. Without this check a filter's size would go unchecked
    inside a liblzma chain, whose output is checked only at the chain's last size: an
    LZMA2 coder could decode past its own size in front of a Delta that declares more.
    A coder over a pack stream has no declared input size here; a COPY there gets a
    :class:`_CopyStage`.
    """
    for position, index in enumerate(run):
        coder = folder.coders[index]
        method = require(coder.method)
        if method.kind is not MethodKind.COPY and not (
            method is METHOD_DELTA or is_bcj(coder.method)
        ):
            continue
        input_size = (
            folder.unpack_sizes[run[position - 1]] if position > 0 else source_size
        )
        if input_size is not None and input_size != folder.unpack_sizes[index]:
            raise CorruptionError(
                f"7z coder {_method_hex(coder.method)} declares "
                f"{folder.unpack_sizes[index]} bytes of output from "
                f"{input_size} bytes of input"
            )


def _is_lzma_codec(coder: SevenZipCoder) -> bool:
    method = lookup(coder.method)
    return method is METHOD_LZMA or method is METHOD_LZMA2


def _plan_lzma_family(
    run: list[SevenZipCoder], unpack_sizes: list[int]
) -> list[_Stage]:
    """Plan a run of LZMA1/LZMA2/Delta/BCJ coders, given in decode order.

    A liblzma raw chain is written in encode order and must end in its one LZMA1 or
    LZMA2 filter, so in decode order that codec comes first and only filters follow
    it. The run is therefore cut into segments, each an LZMA1/LZMA2 coder and the
    filters decoded after it, up to liblzma's filter limit. A filter decoded before
    any codec of its segment (``7z a -m0=LZMA2 -m1=BCJ`` stores BCJ first in decode
    order), or past the limit, runs as its own stage.
    """
    if len(run) != len(unpack_sizes):
        raise CorruptionError("7z LZMA-family run length does not match unpack sizes")
    stages: list[_Stage] = []
    index = 0
    while index < len(run):
        if not _is_lzma_codec(run[index]):
            # liblzma will not build a raw chain without an LZMA1/LZMA2 terminator.
            stages.append(_filter_stage(run[index], unpack_sizes[index]))
            index += 1
            continue
        end = index + 1
        while (
            end < len(run)
            and end - index < _LIBLZMA_MAX_FILTERS
            and not _is_lzma_codec(run[end])
        ):
            end += 1
        if lookup(run[index].method) is METHOD_LZMA:
            # liblzma can silently truncate BCJ look-ahead when LZMA1 lacks EOS
            # (BPO-21872). The LZMA1 chain stops before the first BCJ, and is capped
            # at its declared output size: 7-Zip writes LZMA1 without an end marker,
            # so reading past that size would ask for input that is not there. Each
            # BCJ, and each filter after one, runs as its own stage. The filters are
            # size-preserving and checked against the codec's size by the planner,
            # so the chain's last size is the LZMA1 coder's own.
            chain_end = index + 1
            while chain_end < end and not is_bcj(run[chain_end].method):
                chain_end += 1
            stages.append(
                _lzma_chain_stage(
                    run[index:chain_end], cap_size=unpack_sizes[chain_end - 1]
                )
            )
            stages.extend(
                _filter_stage(coder, size)
                for coder, size in zip(
                    run[chain_end:end], unpack_sizes[chain_end:end], strict=True
                )
            )
        else:
            # LZMA2 ± Delta ± BCJ: one chain. LZMA2 has an end marker, so it is not
            # capped; its declared size is checked against what it decodes instead.
            # As above, the chain's last size is the LZMA2 coder's own.
            stages.append(
                _lzma_chain_stage(
                    run[index:end], cap_size=None, end_check_size=unpack_sizes[end - 1]
                )
            )
        index = end
    return stages


def _decode_lzma_properties(coder: SevenZipCoder, filter_id: int) -> dict:
    if coder.properties is None:
        return {"id": filter_id}
    try:
        return _decode_filter_properties(filter_id, coder.properties)
    except (lzma.LZMAError, ValueError) as exc:
        props = coder.properties
        if filter_id == lzma.FILTER_LZMA1 and len(props) == 5 and props[0] < 9 * 5 * 5:
            # A well-formed lc/lp/pb byte that liblzma refuses: 7-Zip accepts
            # lc + lp up to 12 (``7z a -m0=LZMA:lc=8``), liblzma only up to 4
            # (``LZMA_LCLP_MAX``). The archive is valid; this reader cannot decode it.
            lc, lp = props[0] % 9, props[0] // 9 % 5
            if lc + lp > 4:
                raise UnsupportedFeatureError(
                    f"7z LZMA coder with lc={lc}, lp={lp} is not supported: "
                    "liblzma decodes lc + lp up to 4"
                ) from exc
        raise CorruptionError(
            f"Malformed 7z LZMA coder properties for {_method_hex(coder.method)}"
        ) from exc


def _lzma_filter(coder: SevenZipCoder) -> dict:
    method = require(coder.method)
    if method is METHOD_LZMA or method is METHOD_LZMA2:
        assert method.lzma_filter_id is not None
        return _decode_lzma_properties(coder, method.lzma_filter_id)
    if method is METHOD_DELTA:
        if coder.properties is None:
            return {"id": lzma.FILTER_DELTA}
        if len(coder.properties) != 1:
            raise CorruptionError("Malformed 7z Delta coder properties")
        return {"id": lzma.FILTER_DELTA, "dist": coder.properties[0] + 1}
    if method.lzma_filter_id is not None and is_bcj(coder.method):
        return _bcj_filter(coder, method.lzma_filter_id)
    raise UnsupportedFeatureError(
        f"Unsupported 7z LZMA-family coder {_method_hex(coder.method)}"
    )


def _bcj_filter(coder: SevenZipCoder, filter_id: int) -> dict:
    """liblzma dict for a 7z BCJ coder, carrying its start offset when it has one.

    7z BCJ properties are absent or a 4-byte little-endian start offset. Writers
    almost always omit it or write zero; a non-zero value shifts the addresses the
    filter converts, so dropping it would decode a well-formed archive wrongly.

    The property is read as liblzma's ``start_offset`` (7-Zip's branch coders seed
    their position from it the same way liblzma seeds ``now_pos``). That equivalence
    is from reading both sources: the 7-Zip CLI cannot write a non-zero offset, so no
    7-Zip-written archive checks it, and the tests encode their fixtures with liblzma.
    """
    if not coder.properties:
        return {"id": filter_id}
    if len(coder.properties) != 4:
        raise CorruptionError(
            f"Malformed 7z BCJ coder properties for {_method_hex(coder.method)}"
        )
    start_offset = int.from_bytes(coder.properties, "little")
    if start_offset == 0:
        return {"id": filter_id}
    lzma_filter = {"id": filter_id, "start_offset": start_offset}
    try:
        # liblzma rejects an offset the architecture's alignment forbids (ARM needs
        # 4, IA64 16); find that at plan time, not on the first read.
        lzma.LZMADecompressor(
            lzma.FORMAT_RAW, filters=[lzma_filter, {"id": lzma.FILTER_LZMA2}]
        )
    except lzma.LZMAError:
        raise UnsupportedFeatureError(
            f"7z BCJ coder {_method_hex(coder.method)} start offset {start_offset} "
            "is not supported for this filter"
        ) from None
    return lzma_filter


def _open_aes_stage(
    source: BinaryIO,
    coder: SevenZipCoder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
) -> BinaryIO:
    if password is None:
        raise EncryptionError("Password required to decrypt this 7z folder")
    if coder.properties is None:
        raise CorruptionError("7z AES coder is missing properties")
    try:
        params = key_cache.aes_params_from_properties(password, coder.properties)
    except ValueError as exc:
        raise CorruptionError(f"Malformed 7z AES properties: {exc}") from exc
    return open_aes_decrypt_stream(source, params)


def _filter_stage(coder: SevenZipCoder, unpack_size: int) -> _FilterStage:
    method = require(coder.method)
    if method is not METHOD_DELTA and not is_bcj(coder.method):
        raise UnsupportedFeatureError(
            f"Unsupported 7z filter-only coder {_method_hex(coder.method)}"
        )
    return _FilterStage(_lzma_filter(coder), unpack_size)


def _lzma_chain_stage(
    run: list[SevenZipCoder],
    *,
    cap_size: int | None,
    end_check_size: int | None = None,
) -> _LzmaChainStage:
    has_lzma1 = any(lookup(c.method) is METHOD_LZMA for c in run)
    has_lzma2 = any(lookup(c.method) is METHOD_LZMA2 for c in run)
    if not has_lzma1 and not has_lzma2:
        # liblzma's FORMAT_RAW needs the chain to end in LZMA1/LZMA2; a filter-only
        # chain fails two layers down as "Invalid or unsupported options". The
        # planner routes filter-only runs to _FilterStage, so this is a planner bug.
        raise UnsupportedFeatureError(
            "7z filter-only coder run cannot be a liblzma chain: "
            + ", ".join(_method_hex(c.method) for c in run)
        )
    # Decode order is outer-first; liblzma wants encode order → reversed(run).
    filters = [_lzma_filter(coder) for coder in reversed(run)]
    codec = Codec.LZMA if has_lzma1 and not has_lzma2 else Codec.LZMA2
    return _LzmaChainStage(codec, filters, cap_size, end_check_size)


# The codecs whose output _DecodedPastSizeCheck checks, with 7-Zip's name for each.
# This is an allowlist: a codec belongs here only when it ends its own stream, so that
# output past its declared size is surplus and not data. A codec that relies on the
# declared size to stop (LZMA1, PPMd) must stay out, or valid archives fail the check.
_CODEC_LABELS = {
    Codec.LZMA2: "LZMA2",
    Codec.DEFLATE: "Deflate",
    Codec.DEFLATE64: "Deflate64",
    Codec.BZIP2: "BZip2",
    Codec.ZSTD: "Zstd",
    Codec.LZ4: "LZ4",
    Codec.BROTLI: "Brotli",
}


class _DecodedPastSizeCheck(DelegatingStream):
    """Refuse a coder that decodes more than its declared unpack size.

    It wraps the output of every codec in :data:`_CODEC_LABELS`: an LZMA2 chain, and
    Deflate, Deflate64, BZip2, Zstd, LZ4 and Brotli. LZMA1 and PPMd have no end
    marker in 7z, so they are capped at their size and surplus output is not seen.
    It also wraps a COPY coder that reads a pack stream (:class:`_CopyStage`): pack
    bytes past the COPY coder's size would otherwise reach the coder after it.

    Every reader above this one stops at the declared size (the member slice, a BCJ2
    branch slice, the filter after the codec), so surplus output would otherwise pass
    unseen. 7-Zip reports such a folder as a data error. When the output reaches the
    declared size, this reads **one** more byte of output from the decoder. Only
    decoded bytes count, so a further stream in the same coder (a second BZip2
    stream, a second Zstd or LZ4 frame) is surplus when it decodes to one byte or
    more.

    The probe is one read of output, not a second decode, but its input cost is not
    one read. To produce that byte, the decoder consumes as much input as it needs,
    up to the end of the coder's packed slice: a tail of streams that decode to
    nothing is read to its end. That cost is linear in the archive's own size, the
    same bound as decoding the folder, so it is accepted rather than bounded here.
    Bounding it would need a counter on the codec's input, outside this wrapper.

    AES padding in the codec's input is not output, so it is never surplus: LZMA2
    raises on input after its end marker, and the other decoders end the stream at it
    (or, for the rapidgzip accelerators, never see it: their input is cut to
    ``pack_size``). So a decoder error on the probe read is not surplus output either;
    7-Zip also does not treat input after the end of the stream as a data error.
    Discarding that error is safe because every codec wrapped here verifies its data
    before or together with delivering it (the BZip2 block CRC, the Zstd and LZ4
    content checksums), so a failed check of the declared data raises on the read
    that delivers that data, before the probe: only input after the stream reaches
    the probe read. A codec that verifies only after
    its last byte must not be added to :data:`_CODEC_LABELS` without revisiting this.
    """

    def __init__(self, inner: BinaryIO, *, size: int, label: str) -> None:
        super().__init__(inner)
        self._size = size
        self._label = label
        self._position = 0
        self._checked = False

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        data = read_blocking(self._inner, n)
        self._count(len(data))
        return data

    def readinto(self, b: WriteableBuffer, /) -> int:
        n = try_readinto(self._inner, b)
        if n is None:
            return readinto_via_read(self, b)  # read() counts
        self._count(n)
        return n

    def _count(self, n: int) -> None:
        self._position += n
        if self._position > self._size:
            self._raise_surplus()
        if self._position == self._size and not self._checked:
            self._checked = True
            try:
                surplus = self._inner.read(1)
            except (ArchiveyError, lzma.LZMAError, EOFError):
                surplus = b""
            if surplus:
                self._raise_surplus()

    def _raise_surplus(self) -> None:
        raise CorruptionError(
            f"7z {self._label} coder decodes past its declared unpack size of "
            f"{self._size} bytes"
        )

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._position = self._inner.seek(offset, whence)
        self._checked = False
        return self._position

    def nearest_resume_offset(self, target: int) -> int | None:
        return ask_resume_offset(self._inner, target)


def _execute_stage(
    stream: BinaryIO,
    stage: _Stage,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
    owns_input: bool,
) -> BinaryIO:
    """Open one planned stage on top of ``stream``. The only stream-opening code.

    ``owns_input`` is consumed only by ``_FilterStage`` (``owns_inner``): True when
    ``stream`` is a private earlier output rather than a borrowed pack view. Other
    stages ignore it: stdlib ``LZMAFile`` does not close a passed-in
    fileobj, so ``[AES, LZMA]`` still leaves the AES decrypt stream to GC.
    ``AesDecryptStream`` borrows the pack view (``owns_inner`` default).
    """
    if isinstance(stage, _AesStage):
        return _open_aes_stage(
            stream, stage.coder, password=password, key_cache=key_cache
        )
    if isinstance(stage, _CodecStage):
        decoded = open_codec_stream(
            stage.codec,
            stream,
            config=stream_config,
            params=CodecParams(
                properties=stage.properties,
                unpack_size=stage.unpack_size,
                pack_size=stage.pack_size,
            ),
            collector=collector,
            seekable=seekable,
        )
        label = _CODEC_LABELS.get(stage.codec)
        if label is None:
            # Not on the allowlist: PPMd, which has no end mark in 7z and is capped
            # at unpack_size by the codec itself, so nothing past it can be seen.
            return decoded
        # The planner always sets unpack_size; None is only the dataclass default.
        assert stage.unpack_size is not None
        return _DecodedPastSizeCheck(decoded, size=stage.unpack_size, label=label)
    if isinstance(stage, _LzmaChainStage):
        out = open_codec_stream(
            stage.codec,
            stream,
            config=stream_config,
            params=CodecParams(filters=stage.filters),
            collector=collector,
            seekable=seekable,
        )
        if stage.cap_size is not None:
            out = SlicingStream(out, length=stage.cap_size, owns_inner=True)
        elif stage.end_check_size is not None:
            out = _DecodedPastSizeCheck(
                out, size=stage.end_check_size, label=_CODEC_LABELS[Codec.LZMA2]
            )
        return out
    if isinstance(stage, _CopyStage):
        return _DecodedPastSizeCheck(stream, size=stage.unpack_size, label="Copy")
    return FilterStream(
        stream,
        lzma_filter=stage.lzma_filter,
        unpack_size=stage.unpack_size,
        seekable=seekable,
        owns_inner=owns_input,
    )


def open_folder_pipeline(
    sources: Sequence[BinaryIO],
    folder: SevenZipFolder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
    seekable: bool = False,
) -> BinaryIO:
    """Compose a folder's coder graph into a single pull stream (plan, then fold).

    ``sources`` are borrowed pack views, one per pack stream, in the order of the
    folder's ``packed_indices``. Each stage wraps the previous output.
    Only a ``_FilterStage`` takes ``owns_inner``: True when it is not first in its
    chain, so it closes the previous stage's output — the LZMA1 cap slice, or an
    ``AesDecryptStream`` on ``[AES, BCJ]``. Other follow-on stages do not close
    their input: ``[AES, LZMA]`` (the common encrypted shape) still leaves the AES
    stream unclosed, because stdlib ``LZMAFile`` does not close a passed-in fileobj.
    The AES stream borrows the pack view (``owns_inner`` default) and holds no OS
    handle. Wiring codec stages to close it is a follow-up; seek does not depend on it.

    In a BCJ2 folder, ``seekable`` also applies to every branch, because the
    :class:`Bcj2DecoderStream` seeks backward by rewinding its four inputs. It owns and
    closes the branch outputs.
    """
    config = stream_config if stream_config is not None else DEFAULT_STREAM_CONFIG
    plan = plan_folder(folder)
    if len(sources) != plan.pack_count():
        raise CorruptionError(
            f"7z folder reads {plan.pack_count()} packed streams, "
            f"but {len(sources)} were given"
        )

    # Every decoder of a folder runs at once: the stages of a chain are stacked
    # streams, and a BCJ2 folder's branches (main, call, jump; rc too, when it is not
    # a bare pack stream) run side by side. Each declares its own memory and is
    # checked on its own when it opens; the folder's total is checked here, before
    # any of them is built. One decoder alone is left to its own check, whose
    # message names the field.
    declared = _declared_decoder_memory(plan, sources)
    if len(declared) > 1:
        folder_kind = "7z BCJ2 folder" if plan.has_bcj2() else "7z folder"
        check_decoder_memory(
            sum(declared),
            limits=config.decoder_limits,
            what=f"the sum over a {folder_kind}'s decoders "
            "(LZMA dictionaries, PPMd memory, zstd windows)",
        )

    def open_chain(chain: _Chain, *, seekable: bool) -> BinaryIO:
        stream: BinaryIO
        if isinstance(chain.source, _Bcj2Stage):
            branches: list[BinaryIO] = []
            try:
                for branch in chain.source.branches:
                    branch_stream = open_chain(branch, seekable=seekable)
                    if branch.unpack_size is not None:
                        # A branch ends at its declared size. 7-Zip's LZMA branches
                        # have no end marker, and BCJ2 reads its inputs in blocks,
                        # so an uncapped LZMA1 decoder would read past its data.
                        branch_stream = SlicingStream(
                            branch_stream,
                            length=branch.unpack_size,
                            owns_inner=_opens_streams(branch),
                        )
                    branches.append(branch_stream)
            except BaseException:
                for opened, branch in zip(branches, chain.source.branches):
                    if branch.unpack_size is not None:
                        opened.close()
                raise
            main, call, jump, rc = branches
            stream = Bcj2DecoderStream(
                main,
                call,
                jump,
                rc,
                unpack_size=chain.source.unpack_size,
                # A bare pack-stream branch is a borrowed view (``rc``, usually);
                # every other branch is wrapped in a private slice.
                owns_inputs=[b.unpack_size is not None for b in chain.source.branches],
            )
            owns_input = True
        else:
            stream = sources[chain.source]
            owns_input = False
        for stage in chain.stages:
            stream = _execute_stage(
                stream,
                stage,
                password=password,
                key_cache=key_cache,
                stream_config=config,
                collector=collector,
                seekable=seekable,
                owns_input=owns_input,
            )
            owns_input = True
        return stream

    return open_chain(plan, seekable=seekable)


def _declared_decoder_memory(chain: _Chain, sources: Sequence[BinaryIO]) -> list[int]:
    """The decoder memory each decoder of a chain declares, over every BCJ2 branch.

    Counts what ``max_decoder_memory`` bounds per decoder: LZMA1/LZMA2 dictionary
    sizes, the PPMd memory size and the zstd window. Other codecs declare no working
    memory. PPMd properties that do not parse count as 0 here; the PPMd stage refuses
    them itself when it opens.

    A zstd window is declared in the frame header, not in the coder properties, so it
    is read from the pack stream when the zstd coder reads one directly (the layout
    7-Zip ZS writes): the first frame's window counts. A zstd coder behind another
    coder (AES, say) counts as 0, and so do later frames; each frame's window is still
    held to ``max_decoder_memory`` on its own when the frame starts.
    """
    declared: list[int] = []
    if isinstance(chain.source, _Bcj2Stage):
        for branch in chain.source.branches:
            declared.extend(_declared_decoder_memory(branch, sources))
    elif (
        chain.stages
        and isinstance(chain.stages[0], _CodecStage)
        and chain.stages[0].codec is Codec.ZSTD
    ):
        window = _zstd_first_frame_window(sources[chain.source])
        if window:
            declared.append(window)
    for stage in chain.stages:
        if isinstance(stage, _LzmaChainStage):
            declared.extend(
                spec.get("dict_size", 0)
                for spec in stage.filters
                if spec.get("id") in LZMA_DICTIONARY_FILTERS
            )
        elif isinstance(stage, _CodecStage) and stage.codec is Codec.PPMD:
            try:
                declared.append(parse_ppmd_var_h_properties(stage.properties)[1])
            except ValueError:
                declared.append(0)
    return declared


def _zstd_first_frame_window(source: BinaryIO) -> int | None:
    """The window of the zstd frame at the start of a pack view, which is left as found."""
    if not source.seekable():
        return None
    position = source.tell()
    try:
        source.seek(0)
        header = source.read(MAX_FRAME_HEADER_SIZE)
    finally:
        source.seek(position)
    return frame_window_size(header)


def _opens_streams(chain: _Chain) -> bool:
    """Whether opening ``chain`` creates a stream, rather than returning a pack view."""
    return bool(chain.stages) or chain.has_bcj2()


def decode_folder_to_bytes(
    source: BinaryIO,
    folder: SevenZipFolder,
    *,
    compressed_size: int,
    uncompressed_size: int,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> bytes:
    """Fully decode one folder's packed stream into memory and CRC-check it."""
    stream = open_folder_pipeline(
        [SlicingStream(source, 0, compressed_size)],
        folder,
        password=password,
        key_cache=key_cache,
        stream_config=stream_config,
        collector=collector,
    )
    try:
        decoded = read_exact(stream, uncompressed_size)
        if len(decoded) != uncompressed_size:
            raise TruncatedError("7z folder is truncated after decoding")
        if folder.digest_defined and folder.crc is not None:
            if zlib.crc32(decoded) & 0xFFFFFFFF != folder.crc & 0xFFFFFFFF:
                raise CorruptionError("Decoded 7z folder CRC mismatch")
        return decoded
    finally:
        stream.close()


def decode_encoded_header(
    archive_fp: BinaryIO,
    encoded: EncodedHeader,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> bytes:
    """Materialize an ENCODED_HEADER's packed folders to plaintext header bytes."""
    decoded = bytearray()
    claimed = 0
    for (
        folder,
        absolute_offset,
        compressed_size,
        uncompressed_size,
    ) in encoded_folder_slices(encoded):
        # Hostile archives can claim a multi-EiB folder unpack size. Cap the
        # running total before ``read_exact`` / codec buffers allocate
        # (Atheris: raw MemoryError). Per-folder is redundant: unpack sizes
        # are non-negative, so a single folder over the cap fails the total
        # on the same iteration. Two COPY folders at 40 MiB concatenate past
        # the 64 MiB next-header cap (S2-F2) — that is why the total matters.
        claimed += uncompressed_size
        if claimed > MAX_NEXT_HEADER_SIZE:
            raise CorruptionError(
                f"Encoded 7z header unpack size {claimed} exceeds the "
                f"{MAX_NEXT_HEADER_SIZE}-byte parser limit"
            )
        source = SlicingStream(archive_fp, absolute_offset, compressed_size)
        decoded.extend(
            decode_folder_to_bytes(
                source,
                folder,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                password=password,
                key_cache=key_cache,
                stream_config=stream_config,
                collector=collector,
            )
        )
    return bytes(decoded)


def encoded_header_needs_password(encoded: EncodedHeader) -> bool:
    folders = encoded.streams.folders or []
    return any(folder_is_encrypted(folder) for folder in folders)


def unwrap_encoded_header(
    block: HeaderBlock,
    decode: Callable[[EncodedHeader], bytes],
    *,
    max_members: int | None,
) -> tuple[PlainHeader, bool]:
    """Decode at most one encoded-header layer. 7-Zip writes one.

    Returns the plain header and whether that layer used 7zAES.
    """
    header_encrypted = False
    if isinstance(block, EncodedHeader):
        header_encrypted = encoded_header_needs_password(block)
        block = parse_decoded_header(decode(block), max_members=max_members)
    assert isinstance(block, PlainHeader)
    return block, header_encrypted


def parse_decoded_header(decoded: bytes, *, max_members: int | None) -> PlainHeader:
    """Parse the plaintext an encoded-header layer decoded to."""
    block = parse_header_block(decoded, max_members=max_members)
    if isinstance(block, EncodedHeader):
        # A second EncodedHeader is hostile (COPY payload that is itself; O14).
        raise CorruptionError("Encoded 7z header decoded to another encoded header")
    return block


def parse_sevenzip_archive(
    fp: BinaryIO,
    *,
    password: bytes | None = None,
    key_cache: SevenZipKeyCache | None = None,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
    max_members: int | None = _DEFAULT_MAX_MEMBERS,
) -> SevenZipArchive:
    """Parse a 7z archive end-to-end (plain or encoded header).

    Used by fuzz harnesses and tests. The reader uses the same two-phase flow with
    password-candidate prompting instead of a single ``password``.
    Omitting ``max_members`` applies the ``ListingLimits`` default, the same as
    :func:`~archivey.internal.backends.sevenzip_parser.parse_header_block` and the RAR
    parser's archive-level entry points; ``None`` is the explicit UNLIMITED opt-out.
    """
    cache = key_cache if key_cache is not None else SevenZipKeyCache()
    signature = read_signature_and_next_header(fp)
    if not signature.header_data:
        return empty_archive(signature)

    block = parse_header_block(signature.header_data, max_members=max_members)
    block, header_encrypted = unwrap_encoded_header(
        block,
        lambda encoded: decode_encoded_header(
            fp,
            encoded,
            password=password,
            key_cache=cache,
            stream_config=stream_config,
            collector=collector,
        ),
        max_members=max_members,
    )
    # O8: encrypted headers never legitimately decode to zero file records.
    # Without this, ~0.3% of wrong-password py7zr salts slip through as empty.
    if header_encrypted and not block.files:
        raise EncryptionError("Password(s) rejected for the 7z header")
    return materialize_archive(signature, block, is_header_encrypted=header_encrypted)

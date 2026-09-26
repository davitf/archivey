"""Malformed 7z folder structures are refused at parse time, with the right error class.

One hand-built header per finding from the S2 sweep of the native 7z parser (tracked
internally): coder-graph checks, substream accounting, pack-index overruns, a short
next-header read, Delta-only folders, BCJ start offsets. Plus the ``max_members``
keyword contract the RAR parser already follows.
"""

from __future__ import annotations

import ast
import inspect
import io
import lzma
import struct
import subprocess
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import ListingLimits
from archivey.exceptions import (
    CorruptionError,
    ResourceLimitError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends import sevenzip_parser, sevenzip_pipeline
from archivey.internal.backends.sevenzip_parser import (
    MAGIC_7Z,
    PlainHeader,
    SevenZipArchive,
    SevenZipCoder,
    materialize_archive,
    parse_header_block,
    read_signature_and_next_header,
)
from archivey.internal.backends.sevenzip_pipeline import (
    parse_sevenzip_archive,
    plan_folder,
)
from tests.conftest import requires_binary

_COPY = b"\x00"
_DELTA = b"\x03"
_BCJ_X86 = b"\x04"
_BCJ_ARM = b"\x07"
_LZMA2 = b"\x21"

# ---------------------------------------------------------------------------
# Header builder
# ---------------------------------------------------------------------------


def _num(value: int) -> bytes:
    """7z ``NUMBER``: leading 1-bits of the first byte count the extra LE bytes."""
    for extra in range(8):
        if value < 1 << (8 * extra + 7 - extra):
            first = (0xFF << (8 - extra)) & 0xFF | (value >> (8 * extra))
            return bytes([first]) + (value & ((1 << 8 * extra) - 1)).to_bytes(
                extra, "little"
            )
    return b"\xff" + value.to_bytes(8, "little")


def _coder(
    method: bytes,
    *,
    num_in: int | None = None,
    num_out: int | None = None,
    props: bytes | None = None,
) -> bytes:
    flags = len(method)
    tail = b""
    if num_in is not None or num_out is not None:
        flags |= 0x10
        tail += _num(1 if num_in is None else num_in)
        tail += _num(1 if num_out is None else num_out)
    if props is not None:
        flags |= 0x20
        tail += _num(len(props)) + props
    return bytes([flags]) + method + tail


def _folder(
    coders: list[bytes],
    *,
    bind_pairs: Sequence[tuple[int, int]] = (),
    packed: Sequence[int] = (),
) -> bytes:
    out = _num(len(coders)) + b"".join(coders)
    for in_index, out_index in bind_pairs:
        out += _num(in_index) + _num(out_index)
    for index in packed:
        out += _num(index)
    return out


def _linear(coders: list[bytes]) -> bytes:
    """A linear chain: coder i+1 reads coder i's output, coder 0 reads the pack."""
    return _folder(coders, bind_pairs=[(i + 1, i) for i in range(len(coders) - 1)])


def _header(
    *,
    folders: list[bytes],
    coder_unpack_sizes: list[list[int]],
    pack_sizes: list[int] | None,
    names: list[str],
    folder_crcs: list[int] | None = None,
    substreams: bytes | None = None,
) -> bytes:
    streams = b""
    if pack_sizes is not None:
        streams += b"\x06" + _num(0) + _num(len(pack_sizes))
        streams += b"\x09" + b"".join(_num(size) for size in pack_sizes) + b"\x00"
    streams += b"\x07\x0b" + _num(len(folders)) + b"\x00" + b"".join(folders)
    streams += b"\x0c" + b"".join(
        _num(size) for sizes in coder_unpack_sizes for size in sizes
    )
    if folder_crcs is not None:
        streams += b"\x0a\x01" + b"".join(struct.pack("<I", c) for c in folder_crcs)
    streams += b"\x00"
    if substreams is not None:
        streams += b"\x08" + substreams + b"\x00"
    names_blob = b"\x00" + b"".join(n.encode("utf-16le") + b"\x00\x00" for n in names)
    files = b"\x05" + _num(len(names))
    files += b"\x11" + _num(len(names_blob)) + names_blob + b"\x00"
    return b"\x01\x04" + streams + b"\x00" + files + b"\x00"


def _signature(*, next_offset: int, next_size: int, next_crc: int) -> bytes:
    start_header = struct.pack("<QQI", next_offset, next_size, next_crc)
    start_crc = zlib.crc32(start_header) & 0xFFFFFFFF
    return MAGIC_7Z + bytes([0, 4]) + struct.pack("<I", start_crc) + start_header


def _archive(packed: bytes, header: bytes) -> bytes:
    sig = _signature(
        next_offset=len(packed),
        next_size=len(header),
        next_crc=zlib.crc32(header) & 0xFFFFFFFF,
    )
    return sig + packed + header


def _single_folder_archive(
    coders: list[bytes], packed: bytes, payload: bytes, coder_sizes: list[int]
) -> bytes:
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[coder_sizes],
        pack_sizes=[len(packed)],
        names=["payload.bin"],
        folder_crcs=[zlib.crc32(payload) & 0xFFFFFFFF],
    )
    return _archive(packed, header)


def _read_only_member(data: bytes) -> bytes:
    with open_archive(io.BytesIO(data)) as reader:
        (member,) = reader.members()
        with reader.open(member) as stream:
            return stream.read()


def _materialize(header: bytes) -> SevenZipArchive:
    block = parse_header_block(header)
    assert isinstance(block, PlainHeader)
    signature = sevenzip_parser.SignatureInfo(0, 4, header)
    return materialize_archive(signature, block)


# ---------------------------------------------------------------------------
# Coder graph (S2-F4)
# ---------------------------------------------------------------------------


def _one_folder_header(folder: bytes, sizes: list[int]) -> bytes:
    return _header(
        folders=[folder], coder_unpack_sizes=[sizes], pack_sizes=[1], names=["a"]
    )


@pytest.mark.parametrize(
    ("folder", "sizes", "match"),
    [
        pytest.param(_num(0), [], "no coders", id="zero-coders"),
        pytest.param(
            _folder([_coder(_COPY, num_in=1, num_out=0)]),
            [],
            "no coder out-streams",
            id="zero-out-streams",
        ),
        pytest.param(
            # Two out-streams need one bind pair, but there are no in-streams to bind.
            _folder([_coder(_COPY, num_in=0, num_out=2)], bind_pairs=[(0, 0)]),
            [1, 1],
            "only 0 coder in-streams",
            id="more-binds-than-in-streams",
        ),
        pytest.param(
            _folder([_coder(_COPY), _coder(_COPY)], bind_pairs=[(99, 99)]),
            [1, 1],
            "invalid coder bind pair",
            id="bind-out-of-range",
        ),
        pytest.param(
            # Packed index 0 is also the in-stream the bind pair feeds.
            _folder(
                [_coder(_COPY, num_in=2, num_out=1), _coder(_COPY, num_in=1)],
                bind_pairs=[(0, 1)],
                packed=[0, 2],
            ),
            [1, 1],
            "invalid packed-stream index",
            id="packed-index-is-bound",
        ),
        pytest.param(
            _folder([_coder(_COPY, num_in=2, num_out=1)], packed=[0, 5]),
            [1],
            "invalid packed-stream index",
            id="packed-index-out-of-range",
        ),
        pytest.param(
            _folder([_coder(_COPY, num_in=2, num_out=1)], packed=[1, 1]),
            [1],
            "invalid packed-stream index",
            id="packed-index-repeated",
        ),
    ],
)
def test_malformed_coder_graph_is_corruption_at_parse(
    folder: bytes, sizes: list[int], match: str
) -> None:
    with pytest.raises(CorruptionError, match=match):
        parse_header_block(_one_folder_header(folder, sizes))


def test_bind_pair_reusing_an_out_stream_is_corruption() -> None:
    # Three coders, two bind pairs both consuming out-stream 0: out-stream 1 and 2
    # are then both unbound, so the folder has no single output.
    folder = _folder(
        [_coder(_COPY), _coder(_COPY), _coder(_COPY)], bind_pairs=[(1, 0), (2, 0)]
    )
    with pytest.raises(CorruptionError, match="invalid coder bind pair"):
        parse_header_block(_one_folder_header(folder, [1, 1, 1]))


def test_well_formed_linear_chain_still_parses() -> None:
    block = parse_header_block(
        _one_folder_header(_linear([_coder(_COPY), _coder(_COPY)]), [4, 4])
    )
    assert isinstance(block, PlainHeader)
    (folder,) = block.streams.folders or []
    assert folder.bind_pairs == [(1, 0)]
    assert folder.packed_indices == [0]


# ---------------------------------------------------------------------------
# Substream accounting (S2-F3)
# ---------------------------------------------------------------------------


def test_leftover_substream_is_corruption() -> None:
    # One COPY folder of 10 bytes split 3 + 7, but only one file: 7 bytes belong to
    # no member, and the one-file folder would have been reported solid.
    header = _header(
        folders=[_linear([_coder(_COPY)])],
        coder_unpack_sizes=[[10]],
        pack_sizes=[10],
        names=["a"],
        substreams=b"\x0d" + _num(2) + b"\x09" + _num(3),
    )
    with pytest.raises(CorruptionError, match="declares 2 unpack streams .* only 1"):
        _materialize(header)


def test_multi_stream_folder_without_sizes_is_corruption() -> None:
    header = _header(
        folders=[_linear([_coder(_COPY)])],
        coder_unpack_sizes=[[10]],
        pack_sizes=[10],
        names=["a", "b"],
        substreams=b"\x0d" + _num(2),
    )
    with pytest.raises(CorruptionError, match="2 unpack streams but no substream"):
        parse_header_block(header)


def test_zero_stream_folder_is_skipped_when_mapping_files() -> None:
    # Folder 0 declares no unpack streams; the one file lives in folder 1.
    header = _header(
        folders=[_linear([_coder(_COPY)]), _linear([_coder(_COPY)])],
        coder_unpack_sizes=[[4], [6]],
        pack_sizes=[4, 6],
        names=["a"],
        substreams=b"\x0d" + _num(0) + _num(1),
    )
    archive = _materialize(header)
    (record,) = archive.files
    assert record.folder_index == 1
    assert record.uncompressed_size == 6
    assert record.compressed_size == 6


# ---------------------------------------------------------------------------
# Pack-index overrun (S2-F5)
# ---------------------------------------------------------------------------


def test_folder_overrunning_pack_sizes_is_corruption() -> None:
    # UNPACK_INFO with no PACK_INFO: the folder names a pack stream that is not there.
    header = _header(
        folders=[_linear([_coder(_COPY)])],
        coder_unpack_sizes=[[5]],
        pack_sizes=None,
        names=["a"],
    )
    with pytest.raises(CorruptionError, match="missing pack stream"):
        _materialize(header)


# ---------------------------------------------------------------------------
# Short next-header read (S2-F6)
# ---------------------------------------------------------------------------


def test_short_next_header_is_truncation() -> None:
    data = _signature(next_offset=0, next_size=50, next_crc=0) + b"\x01\x04\x06\x00"
    with pytest.raises(TruncatedError, match="expected 50 bytes"):
        read_signature_and_next_header(io.BytesIO(data))


def test_short_signature_header_is_truncation() -> None:
    with pytest.raises(TruncatedError, match="7z signature header"):
        read_signature_and_next_header(io.BytesIO(MAGIC_7Z + b"\x00\x04"))


def test_full_next_header_with_bad_crc_stays_corruption() -> None:
    header = b"\x01\x00"
    data = _signature(next_offset=0, next_size=len(header), next_crc=0) + header
    with pytest.raises(CorruptionError, match="next header CRC mismatch"):
        read_signature_and_next_header(io.BytesIO(data))


# ---------------------------------------------------------------------------
# Filter-only folders (S2-F8) and BCJ start offsets (S2-F7)
# ---------------------------------------------------------------------------


def _delta_encode(data: bytes, dist: int) -> bytes:
    out = bytearray(len(data))
    for i, byte in enumerate(data):
        prev = data[i - dist] if i >= dist else 0
        out[i] = (byte - prev) & 0xFF
    return bytes(out)


def _x86_encode(data: bytes, start_offset: int = 0) -> bytes:
    """liblzma's x86 BCJ encoding of ``data``, with no compression around it."""
    bcj: dict[str, int] = {"id": lzma.FILTER_X86}
    if start_offset:
        bcj["start_offset"] = start_offset
    lzma2 = {"id": lzma.FILTER_LZMA2, "preset": 0}
    packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[bcj, lzma2])
    return lzma.decompress(packed, format=lzma.FORMAT_RAW, filters=[lzma2])


# x86-looking bytes: CALL rel32 opcodes every few bytes, so the filter changes them.
_X86_PAYLOAD = b"".join(
    b"\xe8" + (i * 7919 & 0xFFFFFF).to_bytes(4, "little") + b"\x90\x90\x90"
    for i in range(512)
)


def test_delta_only_folder_reads() -> None:
    payload = bytes(range(256)) * 16
    packed = _delta_encode(payload, 4)
    data = _single_folder_archive(
        [_coder(_DELTA, props=bytes([4 - 1]))], packed, payload, [len(payload)]
    )
    assert _read_only_member(data) == payload


def test_delta_then_bcj_without_lzma_reads() -> None:
    # Decode order: pack → Delta → BCJ. Previously refused as a non-LZMA coder in
    # a BCJ run; each filter-only coder is now its own stage.
    payload = _X86_PAYLOAD
    packed = _delta_encode(_x86_encode(payload), 2)
    data = _single_folder_archive(
        [_coder(_DELTA, props=b"\x01"), _coder(_BCJ_X86)],
        packed,
        payload,
        [len(payload), len(payload)],
    )
    assert _read_only_member(data) == payload


def test_filter_only_run_is_never_planned_as_an_lzma_chain() -> None:
    folder = sevenzip_parser.SevenZipFolder(
        coders=[SevenZipCoder(_DELTA, 1, 1, b"\x00")],
        bind_pairs=[],
        packed_indices=[0],
        unpack_sizes=[8],
        crc=None,
        digest_defined=False,
    )
    (stage,) = plan_folder(folder).stages
    assert isinstance(stage, sevenzip_pipeline._FilterStage)  # noqa: SLF001
    assert stage.lzma_filter == {"id": lzma.FILTER_DELTA, "dist": 1}
    with pytest.raises(UnsupportedFeatureError, match="filter-only coder run"):
        sevenzip_pipeline._lzma_chain_stage(folder.coders, cap_size=None)  # noqa: SLF001


@requires_binary("7z")
def test_7z_cli_delta_copy_archive_reads(tmp_path: Path) -> None:
    payload = b"delta parity with 7-Zip\n" * 64
    (tmp_path / "small.txt").write_bytes(payload)
    archive = tmp_path / "delta.7z"
    subprocess.run(
        ["7z", "a", "-mhc=off", "-m0=Delta:4", "-m1=Copy", str(archive), "small.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert _read_only_member(archive.read_bytes()) == payload


def test_bcj_start_offset_is_honoured_in_a_filter_stage() -> None:
    start = 0x1000
    packed = _x86_encode(_X86_PAYLOAD, start)
    # The offset matters: decoding without it gives different bytes.
    assert _x86_encode(_X86_PAYLOAD) != packed
    data = _single_folder_archive(
        [_coder(_BCJ_X86, props=start.to_bytes(4, "little"))],
        packed,
        _X86_PAYLOAD,
        [len(_X86_PAYLOAD)],
    )
    assert _read_only_member(data) == _X86_PAYLOAD


def test_bcj_start_offset_is_honoured_in_an_lzma2_chain() -> None:
    start = 0x2000
    lzma2 = {"id": lzma.FILTER_LZMA2, "preset": 1}
    packed = lzma.compress(
        _X86_PAYLOAD,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_X86, "start_offset": start}, lzma2],
    )
    lzma2_props = lzma._encode_filter_properties(lzma2)  # noqa: SLF001
    data = _single_folder_archive(
        [
            _coder(_LZMA2, props=lzma2_props),
            _coder(_BCJ_X86, props=start.to_bytes(4, "little")),
        ],
        packed,
        _X86_PAYLOAD,
        [len(_X86_PAYLOAD), len(_X86_PAYLOAD)],
    )
    assert _read_only_member(data) == _X86_PAYLOAD


def _bcj_folder(method: bytes, props: bytes) -> sevenzip_parser.SevenZipFolder:
    return sevenzip_parser.SevenZipFolder(
        coders=[SevenZipCoder(method, 1, 1, props)],
        bind_pairs=[],
        packed_indices=[0],
        unpack_sizes=[16],
        crc=None,
        digest_defined=False,
    )


def test_bcj_properties_of_the_wrong_length_are_corruption() -> None:
    with pytest.raises(CorruptionError, match="Malformed 7z BCJ coder properties"):
        plan_folder(_bcj_folder(_BCJ_X86, b"\x00\x10\x00"))


def test_misaligned_bcj_start_offset_is_refused_at_plan_time() -> None:
    with pytest.raises(UnsupportedFeatureError, match="start offset 3"):
        plan_folder(_bcj_folder(_BCJ_ARM, (3).to_bytes(4, "little")))


def test_zero_bcj_start_offset_needs_no_option() -> None:
    (stage,) = plan_folder(_bcj_folder(_BCJ_X86, bytes(4))).stages
    assert isinstance(stage, sevenzip_pipeline._FilterStage)  # noqa: SLF001
    assert stage.lzma_filter == {"id": lzma.FILTER_X86}


# ---------------------------------------------------------------------------
# Module contracts: detect imports (S2-F12), max_members keyword
# ---------------------------------------------------------------------------


def test_sevenzip_detect_imports_no_private_parser_names() -> None:
    from archivey.internal.backends import sevenzip_detect

    tree = ast.parse(Path(sevenzip_detect.__file__).read_text(encoding="utf-8"))
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and "sevenzip_parser" in node.module
        for alias in node.names
    ]
    assert imported
    assert not [name for name in imported if name.startswith("_")]


@pytest.mark.parametrize(
    "helper",
    [
        "_parse_plain_header",
        "_read_streams_info",
        "_read_unpack_info",
        "_read_substreams_info",
        "_read_files_info",
    ],
)
def test_internal_helpers_require_max_members(helper: str) -> None:
    param = inspect.signature(getattr(sevenzip_parser, helper)).parameters[
        "max_members"
    ]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_archive_entry_point_applies_the_default_listing_limit() -> None:
    # A header large enough to pass the header-size bound, declaring one unpack
    # stream more than the default max_members. The count is refused before any
    # size is read, so the rest of the header is padding.
    count = ListingLimits().max_members + 1
    header = bytes.fromhex("0104070b010001000c0a00080d") + _num(count) + b"\x00" * count
    data = _signature(
        next_offset=0,
        next_size=len(header),
        next_crc=zlib.crc32(header) & 0xFFFFFFFF,
    )
    with pytest.raises(ResourceLimitError, match="max_members"):
        parse_sevenzip_archive(io.BytesIO(data + header))
    with pytest.raises(ResourceLimitError, match="max_members"):
        parse_header_block(header)


@pytest.mark.parametrize("entry", [parse_header_block, parse_sevenzip_archive])
def test_entry_points_default_to_the_listing_limit(
    entry: Callable[..., object],
) -> None:
    param = inspect.signature(entry).parameters["max_members"]
    assert param.default == ListingLimits().max_members
    assert param.default is not None


@pytest.mark.parametrize("helper", ["unwrap_encoded_header", "parse_decoded_header"])
def test_pipeline_helpers_require_max_members(helper: str) -> None:
    param = inspect.signature(getattr(sevenzip_pipeline, helper)).parameters[
        "max_members"
    ]
    assert param.default is inspect.Parameter.empty

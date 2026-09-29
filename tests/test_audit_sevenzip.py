"""Audit reproducers for the native 7z backend.

Each test asserts the promised behaviour for a gap an audit found. Hand-built
archives reuse the header builder style of ``test_sevenzip_parser_hardening``.
"""

from __future__ import annotations

import bz2
import importlib.util
import io
import lzma
import random
import struct
import subprocess
import zlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import AcceleratorMode, ArchiveyConfig, DecoderLimits
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ResourceLimitError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_parser import MAGIC_7Z
from tests.conftest import requires, requires_binary, requires_zstd, zstd_backend

_COPY = b"\x00"
_DELTA = b"\x03"
_BCJ_X86 = b"\x04"
_LZMA2 = b"\x21"
_LZMA = b"\x03\x01\x01"
_PPMD = b"\x03\x04\x01"
_BCJ2 = b"\x03\x03\x01\x1b"
_AES = b"\x06\xf1\x07\x01"
_MIB = 1 << 20


def _num(value: int) -> bytes:
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
    """Coder 0 reads the pack; coder i+1 reads coder i; the last coder is the output."""
    return _folder(coders, bind_pairs=[(i + 1, i) for i in range(len(coders) - 1)])


def _bools(values: Sequence[bool]) -> bytes:
    out = bytearray()
    for i in range(0, len(values), 8):
        byte = 0
        for j, v in enumerate(values[i : i + 8]):
            if v:
                byte |= 0x80 >> j
        out.append(byte)
    return bytes(out)


def _header(
    *,
    folders: list[bytes],
    coder_unpack_sizes: list[list[int]],
    pack_sizes: list[int],
    names: list[str],
    pack_pos: int = 0,
    folder_crcs: list[int | None] | None = None,
    substreams: bytes | None = None,
    extra_file_props: bytes = b"",
) -> bytes:
    streams = b"\x06" + _num(pack_pos) + _num(len(pack_sizes))
    streams += b"\x09" + b"".join(_num(size) for size in pack_sizes) + b"\x00"
    streams += b"\x07\x0b" + _num(len(folders)) + b"\x00" + b"".join(folders)
    streams += b"\x0c" + b"".join(
        _num(size) for sizes in coder_unpack_sizes for size in sizes
    )
    if folder_crcs is not None:
        defined = [c is not None for c in folder_crcs]
        streams += b"\x0a"
        streams += b"\x01" if all(defined) else b"\x00" + _bools(defined)
        streams += b"".join(struct.pack("<I", c) for c in folder_crcs if c is not None)
    streams += b"\x00"
    if substreams is not None:
        streams += b"\x08" + substreams + b"\x00"
    names_blob = b"\x00" + b"".join(n.encode("utf-16le") + b"\x00\x00" for n in names)
    files = b"\x05" + _num(len(names))
    files += extra_file_props
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


def _crc(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _bio(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


def _read_only_member(data: bytes, **kwargs: object) -> bytes:
    with open_archive(_bio(data), **kwargs) as reader:  # type: ignore[arg-type]
        (member,) = reader.members()
        with reader.open(member) as stream:
            return stream.read()


def _lzma2(data: bytes, dict_size: int = 1 << 16) -> bytes:
    return lzma.compress(
        data,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}],
    )


def _filter_encode(data: bytes, lzma_filter: dict[str, int]) -> bytes:
    """``data`` through one liblzma filter's encoder, with no compression around it."""
    lzma2 = {"id": lzma.FILTER_LZMA2, "preset": 0}
    packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[lzma_filter, lzma2])
    return lzma.decompress(packed, format=lzma.FORMAT_RAW, filters=[lzma2])


# ---------------------------------------------------------------------------
# Deep coder graphs escape as RecursionError
# ---------------------------------------------------------------------------


def test_long_filter_chain_does_not_escape_as_recursion_error() -> None:
    # Each filter-only coder becomes one nested FilterStream, so read() recurses per
    # coder. The parser refuses more than 64 coders per folder, as 7-Zip does.
    payload = bytes(100)  # Delta over zeros is zeros, so every layer is valid.
    count = 300
    header = _header(
        folders=[_linear([_coder(_DELTA) for _ in range(count)])],
        coder_unpack_sizes=[[len(payload)] * count],
        pack_sizes=[len(payload)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    data = _archive(payload, header)
    try:
        assert _read_only_member(data) == payload
    except ArchiveyError:
        pass  # refusing the graph is fine; a non-ArchiveyError is not


def test_nested_bcj2_graph_does_not_escape_as_recursion_error() -> None:
    # BCJ2 coder k's main input is bound to BCJ2 coder k+1's output; every other input
    # is a pack stream. A well-formed tree, 400 deep.
    depth = 400
    coders = [_coder(_BCJ2, num_in=4, num_out=1) for _ in range(depth)]
    bound = {4 * k for k in range(depth - 1)}
    binds = [(4 * k, k + 1) for k in range(depth - 1)]
    packed = [i for i in range(4 * depth) if i not in bound]
    header = _header(
        folders=[_folder(coders, bind_pairs=binds, packed=packed)],
        coder_unpack_sizes=[[1] * depth],
        pack_sizes=[1] * len(packed),
        names=["a"],
    )
    data = _archive(bytes(len(packed)), header)
    try:
        _read_only_member(data)
    except ArchiveyError:
        pass


def _delta_chain_archive(count: int) -> tuple[bytes, bytes]:
    payload = bytes(100)  # Delta over zeros is zeros, so every layer is valid.
    header = _header(
        folders=[_linear([_coder(_DELTA) for _ in range(count)])],
        coder_unpack_sizes=[[len(payload)] * count],
        pack_sizes=[len(payload)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    return _archive(payload, header), payload


def test_folder_at_the_7zip_coder_limit_reads() -> None:
    data, payload = _delta_chain_archive(64)
    assert _read_only_member(data) == payload


def test_folder_past_the_7zip_coder_limit_is_unsupported() -> None:
    data, _ = _delta_chain_archive(65)
    with pytest.raises(UnsupportedFeatureError, match="coder count 65"):
        _read_only_member(data)


# ---------------------------------------------------------------------------
# Decoder-memory sum is only checked for BCJ2 folders
# ---------------------------------------------------------------------------


def test_linear_chain_decoder_memory_counts_together() -> None:
    # pack -> LZMA2 (1 MiB dict) -> Copy -> LZMA2 (1 MiB dict) -> output. Copy splits
    # the LZMA run, so two liblzma decoders are alive at once, as with BCJ2 branches.
    payload = b"hello world " * 100
    inner = _lzma2(payload, _MIB)
    outer = _lzma2(inner, _MIB)
    dict_1mib = bytes([16])  # LZMA2 property byte 16 -> 1 MiB dictionary
    coders = [
        _coder(_LZMA2, props=dict_1mib),
        _coder(_COPY),
        _coder(_LZMA2, props=dict_1mib),
    ]
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[[len(inner), len(inner), len(payload)]],
        pack_sizes=[len(outer)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    data = _archive(outer, header)
    # Sanity: the archive is valid when the cap admits both dictionaries.
    roomy = ArchiveyConfig(decoder_limits=DecoderLimits(max_decoder_memory=2 * _MIB))
    assert _read_only_member(data, config=roomy) == payload
    # Each dictionary fits 1.5 MiB; together they do not.
    tight = ArchiveyConfig(
        decoder_limits=DecoderLimits(max_decoder_memory=_MIB + _MIB // 2)
    )
    with pytest.raises(ResourceLimitError):
        _read_only_member(data, config=tight)


# ---------------------------------------------------------------------------
# LZMA2 followed by a filter in decode order (7-Zip writes these)
# ---------------------------------------------------------------------------


def _lzma2_then(kind: str, payload: bytes) -> tuple[list[bytes], list[int], bytes]:
    compressed = _lzma2(payload)
    if kind == "delta":  # 7z a -m0=LZMA2 -m1=Delta:4
        packed = _filter_encode(compressed, {"id": lzma.FILTER_DELTA, "dist": 4})
        first = _coder(_DELTA, props=bytes([4 - 1]))
    elif kind == "bcj":  # 7z a -m0=LZMA2 -m1=BCJ
        packed = _filter_encode(compressed, {"id": lzma.FILTER_X86})
        first = _coder(_BCJ_X86)
    else:  # 7z a -m0=LZMA2 -m1=LZMA2
        packed = _lzma2(compressed)
        first = _coder(_LZMA2, props=b"\x00")
    coders = [first, _coder(_LZMA2, props=b"\x00")]
    return coders, [len(compressed), len(payload)], packed


@pytest.mark.parametrize("kind", ["delta", "bcj", "lzma2"])
def test_lzma2_after_another_lzma_family_coder_reads(kind: str) -> None:
    payload = b"".join(b"\xe8" + i.to_bytes(4, "little") for i in range(500))
    coders, sizes, packed = _lzma2_then(kind, payload)
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[sizes],
        pack_sizes=[len(packed)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    assert _read_only_member(_archive(packed, header)) == payload


def test_lzma1_after_a_delta_coder_reads() -> None:
    # pack -> Delta -> LZMA1 -> output (7z a -m0=LZMA -m1=Delta:4). Delta decodes
    # before LZMA1, so the two cannot share one liblzma chain either.
    payload = b"".join(i.to_bytes(4, "little") for i in range(500))
    lzma1 = {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}
    compressed = lzma.compress(payload, format=lzma.FORMAT_RAW, filters=[lzma1])
    packed = _filter_encode(compressed, {"id": lzma.FILTER_DELTA, "dist": 4})
    # LZMA1 properties: lc=3, lp=0, pb=2 (93), then a 64 KiB dictionary.
    lzma1_props = bytes([93]) + (1 << 16).to_bytes(4, "little")
    coders = [_coder(_DELTA, props=bytes([4 - 1])), _coder(_LZMA, props=lzma1_props)]
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[[len(compressed), len(payload)]],
        pack_sizes=[len(packed)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    assert _read_only_member(_archive(packed, header)) == payload


# ---------------------------------------------------------------------------
# Password confirmation trusts a rejecting codec that never sees the decrypted bytes
# ---------------------------------------------------------------------------


def _aes_encrypt(payload: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from archivey.internal.backends.sevenzip_aes import derive_sevenzip_aes_key

    key = derive_sevenzip_aes_key(password.encode("utf-16le"), salt=b"", cycles=0)
    padded = payload + bytes(-len(payload) % 16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


@requires("cryptography")
def test_rejecting_codec_upstream_of_aes_does_not_settle_the_password() -> None:
    # pack -> LZMA2 -> AES -> output: LZMA2 decodes the same bytes whatever the key,
    # so it cannot reject a wrong one. The member CRC sits past the 64 KiB prefix.
    payload = random.Random(0).randbytes(100_000)
    ciphertext = _aes_encrypt(payload, "right")
    packed = _lzma2(ciphertext, _MIB)
    # AES props: one byte, NumCyclesPower 0, no salt, zero IV.
    coders = [_coder(_LZMA2, props=bytes([16])), _coder(_AES, props=b"\x00")]
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[[len(ciphertext), len(payload)]],
        pack_sizes=[len(packed)],
        names=["a"],
        folder_crcs=[_crc(payload)],
    )
    data = _archive(packed, header)
    assert _read_only_member(data, password="right") == payload
    assert _read_only_member(data, password=["wrong", "right"]) == payload


# ---------------------------------------------------------------------------
# raw_name is not the stored name
# ---------------------------------------------------------------------------


def test_raw_name_keeps_stored_backslash() -> None:
    payload = b"x"
    header = _header(
        folders=[_linear([_coder(_COPY)])],
        coder_unpack_sizes=[[1]],
        pack_sizes=[1],
        names=["dir\\file.txt"],
        folder_crcs=[_crc(payload)],
    )
    with open_archive(_bio(_archive(payload, header))) as reader:
        (member,) = reader.members()
    assert member.name == "dir/file.txt"
    assert member.raw_name == "dir\\file.txt".encode("utf-16le")


# ---------------------------------------------------------------------------
# A folder that decodes past its declared unpack size is accepted silently
# ---------------------------------------------------------------------------


def test_folder_decoding_past_its_unpack_size_is_corruption() -> None:
    payload = b"hello world " * 100
    packed = _lzma2(payload)  # decodes to 1200 bytes
    declared = payload[:500]  # the header claims 500, with a CRC over those 500
    header = _header(
        folders=[_linear([_coder(_LZMA2, props=b"\x10")])],
        coder_unpack_sizes=[[len(declared)]],
        pack_sizes=[len(packed)],
        names=["a"],
        folder_crcs=[_crc(declared)],
    )
    with pytest.raises(CorruptionError):
        _read_only_member(_archive(packed, header))


# The same, for the other codecs with an end of stream: Deflate, Deflate64, BZip2,
# Zstd, LZ4 and Brotli. LZMA1 and PPMd have no end mark and are capped at their size.

_DEFLATE = b"\x04\x01\x08"
_DEFLATE64 = b"\x04\x01\x09"
_BZIP2 = b"\x04\x02\x02"
_ZSTD = b"\x04\xf7\x11\x01"
_BROTLI = b"\x04\xf7\x11\x02"
_LZ4 = b"\x04\xf7\x11\x04"

_CODEC_METHODS = {
    "deflate": _DEFLATE,
    "deflate64": _DEFLATE64,
    "bzip2": _BZIP2,
    "zstd": _ZSTD,
    "brotli": _BROTLI,
    "lz4": _LZ4,
}

_CODECS = [
    pytest.param("deflate", id="deflate"),
    pytest.param("deflate64", id="deflate64", marks=requires("inflate64")),
    pytest.param("bzip2", id="bzip2"),
    pytest.param("zstd", id="zstd", marks=requires_zstd()),
    pytest.param("lz4", id="lz4", marks=requires("lz4")),
    pytest.param("brotli", id="brotli", marks=requires("brotli")),
]
# The codecs whose decoders read concatenated streams (frames) as one.
_MULTI_STREAM_CODECS = [
    pytest.param("bzip2", id="bzip2"),
    pytest.param("zstd", id="zstd", marks=requires_zstd()),
    pytest.param("lz4", id="lz4", marks=requires("lz4")),
]


def _compress(codec: str, data: bytes) -> bytes:
    """``data`` as one stream of ``codec``, in the form a 7z coder stores it."""
    if codec == "deflate":
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        return compressor.compress(data) + compressor.flush()
    if codec == "deflate64":
        import inflate64

        deflater = inflate64.Deflater()
        return deflater.deflate(data) + deflater.flush()
    if codec == "bzip2":
        return bz2.compress(data)
    if codec == "zstd":
        return zstd_backend().compress(data)
    if codec == "lz4":
        import lz4.frame

        return lz4.frame.compress(data)
    import brotli

    return brotli.compress(data)


def _text(size: int, seed: int = 0) -> bytes:
    return bytes(random.Random(seed).choices(b"abcdefgh \n", k=size))


def _codec_archive(
    coders: list[bytes], sizes: list[int], packed: bytes, declared: bytes
) -> bytes:
    """One member over one linear folder, with the folder CRC over ``declared``."""
    header = _header(
        folders=[_linear(coders)],
        coder_unpack_sizes=[sizes],
        pack_sizes=[len(packed)],
        names=["a"],
        folder_crcs=[_crc(declared)],
    )
    return _archive(packed, header)


@pytest.mark.parametrize("short_by", [1, 3000])
@pytest.mark.parametrize("codec", _CODECS)
def test_codec_decoding_past_its_unpack_size_is_corruption(
    codec: str, short_by: int
) -> None:
    payload = _text(4000)
    packed = _compress(codec, payload)
    declared = payload[:-short_by]
    coder = _coder(_CODEC_METHODS[codec])
    data = _codec_archive([coder], [len(declared)], packed, declared)
    with pytest.raises(CorruptionError, match="past its declared unpack size"):
        _read_only_member(data)


@pytest.mark.parametrize("codec", _CODECS)
def test_codec_at_its_unpack_size_reads(codec: str) -> None:
    payload = _text(4000)
    packed = _compress(codec, payload)
    coder = _coder(_CODEC_METHODS[codec])
    data = _codec_archive([coder], [len(payload)], packed, payload)
    assert _read_only_member(data) == payload


_ACCELERATED = [
    pytest.param("deflate", ArchiveyConfig(use_rapidgzip=AcceleratorMode.ON)),
    pytest.param("bzip2", ArchiveyConfig(use_indexed_bzip2=AcceleratorMode.ON)),
]


@requires("rapidgzip")
@pytest.mark.parametrize(("codec", "config"), _ACCELERATED)
def test_accelerated_codec_decoding_past_its_unpack_size_is_corruption(
    codec: str, config: ArchiveyConfig
) -> None:
    payload = _text(4000)
    packed = _compress(codec, payload)
    coder = _coder(_CODEC_METHODS[codec])
    exact = _codec_archive([coder], [len(payload)], packed, payload)
    assert _read_only_member(exact, config=config) == payload
    declared = payload[:-1]
    data = _codec_archive([coder], [len(declared)], packed, declared)
    with pytest.raises(CorruptionError, match="past its declared unpack size"):
        _read_only_member(data, config=config)


def _padded_payload(codec: str, base: int = 4000) -> tuple[bytes, bytes]:
    """A payload whose packed form is not a whole number of AES blocks, and that form."""
    for size in range(base, base + 100):
        payload = _text(size)
        packed = _compress(codec, payload)
        if len(packed) % 16:
            return payload, packed
    raise AssertionError("no payload size leaves an AES pad")


def _surplus_in_chain(exc: BaseException) -> bool:
    """Whether ``exc`` is, or was raised from, the past-size ``CorruptionError``."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, CorruptionError) and "past its declared unpack size" in str(
            seen
        ):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


@requires("cryptography")
@pytest.mark.parametrize("codec", _CODECS)
def test_codec_behind_aes_reads_with_the_aes_padding(codec: str) -> None:
    # pack -> AES -> codec -> output. AES decrypts whole blocks, so the codec's input
    # ends in pad bytes past the AES coder's unpack size. They are not codec output.
    payload, packed = _padded_payload(codec)
    ciphertext = _aes_encrypt(packed, "pw")
    coders = [_coder(_AES, props=b"\x00"), _coder(_CODEC_METHODS[codec])]
    data = _codec_archive(coders, [len(packed), len(payload)], ciphertext, payload)
    assert _read_only_member(data, password="pw") == payload


@requires("cryptography")
@pytest.mark.parametrize("base", [4000, 100_000], ids=["small", "large"])
@pytest.mark.parametrize("codec", _CODECS)
def test_codec_behind_aes_decoding_past_its_unpack_size_is_corruption(
    codec: str, base: int
) -> None:
    payload, packed = _padded_payload(codec, base)
    ciphertext = _aes_encrypt(packed, "pw")
    declared = payload[:-1]
    coders = [_coder(_AES, props=b"\x00"), _coder(_CODEC_METHODS[codec])]
    data = _codec_archive(coders, [len(packed), len(declared)], ciphertext, declared)
    # When the password check decodes the whole folder, it meets the surplus first
    # and reports the folder as "wrong password or corrupt", as for any other damage
    # it meets; the surplus error is its cause.
    with pytest.raises(ArchiveyError) as caught:
        _read_only_member(data, password="pw")
    assert _surplus_in_chain(caught.value)


@pytest.mark.parametrize("codec", _MULTI_STREAM_CODECS)
def test_codec_streams_count_together_against_the_unpack_size(codec: str) -> None:
    # Two concatenated streams in one coder. Their output together is the coder's
    # output, so the second stream is data when the size counts it and surplus when
    # the size stops at the first.
    first, second = _text(3000, seed=1), _text(2000, seed=2)
    packed = _compress(codec, first) + _compress(codec, second)
    coder = _coder(_CODEC_METHODS[codec])
    whole = first + second
    exact = _codec_archive([coder], [len(whole)], packed, whole)
    assert _read_only_member(exact) == whole
    data = _codec_archive([coder], [len(first)], packed, first)
    with pytest.raises(CorruptionError, match="past its declared unpack size"):
        _read_only_member(data)


@pytest.mark.parametrize("kind", ["delta", "bcj"])
@pytest.mark.parametrize("codec", _CODECS)
def test_codec_then_filter_decoding_past_its_unpack_size_is_corruption(
    codec: str, kind: str
) -> None:
    # pack -> codec -> filter -> output (7z a -m0=Delta:4 -m1=Deflate, or BCJ). The
    # filter keeps the length, so both coders declare the same size.
    payload = b"".join(b"\xe8" + i.to_bytes(4, "little") for i in range(800))
    if kind == "delta":
        filtered = _filter_encode(payload, {"id": lzma.FILTER_DELTA, "dist": 4})
        second = _coder(_DELTA, props=bytes([4 - 1]))
    else:
        filtered = _filter_encode(payload, {"id": lzma.FILTER_X86})
        second = _coder(_BCJ_X86)
    packed = _compress(codec, filtered)
    coders = [_coder(_CODEC_METHODS[codec]), second]
    exact = _codec_archive(coders, [len(payload)] * 2, packed, payload)
    assert _read_only_member(exact) == payload
    declared = payload[:-5]
    data = _codec_archive(coders, [len(declared)] * 2, packed, declared)
    with pytest.raises(CorruptionError, match="past its declared unpack size"):
        _read_only_member(data)


@requires_binary("7z")
@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
@pytest.mark.parametrize("method", ["Deflate", "Deflate64", "BZip2"])
def test_7zip_codec_archives_read_clean(
    tmp_path: Path, method: str, encrypted: bool
) -> None:
    if method == "Deflate64" and importlib.util.find_spec("inflate64") is None:
        pytest.skip("requires optional package(s): inflate64")
    if encrypted and importlib.util.find_spec("cryptography") is None:
        pytest.skip("requires optional package(s): cryptography")
    contents = {f"f{i}.txt": _text(3000 + 7 * i, seed=i) for i in range(5)}
    for name, content in contents.items():
        (tmp_path / name).write_bytes(content)
    archive = tmp_path / "a.7z"
    options = ["-mhe=on", "-psecret"] if encrypted else []
    subprocess.run(
        ["7z", "a", "-t7z", f"-m0={method}", *options, str(archive), *contents],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    with open_archive(archive, password="secret" if encrypted else None) as reader:
        read = {}
        for member in reader.members():
            with reader.open(member) as stream:
                read[member.name] = stream.read()
    assert read == contents


# ---------------------------------------------------------------------------
# Unknown FILES_INFO properties refuse the whole archive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("property_id", [0x1A, 0x30])
def test_unknown_files_info_property_is_skipped(property_id: int) -> None:
    payload = b"data"
    unknown = bytes([property_id]) + _num(3) + b"\x00\x01\x02"
    header = _header(
        folders=[_linear([_coder(_COPY)])],
        coder_unpack_sizes=[[len(payload)]],
        pack_sizes=[len(payload)],
        names=["a"],
        folder_crcs=[_crc(payload)],
        extra_file_props=unknown,
    )
    assert _read_only_member(_archive(payload, header)) == payload

"""ZIP member decode through the shared codec layer (extended methods).

Covers Deflate64 / Zstd / PPMd ZIP members that stdlib ``zipfile`` cannot decode,
plus missing-backend → ``PackageNotInstalledError`` and corrupt-body → ``CorruptionError``.
"""

from __future__ import annotations

import io
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import AcceleratorMode, ArchiveyConfig, DecoderLimits
from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    UnsupportedFeatureError,
)
from archivey.internal.streams import codecs as codecs_module
from archivey.types import CompressionAlgorithm
from tests.conftest import requires, requires_binary, requires_zstd, zstd_backend
from tests.zipcrypto import build_zipcrypto_zip

_PAYLOAD = (b"zip-native-codec-payload\n" * 80) + bytes(range(256))


def _build_minimal_zip(
    name: bytes,
    compressed: bytes,
    uncompressed: bytes,
    method: int,
) -> bytes:
    """Hand-build a single-entry ZIP with an arbitrary compression method id."""
    crc = zlib.crc32(uncompressed) & 0xFFFFFFFF
    name_len = len(name)
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        method,
        0,
        0,
        crc,
        len(compressed),
        len(uncompressed),
        name_len,
        0,
    )
    local += name + compressed
    cd = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        method,
        0,
        0,
        crc,
        len(compressed),
        len(uncompressed),
        name_len,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    cd += name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(local), 0)
    return local + cd + eocd


def _7z_zip(tmp_path: Path, method: str, payload: bytes) -> Path:
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    archive = tmp_path / f"{method.lower()}.zip"
    result = subprocess.run(
        ["7z", "a", "-tzip", f"-mm={method}", str(archive), src.name, "-y"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not archive.is_file():
        pytest.skip(f"7z CLI cannot write ZIP {method} fixture: {result.stderr}")
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
        if method == "Deflate64" and info.compress_type != 9:
            pytest.skip(
                f"7z wrote ZIP method {info.compress_type} instead of Deflate64 (9)"
            )
        if method == "PPMd" and info.compress_type != 98:
            pytest.skip(
                f"7z wrote ZIP method {info.compress_type} instead of PPMd (98)"
            )
    return archive


@requires_binary("7z")
@requires("inflate64")
def test_zip_deflate64_roundtrip(tmp_path: Path) -> None:
    archive = _7z_zip(tmp_path, "Deflate64", _PAYLOAD)
    with open_archive(archive) as ar:
        members = ar.members()
        assert len(members) == 1
        assert members[0].compression[0].algo is CompressionAlgorithm.DEFLATE64
        assert ar.read(members[0]) == _PAYLOAD


@requires_binary("7z")
@requires("pyppmd")
def test_zip_ppmd_roundtrip(tmp_path: Path) -> None:
    archive = _7z_zip(tmp_path, "PPMd", _PAYLOAD)
    with open_archive(archive) as ar:
        members = ar.members()
        assert len(members) == 1
        assert members[0].compression[0].algo is CompressionAlgorithm.PPMD
        assert ar.read(members[0]) == _PAYLOAD


@requires_zstd()
def test_zip_zstd_handbuilt_roundtrip() -> None:
    zstd = zstd_backend()
    compressed = zstd.compress(_PAYLOAD)
    data = _build_minimal_zip(b"zstd.txt", compressed, _PAYLOAD, 93)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        assert member.compression[0].algo is CompressionAlgorithm.ZSTD
        assert ar.read(member) == _PAYLOAD


@requires_binary("7z")
def test_zip_deflate64_without_inflate64_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _7z_zip(tmp_path, "Deflate64", _PAYLOAD)
    monkeypatch.setattr(
        codecs_module.deps,
        "inflate64",
        codecs_module.deps.LazyOptional("inflate64", present=False),
    )
    with open_archive(archive) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError, match="inflate64"):
            ar.read(member)


@requires_binary("7z")
def test_zip_ppmd_without_pyppmd_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _7z_zip(tmp_path, "PPMd", _PAYLOAD)
    monkeypatch.setattr(
        codecs_module.deps,
        "pyppmd",
        codecs_module.deps.LazyOptional("pyppmd", present=False),
    )
    with open_archive(archive) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError, match="pyppmd"):
            ar.read(member)


def test_zip_zstd_without_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hand-built fixture so the test does not need a zstd writer; only the reader backend
    # presence is gated.
    compressed = b"not-real-zstd"  # never decoded — open fails on missing backend first
    data = _build_minimal_zip(b"z.txt", compressed, b"x" * 10, 93)
    monkeypatch.setattr(codecs_module.deps, "zstd", None)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError):
            ar.read(member)


def test_zip_corrupt_deflate_body_raises_corruption() -> None:
    # Flip bits in a real DEFLATE bitstream so inflate fails loudly and surfaces as
    # CorruptionError / TruncatedError via the shared codec translator (not a raw zlib.error).
    good = zlib.compress(_PAYLOAD)[2:-4]  # raw deflate (strip zlib wrapper)
    corrupt = bytearray(good)
    mid = len(corrupt) // 2
    corrupt[mid] ^= 0xFF
    corrupt[mid + 1] ^= 0xFF
    data = _build_minimal_zip(b"bad.txt", bytes(corrupt), _PAYLOAD, 8)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(CorruptionError):
            ar.read(member)


def test_zip_unknown_method_raises_unsupported() -> None:
    data = _build_minimal_zip(b"x.bin", b"raw", b"raw", method=97)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match="compression method 97") as e:
            ar.read(member)
    # Nothing checksums the method field, so the message names the other cause.
    assert "a damaged header reads the same way" in str(e.value)


def test_zip_method_99_without_aes_extra_raises() -> None:
    # Method 99 with no 0x9901 extra is not a valid AE member.
    data = _build_minimal_zip(b"x.bin", b"raw", b"raw", method=99)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match="0x9901"):
            ar.read(member)


def test_zip_stdlib_methods_still_roundtrip(tmp_path: Path) -> None:
    archive = tmp_path / "stdlib.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("stored.txt", _PAYLOAD, compress_type=zipfile.ZIP_STORED)
        zf.writestr("deflated.txt", _PAYLOAD, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("bzip2.txt", _PAYLOAD, compress_type=zipfile.ZIP_BZIP2)
        zf.writestr("lzma.txt", _PAYLOAD, compress_type=zipfile.ZIP_LZMA)
    with open_archive(archive) as ar:
        by_name = {m.name: m for m in ar.members()}
        for name in ("stored.txt", "deflated.txt", "bzip2.txt", "lzma.txt"):
            assert ar.read(by_name[name]) == _PAYLOAD


def test_zip_concurrent_codec_path_interleaved(tmp_path: Path) -> None:
    archive = tmp_path / "concurrent.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", b"aaaa" * 1000, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("b.txt", b"bbbb" * 1000, compress_type=zipfile.ZIP_DEFLATED)
    with open_archive(archive, concurrent_members=True) as ar:
        s1 = ar.open("a.txt")
        s2 = ar.open("b.txt")
        assert s1.read(4) == b"aaaa"
        assert s2.read(4) == b"bbbb"
        assert s1.read() == b"aaaa" * 999
        assert s2.read() == b"bbbb" * 999
        s1.close()
        s2.close()


# LZMA1 properties lc=8, lp=0, pb=2 and a 64 KiB dictionary: what ``7z a -tzip
# -mm=LZMA:lc=8`` writes. 7-Zip reads it; liblzma decodes lc + lp up to 4 only.
_LZMA_LC8_PROPS = bytes([8 + 9 * (0 + 5 * 2)]) + (1 << 16).to_bytes(4, "little")


def _zip_lzma_body(props: bytes) -> bytes:
    # ZIP method 14: version (2), properties size (2), properties, then the stream.
    return b"\x09\x14" + struct.pack("<H", len(props)) + props + b"\x00" * 16


def test_zip_lzma_lc_lp_over_four_is_unsupported() -> None:
    """format-zip: an LZMA member liblzma cannot decode is unsupported, not corrupt,
    as for a 7z LZMA coder; the same shared check decides both."""
    data = _build_minimal_zip(
        b"lc8.txt", _zip_lzma_body(_LZMA_LC8_PROPS), _PAYLOAD, method=14
    )
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match=r"lc=8, lp=0"):
            ar.read(member)


def test_zip_lzma_out_of_range_properties_stay_corrupt() -> None:
    # 225 and above is not an lc/lp/pb byte at all.
    props = bytes([225]) + (1 << 16).to_bytes(4, "little")
    data = _build_minimal_zip(b"bad.txt", _zip_lzma_body(props), _PAYLOAD, method=14)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(CorruptionError):
            ar.read(member)


@requires_binary("7z")
def test_zip_lzma_lc8_written_by_7zip_is_unsupported(tmp_path: Path) -> None:
    archive = _7z_zip(tmp_path, "LZMA:lc=8", _PAYLOAD)
    with zipfile.ZipFile(archive) as zf:
        if zf.infolist()[0].compress_type != zipfile.ZIP_LZMA:
            pytest.skip("7z did not write an LZMA member")
    with open_archive(archive) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match=r"lc=8"):
            ar.read(member)


def test_zip_lzma_lc8_under_zipcrypto_is_a_candidate_failure() -> None:
    """Under ZipCrypto the properties are decrypted with a key only one byte vouched
    for, so lc + lp over 4 may be a wrong key's garbage: it counts as the candidate
    failing (the password-or-damage error), not as an unsupported member."""
    data = build_zipcrypto_zip(
        b"pw",
        b"lc8.txt",
        _PAYLOAD,
        compression=zipfile.ZIP_LZMA,
        compressed=_zip_lzma_body(_LZMA_LC8_PROPS),
    )
    with open_archive(io.BytesIO(data), password="pw") as ar:
        (member,) = ar.members()
        with pytest.raises(EncryptionError) as excinfo:
            ar.read(member)
    assert "lc=8" in str(excinfo.value.__cause__)


def _zip_ppmd_header(restore: int) -> bytes:
    # order 6, 16 MiB, then the restore method in the top four bits.
    return struct.pack("<H", (6 - 1) | ((16 - 1) << 4) | (restore << 12))


def test_zip_ppmd_restore_method_2_is_unsupported() -> None:
    """7-Zip: "Unsupported Method" for restore method 2 (PPMd8 freeze, which it
    builds without)."""
    body = _zip_ppmd_header(2) + b"\x00" * 16
    data = _build_minimal_zip(b"p.txt", body, _PAYLOAD, method=98)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match="restore method 2"):
            ar.read(member)


@pytest.mark.parametrize("restore", [3, 15])
def test_zip_ppmd_restore_method_above_2_is_corrupt(restore: int) -> None:
    """7-Zip: "Data Error" for a restore method above 2."""
    body = _zip_ppmd_header(restore) + b"\x00" * 16
    data = _build_minimal_zip(b"p.txt", body, _PAYLOAD, method=98)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(CorruptionError, match=f"restore method {restore}"):
            ar.read(member)


# DR-3: bytes inside a member's compressed size after the codec's end of stream are
# hidden data, so the member is CorruptionError. 7-Zip reports them as an error ("There
# are some data after the end of the payload data", or "Data Error" for PPMd), a zero
# byte too. LZMA (method 14) is pinned in test_audit2_zip.py and test_surplus_input.py.

_TAILS = [
    pytest.param(b"\x00", id="zero"),
    pytest.param(b"\x00" * 8, id="zeros"),
    pytest.param(b"JUNKJUNK", id="junk"),
]


def _member_payload(archive: Path) -> bytes:
    """The compressed data of the only member of ``archive``, as stored."""
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
    data = archive.read_bytes()
    name_len, extra_len = struct.unpack_from("<HH", data, info.header_offset + 26)
    start = info.header_offset + 30 + name_len + extra_len
    return data[start : start + info.compress_size]


def _with_tail(archive: Path, method: int, tail: bytes) -> bytes:
    """``archive``'s member rebuilt with ``tail`` after its stream, inside its size."""
    return _build_minimal_zip(
        b"payload.bin", _member_payload(archive) + tail, _PAYLOAD, method
    )


def _7z_test_fails(tmp_path: Path, data: bytes) -> bool:
    path = tmp_path / "tail.zip"
    path.write_bytes(data)
    result = subprocess.run(
        ["7z", "t", str(path)], check=False, capture_output=True, text=True
    )
    return result.returncode != 0


def _read_whole(data: bytes, chunk: int | None) -> bytes:
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        if chunk is None:
            return ar.read(member)
        with ar.open(member) as stream:
            return b"".join(iter(lambda: stream.read(chunk), b""))


_7Z_METHODS = [
    pytest.param("Deflate", 8, marks=(), id="deflate"),
    pytest.param("Deflate64", 9, marks=requires("inflate64"), id="deflate64"),
    pytest.param("BZip2", 12, marks=(), id="bzip2"),
    pytest.param("PPMd", 98, marks=requires("pyppmd"), id="ppmd"),
]


@requires_binary("7z")
@pytest.mark.parametrize(("method_name", "method"), _7Z_METHODS)
@pytest.mark.parametrize("tail", _TAILS)
@pytest.mark.parametrize("chunk", [None, 7], ids=["whole", "chunked"])
def test_zip_member_with_input_after_its_stream_is_corrupt(
    tmp_path: Path, method_name: str, method: int, tail: bytes, chunk: int | None
) -> None:
    archive = _7z_zip(tmp_path, method_name, _PAYLOAD)
    clean = _with_tail(archive, method, b"")
    assert _read_whole(clean, chunk) == _PAYLOAD
    data = _with_tail(archive, method, tail)
    assert _7z_test_fails(tmp_path, data)
    with pytest.raises(CorruptionError, match="left after its end"):
        _read_whole(data, chunk)


@requires_zstd()
@pytest.mark.parametrize("tail", _TAILS)
def test_zip_zstd_member_with_input_after_its_frame_is_corrupt(tail: bytes) -> None:
    # 7-Zip 23 writes no zstd ZIP member, so this one is built here.
    compressed = zstd_backend().compress(_PAYLOAD)
    clean = _build_minimal_zip(b"z.bin", compressed, _PAYLOAD, 93)
    assert _read_whole(clean, None) == _PAYLOAD
    data = _build_minimal_zip(b"z.bin", compressed + tail, _PAYLOAD, 93)
    with pytest.raises(CorruptionError, match="left after its end"):
        _read_whole(data, None)


@requires_zstd()
@pytest.mark.parametrize("chunk", [None, 7], ids=["whole", "chunked"])
def test_zip_zstd_member_with_a_second_frame_is_corrupt(chunk: int | None) -> None:
    # A second frame is input after the first frame's end, though zstd reads it as more
    # content. The declared size and CRC cover both frames' output.
    zstd = zstd_backend()
    second = b"second frame\n"
    compressed = zstd.compress(_PAYLOAD) + zstd.compress(second)
    data = _build_minimal_zip(b"z.bin", compressed, _PAYLOAD + second, 93)
    with pytest.raises(CorruptionError, match="left after its end"):
        _read_whole(data, chunk)


@requires_zstd()
@pytest.mark.parametrize(
    "skippable",
    [
        pytest.param(struct.pack("<II", 0x184D2A50, 0), id="empty"),
        pytest.param(struct.pack("<II", 0x184D2A5F, 6) + b"hidden", id="payload"),
    ],
)
def test_zip_zstd_member_with_a_skippable_frame_is_corrupt(skippable: bytes) -> None:
    # A skippable frame decodes to nothing, so the size and CRC match and only the
    # input-after-end check sees the hidden bytes.
    compressed = zstd_backend().compress(_PAYLOAD) + skippable
    data = _build_minimal_zip(b"z.bin", compressed, _PAYLOAD, 93)
    with pytest.raises(CorruptionError, match="left after its end"):
        _read_whole(data, None)


@pytest.mark.parametrize("chunk", [None, 7], ids=["whole", "chunked"])
def test_framed_stream_refuses_a_second_stream_after_the_end(
    chunk: int | None,
) -> None:
    # The shared framed path. With the concatenation magic a bare .bz2 file uses, a
    # second stream is content; with a single stream's magic (a container coder's,
    # CodecParams.single_stream) it is input after the first one's end.
    import bz2

    from archivey.internal.streams.codecs.framed_decoder import (
        FramedDecompressorStream,
        StreamStart,
        stream_magic,
    )

    data = bz2.compress(_PAYLOAD) + bz2.compress(b"second")
    concatenated = stream_magic((b"B", b"Z", b"h", b"123456789"))

    def read(magic: StreamStart, refuse: bool) -> bytes:
        stream = FramedDecompressorStream(
            io.BytesIO(data),
            bz2.BZ2Decompressor,
            codec_name="bzip2",
            magic=magic,
            refuse_input_after_end=refuse,
        )
        if chunk is None:
            return stream.read()
        return b"".join(iter(lambda: stream.read(chunk), b""))

    assert read(concatenated, refuse=False) == _PAYLOAD + b"second"
    assert read(concatenated, refuse=True) == _PAYLOAD + b"second"
    with pytest.raises(CorruptionError, match="left after its end"):
        read(stream_magic(), refuse=True)


@requires_binary("7z")
@requires("rapidgzip")
@pytest.mark.parametrize(
    ("method_name", "method", "accelerator"),
    [("Deflate", 8, "use_rapidgzip"), ("BZip2", 12, "use_indexed_bzip2")],
    ids=["deflate", "bzip2"],
)
@pytest.mark.parametrize("tail", _TAILS)
def test_zip_input_after_the_stream_is_corrupt_under_the_accelerator(
    tmp_path: Path, method_name: str, method: int, accelerator: str, tail: bytes
) -> None:
    archive = _7z_zip(tmp_path, method_name, _PAYLOAD)
    config = ArchiveyConfig(**{accelerator: AcceleratorMode.ON})
    for suffix, raises in ((b"", False), (tail, True)):
        data = _with_tail(archive, method, suffix)
        with open_archive(io.BytesIO(data), config=config) as ar:
            (member,) = ar.members()
            if not raises:
                assert ar.read(member) == _PAYLOAD
                continue
            with pytest.raises(CorruptionError, match="left after its end"):
                ar.read(member)


@requires_binary("7z")
@requires("pyppmd")
@pytest.mark.parametrize("tail", _TAILS)
def test_zip_ppmd_input_after_the_end_mark_is_corrupt_in_a_child_process(
    tmp_path: Path, tail: bytes
) -> None:
    # A member past max_ppmd_in_process_input decodes in a child process, which
    # reports the decoder's unused input the same way.
    archive = _7z_zip(tmp_path, "PPMd", _PAYLOAD)
    config = ArchiveyConfig(decoder_limits=DecoderLimits(max_ppmd_in_process_input=16))
    for suffix, raises in ((b"", False), (tail, True)):
        data = _with_tail(archive, 98, suffix)
        with open_archive(io.BytesIO(data), config=config) as ar:
            (member,) = ar.members()
            if not raises:
                assert ar.read(member) == _PAYLOAD
                continue
            with pytest.raises(CorruptionError, match="left after its end"):
                ar.read(member)


def _raw_deflate(data: bytes) -> bytes:
    body = zlib.compressobj(9, zlib.DEFLATED, -15)
    return body.compress(data) + body.flush()


@pytest.mark.parametrize("tail", _TAILS)
@pytest.mark.parametrize("chunk", [None, 7], ids=["whole", "chunked"])
def test_zip_zipcrypto_member_with_input_after_its_stream_is_corrupt(
    tail: bytes, chunk: int | None
) -> None:
    # The decrypt stage hands the codec ``compress_size - 12`` bytes: the tail is
    # inside them, after the DEFLATE stream's end. Under ZipCrypto a damaged member
    # cannot be told from a wrong password that passed the one-byte check, so the
    # CorruptionError arrives as the cause of an EncryptionError.
    for suffix, raises in ((b"", False), (tail, True)):
        data = build_zipcrypto_zip(
            b"pw",
            b"c.bin",
            _PAYLOAD,
            compression=zipfile.ZIP_DEFLATED,
            compressed=_raw_deflate(_PAYLOAD) + suffix,
        )
        with open_archive(io.BytesIO(data), password=b"pw") as ar:
            (member,) = ar.members()
            if not raises:
                assert ar.read(member) == _PAYLOAD
                continue
            with pytest.raises(EncryptionError) as excinfo:
                if chunk is None:
                    ar.read(member)
                else:
                    with ar.open(member) as stream:
                        while stream.read(chunk):
                            pass
            cause = excinfo.value.__cause__
            assert isinstance(cause, CorruptionError)
            assert "left after its end" in str(cause)


@requires("cryptography")
@pytest.mark.parametrize("tail", _TAILS)
def test_zip_winzip_aes_member_with_input_after_its_stream_is_corrupt(
    tail: bytes,
) -> None:
    # The authentication code follows the ciphertext, so the codec's input ends
    # exactly at the ciphertext's end; the tail is encrypted inside it.
    from tests.zip_aes_fixture import build_aes_zip

    for suffix, raises in ((b"", False), (tail, True)):
        data = build_aes_zip(
            [(b"a.bin", _PAYLOAD)], password=b"pw", compressed_tail=suffix
        )
        with open_archive(io.BytesIO(data), password=b"pw") as ar:
            (member,) = ar.members()
            if not raises:
                assert ar.read(member) == _PAYLOAD
                continue
            with pytest.raises(CorruptionError, match="left after its end"):
                ar.read(member)


@requires("pyppmd")
@pytest.mark.parametrize("in_child", [False, True], ids=["in-process", "child"])
@pytest.mark.parametrize("chunk", [None, 7], ids=["whole", "chunked"])
def test_zip_ppmd_member_without_an_end_mark_is_corrupt(
    in_child: bool, chunk: int | None
) -> None:
    # A ZIP PPMd8 member carries an end mark; without one its input cannot be told
    # from a declared size cut short of the stream, and 7-Zip 23.01 fails it ("Data
    # Error"). Read in 7-byte chunks, this payload's end-mark probe parks the worker,
    # which close() then has to quiesce.
    import pyppmd

    enc = pyppmd.Ppmd8Encoder(6, 16 << 20, 0)
    body = _zip_ppmd_header(0) + enc.encode(_PAYLOAD) + enc.flush(False)
    data = _build_minimal_zip(b"p.bin", body, _PAYLOAD, 98)
    limits = DecoderLimits(max_ppmd_in_process_input=16 if in_child else 1 << 20)
    with open_archive(
        io.BytesIO(data), config=ArchiveyConfig(decoder_limits=limits)
    ) as ar:
        (member,) = ar.members()
        with pytest.raises(CorruptionError, match="no end mark"):
            if chunk is None:
                ar.read(member)
            else:
                with ar.open(member) as stream:
                    while stream.read(chunk):
                        pass

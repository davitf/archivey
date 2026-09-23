"""Tests for ``DecoderLimits`` — the cap on archive-declared decoder memory.

The end-to-end cases here build their archives with the ``7z`` CLI rather than
reading a committed fixture, because the interesting one is a file no compressor
writes: a 153-byte archive whose PPMd coder properties have been rewritten to
declare a 4 GiB window. Keeping the rewrite in the test keeps the two numbers
that matter (what the writer put there, what the test changed it to) next to the
assertion instead of inside an opaque blob.

Nothing here allocates what the hostile archive asks for. The assertion is always
that ``ResourceLimitError`` was raised *instead of* the allocation — never a
``MemoryError``, which on a machine with enough free memory would simply not
happen, and on one without would arrive as a SIGABRT from inside pyppmd rather
than as an exception at all.
"""

from __future__ import annotations

import dataclasses
import io
import lzma
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

import archivey
from archivey import ArchiveyConfig, DecoderLimits, open_archive
from archivey.exceptions import ResourceLimitError
from archivey.internal.config import (
    DEFAULT_STREAM_CONFIG,
    check_decoder_memory,
    stream_config_from_archivey,
)
from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.xz import XzDecompressorStream
from archivey.types import CompressionAlgorithm
from tests.conftest import requires, requires_binary
from tests.streams_util import (
    NonSeekableBytesIO,
    make_lzip_member,
    xz_cli_available,
)

# A writer declares what ``mem=`` asked for, reduced for a small member to 16x its
# size rounded up to a power of two, with a 64 KiB floor; the 39-byte member every
# case here uses lands on that floor. Cases that want "declared above the cap"
# lower the cap under that figure rather than asking for a genuinely large
# allocation.
_TINY_MEMBER_DECLARED_MEM = 64 * 1024

_PPMD_CODER_HEADER = bytes([0x23, 0x03, 0x04, 0x01, 0x05])
"""7z coder record: attributes|idSize=3, PPMd id ``03 04 01``, propsSize 5."""


# --- the guard on its own, with no codec backend needed --------------------------------


def test_default_cap_is_two_gib() -> None:
    assert DecoderLimits().max_decoder_memory == 2 * 2**30
    assert DecoderLimits.UNLIMITED.max_decoder_memory is None
    assert ArchiveyConfig().decoder_limits == DecoderLimits()


def test_decoder_limits_is_frozen() -> None:
    limits = DecoderLimits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_decoder_memory = 1  # type: ignore[misc]


def test_public_api_exports_decoder_limits() -> None:
    assert "DecoderLimits" in archivey.__all__
    assert archivey.DecoderLimits is DecoderLimits


def test_stream_config_carries_the_caller_s_limits() -> None:
    limits = DecoderLimits(max_decoder_memory=7)
    stream_cfg = stream_config_from_archivey(
        ArchiveyConfig(decoder_limits=limits), streaming=False
    )
    assert stream_cfg.decoder_limits is limits
    # A StreamConfig built directly still decodes someone else's file, so it must
    # not default to "no cap".
    assert DEFAULT_STREAM_CONFIG.decoder_limits == DecoderLimits()


@pytest.mark.parametrize(
    ("declared", "cap", "refused"),
    [
        (1024, 1024, False),  # exactly at the cap is allowed
        (1025, 1024, True),
        # The default's own boundary, checked here rather than end to end so
        # that pinning it costs nothing: the allowed side would otherwise build
        # a real 2 GiB PPMd model.
        (2 * 2**30, 2 * 2**30, False),
        (2 * 2**30 + 1, 2 * 2**30, True),
        (2**32 - 1, 2 * 2**30, True),  # the default against the widest 7z field
        (2**32 - 1, None, False),  # UNLIMITED
        (0, 1024, False),
    ],
)
def test_check_decoder_memory_boundaries(
    declared: int, cap: int | None, refused: bool
) -> None:
    limits = DecoderLimits(max_decoder_memory=cap)
    if not refused:
        check_decoder_memory(declared, limits=limits, what="test field")
        return
    with pytest.raises(ResourceLimitError) as excinfo:
        check_decoder_memory(declared, limits=limits, what="test field")
    # The caller who raises the cap needs both numbers and the field's name.
    message = str(excinfo.value)
    assert "test field" in message
    assert str(declared) in message
    assert str(cap) in message


# --- end to end, through a real archive ------------------------------------------------


def _write_ppmd_7z(path: Path, member: Path) -> None:
    """Write a single-member 7z using PPMd, with a plaintext (uncompressed) header."""
    subprocess.run(
        ["7z", "a", "-mhc=off", "-m0=PPMd", str(path), str(member)],
        check=True,
        capture_output=True,
    )


def _declare_ppmd_memory(path: Path, mem_size: int) -> int:
    """Rewrite the 7z PPMd window size in place, repairing both header CRCs.

    Returns what the writer had declared, so a caller can assert the archive it
    started from was the one it thought.

    The two CRCs have to be redone in order (:func:`_repair_7z_header_crcs`): the
    next header's CRC lives in the start header, and the start header carries a CRC
    over itself. Without this the
    parser refuses the file for corruption and the decoder is never reached, which
    would make this test pass for entirely the wrong reason.
    """
    data = bytearray(path.read_bytes())
    coder = data.find(_PPMD_CODER_HEADER)
    assert coder > 0, "no plaintext PPMd coder record in the 7z header"
    order, previous = struct.unpack("<BL", bytes(data[coder + 5 : coder + 10]))
    data[coder + 6 : coder + 10] = struct.pack("<L", mem_size)
    _repair_7z_header_crcs(data)
    path.write_bytes(bytes(data))
    assert order > 0
    return int(previous)


def _repair_7z_header_crcs(data: bytearray) -> None:
    """Redo a plaintext 7z header's two CRCs after rewriting a byte inside it."""
    next_header_offset, next_header_size = struct.unpack("<QQ", bytes(data[12:28]))
    start = 32 + next_header_offset
    data[28:32] = struct.pack(
        "<L", zlib.crc32(bytes(data[start : start + next_header_size]))
    )
    data[8:12] = struct.pack("<L", zlib.crc32(bytes(data[12:32])))


@requires_binary("7z")
@requires("pyppmd")
def test_7z_ppmd_below_the_cap_still_reads(tmp_path: Path) -> None:
    member = tmp_path / "a.txt"
    member.write_bytes(b"hello ppmd world, compress me a little\n")
    archive = tmp_path / "a.7z"
    _write_ppmd_7z(archive, member)

    with open_archive(archive) as reader:
        (entry,) = reader.members()
        # 7-Zip stores what it cannot compress; a stored member would never reach
        # the guard, so this test would pass without proving anything.
        assert [m.algo for m in entry.compression] == [CompressionAlgorithm.PPMD]
        with reader.open(entry) as stream:
            assert stream.read() == member.read_bytes()


@requires_binary("7z")
@requires("pyppmd")
def test_7z_ppmd_declaring_four_gib_is_refused_before_allocating(
    tmp_path: Path,
) -> None:
    """A 153-byte archive asks for 4 GiB; the cap answers before pyppmd is built.

    Under a memory cap that refusal is not optional. Measured on pyppmd 1.3.1,
    ``Ppmd7Decoder(6, 0xFFFFFFFF)`` with ``RLIMIT_AS`` at 2 GiB aborts the process
    on ``double free or corruption`` — SIGABRT, no traceback, nothing to catch.
    """
    member = tmp_path / "a.txt"
    member.write_bytes(b"hello ppmd world, compress me a little\n")
    archive = tmp_path / "hostile.7z"
    _write_ppmd_7z(archive, member)
    previous = _declare_ppmd_memory(archive, 0xFFFFFFFF)
    assert previous == _TINY_MEMBER_DECLARED_MEM
    assert archive.stat().st_size < 1024, "the point is the size the demand comes in"

    with open_archive(archive) as reader:
        (entry,) = reader.members()
        with pytest.raises(ResourceLimitError) as excinfo:
            reader.open(entry)
    assert "max_decoder_memory" in str(excinfo.value)
    assert str(0xFFFFFFFF) in str(excinfo.value)


@requires_binary("7z")
@requires("pyppmd")
def test_the_default_refuses_just_above_its_own_boundary(tmp_path: Path) -> None:
    """One byte over 2 GiB is refused under the default config, end to end.

    Pinned because the default is a policy number a future edit could move
    without meaning to. Only the refused side runs through a real archive: the
    allowed side of the same boundary would build a 2 GiB PPMd model for a
    39-byte member, so it is checked against the guard itself in
    :func:`test_check_decoder_memory_boundaries`.
    """
    member = tmp_path / "a.txt"
    member.write_bytes(b"hello ppmd world, compress me a little\n")
    archive = tmp_path / "boundary.7z"
    _write_ppmd_7z(archive, member)
    _declare_ppmd_memory(archive, 2 * 2**30 + 1)

    with open_archive(archive) as reader:
        (entry,) = reader.members()
        with pytest.raises(ResourceLimitError, match="max_decoder_memory"):
            reader.open(entry)


@requires_binary("7z")
@requires("pyppmd")
def test_7z_ppmd_cap_applies_to_what_a_writer_declares_too(tmp_path: Path) -> None:
    """The guard reads the header, not the patch — an unmodified archive trips it."""
    member = tmp_path / "a.txt"
    member.write_bytes(b"hello ppmd world, compress me a little\n")
    archive = tmp_path / "a.7z"
    _write_ppmd_7z(archive, member)

    config = ArchiveyConfig(decoder_limits=DecoderLimits(max_decoder_memory=1024))
    with open_archive(archive, config=config) as reader:
        (entry,) = reader.members()
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=1024"):
            reader.open(entry)


@requires_binary("7z")
@requires("pyppmd")
def test_unlimited_lets_the_declared_window_through(tmp_path: Path) -> None:
    """``UNLIMITED`` is a real opt-out, checked at a window small enough to build."""
    member = tmp_path / "a.txt"
    member.write_bytes(b"hello ppmd world, compress me a little\n")
    archive = tmp_path / "a.7z"
    _write_ppmd_7z(archive, member)

    config = ArchiveyConfig(decoder_limits=DecoderLimits.UNLIMITED)
    with open_archive(archive, config=config) as reader:
        (entry,) = reader.members()
        with reader.open(entry) as stream:
            assert stream.read() == member.read_bytes()


@requires_binary("7z")
@requires("pyppmd")
def test_zip_method_98_is_capped_on_the_same_field(tmp_path: Path) -> None:
    """ZIP PPMd8 declares megabytes in a 2-byte header; same cap, same error.

    Its field tops out at 256 MiB, so this is the smaller exposure of the two —
    but it is the same attacker-chosen allocation on the same ``open()`` path, and
    a cap that covered only 7z would be a surprise.
    """
    member = tmp_path / "a.txt"
    # 7-Zip stores a member it cannot usefully compress, and a stored member would
    # take the guard nowhere near PPMd — hence a payload big enough to be worth
    # compressing, and the assertion below that the writer really did use method 98.
    member.write_bytes(b"hello ppmd world, compress me a little\n" * 4096)
    archive = tmp_path / "a.zip"
    subprocess.run(
        ["7z", "a", "-tzip", "-mm=PPMd", str(archive), str(member)],
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(archive) as zf:
        assert [info.compress_type for info in zf.infolist()] == [98]

    config = ArchiveyConfig(decoder_limits=DecoderLimits(max_decoder_memory=1024))
    with open_archive(archive, config=config) as reader:
        (entry,) = reader.members()
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=1024"):
            reader.open(entry)


# --- LZMA: the dictionary size -----------------------------------------------------------
#
# Every LZMA container carries the dictionary size in its own header: 7z coder
# properties (LZMA1 32 bits, LZMA2 one encoded byte), the ZIP method-14 header, each
# xz block header, the .lzma header, and each lzip member header. What makes it worth
# capping was measured, not assumed: liblzma reserves the whole declared dictionary
# when the decoder is built and touches it as output is written, so a 151 KB stream
# declaring 4 GiB held 1.1 GiB resident after producing 1 GiB of zeros, where the
# same stream declaring 1 MiB held 59 MB. The refusal therefore has to come before
# the decoder is built, and the spy tests below check exactly that.

_LZMA_MEMBER = b"hello lzma world, compress me a little\n" * 4096
_FOUR_GIB_MINUS_ONE = 0xFFFFFFFF

_SEVENZIP_LZMA_CODER = bytes([0x23, 0x03, 0x01, 0x01, 0x05])
"""7z coder record: attributes|idSize=3, LZMA id ``03 01 01``, propsSize 5."""

_SEVENZIP_LZMA2_CODER = bytes([0x21, 0x21, 0x01])
"""7z coder record: attributes|idSize=1, LZMA2 id ``21``, propsSize 1."""


def _lzma2_dict_size(code: int) -> int:
    """The dictionary an LZMA2 properties byte declares (7z and xz share the encoding)."""
    if code == 40:
        return _FOUR_GIB_MINUS_ONE
    return (2 | (code & 1)) << (code // 2 + 11)


def _write_7z(path: Path, member: Path, method: str) -> None:
    subprocess.run(
        ["7z", "a", "-mhc=off", f"-m0={method}", str(path), str(member)],
        check=True,
        capture_output=True,
    )


def _declare_7z_lzma_dictionary(path: Path, dict_size: int) -> int:
    """Rewrite a 7z LZMA1 coder's 32-bit dictionary size; return what was there."""
    data = bytearray(path.read_bytes())
    coder = data.find(_SEVENZIP_LZMA_CODER)
    assert coder > 0, "no plaintext LZMA coder record in the 7z header"
    props = coder + len(_SEVENZIP_LZMA_CODER)
    (previous,) = struct.unpack("<L", bytes(data[props + 1 : props + 5]))
    data[props + 1 : props + 5] = struct.pack("<L", dict_size)
    _repair_7z_header_crcs(data)
    path.write_bytes(bytes(data))
    return int(previous)


def _declare_7z_lzma2_dictionary(path: Path, code: int) -> int:
    """Rewrite a 7z LZMA2 coder's dictionary byte; return the code that was there."""
    data = bytearray(path.read_bytes())
    coder = data.find(_SEVENZIP_LZMA2_CODER)
    assert coder > 0, "no plaintext LZMA2 coder record in the 7z header"
    props = coder + len(_SEVENZIP_LZMA2_CODER)
    previous = data[props]
    data[props] = code
    _repair_7z_header_crcs(data)
    path.write_bytes(bytes(data))
    return previous


def _declare_zip_lzma_dictionary(path: Path, dict_size: int) -> int:
    """Rewrite a ZIP method-14 member's dictionary size; return what was there.

    The LZMA header (version, properties size, then 5 bytes of properties) is the
    start of the member's data, which no ZIP CRC covers — the CRC is over the
    decompressed bytes.
    """
    with zipfile.ZipFile(path) as zf:
        (info,) = zf.infolist()
        assert info.compress_type == zipfile.ZIP_LZMA
        offset = info.header_offset
    data = bytearray(path.read_bytes())
    name_len, extra_len = struct.unpack("<HH", bytes(data[offset + 26 : offset + 30]))
    lzma_header = offset + 30 + name_len + extra_len
    assert struct.unpack("<H", bytes(data[lzma_header + 2 : lzma_header + 4])) == (5,)
    field = lzma_header + 4 + 1
    (previous,) = struct.unpack("<L", bytes(data[field : field + 4]))
    data[field : field + 4] = struct.pack("<L", dict_size)
    path.write_bytes(bytes(data))
    return int(previous)


def _declare_xz_dictionary(data: bytes, code: int) -> bytes:
    """Rewrite the first xz block header's LZMA2 dictionary byte, repairing its CRC.

    Assumes the layout liblzma writes for a single LZMA2 filter: block header size,
    flags, filter id ``0x21``, properties size ``1``, then the dictionary byte.
    """
    out = bytearray(data)
    block = 12  # the stream header is 12 bytes
    header_size = (out[block] + 1) * 4
    assert out[block + 2 : block + 4] == b"\x21\x01", "not a lone LZMA2 filter"
    out[block + 4] = code
    crc_at = block + header_size - 4
    out[crc_at : crc_at + 4] = struct.pack("<L", zlib.crc32(bytes(out[block:crc_at])))
    return bytes(out)


def _read_only_member(path: Path, config: ArchiveyConfig | None = None) -> bytes:
    with open_archive(path, config=config) as reader:
        (entry,) = reader.members()
        with reader.open(entry) as stream:
            return stream.read()


@requires_binary("7z")
@pytest.mark.parametrize("method", ["LZMA", "LZMA2"])
def test_7z_lzma_as_written_still_reads(tmp_path: Path, method: str) -> None:
    member = tmp_path / "a.txt"
    member.write_bytes(_LZMA_MEMBER)
    archive = tmp_path / "a.7z"
    _write_7z(archive, member, method)
    assert _read_only_member(archive) == _LZMA_MEMBER


@requires_binary("7z")
def test_7z_lzma_declaring_four_gib_is_refused(tmp_path: Path) -> None:
    member = tmp_path / "a.txt"
    member.write_bytes(_LZMA_MEMBER)
    archive = tmp_path / "hostile.7z"
    _write_7z(archive, member, "LZMA")
    previous = _declare_7z_lzma_dictionary(archive, _FOUR_GIB_MINUS_ONE)
    # 7-Zip shrinks the dictionary it declares to about the member's size.
    assert previous < 2**20
    assert archive.stat().st_size < 4096, "the point is the size the demand comes in"

    with pytest.raises(ResourceLimitError) as excinfo:
        _read_only_member(archive)
    message = str(excinfo.value)
    assert "max_decoder_memory" in message
    assert "LZMA dictionary size" in message
    assert str(_FOUR_GIB_MINUS_ONE) in message


@requires_binary("7z")
@pytest.mark.parametrize(
    ("code", "refused"),
    [
        (38, False),  # exactly 2 GiB: the default's own boundary, allowed
        (39, True),  # 3 GiB
        (40, True),  # 4 GiB - 1, the top of the encoding
    ],
)
def test_7z_lzma2_dictionary_against_the_default(
    tmp_path: Path, code: int, refused: bool
) -> None:
    """The LZMA2 byte encodes 2^n or 3 * 2^(n-1); 2 GiB is one of its values.

    The allowed side runs end to end: unlike a PPMd model, liblzma's dictionary is
    only reserved when the decoder is built, and touched as output is written, so a
    2 GiB declaration over a 160 KB member costs about 160 KB of resident memory.
    """
    member = tmp_path / "a.txt"
    member.write_bytes(_LZMA_MEMBER)
    archive = tmp_path / "a.7z"
    _write_7z(archive, member, "LZMA2")
    previous = _declare_7z_lzma2_dictionary(archive, code)
    assert _lzma2_dict_size(previous) < 2**20

    if not refused:
        assert _read_only_member(archive) == _LZMA_MEMBER
        return
    with pytest.raises(ResourceLimitError) as excinfo:
        _read_only_member(archive)
    assert "LZMA2 dictionary size" in str(excinfo.value)
    assert str(_lzma2_dict_size(code)) in str(excinfo.value)


@requires_binary("7z")
def test_7z_lzma_unlimited_reads_the_hostile_declaration(tmp_path: Path) -> None:
    member = tmp_path / "a.txt"
    member.write_bytes(_LZMA_MEMBER)
    archive = tmp_path / "a.7z"
    _write_7z(archive, member, "LZMA")
    _declare_7z_lzma_dictionary(archive, _FOUR_GIB_MINUS_ONE)

    config = ArchiveyConfig(decoder_limits=DecoderLimits.UNLIMITED)
    assert _read_only_member(archive, config) == _LZMA_MEMBER


@requires_binary("7z")
def test_zip_lzma_is_capped_on_the_same_field(tmp_path: Path) -> None:
    member = tmp_path / "a.txt"
    member.write_bytes(_LZMA_MEMBER)
    archive = tmp_path / "a.zip"
    subprocess.run(
        ["7z", "a", "-tzip", "-mm=LZMA", str(archive), str(member)],
        check=True,
        capture_output=True,
    )
    assert _read_only_member(archive) == _LZMA_MEMBER
    previous = _declare_zip_lzma_dictionary(archive, _FOUR_GIB_MINUS_ONE)
    assert previous < 2**20

    with pytest.raises(ResourceLimitError) as excinfo:
        _read_only_member(archive)
    assert "LZMA dictionary size" in str(excinfo.value)
    assert str(_FOUR_GIB_MINUS_ONE) in str(excinfo.value)


def test_xz_block_declaring_four_gib_is_refused(tmp_path: Path) -> None:
    """liblzma writes xz, so ``lzma.compress`` is the reference writer's output."""
    archive = tmp_path / "a.txt.xz"
    written = lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_XZ)
    archive.write_bytes(written)
    assert _read_only_member(archive) == _LZMA_MEMBER

    archive.write_bytes(_declare_xz_dictionary(written, 40))
    with pytest.raises(ResourceLimitError, match="max_decoder_memory"):
        _read_only_member(archive)

    config = ArchiveyConfig(decoder_limits=DecoderLimits.UNLIMITED)
    assert _read_only_member(archive, config) == _LZMA_MEMBER


@pytest.mark.parametrize(
    ("code", "refused"),
    [
        (28, False),  # 64 MiB, exactly the cap: the overhead allowance admits it
        (29, True),  # 96 MiB, the next size the encoding can say
    ],
)
def test_xz_boundary_matches_the_other_paths(
    tmp_path: Path, code: int, refused: bool
) -> None:
    """xz goes through liblzma's ``memlimit``, which counts the decoder's overhead too.

    Without the allowance a dictionary exactly at the cap would be refused on xz and
    admitted everywhere else. Checked at a 64 MiB cap so neither side needs a large
    reservation.
    """
    archive = tmp_path / "a.txt.xz"
    written = lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_XZ)
    archive.write_bytes(_declare_xz_dictionary(written, code))
    config = ArchiveyConfig(decoder_limits=DecoderLimits(max_decoder_memory=64 * 2**20))
    if not refused:
        assert _read_only_member(archive, config) == _LZMA_MEMBER
        return
    with pytest.raises(ResourceLimitError, match=f"max_decoder_memory={64 * 2**20}"):
        _read_only_member(archive, config)


@pytest.mark.skipif(not xz_cli_available(), reason="xz CLI not on PATH")
def test_xz_block_chain_after_a_seek_is_capped_too() -> None:
    """A seek into a multi-block xz resumes through a second decoder; it is capped.

    ``xz -0`` declares 256 KiB in every block, so a 64 KiB cap refuses them all,
    overhead allowance included.
    Seeking to the end builds the index from the trailer without decoding anything;
    the read after the seek back is the first decode, and it goes through the
    block-chain engine rather than the sequential one.
    """
    data = bytes(range(256)) * 4096
    compressed = subprocess.run(
        ["xz", "-0", "-z", "-c", "--block-size=65536"],
        input=data,
        capture_output=True,
        check=True,
    ).stdout
    limits = DecoderLimits(max_decoder_memory=64 * 1024)
    with XzDecompressorStream(
        io.BytesIO(compressed), seekable=True, decoder_limits=limits
    ) as stream:
        stream.seek(0, io.SEEK_END)
        stream.seek(len(data) // 2)
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=65536"):
            stream.read(16)


def test_lzma_alone_declaring_four_gib_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "a.txt.lzma"
    written = bytearray(lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_ALONE))
    archive.write_bytes(bytes(written))
    assert _read_only_member(archive) == _LZMA_MEMBER

    written[1:5] = struct.pack("<L", _FOUR_GIB_MINUS_ONE)
    archive.write_bytes(bytes(written))
    with pytest.raises(ResourceLimitError) as excinfo:
        _read_only_member(archive)
    assert "LZMA Alone dictionary size" in str(excinfo.value)
    assert str(_FOUR_GIB_MINUS_ONE) in str(excinfo.value)

    # Detection still calls it .lzma, so a caller who lifts the cap gets the read.
    config = ArchiveyConfig(decoder_limits=DecoderLimits.UNLIMITED)
    assert _read_only_member(archive, config) == _LZMA_MEMBER


def test_lzma_alone_non_seekable_source_is_checked_and_replayed() -> None:
    written = lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_ALONE)
    config = dataclasses.replace(
        DEFAULT_STREAM_CONFIG, decoder_limits=DecoderLimits(max_decoder_memory=2**20)
    )
    # 64 MiB (-6's dictionary) is over a 1 MiB cap. The refusal comes on read, as it
    # does for xz and lzip; see ``_RefusedAloneStream`` for why not on open.
    with (
        pytest.raises(ResourceLimitError, match="LZMA Alone dictionary size"),
        open_codec_stream(
            Codec.LZMA_ALONE, NonSeekableBytesIO(written), config=config
        ) as stream,
    ):
        stream.read()
    # The refused stream is not seekable whatever the source was: no position it
    # could report would be true of a stream that decodes nothing.
    with open_codec_stream(
        Codec.LZMA_ALONE, io.BytesIO(written), config=config
    ) as stream:
        assert stream.seekable() is False
        with pytest.raises(io.UnsupportedOperation):
            stream.tell()
    # Under the cap, the header the check read is still there for liblzma.
    with open_codec_stream(
        Codec.LZMA_ALONE, NonSeekableBytesIO(written), config=DEFAULT_STREAM_CONFIG
    ) as stream:
        assert stream.read() == _LZMA_MEMBER


def test_lzip_member_dictionary_is_capped(tmp_path: Path) -> None:
    """lzip's field tops out at 512 MiB, under the default, so a caller's cap is needed.

    No lzip writer is installed on the CI images; the member is built the way
    ``tests/streams_util.py`` builds every lzip fixture, with a 1 MiB dictionary.
    """
    archive = tmp_path / "a.txt.lz"
    archive.write_bytes(make_lzip_member(_LZMA_MEMBER, 20))
    assert _read_only_member(archive) == _LZMA_MEMBER

    config = ArchiveyConfig(decoder_limits=DecoderLimits(max_decoder_memory=2**19))
    with pytest.raises(ResourceLimitError) as excinfo:
        _read_only_member(archive, config)
    assert "lzip dictionary size" in str(excinfo.value)
    assert str(2**20) in str(excinfo.value)


# --- the refusal comes before the decoder is built ---------------------------------------


@pytest.fixture
def lzma_decoders_built(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record the keyword arguments of every liblzma decoder built from now on.

    ``lzma.LZMAFile`` builds its decoder through the same module-level name, so this
    sees the raw and .lzma paths as well as the ones that build one directly.
    """
    built: list[dict[str, object]] = []
    real = lzma.LZMADecompressor

    def spy(*args: object, **kwargs: object) -> lzma.LZMADecompressor:
        built.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(lzma, "LZMADecompressor", spy)
    return built


_HUGE = {"id": lzma.FILTER_LZMA1, "dict_size": _FOUR_GIB_MINUS_ONE}


@pytest.mark.parametrize(
    ("codec", "payload", "params"),
    [
        pytest.param(
            Codec.LZMA,
            lzma.compress(
                _LZMA_MEMBER,
                format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA1}],
            ),
            CodecParams(filters=[_HUGE]),
            id="raw-lzma1",
        ),
        pytest.param(
            Codec.LZMA2,
            lzma.compress(
                _LZMA_MEMBER,
                format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA2}],
            ),
            CodecParams(
                filters=[{"id": lzma.FILTER_LZMA2, "dict_size": _FOUR_GIB_MINUS_ONE}]
            ),
            id="raw-lzma2",
        ),
        pytest.param(
            Codec.LZMA_ALONE,
            b"\x5d"
            + struct.pack("<L", _FOUR_GIB_MINUS_ONE)
            + lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_ALONE)[5:],
            CodecParams(),
            id="lzma-alone",
        ),
    ],
)
def test_refused_before_any_decoder_is_built(
    lzma_decoders_built: list[dict[str, object]],
    codec: Codec,
    payload: bytes,
    params: CodecParams,
) -> None:
    # Raw LZMA refuses on open; .lzma on the first read. Either way nothing is built.
    with pytest.raises(ResourceLimitError):
        with open_codec_stream(codec, io.BytesIO(payload), params=params) as stream:
            stream.read()
    assert lzma_decoders_built == []


def test_lzip_refused_before_its_decoder_is_built(
    lzma_decoders_built: list[dict[str, object]],
) -> None:
    config = dataclasses.replace(
        DEFAULT_STREAM_CONFIG, decoder_limits=DecoderLimits(max_decoder_memory=2**19)
    )
    with (
        pytest.raises(ResourceLimitError),
        open_codec_stream(
            Codec.LZIP, io.BytesIO(make_lzip_member(_LZMA_MEMBER, 20)), config=config
        ) as stream,
    ):
        stream.read()
    assert lzma_decoders_built == []


def test_xz_decoder_carries_the_cap_as_its_memlimit(
    lzma_decoders_built: list[dict[str, object]],
) -> None:
    """xz hands the cap to liblzma, which checks it before allocating a block's filters."""
    written = _declare_xz_dictionary(
        lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_XZ), 40
    )
    with (
        pytest.raises(ResourceLimitError),
        open_codec_stream(Codec.XZ, io.BytesIO(written)) as stream,
    ):
        stream.read()
    assert lzma_decoders_built
    cap = DecoderLimits().max_decoder_memory
    assert cap is not None
    for kwargs in lzma_decoders_built:
        memlimit = kwargs.get("memlimit")
        assert isinstance(memlimit, int)
        assert cap <= memlimit <= cap + 128 * 1024


def test_xz_cap_too_large_for_liblzma_reads_as_unlimited() -> None:
    """liblzma's memlimit is a uint64; a cap past it refuses nothing and must not crash.

    ``2**64 - 1`` plus the overhead allowance does not fit, and handing it over raised
    ``OverflowError`` from the decompressor's constructor.
    """
    written = lzma.compress(_LZMA_MEMBER, format=lzma.FORMAT_XZ)
    for cap in (2**64 - 1, 2**70):
        config = dataclasses.replace(
            DEFAULT_STREAM_CONFIG, decoder_limits=DecoderLimits(max_decoder_memory=cap)
        )
        with open_codec_stream(Codec.XZ, io.BytesIO(written), config=config) as stream:
            assert stream.read() == _LZMA_MEMBER


class _DrainFailingDecompressor:
    """Stands in for ``LZMADecompressor`` at the point ``_XzState.flush`` drains it."""

    needs_input = False

    def __init__(self, message: str) -> None:
        self._message = message

    def decompress(self, data: bytes, max_length: int = -1) -> bytes:
        raise lzma.LZMAError(self._message)


@pytest.mark.parametrize(
    ("message", "raises"),
    [
        pytest.param("Memory usage limit exceeded", True, id="memlimit-is-raised"),
        pytest.param("Corrupt input data", False, id="corruption-reads-as-truncation"),
    ],
)
def test_xz_flush_drain_does_not_swallow_a_memlimit_refusal(
    monkeypatch: pytest.MonkeyPatch, message: str, raises: bool
) -> None:
    """The mid-stream drain treats a liblzma error as a truncated tail, except a limit.

    White-box: the drain runs only when the decoder still holds input after the
    final ``_process`` pass, which a real stream does not reliably reach.
    """
    from archivey.internal.streams import xz

    state = xz._XzState(DecoderLimits(max_decoder_memory=2**16))
    state._state = xz._XzState._IN_STREAM
    monkeypatch.setattr(state, "_dec", _DrainFailingDecompressor(message))
    monkeypatch.setattr(state, "_process", lambda max_length=-1: (b"", []))
    if raises:
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=65536"):
            state.flush()
    else:
        assert state.flush() == (b"", [])
        assert state.truncated is True

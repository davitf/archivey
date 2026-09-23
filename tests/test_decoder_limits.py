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
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

import archivey
from archivey import ArchiveyConfig, DecoderLimits, open_archive
from archivey.exceptions import ResourceLimitError
from archivey.internal.config import DEFAULT_STREAM_CONFIG, stream_config_from_archivey
from archivey.internal.streams.codecs import check_decoder_memory
from archivey.types import CompressionAlgorithm
from tests.conftest import requires, requires_binary

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
    config = dataclasses.replace(
        DEFAULT_STREAM_CONFIG, decoder_limits=DecoderLimits(max_decoder_memory=cap)
    )
    if not refused:
        check_decoder_memory(declared, config=config, what="test field")
        return
    with pytest.raises(ResourceLimitError) as excinfo:
        check_decoder_memory(declared, config=config, what="test field")
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

    The two CRCs have to be redone in order: the next header's CRC lives in the
    start header, and the start header carries a CRC over itself. Without this the
    parser refuses the file for corruption and the decoder is never reached, which
    would make this test pass for entirely the wrong reason.
    """
    data = bytearray(path.read_bytes())
    coder = data.find(_PPMD_CODER_HEADER)
    assert coder > 0, "no plaintext PPMd coder record in the 7z header"
    order, previous = struct.unpack("<BL", bytes(data[coder + 5 : coder + 10]))
    data[coder + 6 : coder + 10] = struct.pack("<L", mem_size)

    next_header_offset, next_header_size = struct.unpack("<QQ", bytes(data[12:28]))
    start = 32 + next_header_offset
    data[28:32] = struct.pack(
        "<L", zlib.crc32(bytes(data[start : start + next_header_size]))
    )
    data[8:12] = struct.pack("<L", zlib.crc32(bytes(data[12:32])))
    path.write_bytes(bytes(data))
    assert order > 0
    return int(previous)


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

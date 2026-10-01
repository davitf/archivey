"""Second-round audit reproducers: ISO, directory sources, detection and sources.

Each test states the promised behaviour. A test marked ``xfail(strict=True)`` fails
today for the reason in its marker; it turns green (and so strict-fails) when the bug
is fixed, and the marker must then be removed.

Finding ids: ``I<n>`` for the ISO backend, ``D<n>`` for detection, sources and the
directory backend. Every fixture is built in the test; nothing is committed.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import struct
import subprocess
import sys
import threading
import tracemalloc
from pathlib import Path
from typing import Any, Callable

import pytest

from archivey import ArchiveFormat, detect_format, open_archive
from archivey.config import ArchiveyConfig, ListingLimits
from archivey.exceptions import FormatDetectionError, ResourceLimitError
from tests.conftest import requires

# --- ISO builders --------------------------------------------------------------


def _build_iso(populate: Callable[[Any], None], **new_kwargs: Any) -> bytes:
    """An image whose contents ``populate(iso)`` adds, written by pycdlib."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, **new_kwargs)
    populate(iso)
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


def _replace_tf(data: bytes, name: bytes, entries: bytes) -> bytes:
    """Replace the 26-byte ``TF`` entry after Rock Ridge name ``name`` by ``entries``.

    pycdlib writes a short-form ``TF`` with three 7-byte dates (26 bytes); the
    replacement keeps the System Use area's length, so nothing else moves.
    """
    at = data.index(b"NM" + bytes([5 + len(name)]) + b"\x01\x00" + name)
    tf = data.index(b"TF\x1a\x01", at)
    assert len(entries) == 26
    return data[:tf] + entries + data[tf + 26 :]


# ---------------------------------------------------------------------------------
# I1: a Rock Ridge TF long-form date at the edge of datetime's range
# ---------------------------------------------------------------------------------


@requires("pycdlib")
def test_iso_long_form_tf_date_at_year_one_does_not_break_modified_utc() -> None:
    def populate(iso: Any) -> None:
        iso.add_fp(io.BytesIO(b"AAAA"), 4, "/AAA.;1", rr_name="aaa")

    data = _build_iso(populate, rock_ridge="1.09")
    # TF, length 22, version 1, flags: modification time (bit 2) + LONG_FORM (bit 7).
    # 17-byte date: 0001-01-01 00:00:00.00, GMT offset +52 quarter hours (+13:00).
    tf = b"TF" + bytes([22, 1, 0x84]) + b"0001010100000000" + struct.pack("b", 52)
    # A 4-byte SUSP entry of no known type pads the area back to its old length.
    data = _replace_tf(data, b"aaa", tf + b"XX\x04\x01")

    with open_archive(io.BytesIO(data)) as archive:
        (member,) = [m for m in archive.members() if m.name == "aaa"]
        # The date is stored and valid; what matters is that the public helper
        # answers with a value or None rather than a raw exception.
        member.modified_utc()


# ---------------------------------------------------------------------------------
# I2: ListingLimits do not bound what pycdlib builds at open
# ---------------------------------------------------------------------------------


@requires("pycdlib")
def test_iso_listing_limits_bound_the_memory_spent_at_open() -> None:
    count = 3000

    def populate(iso: Any) -> None:
        iso.add_directory("/D")
        for index in range(count):
            iso.add_fp(io.BytesIO(b""), 0, f"/D/F{index}.;1")

    data = _build_iso(populate)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=10))

    tracemalloc.start()
    try:
        try:
            with open_archive(io.BytesIO(data), config=config) as archive:
                archive.members()
        except ResourceLimitError:
            pass
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # 7z and RAR refuse at open once the member count passes max_members; the cost
    # of an over-limit ISO should likewise be about the budget, not a multiple of
    # the image. Today the peak is 15-20x the image (hundreds of bytes of Python
    # objects per directory record), so a 64 MiB image of records costs about 1 GiB.
    assert peak < 2 * len(data), (peak, len(data))


# ---------------------------------------------------------------------------------
# I3: one Rock Ridge continuation area shared by every record
# ---------------------------------------------------------------------------------


def _shared_continuation_image(count: int) -> bytes:
    """``count`` empty files whose Rock Ridge ``CE`` entries all name one 2 KiB area.

    pycdlib accepts records that share a continuation range exactly (it tracks the
    range once, for writers that share one across ``.``/``..`` records) and parses the
    area again for each record. The area here is a chain of ``NM`` entries with the
    CONTINUE flag, so every file's name is about 1.9 KB read from the same sector.
    Each file's 36-byte ``PX`` entry is replaced by the 28-byte ``CE`` plus an 8-byte
    entry of no known type, so no record changes length.
    """

    def populate(iso: Any) -> None:
        iso.add_directory("/D", rr_name="d")
        for index in range(count):
            iso.add_fp(io.BytesIO(b""), 0, f"/D/F{index}.;1", rr_name=f"f{index}")

    data = bytearray(_build_iso(populate, rock_ridge="1.09"))
    block = len(data) // 2048
    area = bytearray()
    while len(area) + 255 <= 2048 - 16:
        area += b"NM" + bytes([255, 1, 1]) + b"z" * 250  # flags: CONTINUE
    area += b"NM" + bytes([6, 1, 0]) + b"e" + b"ST\x04\x01"
    data += area + bytes(2048 - len(area))

    def both(value: int) -> bytes:
        return struct.pack("<I", value) + struct.pack(">I", value)

    ce = b"CE\x1c\x01" + both(block) + both(0) + both(2048)
    filler = b"XX\x08\x01" + bytes(4)
    patched = 0
    at = data.find(b"PX\x24\x01")
    while at >= 0:
        mode = struct.unpack_from("<I", data, at + 4)[0]
        if mode & 0o170000 == 0o100000:  # regular files only
            data[at : at + 36] = ce + filler
            patched += 1
        at = data.find(b"PX\x24\x01", at + 1)
    assert patched == count
    return bytes(data)


@requires("pycdlib")
def test_iso_shared_continuation_area_does_not_multiply_memory_at_open() -> None:
    data = _shared_continuation_image(1000)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=10))

    tracemalloc.start()
    try:
        try:
            with open_archive(io.BytesIO(data), config=config) as archive:
                archive.members()
        except ResourceLimitError:
            pass
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Every name is ~1.9 KB, all from one 2 KiB sector. The listing budget is 10
    # members; what open_archive() spends should not scale with the number of
    # records pointing at that sector.
    assert peak < 4 * len(data), (peak, len(data))


def _count_record_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count pycdlib ``DirectoryRecord.parse`` calls (archivey's hook included)."""
    from pycdlib import dr

    calls: list[int] = []
    hooked = dr.DirectoryRecord.parse

    def counting(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return hooked(self, *args, **kwargs)

    monkeypatch.setattr(dr.DirectoryRecord, "parse", counting)
    return calls


@requires("pycdlib")
def test_iso_max_members_refuses_before_pycdlib_parses_the_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def populate(iso: Any) -> None:
        iso.add_directory("/D")
        for index in range(3000):
            iso.add_fp(io.BytesIO(b""), 0, f"/D/F{index}.;1")

    data = _build_iso(populate)
    calls = _count_record_parses(monkeypatch)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=10))
    with pytest.raises(ResourceLimitError, match="max_members=10"):
        open_archive(io.BytesIO(data), config=config)
    # The root, the four dot records and eleven members; nowhere near 3000.
    assert len(calls) < 20, len(calls)


@requires("pycdlib")
def test_iso_max_metadata_bytes_counts_a_shared_continuation_each_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _shared_continuation_image(1000)
    calls = _count_record_parses(monkeypatch)
    # 64 KiB: under the image's own size, so only the repeated 2 KiB area can cross it.
    config = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=65536))
    assert len(data) > 65536
    with pytest.raises(ResourceLimitError, match="max_metadata_bytes=65536"):
        open_archive(io.BytesIO(data), config=config)
    assert len(calls) < 40, len(calls)


@requires("pycdlib")
def test_iso_parse_budget_leaves_unlimited_and_default_opens_alone() -> None:
    def populate(iso: Any) -> None:
        iso.add_directory("/D")
        for index in range(3000):
            iso.add_fp(io.BytesIO(b""), 0, f"/D/F{index}.;1")

    data = _build_iso(populate)
    for limits in (ListingLimits.UNLIMITED, ListingLimits()):
        config = ArchiveyConfig(listing_limits=limits)
        with open_archive(io.BytesIO(data), config=config) as archive:
            assert len(archive.members()) == 3001
    with open_archive(io.BytesIO(_shared_continuation_image(1000))) as archive:
        assert len(archive.members()) == 1001


# ---------------------------------------------------------------------------------
# D1: a directory source deeper than PATH_MAX
# ---------------------------------------------------------------------------------


def _make_deep_tree(root: Path, component: str, depth: int) -> None:
    """Create ``root/component/.../component/f`` without ever naming the full path."""
    fd = os.open(root, os.O_RDONLY)
    try:
        for _ in range(depth):
            os.mkdir(component, dir_fd=fd)
            next_fd = os.open(component, os.O_RDONLY, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        leaf = os.open("f", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)
        try:
            os.write(leaf, b"deep")
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


@pytest.mark.skipif(
    sys.platform == "win32" or os.mkdir not in os.supports_dir_fd,
    reason="needs POSIX dir_fd to build the tree",
)
def test_directory_source_deeper_than_path_max_lists_and_reads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    component = "a" * 100
    path_max = os.pathconf(str(tmp_path), "PC_PATH_MAX")
    depth = path_max // (len(component) + 1) + 5
    _make_deep_tree(root, component, depth)

    with open_archive(root) as archive:
        members = archive.members()
        leaf = members[-1]
        assert leaf.name.endswith("/f")
        # Reading already opens one component at a time; the listing should too.
        assert archive.read(leaf) == b"deep"


# ---------------------------------------------------------------------------------
# D2: detect_format on a path naming a pipe
# ---------------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_detect_format_on_a_fifo_path_matches_open_archive(tmp_path: Path) -> None:
    fifo = tmp_path / "payload"
    os.mkfifo(fifo)
    payload = gzip.compress(b"hello pipe")

    def writer() -> None:
        try:
            with open(fifo, "wb") as out:
                out.write(payload)
        except (BrokenPipeError, OSError):
            pass

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        info = detect_format(fifo)
    finally:
        # Unblock the writer if detection never opened the pipe.
        try:
            os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
        thread.join(5)
    assert info.format == ArchiveFormat.GZ


# ---------------------------------------------------------------------------------
# D3: a block or character device path is measured as zero bytes
# ---------------------------------------------------------------------------------


def _iso_image() -> bytes:
    def populate(iso: Any) -> None:
        iso.add_fp(io.BytesIO(b"on a device"), 11, "/F.;1", rr_name="f")

    return _build_iso(populate, rock_ridge="1.09")


@requires("pycdlib")
@pytest.mark.skipif(
    sys.platform != "linux"
    or not hasattr(os, "geteuid")
    or os.geteuid() != 0
    or shutil.which("losetup") is None,
    reason="needs root and losetup to attach a loop device",
)
def test_iso_on_a_block_device_opens(tmp_path: Path) -> None:
    image = tmp_path / "image.iso"
    image.write_bytes(_iso_image())
    attach = subprocess.run(
        ["losetup", "--find", "--show", "--read-only", str(image)],
        capture_output=True,
        text=True,
        check=False,
    )
    if attach.returncode != 0:
        pytest.skip(f"losetup could not attach a loop device: {attach.stderr.strip()}")
    device = attach.stdout.strip()
    try:
        # The ArchiveSource shape table lists "path (block device)" as a seekable
        # source; /dev/sr0 and loop devices are where ISO images live.
        assert detect_format(device).format == ArchiveFormat.ISO
        with open_archive(device) as archive:
            assert archive.read("f") == b"on a device"
    finally:
        subprocess.run(["losetup", "--detach", device], check=False)


@pytest.mark.skipif(not os.path.exists("/dev/zero"), reason="needs /dev/zero")
def test_detection_does_not_call_a_character_device_empty() -> None:
    with pytest.raises(FormatDetectionError) as caught:
        detect_format("/dev/zero")
    assert "no bytes to read" not in str(caught.value)

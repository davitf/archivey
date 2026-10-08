"""ISO metadata whose declared size pycdlib reads at open, bounded before the read.

pycdlib sizes two reads inside ``open_fp`` from the image's own fields and reads them
before any of archivey's ``ListingLimits`` hooks used to run: the path tables, whose
size the volume descriptor declares, and a Rock Ridge ``CE`` continuation area, whose
length the ``CE`` entry declares. Every fixture is built in the test.
"""

from __future__ import annotations

import importlib.metadata
import io
import struct
from pathlib import Path
from typing import Any

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig, ListingLimits
from archivey.exceptions import CorruptionError, ResourceLimitError
from tests.conftest import requires
from tests.memory_util import traced_peak

pytestmark = requires("pycdlib")

_PVD = 16 * 2048
# ECMA-119 §8.4: the path table size is a both-endian 32-bit field at byte 132 of
# the primary volume descriptor (little-endian at 132, big-endian at 136).
_PATH_TABLE_SIZE = _PVD + 132


def _both(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _build_iso() -> bytearray:
    """A Rock Ridge image with one file whose 230-byte name needs a ``CE`` area."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    iso.add_fp(io.BytesIO(b"hello\n"), 6, "/A.TXT;1", rr_name="a" * 230)
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return bytearray(out.getvalue())


def _peak_at_open(source: Any, config: ArchiveyConfig, error: type[Exception]) -> int:
    """Peak traced bytes while ``open_archive`` refuses ``source`` with ``error``."""

    def attempt() -> None:
        with pytest.raises(error):
            open_archive(
                io.BytesIO(source) if isinstance(source, bytes) else source,
                config=config,
            )

    # One untraced run first, so lazy imports and first-use caches are not counted.
    attempt()
    return traced_peak(attempt)


def _with_path_table_size(size: int, image_size: int) -> bytes:
    data = _build_iso()
    (stored,) = struct.unpack_from("<I", data, _PATH_TABLE_SIZE)
    assert data[_PATH_TABLE_SIZE : _PATH_TABLE_SIZE + 8] == _both(stored)
    data[_PATH_TABLE_SIZE : _PATH_TABLE_SIZE + 8] = _both(size)
    return bytes(data + bytes(image_size - len(data)))


# --- the path table -------------------------------------------------------------


@pytest.mark.parametrize(
    "limits", [ListingLimits(), ListingLimits.UNLIMITED], ids=["default", "unlimited"]
)
def test_a_path_table_past_the_image_is_refused_before_pycdlib_parses_it(
    limits: ListingLimits,
) -> None:
    """A size past the image is corruption, whatever the limits, before the read.

    Unchecked, pycdlib 1.16 read the 2 MiB the image held and parsed it into one
    object per 8-byte record, 28 MiB, before failing on the short read; 1.21 reads
    it and refuses the short read before the parse, at about twice the image. The
    image is read once here, and the bound holds about 512 KiB above it.
    """
    data = _with_path_table_size(64 * 2**20, image_size=2 * 2**20)
    config = ArchiveyConfig(listing_limits=limits)

    with pytest.raises(CorruptionError, match="path table at block"):
        open_archive(io.BytesIO(data), config=config)
    peak = _peak_at_open(data, config, CorruptionError)
    assert peak < len(data) + 2**19, (peak, len(data))


def test_a_path_table_over_max_metadata_bytes_is_refused_before_pycdlib_parses_it() -> (
    None
):
    """The path tables are weighed with the tree they index, before pycdlib reads them."""
    data = _with_path_table_size(2**20, image_size=2 * 2**20)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=2**19))

    with pytest.raises(ResourceLimitError, match=r"max_metadata_bytes=524288 .*path"):
        open_archive(io.BytesIO(data), config=config)
    peak = _peak_at_open(data, config, ResourceLimitError)
    assert peak < 2 * len(data), (peak, len(data))


def test_both_path_tables_count_against_max_metadata_bytes() -> None:
    """The little- and big-endian tables are each parsed, so each is weighed.

    Before them pycdlib has parsed one record, the PVD's 34-byte root record. A
    budget that holds it and one table is crossed by the second, before the walk.
    """
    data = bytes(_build_iso())
    (size,) = struct.unpack_from("<I", data, _PATH_TABLE_SIZE)
    budget = 34 + size
    limits = ListingLimits(max_metadata_bytes=budget)
    with pytest.raises(
        ResourceLimitError,
        match=f"max_metadata_bytes={budget} .*has {34 + 2 * size} bytes of path tables",
    ):
        open_archive(io.BytesIO(data), config=ArchiveyConfig(listing_limits=limits))


def test_path_table_entries_over_max_members_are_refused_as_pycdlib_parses_them() -> (
    None
):
    """Each entry is a directory, so a table of more than ``max_members + 1`` is refused.

    A 1 MiB table of 8-byte entries fits the default ``max_metadata_bytes``; parsed
    whole it cost about 30 MB. Counted against ``max_members=1000`` it stops after
    1001 entries.
    """
    data = _with_path_table_size(2**20, image_size=2 * 2**20)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=1000))

    with pytest.raises(
        ResourceLimitError, match=r"max_members=1000 \(ISO path table holds more"
    ):
        open_archive(io.BytesIO(data), config=config)
    peak = _peak_at_open(data, config, ResourceLimitError)
    assert peak < 2 * len(data), (peak, len(data))


def test_an_image_whose_directories_fill_max_members_still_opens() -> None:
    """Real images never notice: the path table holds the root plus the members."""
    import pycdlib

    count = 300
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)
    for index in range(count):
        iso.add_directory(f"/D{index}")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()

    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=count))
    with open_archive(io.BytesIO(out.getvalue()), config=config) as reader:
        assert len(reader.members()) == count


# --- the Rock Ridge continuation area ---------------------------------------------


def _ce_entry_at(data: bytearray) -> int:
    """The ``CE`` entry of the file's record (the root's ``.`` record has one too)."""
    at = data.find(b"CE\x1c\x01", data.index(b"A.TXT;1"))
    assert at > 0 and data.find(b"CE\x1c\x01", at + 1) < 0
    return at


def _with_ce(offset: int | None = None, length: int | None = None) -> bytearray:
    data = _build_iso()
    at = _ce_entry_at(data)
    if offset is not None:
        data[at + 12 : at + 20] = _both(offset)
    if length is not None:
        data[at + 20 : at + 28] = _both(length)
    return data


def test_a_continuation_area_past_its_block_is_refused_before_pycdlib_reads_it(
    tmp_path: Path,
) -> None:
    """pycdlib read the whole declared length, then refused it as past the block.

    The image is a sparse file, so the source let pycdlib read 8 MiB of it; the
    refusal is the same ``CorruptionError`` as before, raised before the read.
    """
    path = tmp_path / "ce.iso"
    with path.open("wb") as f:
        f.write(_with_ce(length=8 * 2**20))
        f.truncate(16 * 2**20)

    for limits in (ListingLimits(), ListingLimits.UNLIMITED):
        config = ArchiveyConfig(listing_limits=limits)
        with pytest.raises(CorruptionError, match="continuation area of 8388608 bytes"):
            open_archive(path, config=config)
        peak = _peak_at_open(path, config, CorruptionError)
        assert peak < 2**20, peak


def _with_chained_ce(length: int) -> bytearray:
    """The file's ``CE`` area, which fits its block, ending in a ``CE`` of ``length``.

    The second entry names block 100, past the image's own blocks; the tests pad the
    image out to it.
    """
    data = _build_iso()
    at = _ce_entry_at(data)
    (block,) = struct.unpack_from("<I", data, at + 4)
    (offset,) = struct.unpack_from("<I", data, at + 12)
    (first,) = struct.unpack_from("<I", data, at + 20)
    end = block * 2048 + offset + first
    assert offset + first + 28 <= 2048 and not any(data[end : end + 28])
    data[end : end + 28] = b"CE\x1c\x01" + _both(100) + _both(0) + _both(length)
    data[at + 20 : at + 28] = _both(first + 28)
    return data


def _pycdlib_follows_ce_chains() -> bool:
    """pycdlib follows a ``CE`` chain from 1.21; before, a second ``CE`` is invalid.

    ``uv.lock`` pins an older pycdlib, so CI takes the ``False`` branch: there the
    chained tests pin pycdlib's own refusal of the second ``CE``, raised while it parses
    the first area, and pass with archivey's check on continuation areas removed. The
    check itself is exercised only on 1.21+ (``uv run --with pycdlib==1.21.0``).
    """
    version = importlib.metadata.version("pycdlib")
    return tuple(int(part) for part in version.split(".")[:2]) >= (1, 21)


# What older pycdlib raises, in its own parse, for a CE inside a continuation area.
_NO_CHAINS = r"Only single CE record supported"


def test_a_chained_continuation_area_past_its_block_is_refused_before_the_read(
    tmp_path: Path,
) -> None:
    """Every link of a ``CE`` chain is held to its block, not only the record's own.

    pycdlib 1.21 follows the chain and reads each further link for its declared
    length, clamped only to the image, before it refuses a link past its block: 8 MiB
    here, under any limits. Older pycdlib refuses the second ``CE`` while parsing the
    first area, before any read.
    """
    path = tmp_path / "ce-chain.iso"
    with path.open("wb") as f:
        f.write(_with_chained_ce(8 * 2**20))
        f.truncate(16 * 2**20)

    match = (
        "continuation area of 8388608 bytes at offset 0 of block 100"
        if _pycdlib_follows_ce_chains()
        else _NO_CHAINS
    )
    for limits in (ListingLimits(), ListingLimits.UNLIMITED):
        config = ArchiveyConfig(listing_limits=limits)
        with pytest.raises(CorruptionError, match=match):
            open_archive(path, config=config)
        peak = _peak_at_open(path, config, CorruptionError)
        assert peak < 2**20, peak


def test_a_chained_continuation_area_inside_its_block_is_read() -> None:
    """The check does not refuse a link that fits its block (where pycdlib chains)."""
    data = _with_chained_ce(64)
    data += bytes(101 * 2048 - len(data))
    if not _pycdlib_follows_ce_chains():
        with pytest.raises(CorruptionError, match=_NO_CHAINS):
            open_archive(io.BytesIO(bytes(data)))
        return
    with open_archive(io.BytesIO(bytes(data))) as reader:
        assert [m.name for m in reader.members()] == ["a" * 230]


def test_a_continuation_area_is_held_to_the_primary_descriptors_block() -> None:
    """The block is the PVD's, which pycdlib checks against, in every tree.

    The Joliet descriptor here declares 4096-byte blocks, and its root's ``.`` record
    carries a ``CE`` for 3000 bytes: inside the Joliet block size, past the PVD's
    2048. Held to the descriptor being walked, the area was read and then refused by
    pycdlib ("No room in continuation block").
    """
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09", joliet=3)
    iso.add_fp(io.BytesIO(b"hello\n"), 6, "/A.TXT;1", rr_name="a", joliet_path="/a.txt")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    data = bytearray(out.getvalue())

    svd = 17 * 2048
    assert data[svd : svd + 6] == b"\x02CD001"
    # ECMA-119 §8.5: the logical block size, both-endian 16-bit, at byte 128.
    data[svd + 128 : svd + 132] = struct.pack("<H", 4096) + struct.pack(">H", 4096)
    # The root's records, rewritten with a CE entry appended to ``.``.
    (root,) = struct.unpack_from("<I", data, svd + 156 + 2)
    records = []
    at = root * 2048
    while data[at]:
        records.append(bytes(data[at : at + data[at]]))
        at += data[at]
    dot = bytearray(records[0]) + b"CE\x1c\x01" + _both(100) + _both(0) + _both(3000)
    dot[0] = len(dot)
    directory = bytes(dot) + b"".join(records[1:])
    data[root * 2048 : root * 2048 + len(directory)] = directory
    data += bytes(102 * 2048 - len(data))

    with pytest.raises(CorruptionError, match="runs past the 2048-byte block"):
        open_archive(io.BytesIO(bytes(data)))


@pytest.mark.parametrize("offset", [2040, 2049])
def test_a_continuation_area_offset_past_its_block_is_refused(offset: int) -> None:
    data = _with_ce(offset=offset, length=16)
    with pytest.raises(CorruptionError, match=f"at offset {offset} of block"):
        open_archive(io.BytesIO(bytes(data)))


def test_a_continuation_area_ending_at_its_block_end_opens() -> None:
    """The bound is the block: an area that ends exactly at the block end is read."""
    data = _with_ce()
    at = _ce_entry_at(data)
    (offset,) = struct.unpack_from("<I", data, at + 12)
    (length,) = struct.unpack_from("<I", data, at + 20)
    assert offset + length < 2048
    data[at + 20 : at + 28] = _both(2048 - offset)
    with open_archive(io.BytesIO(bytes(data))) as reader:
        assert [m.name for m in reader.members()] == ["a" * 230]

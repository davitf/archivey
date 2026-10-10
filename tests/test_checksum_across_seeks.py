"""A member's stored checksum survives seeks that decode the skipped bytes anyway.

ADR 0014: the member verifier hashes from 0 to its furthest read. A seek back keeps the
checksum, and so does a forward seek whose inner decodes the gap anyway (the verifier
reads it itself). Only a read that skips bytes the inner jumped over (a stored member,
an index) loses it. These run the rule end to end through ``open_archive``.
"""

from __future__ import annotations

import io
import random
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import CorruptionError

_DATA = bytes(random.Random(7).choice(b"abcdefgh \n") for _ in range(200_000))


def _zip_with_crc(tmp_path: Path, method: int, *, bad_crc: bool) -> Path:
    """One member ``m.txt``; with ``bad_crc`` its stored CRC-32 is off by one."""
    path = tmp_path / f"m{method}{'bad' if bad_crc else ''}.zip"
    with zipfile.ZipFile(path, "w", compression=method) as archive:
        archive.writestr("m.txt", _DATA)
    if bad_crc:
        raw = path.read_bytes()
        good = struct.pack("<I", zlib.crc32(_DATA))
        wrong = struct.pack("<I", (zlib.crc32(_DATA) + 1) & 0xFFFFFFFF)
        # The local header and the central directory each carry it once.
        assert raw.count(good) == 2
        path.write_bytes(raw.replace(good, wrong))
    return path


def _read_after(path: Path, moves: list[tuple[str, int]]) -> bytes:
    with open_archive(path, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            for op, value in moves:
                if op == "read":
                    stream.read(value)
                else:
                    stream.seek(value)
            return stream.read()


_MOVES = {
    "forward": [("read", 10), ("seek", 150_000)],
    "backward": [("read", 150_000), ("seek", 100)],
    "forward-from-behind": [("read", 1_000), ("seek", 10), ("seek", 150_000)],
}


@pytest.mark.parametrize("moves", list(_MOVES), ids=list(_MOVES))
def test_deflate_member_checksum_survives_seeks(tmp_path: Path, moves: str) -> None:
    bad = _zip_with_crc(tmp_path, zipfile.ZIP_DEFLATED, bad_crc=True)
    with pytest.raises(CorruptionError):
        _read_after(bad, _MOVES[moves])
    good = _zip_with_crc(tmp_path, zipfile.ZIP_DEFLATED, bad_crc=False)
    position = _MOVES[moves][-1][1]
    assert _read_after(good, _MOVES[moves]) == _DATA[position:]


def test_stored_member_checksum_survives_a_backward_seek(tmp_path: Path) -> None:
    bad = _zip_with_crc(tmp_path, zipfile.ZIP_STORED, bad_crc=True)
    with pytest.raises(CorruptionError):
        _read_after(bad, _MOVES["backward"])


def test_stored_member_forward_seek_jumps_and_loses_the_checksum(
    tmp_path: Path,
) -> None:
    # A stored member seeks straight to the target; reading on from there skips bytes
    # nothing decoded, so the CRC cannot be checked. The digest is reported lost.
    bad = _zip_with_crc(tmp_path, zipfile.ZIP_STORED, bad_crc=True)
    with open_archive(bad, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            stream.read(10)
            stream.seek(150_000)
            assert stream.read() == _DATA[150_000:]
            assert stream.digest_intact() is False


def test_seek_to_the_end_then_read_checks_the_crc(tmp_path: Path) -> None:
    # The concluding read hashes the gap a seek to the end skipped, on any member.
    for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        bad = _zip_with_crc(tmp_path, method, bad_crc=True)
        with open_archive(bad, seekable_members=True) as reader:
            with reader.open("m.txt") as stream:
                stream.read(10)
                stream.seek(0, io.SEEK_END)
                with pytest.raises(CorruptionError):
                    stream.read(1)

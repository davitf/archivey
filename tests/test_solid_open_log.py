"""The once-per-reader INFO record for repeated ``open()`` on a solid archive.

On a ``SOLID`` reader each ``open()`` decodes the member's compression run from its
start, so the second call decodes again what the first did. That call logs one INFO
record on ``archivey.backends``; nothing else does, and a ``DIRECT`` reader never logs.
"""

from __future__ import annotations

import io
import logging
import tarfile
import zipfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.cost import AccessCost

_LOGGER = "archivey.backends"


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if r.name == _LOGGER and "is solid" in r.getMessage()
    ]


def _tar_gz(tmp_path: Path) -> Path:
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name in ("a.txt", "b.txt", "c.txt"):
            data = name.encode() * 10
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _zip(tmp_path: Path) -> Path:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("a.txt", "b.txt", "c.txt"):
            z.writestr(name, name.encode() * 10)
    return path


def test_second_open_on_a_solid_archive_logs_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    with open_archive(_tar_gz(tmp_path)) as reader:
        assert reader.cost.access_cost is AccessCost.SOLID
        assert reader.read("c.txt") == b"c.txt" * 10
        assert _records(caplog) == []
        reader.read("a.txt")
        (record,) = _records(caplog)
        assert record.levelno == logging.INFO
        message = record.getMessage()
        assert "a.tar.gz" in message
        assert "quadratic" in message
        assert "stream_members()" in message
        reader.read("b.txt")
        with reader.open("c.txt") as stream:
            stream.read()
        assert len(_records(caplog)) == 1


def test_each_reader_logs_its_own_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    path = _tar_gz(tmp_path)
    for _ in range(2):
        with open_archive(path) as reader:
            reader.read("a.txt")
            reader.read("b.txt")
    assert len(_records(caplog)) == 2


def test_stream_members_does_not_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    with open_archive(_tar_gz(tmp_path)) as reader:
        for _member, stream in reader.stream_members():
            if stream is not None:
                stream.read()
        reader.read("a.txt")
    assert _records(caplog) == []


def test_a_direct_archive_never_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    with open_archive(_zip(tmp_path)) as reader:
        assert reader.cost.access_cost is AccessCost.DIRECT
        for name in ("c.txt", "a.txt", "b.txt", "a.txt"):
            reader.read(name)
    assert _records(caplog) == []


def test_a_failed_open_does_not_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    with open_archive(_tar_gz(tmp_path)) as reader:
        reader.read("a.txt")
        with pytest.raises(KeyError):
            reader.open("missing.txt")
        assert _records(caplog) == []

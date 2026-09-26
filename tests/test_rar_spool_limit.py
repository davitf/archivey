"""``SpoolLimits.max_bytes`` bounds the disk copy a RAR stream source needs for unrar.

A RAR opened from a stream is copied to temporary storage the first time a member has
to go through ``unrar``, which reads only files. These tests pin the bound on that copy:
refused before anything is written when the size is known, stopped at the limit when it
is not, measured across a whole volume set, and never applied to a path source.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, SpoolLimits, open_archive
from archivey.exceptions import ArchiveyUsageError, ResourceLimitError
from archivey.internal.backends import rar_reader
from archivey.internal.spool import SpoolBudget
from archivey.types import ArchiveFormat
from tests.conftest import requires_binary

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"
# Solid, so every member goes through unrar and needs the copy.
_SOLID = _FIXTURES / "basic_solid__.rar"
_VOLUMES = [_FIXTURES / "tinyvol.part1.rar", _FIXTURES / "tinyvol.part2.rar"]
_VOLUME_PAYLOAD = b"ABCDEFGH" * 200


def _config(max_bytes: int | None) -> ArchiveyConfig:
    return ArchiveyConfig(spool_limits=SpoolLimits(max_bytes=max_bytes))


def _volume_streams() -> list[io.BytesIO]:
    return [io.BytesIO(path.read_bytes()) for path in _VOLUMES]


def _volume_total() -> int:
    return sum(path.stat().st_size for path in _VOLUMES)


@pytest.fixture
def temp_artifacts(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every temp file and directory the RAR reader creates."""
    created: list[Path] = []
    real_mkdtemp = rar_reader.tempfile.mkdtemp
    real_mkstemp = rar_reader.tempfile.mkstemp

    def spy_mkdtemp(*args: object, **kwargs: object) -> str:
        made = real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(made))
        return made

    def spy_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, made = real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(made))
        return fd, made

    monkeypatch.setattr(rar_reader.tempfile, "mkdtemp", spy_mkdtemp)
    monkeypatch.setattr(rar_reader.tempfile, "mkstemp", spy_mkstemp)
    return created


@pytest.fixture
def no_unrar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the reader spawns unrar."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("unrar was spawned")

    monkeypatch.setattr(rar_reader, "open_unrar_p", refuse)


class _UnsizedStream(io.RawIOBase):
    """A seekable stream whose size archivey cannot probe cheaply.

    ``source_byte_size`` does not trust an end-seek on a stream type it does not know,
    so the reader reaches the copy without a size to check up front.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        chunk = self._data[self._pos : self._pos + len(buffer)]
        buffer[: len(chunk)] = chunk
        self._pos += len(chunk)
        return len(chunk)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: len(self._data)}
        self._pos = base[whence] + offset
        return self._pos

    def tell(self) -> int:
        return self._pos


# --- the setting ----------------------------------------------------------------------


def test_default_is_one_gib_and_unlimited_disables_it() -> None:
    assert ArchiveyConfig().spool_limits == SpoolLimits()
    assert SpoolLimits().max_bytes == 2**30
    assert SpoolLimits.UNLIMITED.max_bytes is None


@pytest.mark.parametrize("bad", [-1, True, "1", 1.5])
def test_bad_max_bytes_is_refused_at_construction(bad: object) -> None:
    with pytest.raises(ArchiveyUsageError, match=r"SpoolLimits\.max_bytes"):
        SpoolLimits(max_bytes=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["none", None, SpoolLimits, 0])
def test_config_refuses_a_non_spool_limits_value(bad: object) -> None:
    with pytest.raises(ArchiveyUsageError, match=r"spool_limits"):
        ArchiveyConfig(spool_limits=bad)  # type: ignore[arg-type]


# --- the budget itself ----------------------------------------------------------------


def _budget(max_bytes: int | None) -> SpoolBudget:
    return SpoolBudget(
        SpoolLimits(max_bytes=max_bytes),
        what="test copy",
        archive_name="a.rar",
        source_format=ArchiveFormat.RAR,
    )


def test_budget_copy_stops_at_the_limit_without_writing_past_it() -> None:
    out = io.BytesIO()
    with pytest.raises(ResourceLimitError, match=r"SpoolLimits\.max_bytes=100"):
        _budget(100).copy(io.BytesIO(b"x" * 101), out)
    assert len(out.getvalue()) <= 100


def test_budget_copy_exactly_at_the_limit_succeeds() -> None:
    out = io.BytesIO()
    _budget(100).copy(io.BytesIO(b"x" * 100), out)
    assert out.getvalue() == b"x" * 100


def test_budget_is_shared_across_copies() -> None:
    budget = _budget(100)
    budget.copy(io.BytesIO(b"x" * 60), io.BytesIO())
    with pytest.raises(ResourceLimitError):
        budget.copy(io.BytesIO(b"x" * 41), io.BytesIO())


def test_budget_without_a_limit_copies_everything() -> None:
    out = io.BytesIO()
    budget = _budget(None)
    budget.check_total(10**15)
    budget.copy(io.BytesIO(b"x" * 5000), out)
    assert len(out.getvalue()) == 5000


# --- single stream source -------------------------------------------------------------


@requires_binary("unrar")
def test_stream_within_the_limit_reads(temp_artifacts: list[Path]) -> None:
    blob = _SOLID.read_bytes()
    with open_archive(io.BytesIO(blob), config=_config(len(blob))) as archive:
        assert archive.read("file1.txt")
        assert len(temp_artifacts) == 1
    assert not temp_artifacts[0].exists()


def test_stream_over_the_limit_refuses_before_writing_or_spawning(
    temp_artifacts: list[Path], no_unrar: None
) -> None:
    blob = _SOLID.read_bytes()
    with open_archive(io.BytesIO(blob), config=_config(len(blob) - 1)) as archive:
        # Listing is served from the stream and needs no copy.
        assert "file1.txt" in [m.name for m in archive.members()]
        with pytest.raises(ResourceLimitError) as info:
            archive.read("file1.txt")
    message = str(info.value)
    assert "SpoolLimits.max_bytes" in message
    assert f"{len(blob)} bytes" in message
    assert info.value.source_format is ArchiveFormat.RAR
    assert temp_artifacts == []


def test_stream_of_unknown_size_stops_at_the_limit_and_removes_the_copy(
    temp_artifacts: list[Path], no_unrar: None
) -> None:
    blob = _SOLID.read_bytes()
    source = _UnsizedStream(blob)
    with open_archive(source, config=_config(len(blob) // 2)) as archive:
        with pytest.raises(ResourceLimitError, match=r"SpoolLimits\.max_bytes"):
            archive.read("file1.txt")
    assert len(temp_artifacts) == 1
    assert not temp_artifacts[0].exists()


def test_zero_limit_keeps_direct_reads_and_refuses_the_copy(
    temp_artifacts: list[Path], no_unrar: None
) -> None:
    stored = (_FIXTURES / "stored_m0.rar").read_bytes()
    with open_archive(io.BytesIO(stored), config=_config(0)) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert archive.read(member) == b"stored payload"
    solid = _SOLID.read_bytes()
    with open_archive(io.BytesIO(solid), config=_config(0)) as archive:
        with pytest.raises(ResourceLimitError):
            archive.read("file1.txt")
    assert temp_artifacts == []


@requires_binary("unrar")
@pytest.mark.parametrize(
    "limits",
    [SpoolLimits.UNLIMITED, SpoolLimits(max_bytes=None)],
    ids=["UNLIMITED", "None"],
)
def test_no_limit_never_refuses(limits: SpoolLimits) -> None:
    blob = _SOLID.read_bytes()
    config = ArchiveyConfig(spool_limits=limits)
    with open_archive(io.BytesIO(blob), config=config) as archive:
        assert archive.read("file1.txt")
    with open_archive(_UnsizedStream(blob), config=config) as archive:
        assert archive.read("file1.txt")


def test_cost_note_names_the_limit() -> None:
    blob = _SOLID.read_bytes()
    with open_archive(io.BytesIO(blob), config=_config(12345)) as archive:
        (note,) = archive.cost.notes
    assert "will copy the whole archive to disk" in note
    assert "SpoolLimits.max_bytes=12345" in note
    with open_archive(io.BytesIO(blob), config=_config(None)) as archive:
        (note,) = archive.cost.notes
    assert "no size limit" in note


def test_over_default_limit_is_refused(
    tmp_path: Path, temp_artifacts: list[Path], no_unrar: None
) -> None:
    """The 1 GiB default itself, driven with a sparse file one byte over it.

    No corpus archive comes near 1 GiB, so this is the only test that would notice a
    wrong default. The trailing zeros sit after the end-of-archive block, where the
    parser does not read; opening the file as a stream makes it a copy candidate.
    """
    big = tmp_path / "big.rar"
    with big.open("wb") as out:
        out.write(_SOLID.read_bytes())
        out.truncate(2**30 + 1)
    with big.open("rb") as handle, open_archive(handle) as archive:
        with pytest.raises(ResourceLimitError, match=str(2**30 + 1)):
            archive.read("file1.txt")
    assert temp_artifacts == []


# --- stream volume set ----------------------------------------------------------------


@requires_binary("unrar")
def test_volume_set_within_the_limit_reads(temp_artifacts: list[Path]) -> None:
    with open_archive(_volume_streams(), config=_config(_volume_total())) as archive:
        assert archive.read("payload.bin") == _VOLUME_PAYLOAD
        assert len(temp_artifacts) == 1
    assert not temp_artifacts[0].exists()


def test_volume_set_limit_weighs_the_total_not_each_volume(
    temp_artifacts: list[Path], no_unrar: None
) -> None:
    # Every volume fits on its own; the set does not.
    limit = _volume_total() - 1
    assert all(path.stat().st_size <= limit for path in _VOLUMES)
    with open_archive(_volume_streams(), config=_config(limit)) as archive:
        with pytest.raises(ResourceLimitError, match=r"every volume"):
            archive.read("payload.bin")
    assert temp_artifacts == []


@pytest.mark.parametrize(
    ("sources", "limit"),
    [
        # Runs out part-way through the second volume.
        pytest.param(
            _volume_streams, _VOLUMES[0].stat().st_size + 10, id="stream-volumes"
        ),
        # A mixed set copies its file volume too, and that copy counts. The limit
        # holds the stream volume alone but runs out inside the first volume, which
        # is a path.
        pytest.param(
            lambda: [_VOLUMES[0], io.BytesIO(_VOLUMES[1].read_bytes())],
            _VOLUMES[1].stat().st_size + 10,
            id="file-volume-in-a-mixed-set",
        ),
    ],
)
def test_volume_set_limit_stops_mid_copy_and_removes_the_directory(
    monkeypatch: pytest.MonkeyPatch,
    temp_artifacts: list[Path],
    no_unrar: None,
    sources: Callable[[], list[io.BytesIO | Path]],
    limit: int,
) -> None:
    """The copy itself enforces the limit, not only the up-front total.

    The up-front check is switched off, so what refuses here is the budget running
    out during the copy.
    """
    monkeypatch.setattr(SpoolBudget, "check_total", lambda self, total: None)
    with open_archive(sources(), config=_config(limit)) as archive:
        with pytest.raises(ResourceLimitError):
            archive.read("payload.bin")
    assert len(temp_artifacts) == 1
    assert not temp_artifacts[0].exists()


# --- path sources ---------------------------------------------------------------------


@requires_binary("unrar")
def test_path_source_is_never_copied_or_refused(temp_artifacts: list[Path]) -> None:
    with open_archive(_SOLID, config=_config(0)) as archive:
        assert archive.cost.notes == ()
        assert archive.read("file1.txt")
    with open_archive(_VOLUMES[0], config=_config(0)) as archive:
        assert archive.read("payload.bin") == _VOLUME_PAYLOAD
    assert temp_artifacts == []

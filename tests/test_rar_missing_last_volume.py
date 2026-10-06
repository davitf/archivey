"""A RAR volume set missing its last volume lists what it has, then raises.

Ruled 2026-10-06: the members in the volumes present are listed, those wholly
inside them read normally, the member that runs into the missing volume raises
``TruncatedError`` when read, and the listing ends with ``TruncatedError``: the
channel a cut single RAR already uses. ``unrar t`` on such a set tests every
complete member OK and fails only the one that needs the missing volume. Opening
used to refuse with "Incomplete RAR multi-volume set" and list nothing.
"""

from __future__ import annotations

import io
import random
import shutil
import subprocess
from pathlib import Path
from typing import BinaryIO

import pytest

from archivey import ArchiveyConfig, DiagnosticPolicy, open_archive
from archivey.config import RarDecompressor
from archivey.exceptions import TruncatedError
from tests.conftest import binary_refusal

_NAMES = ["a.txt", "b.txt", "c.txt", "d.txt"]
_SETS = {
    # Stored: read straight from the volumes, no decompressor involved.
    "stored": ("-m0", "-v20k"),
    # Solid and compressed: one decompressor pass over the set.
    "solid": ("-s", "-m3", "-v10k"),
}


def _payload(index: int) -> bytes:
    """Text that compresses about threefold, so either set spans several volumes."""
    rng = random.Random(index)
    return bytes(rng.choice(b"abcdefgh \n") for _ in range(20000))


def _incomplete_set(tmp_path: Path, kind: str) -> list[Path]:
    """A set written by ``rar``, with its last volume deleted: the volumes left."""
    if shutil.which("rar") is None:
        pytest.skip("needs the rar CLI to write a volume set")
    src = tmp_path / "src"
    src.mkdir()
    for index, name in enumerate(_NAMES):
        (src / name).write_bytes(_payload(index))
    out = tmp_path / "set"
    out.mkdir()
    subprocess.run(
        ["rar", "a", "-idq", "-ep1", *_SETS[kind], str(out / "x.rar")]
        + [str(src / name) for name in _NAMES],
        check=True,
    )
    volumes = sorted(out.iterdir())
    assert len(volumes) >= 3, volumes
    volumes[-1].unlink()
    return volumes[:-1]


def _config(decompressor: str, *, strict: bool = False) -> ArchiveyConfig:
    if strict:
        return ArchiveyConfig(
            rar_decompressor=RarDecompressor(decompressor),
            diagnostic_policy=DiagnosticPolicy.strict(),
        )
    return ArchiveyConfig(rar_decompressor=RarDecompressor(decompressor))


def _outcomes(
    source: Path | list[BinaryIO], config: ArchiveyConfig, *, streaming: bool
) -> list[str]:
    """What ``stream_members`` gives for each member, then how the listing ended."""
    seen: list[str] = []
    with open_archive(source, config=config, streaming=streaming) as archive:
        try:
            for member, stream in archive.stream_members():
                assert stream is not None
                try:
                    data = stream.read()
                except TruncatedError as exc:
                    assert "continues into a volume that is missing" in str(exc)
                    seen.append(f"{member.name}: truncated")
                else:
                    assert data == _payload(_NAMES.index(member.name)), member.name
                    seen.append(member.name)
        except TruncatedError as exc:
            assert "expects another volume" in str(exc)
            seen.append("end: truncated")
    return seen


def _expected(listed: int) -> list[str]:
    """Every listed member reads but the last, which runs into the gap."""
    return [*_NAMES[: listed - 1], f"{_NAMES[listed - 1]}: truncated", "end: truncated"]


def _needs(decompressor: str) -> None:
    """Members split across volumes go through the decompressor even when stored."""
    refusal = binary_refusal(decompressor)
    if refusal is not None:
        pytest.skip(refusal)


@pytest.mark.parametrize("kind", sorted(_SETS))
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_the_listing_keeps_the_members_present_and_ends_truncated(
    tmp_path: Path, kind: str, streaming: bool
) -> None:
    volumes = _incomplete_set(tmp_path, kind)
    with open_archive(volumes[0], streaming=streaming) as archive:
        report = archive.members_report()
        names = [m.name for m in report.members]
        assert names == _NAMES[: len(names)] and len(names) >= 3, names
        assert isinstance(report.error, TruncatedError)
        assert f"volume {len(volumes) + 1} is missing" in str(report.error)
    if not streaming:
        with open_archive(volumes[0]) as archive:
            with pytest.raises(TruncatedError, match="expects another volume"):
                archive.members()


@pytest.mark.parametrize("kind", sorted(_SETS))
@pytest.mark.parametrize("decompressor", ["unrar", "unar"])
@pytest.mark.parametrize("strict", [False, True], ids=["lenient", "strict"])
def test_complete_members_read_and_the_cut_one_is_truncated(
    tmp_path: Path, kind: str, decompressor: str, strict: bool
) -> None:
    """The same under a strict policy: a truncation is an error either way, and
    nothing about the members before it is a diagnostic."""
    _needs(decompressor)
    volumes = _incomplete_set(tmp_path, kind)
    config = _config(decompressor, strict=strict)
    with open_archive(volumes[0], config=config) as archive:
        listed = len(archive.members_report().members)
    for streaming in (False, True):
        assert _outcomes(volumes[0], config, streaming=streaming) == _expected(listed)


@pytest.mark.parametrize("kind", sorted(_SETS))
def test_stream_volumes_behave_like_files(tmp_path: Path, kind: str) -> None:
    _needs("unrar")
    volumes = _incomplete_set(tmp_path, kind)
    streams: list[BinaryIO] = [io.BytesIO(v.read_bytes()) for v in volumes]
    with open_archive(volumes[0]) as archive:
        listed = len(archive.members_report().members)
    assert _outcomes(streams, _config("unrar"), streaming=False) == _expected(listed)


@pytest.mark.parametrize("kind", sorted(_SETS))
def test_extract_all_writes_the_complete_members_then_raises(
    tmp_path: Path, kind: str
) -> None:
    _needs("unrar")
    volumes = _incomplete_set(tmp_path, kind)
    dest = tmp_path / "out"
    with open_archive(volumes[0], config=_config("unrar")) as archive:
        listed = len(archive.members_report().members)
        with pytest.raises(TruncatedError):
            archive.extract_all(dest)
    for index, name in enumerate(_NAMES[: listed - 1]):
        assert (dest / name).read_bytes() == _payload(index)
    assert not (dest / _NAMES[listed - 1]).exists()


@pytest.mark.skipif(shutil.which("unrar") is None, reason="needs the unrar CLI")
@pytest.mark.parametrize("kind", sorted(_SETS))
def test_unrar_tests_the_same_members(tmp_path: Path, kind: str) -> None:
    """The oracle: ``unrar t`` passes the complete members and fails the last one
    on the missing volume."""
    volumes = _incomplete_set(tmp_path, kind)
    with open_archive(volumes[0]) as archive:
        listed = [m.name for m in archive.members_report().members]
    result = subprocess.run(
        ["unrar", "t", "-p-", str(volumes[0])],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "Cannot find volume" in output, output
    for name in listed[:-1]:
        assert any(
            name in line and line.rstrip().endswith("OK")
            for line in output.splitlines()
        ), (name, output)

"""The shared test cache's publish step tolerates a parallel worker's finished build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.sample_archives import publish_cached


def test_a_build_is_moved_onto_the_cache_path(tmp_path: Path) -> None:
    final = tmp_path / "entry.zip"
    tmp = tmp_path / ".building-x"
    tmp.write_bytes(b"ours")
    publish_cached(tmp, final)
    assert final.read_bytes() == b"ours"
    assert not tmp.exists()


def _refuse_replace(src: object, dst: object) -> None:
    raise PermissionError(13, "Access is denied")  # what Windows says for an open dst


def test_a_build_another_worker_already_published_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = tmp_path / "entry.zip"
    final.write_bytes(b"theirs")
    tmp = tmp_path / ".building-x"
    tmp.write_bytes(b"ours")
    monkeypatch.setattr(os, "replace", _refuse_replace)
    publish_cached(tmp, final)
    assert final.read_bytes() == b"theirs"
    assert not tmp.exists()


def test_a_refused_publish_with_nothing_there_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp = tmp_path / ".building-x"
    tmp.write_bytes(b"ours")
    monkeypatch.setattr(os, "replace", _refuse_replace)
    with pytest.raises(PermissionError):
        publish_cached(tmp, tmp_path / "entry.zip")

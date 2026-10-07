"""``ArchiveyConfig.rar_decompressor="none"``: RAR without any external program.

Every test here makes starting a process fail, so a read that would have run
``unrar`` or ``unar`` shows up as that failure instead of passing quietly.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, RarDecompressor, SpoolLimits, open_archive
from archivey.exceptions import UnsupportedFeatureError
from tests.conftest import requires

_RAR = Path(__file__).parent / "fixtures" / "rar"
_CORPUS = Path(__file__).parent / "fixtures" / "corpus" / "rar"
_NONE = ArchiveyConfig(rar_decompressor=RarDecompressor.NONE)


@pytest.fixture(autouse=True)
def _no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a process was started: {args[:1]}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)


def test_name_is_accepted() -> None:
    assert ArchiveyConfig(rar_decompressor="none").rar_decompressor is (
        RarDecompressor.NONE
    )


@pytest.mark.parametrize("as_stream", [False, True], ids=["path", "stream"])
def test_stored_nonsolid_members_read(as_stream: bool) -> None:
    path = _RAR / "basic_nonsolid__.rar"
    source = io.BytesIO(path.read_bytes()) if as_stream else path
    with open_archive(source, config=_NONE) as ar:
        files = [m for m in ar.members() if m.is_file]
        assert files
        for member in files:
            assert len(ar.read(member)) == member.size
        # Nothing here needs a program, so there is nothing to warn about.
        assert ar.cost.notes == ()


@pytest.mark.parametrize(
    ("name", "member_name", "why"),
    [
        ("basic_solid__.rar", None, "it is compressed"),
        ("encryption__.rar", None, "it is encrypted"),
        ("encryption_stored__.rar", None, "it is encrypted"),
        ("tinyvol.part1.rar", None, "it is split across volumes"),
        # Stored, but rar set the member's own solid flag (``-s -msbin``).
        ("stored_solid_member__.rar", "second.bin", "it is part of a solid stream"),
    ],
)
def test_other_members_refused_before_any_process(
    name: str, member_name: str | None, why: str
) -> None:
    with open_archive(_RAR / name, config=_NONE, password="password") as ar:
        assert any("rar_decompressor is 'none'" in note for note in ar.cost.notes)
        member = next(
            m for m in ar.members() if m.is_file and member_name in (None, m.name)
        )
        with pytest.raises(UnsupportedFeatureError, match=why) as info:
            ar.read(member)
        assert "Set it to 'unrar'" in str(info.value)


def test_refusal_comes_before_a_stream_source_is_copied() -> None:
    # With no spool allowance, a copy would raise ResourceLimitError instead.
    config = ArchiveyConfig(
        rar_decompressor=RarDecompressor.NONE, spool_limits=SpoolLimits(max_bytes=0)
    )
    source = io.BytesIO((_RAR / "basic_solid__.rar").read_bytes())
    with open_archive(source, config=config) as ar:
        member = next(m for m in ar.members() if m.is_file)
        with pytest.raises(UnsupportedFeatureError, match="it is compressed"):
            ar.read(member)


def test_mixed_archive_reads_plain_stored_and_refuses_encrypted() -> None:
    with open_archive(
        _CORPUS / "encrypted-mixed.rar", config=_NONE, password="password"
    ) as ar:
        outcomes = []
        for _member, stream in ar.stream_members():
            if stream is None:
                continue
            try:
                stream.read()
            except UnsupportedFeatureError:
                outcomes.append("refused")
            else:
                outcomes.append("read")
        assert sorted(outcomes) == ["read", "refused", "refused"]


def test_solid_stream_members_refuses_each_member() -> None:
    with open_archive(_RAR / "basic_solid__.rar", config=_NONE) as ar:
        for _member, stream in ar.stream_members():
            if stream is not None:
                with pytest.raises(UnsupportedFeatureError):
                    stream.read()


@requires("cryptography")
def test_header_encrypted_archive_lists() -> None:
    with open_archive(
        _RAR / "encrypted_header__.rar", config=_NONE, password="header_password"
    ) as ar:
        assert [m for m in ar.members() if m.is_file]


def test_compressed_old_comment_is_none() -> None:
    # The archive comment is compressed with the RAR 1.5 algorithm; decoding it would
    # need a program.
    with open_archive(_RAR / "rar15-comment.rar", config=_NONE) as ar:
        assert ar.info.comment is None

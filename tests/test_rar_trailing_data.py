"""Bytes after a RAR end-of-archive block are ``ARCHIVE_TRAILING_DATA`` (DR-3).

A non-zero byte after the end block of any volume is a warning by default and refused
under ``DiagnosticPolicy.strict()``; zero padding is silent. ``unrar`` says nothing
about such bytes; 7-Zip 23.01 warns "There are data after the end of archive". The
listing is native, so none of these tests needs ``unrar``.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import ArchiveEofContext, DiagnosticCode, DiagnosticPolicy
from archivey.exceptions import DiagnosticRaisedError
from archivey.internal.trailing_scan import MAX_TRAILING_SCAN
from tests.conftest import requires, requires_binary

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"

# (fixture, header password): RAR 1.5-4 and RAR5, headers plain and encrypted. With
# encrypted headers the end block's bytes stop after its AES padding.
_ARCHIVES = [
    pytest.param("basic_nonsolid__rar4.rar", None, id="rar4"),
    pytest.param("basic_nonsolid__.rar", None, id="rar5"),
    pytest.param(
        "encrypted_header__rar4.rar",
        "header_password",
        id="rar4-hp",
        marks=requires("cryptography"),
    ),
    pytest.param(
        "encrypted_header__.rar",
        "header_password",
        id="rar5-hp",
        marks=requires("cryptography"),
    ),
]


def _trailing(source: object, password: str | None) -> list[ArchiveEofContext]:
    with open_archive(source, password=password) as reader:  # type: ignore[arg-type]
        assert reader.members()
        found = [
            d.context
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
        ]
    assert all(isinstance(c, ArchiveEofContext) for c in found)
    return found  # type: ignore[return-value]


def _data(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


@pytest.mark.parametrize(("name", "password"), _ARCHIVES)
def test_archive_that_ends_at_its_end_block_reports_nothing(
    name: str, password: str | None
) -> None:
    assert _trailing(io.BytesIO(_data(name)), password) == []


@pytest.mark.parametrize(("name", "password"), _ARCHIVES)
def test_zero_padding_after_the_end_block_is_silent(
    name: str, password: str | None
) -> None:
    assert _trailing(io.BytesIO(_data(name) + b"\x00" * 4096), password) == []


@pytest.mark.parametrize(("name", "password"), _ARCHIVES)
@pytest.mark.parametrize("zeros", [0, 100, 70_000])
def test_junk_after_the_end_block_is_reported(
    name: str, password: str | None, zeros: int
) -> None:
    data = _data(name) + b"\x00" * zeros + b"JUNK"
    (context,) = _trailing(io.BytesIO(data), password)
    assert context.format == "rar"
    assert context.expected_marker == "zeros_to_eof"
    assert context.observed_kind == "nonzero"
    assert context.observed_bytes == zeros


def test_junk_past_the_scan_bound_goes_unseen() -> None:
    data = _data("basic_nonsolid__.rar") + b"\x00" * MAX_TRAILING_SCAN + b"JUNK"
    assert _trailing(io.BytesIO(data), None) == []


def test_strict_policy_refuses_trailing_data() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    data = _data("basic_nonsolid__.rar") + b"JUNK"
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(io.BytesIO(data), config=config) as reader:
            reader.members()


def test_strict_policy_accepts_zero_padding() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    data = _data("basic_nonsolid__.rar") + b"\x00" * 512
    with open_archive(io.BytesIO(data), config=config) as reader:
        assert reader.members()


def test_junk_after_a_middle_volume_names_that_volume(tmp_path: Path) -> None:
    for part in ("tinyvol.part1.rar", "tinyvol.part2.rar"):
        shutil.copy(_FIXTURES / part, tmp_path / part)
    with (tmp_path / "tinyvol.part1.rar").open("ab") as handle:
        handle.write(b"JUNK")
    with open_archive(tmp_path / "tinyvol.part1.rar") as reader:
        assert reader.members()
        (diagnostic,) = [
            d
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
        ]
    assert "volume 1" in diagnostic.message.lower()
    assert isinstance(diagnostic.context, ArchiveEofContext)
    assert diagnostic.context.observed_bytes == 0


@requires_binary("rar")
def test_zeros_rar_writes_after_a_non_last_volume_are_silent(tmp_path: Path) -> None:
    # rar 7.00 -v ends every volume but the last with eight zero bytes after the end
    # block, so the zero rule is what keeps an untouched set clean.
    (tmp_path / "big.bin").write_bytes(bytes(range(256)) * 1200)
    subprocess.run(
        ["rar", "a", "-idq", "-m0", "-v100k", "vol.rar", "big.bin"],
        cwd=tmp_path,
        check=True,
    )
    first = tmp_path / "vol.part1.rar"
    assert first.read_bytes()[-8:] == b"\x00" * 8
    assert _trailing(first, None) == []

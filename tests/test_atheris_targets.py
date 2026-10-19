"""The Atheris target callables, run on their seeds without libFuzzer.

A seed that raises outside ``ArchiveyError`` would fail every fuzz run on its
first iteration. These tests are that check, plus the paths the new targets
exist to reach: an SFX payload, a ZIP pipe, a UDIF refusal, a filter decode.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from archivey import ArchiveFormat, ArchiveyError, open_archive
from tests.atheris_fuzz import TARGET_NAMES
from tests.atheris_fuzz.seeds import (
    _compressed_tar_seeds,
    _koly_block,
    _uncompressed_header_sevenzip_seeds,
    filter_seeds,
    open_detected_seeds,
)
from tests.atheris_fuzz.targets import (
    filters_one,
    open_detected_one,
    sevenzip_open_one,
    zip_open_one,
)

_PAYLOAD = b"sfx-member\n"


def _stored_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("member.txt", _PAYLOAD)
    return buf.getvalue()


def test_open_detected_reads_a_zip_behind_a_stub() -> None:
    data = b"MZ" + b"\x00" * 128 + _stored_zip()
    open_detected_one(data)
    with open_archive(io.BytesIO(data)) as arc:
        members = [member for member in arc if member.is_file]
        assert [member.name for member in members] == ["member.txt"]
        with arc.open(members[0]) as stream:
            assert stream.read() == _PAYLOAD


def test_open_detected_refuses_a_udif_image() -> None:
    data = _koly_block()
    open_detected_one(data)
    with pytest.raises(ArchiveyError, match="UDIF"):
        with open_archive(io.BytesIO(data)) as arc:
            list(arc)


def test_open_detected_reads_a_compressed_tar_and_the_gzip_around_it() -> None:
    data = _compressed_tar_seeds()[0]
    open_detected_one(data)
    with open_archive(io.BytesIO(data)) as arc:
        names = [member.name for member in arc if member.is_file]
    assert any(name.endswith("file.txt") for name in names)
    with open_archive(io.BytesIO(data), format="gz") as arc:
        assert any(member.is_file for member in arc)


def test_zip_target_reads_a_stored_archive_both_ways() -> None:
    zip_open_one(_stored_zip())


def test_sevenzip_open_reads_an_uncompressed_header(tmp_path: Path) -> None:
    """A clear next header plus a bounded read must return the member bytes.

    Header compression would hide a coder-field mutation behind a failed
    decompress. These seeds are the ones that keep the coder graph reachable,
    and the open target has to actually decode them.
    """
    blobs = _uncompressed_header_sevenzip_seeds(tmp_path)
    if not blobs:
        pytest.skip("7z CLI cannot write the filter seeds")
    for blob in blobs:
        sevenzip_open_one(blob)
        with open_archive(
            io.BytesIO(blob),
            format=ArchiveFormat.SEVEN_Z,
            password=["", "password"],
        ) as arc:
            files = [member for member in arc if member.is_file]
            assert files
            with arc.open(files[0]) as stream:
                assert stream.read(64)


def test_filter_seeds_stay_inside_the_typed_error_contract() -> None:
    for seed in filter_seeds():
        filters_one(seed)


def test_open_detected_seeds_stay_inside_the_typed_error_contract() -> None:
    for seed in open_detected_seeds():
        open_detected_one(seed)


def test_workflow_sets_a_budget_for_every_atheris_target() -> None:
    text = Path(".github/workflows/atheris-fuzz.yml").read_text(encoding="utf-8")
    missing = [
        name
        for name in TARGET_NAMES
        if f"ARCHIVEY_FUZZ_BUDGET_{name.upper()}" not in text
    ]
    assert missing == []

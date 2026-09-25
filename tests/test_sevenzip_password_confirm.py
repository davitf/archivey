"""End-to-end: the 7z password-confirmation ladder decodes only what it must.

Each test asserts *bytes decoded by the confirm pipeline*, not wall time, and each
carries its own mutation check: the same measurement taken with the ladder forced onto
the path the test exists to rule out, so a measurement that could not tell the two
apart fails here instead of passing quietly (design §5 preamble of the
bounded-password-confirmation change).

Fixtures are written into ``tmp_path`` by the ``7z`` CLI, a few MiB each; the property
they pin (confirm stops at the first member's CRC, or at the prefix) does not depend on
the folder being 200 MiB.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import BinaryIO

import pytest

import archivey.internal.backends.sevenzip_reader as sevenzip_reader_mod
from archivey import open_archive
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import ArchiveyError, EncryptionError
from archivey.internal.password_confirm import (
    CONFIRM_PREFIX_BYTES,
    ConfirmPlan,
    plan_confirm,
)
from tests.conftest import requires, requires_binary

pytestmark = [requires("cryptography"), requires_binary("7z")]

_SMALL = 4096
_BIG = 4 * 1024 * 1024
_PASSWORD = "secret"


class _CountingStream:
    """Counts the bytes read through a folder pipeline."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner
        self.bytes_read = 0

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        self.bytes_read += len(data)
        return data

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _count_pipelines(monkeypatch: pytest.MonkeyPatch) -> list[_CountingStream]:
    streams: list[_CountingStream] = []
    original = sevenzip_reader_mod.open_folder_pipeline

    def counting(*args: object, **kwargs: object) -> _CountingStream:
        stream = _CountingStream(original(*args, **kwargs))  # type: ignore[arg-type]
        streams.append(stream)
        return stream

    monkeypatch.setattr(sevenzip_reader_mod, "open_folder_pipeline", counting)
    return streams


def _payload(size: int, seed: int) -> bytes:
    # Compressible but not trivially so: LZMA2 still has work to do.
    words = [b"alpha", b"bravo", b"charlie", b"delta", b"echo", b"foxtrot"]
    out = bytearray()
    i = seed
    while len(out) < size:
        out += words[(i * 7 + i // 3) % len(words)] + b" "
        i += 1
    return bytes(out[:size])


def _build(
    tmp_path: Path, name: str, files: dict[str, bytes], *, method: str, solid: bool
) -> Path:
    src = tmp_path / f"{name}-src"
    src.mkdir()
    for file_name, data in files.items():
        (src / file_name).write_bytes(data)
    archive = tmp_path / f"{name}.7z"
    result = subprocess.run(
        [
            "7z",
            "a",
            "-t7z",
            f"-p{_PASSWORD}",
            "-mhe=off",
            f"-m0={method}",
            f"-ms={'on' if solid else 'off'}",
            str(archive),
            *files,
            "-y",
        ],
        cwd=src,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"7z CLI cannot build the fixture: {result.stderr}")
    return archive


def _first_member_read(archive: Path, password: object) -> tuple[bytes, object]:
    with open_archive(archive, password=password) as reader:  # type: ignore[arg-type]
        members = [m for m in reader.members() if m.is_file]
        with reader.open(members[0]) as stream:
            data = stream.read(1)
        return data, reader.diagnostics


def _walk_whole_unit(
    substreams: object, tail_crc: object, *, budget: int, codec_rejects: bool
) -> ConfirmPlan:
    # The mutation: the pre-change ladder, which walked every member CRC to the end.
    items = list(substreams)  # type: ignore[call-overload]
    return ConfirmPlan(tuple(items), None, confirms=True, bounded=False)


def _build_solid_copy(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """A solid store+AES folder. The ``7z`` CLI writes one folder per file for Copy."""
    py7zr = pytest.importorskip("py7zr")
    from tests.test_sevenzip_reader import _write_py7zr_archive

    archive = tmp_path / "solid-copy.7z"
    _write_py7zr_archive(
        archive,
        files,
        filters=[
            {"id": py7zr.FILTER_COPY},
            {"id": py7zr.FILTER_CRYPTO_AES256_SHA256},
        ],
        password=_PASSWORD,
    )
    return archive


@pytest.mark.parametrize("method", ["LZMA2", "Copy"])
def test_solid_folder_confirm_decodes_only_the_first_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    small, big = _payload(_SMALL, 1), _payload(_BIG, 2)
    files = {"a.bin": small, "b.bin": big}
    if method == "Copy":
        archive = _build_solid_copy(tmp_path, files)
    else:
        archive = _build(tmp_path, "solid", files, method=method, solid=True)
    with open_archive(archive, password=_PASSWORD) as reader:
        assert reader.info.is_solid, "fixture must hold both members in one folder"
    streams = _count_pipelines(monkeypatch)
    data, _ = _first_member_read(archive, _PASSWORD)
    assert data == small[:1]
    confirm = streams[0]
    # The first member's CRC settles the key.
    assert confirm.bytes_read == _SMALL

    # Mutation check: with the plan walking the folder, the same count sees it all.
    monkeypatch.setattr(sevenzip_reader_mod, "plan_confirm", _walk_whole_unit)
    streams.clear()
    _first_member_read(archive, _PASSWORD)
    assert streams[0].bytes_read == _SMALL + _BIG


def test_lzma2_late_crc_confirm_reads_the_prefix_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = _payload(_BIG, 3)
    archive = _build(tmp_path, "lzma2", {"big.bin": big}, method="LZMA2", solid=True)
    streams = _count_pipelines(monkeypatch)
    data, diagnostics = _first_member_read(archive, _PASSWORD)
    assert data == big[:1]
    # A rejecting codec settles a wrong key inside the prefix; the CRC at 4 MiB is not
    # walked.
    assert streams[0].bytes_read == CONFIRM_PREFIX_BYTES
    # Accepted without a checksum, then abandoned: reported.
    assert diagnostics.counts.get(DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED) == 1

    # Mutation check: a plan that walks to the CRC reads the whole member.
    monkeypatch.setattr(sevenzip_reader_mod, "plan_confirm", _walk_whole_unit)
    streams.clear()
    _first_member_read(archive, _PASSWORD)
    assert streams[0].bytes_read == _BIG

    monkeypatch.undo()
    with pytest.raises(EncryptionError, match="Wrong password or corrupt 7z folder"):
        _first_member_read(archive, "wrong")


def test_lzma2_late_crc_full_read_is_not_reported(tmp_path: Path) -> None:
    big = _payload(_BIG, 3)
    archive = _build(tmp_path, "lzma2", {"big.bin": big}, method="LZMA2", solid=True)
    with open_archive(archive, password=_PASSWORD) as reader:
        member = next(m for m in reader.members() if m.is_file)
        assert reader.read(member) == big
        assert (
            DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED not in reader.diagnostics.counts
        )


def test_copy_late_crc_is_walked_and_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = _payload(_BIG, 4)
    archive = _build(tmp_path, "copy", {"big.bin": big}, method="Copy", solid=True)
    streams = _count_pipelines(monkeypatch)
    data, diagnostics = _first_member_read(archive, _PASSWORD)
    assert data == big[:1]
    # Copy cannot reject a wrong key, so the CRC at the end is walked...
    assert streams[0].bytes_read == _BIG
    # ...and, having matched, the key is confirmed: an abandoned read is not reported.
    assert DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED not in diagnostics.counts

    monkeypatch.undo()
    with pytest.raises(EncryptionError, match="Wrong password or corrupt 7z folder"):
        _first_member_read(archive, "wrong")


def test_store_aes_ambiguous_candidates_the_crc_picks_the_right_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.4: store+AES, the only anchor at the folder end, a wrong candidate first."""
    big = _payload(_BIG, 5)
    archive = _build(tmp_path, "copy", {"big.bin": big}, method="Copy", solid=True)
    with open_archive(archive, password=["wrong", _PASSWORD]) as reader:
        member = next(m for m in reader.members() if m.is_file)
        assert reader.read(member) == big

    # Mutation check: skip the anchor pass (treat Copy as rejecting, so the plan stops
    # at the prefix). "wrong" then survives as INCONCLUSIVE and is served.
    monkeypatch.setattr(sevenzip_reader_mod, "_folder_codec_rejects", lambda _f: True)
    with open_archive(archive, password=["wrong", _PASSWORD]) as reader:
        member = next(m for m in reader.members() if m.is_file)
        try:
            served = reader.read(member)
        except ArchiveyError:
            served = None
        assert served != big


def test_inconclusive_candidate_is_not_promoted_when_ambiguous(
    tmp_path: Path,
) -> None:
    big = _payload(_BIG, 6)
    archive = _build(
        tmp_path,
        "lzma2-two",
        {"a.bin": big, "b.bin": _payload(_BIG, 7)},
        method="LZMA2",
        solid=False,
    )
    with open_archive(archive, password=["other", _PASSWORD]) as reader:
        members = [m for m in reader.members() if m.is_file]
        with reader.open(members[0]) as stream:
            assert stream.read(1) == big[:1]
        # "secret" survived its prefix without a CRC match, and another candidate
        # exists: accepted for this folder, kept out of known-good.
        assert reader._passwords._known_good == []  # noqa: SLF001

    with open_archive(archive, password=_PASSWORD) as reader:
        members = [m for m in reader.members() if m.is_file]
        with reader.open(members[0]) as stream:
            stream.read(1)
        # One distinct candidate: nothing else could be the right one, so promote.
        assert reader._passwords._known_good == [_PASSWORD.encode()]  # noqa: SLF001


def test_plan_confirm_is_what_the_reader_calls() -> None:
    # The mutation checks above patch this name; make sure it is the planner itself.
    assert sevenzip_reader_mod.plan_confirm is plan_confirm

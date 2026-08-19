"""Self-extracting archives: the shared forward scan and the 7z start offset.

A self-extracting archive is an executable stub with a real archive appended, so the
archive magic sits at some offset ``N > 0``. Three pieces have to agree about that
offset — the shared scanner in ``archivey.internal.sfx``, each parser's own scan, and
(in the sibling change) ``detect_format`` — so they are exercised together here rather
than once per backend.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from archivey import DEFAULT_ARCHIVEY_CONFIG, open_archive
from archivey.exceptions import CorruptionError, UnsupportedFeatureError
from archivey.internal.backends.sevenzip_parser import MAGIC_7Z, find_signature_offset
from archivey.internal.backends.sevenzip_reader import SevenZipReadBackend
from archivey.internal.password import _PasswordCandidates
from archivey.internal.sfx import SFX_MAX, scan_for_magic
from archivey.internal.streams.streamtools.slice import SlicingStream
from archivey.types import ArchiveFormat
from tests.conftest import requires

# The stub shape from Topic 8 A-34: `MZ` plus low-entropy filler. Deliberately the
# *synthetic* one — real PE/ELF stubs are the easy case, this one is what the Brotli
# content probe claims (see dev-docs/investigations/brotli-content-probe-brief.md).
_STUB = b"MZ" + b"\x90" * 4094

_FILES = {
    "alpha.txt": b"alpha\n" * 2000,
    "nested/beta.bin": bytes(range(256)) * 40,
}


def _write_7z(path: Path, *, header_encryption: bool = False) -> None:
    py7zr = pytest.importorskip("py7zr")
    source = path.parent / f"{path.stem}-src"
    for name, data in _FILES.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    password = "hunter2" if header_encryption else None
    with py7zr.SevenZipFile(
        path, "w", password=password, header_encryption=header_encryption
    ) as archive:
        for name in sorted(_FILES):
            archive.write(source / name, arcname=name)


def _sfx(tmp_path: Path, name: str, *, header_encryption: bool = False) -> Path:
    inner = tmp_path / f"{name}-inner.7z"
    _write_7z(inner, header_encryption=header_encryption)
    path = tmp_path / f"{name}.exe"
    path.write_bytes(_STUB + inner.read_bytes())
    return path


# --- the shared scanner ----------------------------------------------------------------


def test_scan_finds_magic_at_offset() -> None:
    data = b"stub" * 100 + MAGIC_7Z + b"rest"
    assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,)) == 400


def test_scan_returns_none_when_absent() -> None:
    assert scan_for_magic(io.BytesIO(b"\x00" * 10_000), (MAGIC_7Z,)) is None


def test_scan_finds_magic_straddling_a_chunk_boundary() -> None:
    # The overlap carried between reads is what makes this work; without it a magic
    # split across two 64 KiB reads is invisible.
    for split in range(1, len(MAGIC_7Z)):
        offset = 65536 - split
        data = b"\x00" * offset + MAGIC_7Z + b"\x00" * 128
        assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,)) == offset


def test_scan_returns_the_earliest_of_several_needles() -> None:
    data = b"\x00" * 50 + b"SECOND" + b"\x00" * 50 + b"FIRST"
    # Earliest position wins, not the order the needles were passed in.
    assert scan_for_magic(io.BytesIO(data), (b"FIRST", b"SECOND")) == 50


def test_scan_requires_the_whole_magic_inside_the_limit() -> None:
    data = b"\x00" * 10 + MAGIC_7Z
    assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,), limit=10 + len(MAGIC_7Z)) == 10
    # One byte short: the magic starts inside the window but does not fit in it.
    assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,), limit=15) is None


def test_scan_stops_at_the_limit() -> None:
    data = b"\x00" * 5000 + MAGIC_7Z
    assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,), limit=1000) is None


# --- 7z: signature at a nonzero origin -------------------------------------------------


def test_find_signature_offset_fast_path_and_restore() -> None:
    fp = io.BytesIO(MAGIC_7Z + b"tail")
    assert find_signature_offset(fp) == 0
    assert fp.tell() == 0, "the probe must leave the stream where it found it"


def test_find_signature_offset_scans_and_restores() -> None:
    fp = io.BytesIO(_STUB + MAGIC_7Z)
    assert find_signature_offset(fp) == len(_STUB)
    assert fp.tell() == 0


def test_find_signature_offset_reports_the_scan_window_on_a_miss() -> None:
    with pytest.raises(CorruptionError, match="self-extracting scan window"):
        find_signature_offset(io.BytesIO(b"\x90" * 1000), limit=1000)


@requires("py7zr")
def test_forced_format_opens_a_7z_behind_a_stub(tmp_path: Path) -> None:
    # Forced format=RAR has always worked on an SFX file; this is the 7z parity that
    # used to raise CorruptionError("bad magic bytes").
    path = _sfx(tmp_path, "plain")
    with open_archive(path, format=ArchiveFormat.SEVEN_Z) as archive:
        members = {m.name: m for m in archive.members() if m.is_file}
        assert set(members) == set(_FILES)
        for name, expected in _FILES.items():
            assert archive.read(members[name]) == expected


@requires("py7zr")
def test_forced_format_opens_packed_and_encoded_header_behind_a_stub(
    tmp_path: Path,
) -> None:
    # Pack-stream and encoded-header offsets are the ones that would silently read the
    # stub if only the signature seek had been rebased.
    path = _sfx(tmp_path, "encoded", header_encryption=True)
    with open_archive(
        path, format=ArchiveFormat.SEVEN_Z, password="hunter2"
    ) as archive:
        members = {m.name: m for m in archive.members() if m.is_file}
        assert set(members) == set(_FILES)
        for name, expected in _FILES.items():
            assert archive.read(members[name]) == expected


@requires("py7zr")
def test_no_7z_magic_within_the_window_raises_rather_than_listing_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-an-archive.exe"
    path.write_bytes(b"MZ" + b"\x90" * 200_000)
    with pytest.raises(CorruptionError, match="Not a 7z archive"):
        open_archive(path, format=ArchiveFormat.SEVEN_Z)


def _open_with(source, *, password=None, start_offset=0):
    return SevenZipReadBackend().open_read(
        source,
        format=ArchiveFormat.SEVEN_Z,
        streaming=False,
        passwords=_PasswordCandidates.from_input(password),
        encoding=None,
        archive_name="equiv.exe",
        config=DEFAULT_ARCHIVEY_CONFIG,
        start_offset=start_offset,
    )


@requires("py7zr")
@pytest.mark.parametrize("header_encryption", [False, True])
def test_start_offset_on_a_path_equals_an_offset_view_on_a_stream(
    tmp_path: Path, header_encryption: bool
) -> None:
    """``start_offset`` means "as if you had sliced the source" — checked literally.

    The two opens differ only in how the payload origin is expressed. Any divergence
    means the offset leaked into something other than the view.
    """
    path = _sfx(tmp_path, "equiv", header_encryption=header_encryption)
    password = "hunter2" if header_encryption else None

    with _open_with(path, password=password, start_offset=len(_STUB)) as by_offset:
        by_offset_read = {
            m.name: by_offset.read(m) for m in by_offset.members() if m.is_file
        }

    with path.open("rb") as handle:
        with _open_with(
            SlicingStream(handle, start=len(_STUB)), password=password
        ) as by_view:
            by_view_read = {
                m.name: by_view.read(m) for m in by_view.members() if m.is_file
            }

    assert by_offset_read == by_view_read == _FILES


@requires("py7zr")
def test_start_offset_is_believed_rather_than_rescanned(tmp_path: Path) -> None:
    """A decoy magic in the stub separates "opened at the offset" from "scanned again".

    With the offset supplied the decoy sits behind the origin and is invisible; without
    it the forced-format scan finds the decoy first and fails on its header.
    """
    inner = tmp_path / "decoy-inner.7z"
    _write_7z(inner)
    stub = b"MZ" + b"\x90" * 512 + MAGIC_7Z + b"\x90" * 3576
    path = tmp_path / "decoy.exe"
    path.write_bytes(stub + inner.read_bytes())

    with _open_with(path, start_offset=len(stub)) as archive:
        assert {m.name for m in archive.members() if m.is_file} == set(_FILES)

    with pytest.raises(CorruptionError):
        _open_with(path)


def test_a_format_without_stubs_refuses_a_start_offset(tmp_path: Path) -> None:
    from archivey.internal.backends.tar_reader import TarReadBackend

    path = tmp_path / "x.tar"
    path.write_bytes(b"\x00" * 1024)
    with pytest.raises(UnsupportedFeatureError, match="nonzero start offset"):
        TarReadBackend().open_read(
            path,
            format=ArchiveFormat.TAR,
            streaming=False,
            passwords=None,
            encoding=None,
            archive_name=path.name,
            config=DEFAULT_ARCHIVEY_CONFIG,
            start_offset=64,
        )


def test_sfx_max_is_one_shared_bound() -> None:
    """RAR, 7z and detection bind to the same constant, not three drifting copies."""
    from archivey.internal.backends import rar_parser

    assert rar_parser.SFX_MAX is SFX_MAX

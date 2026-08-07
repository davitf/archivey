"""Guardrail assertions for the simplicity-consistency review.

Pins **today's** behaviour for:
- format-law cells (so they cannot silently change), and
- accidents (red half of eventual fixes — may xfail once pay list lands).

These tests live under ``review/`` so they do not expand the product suite until
the maintainer promotes them. Run with:

    uv run --no-sync pytest review/simplicity-consistency/tests/ -q

Config: ``[all]``.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path

import pytest

from archivey import extract, open_archive
from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
)


def _pipe(data: bytes) -> io.RawIOBase:
    class Pipe(io.RawIOBase):
        def __init__(self, blob: bytes) -> None:
            self._buf = io.BytesIO(blob)

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            return self._buf.read(size)

        def seekable(self) -> bool:
            return False

    return Pipe(data)


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"hi")
    return buf.getvalue()


# --- Format law (guardrails) -------------------------------------------------


def test_pipe_ra_refuses_loudly_zip_and_tar() -> None:
    """ADR 0010: streaming=False never spools a pipe."""
    import tarfile

    z = _zip_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(_pipe(z))

    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(StreamNotSeekableError):
        open_archive(_pipe(tbuf.getvalue()))


def test_zip_cannot_stream_from_pipe_even_with_streaming_true() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(_pipe(_zip_bytes()), streaming=True)


def test_extract_auto_streams_tar_pipe_but_not_zip(tmp_path: Path) -> None:
    import tarfile

    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    report = extract(_pipe(tbuf.getvalue()), tmp_path / "tar_out")
    assert len(report.results) >= 1

    with pytest.raises(StreamNotSeekableError):
        extract(_pipe(_zip_bytes()), tmp_path / "zip_out")


def test_header_encrypted_rar_needs_password_at_open() -> None:
    path = Path("tests/fixtures/rar/encrypted_header__.rar")
    if not path.exists():
        pytest.skip("fixture missing")
    with pytest.raises(EncryptionError):
        with open_archive(path) as r:
            list(r.members())


def test_data_encrypted_rar_lists_without_password() -> None:
    path = Path("tests/fixtures/rar/encryption__.rar")
    if not path.exists():
        pytest.skip("fixture missing")
    with open_archive(path) as r:
        assert list(r.members())  # listing OK
        enc = next(m for m in r.members() if m.is_encrypted)
        with pytest.raises(EncryptionError):
            r.read(enc)


def test_unique_names_all_is_current() -> None:
    with open_archive(io.BytesIO(_zip_bytes())) as r:
        assert all(m.is_current for m in r.members())


def test_duplicate_zip_last_entry_wins_is_current() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dup.txt", b"first")
        z.writestr("dup.txt", b"second")
    with open_archive(io.BytesIO(buf.getvalue())) as r:
        dups = [m for m in r.members() if m.name == "dup.txt"]
        assert [m.is_current for m in dups] == [False, True]


# --- Accidents (red half — pin current wrong behaviour) ---------------------


def test_empty_volume_sequence_raises_raw_valueerror_today() -> None:
    """F1 red: today builtins.ValueError; fix should become ArchiveyUsageError."""
    with pytest.raises(ValueError, match="empty"):
        open_archive([])


def test_encoding_silently_accepted_on_7z_today() -> None:
    """F2 red: encoding= ignored on 7z; fix may reject."""
    path = Path("tests/fixtures/sevenzip/lz4.7z")
    if not path.exists():
        pytest.skip("fixture missing")
    with open_archive(path, encoding="cp932") as r:
        assert list(r.members())


def test_zip_underlying_close_misclassified_as_corruption_today() -> None:
    """F3 red: underlying ZipFile closed while reader live → CorruptionError."""
    buf = io.BytesIO(_zip_bytes())
    with open_archive(buf) as r:
        member = next(m for m in r.members() if m.type.name == "FILE")
        r._archive.close()  # type: ignore[attr-defined]
        with pytest.raises(CorruptionError, match="(?i)already closed"):
            r.open(member)


def test_single_file_compressed_size_path_gate_today(tmp_path: Path) -> None:
    """F6 red: BytesIO leaves compressed_size None; Path fills it."""
    raw = gzip.compress(b"hello world" * 50)
    with open_archive(io.BytesIO(raw)) as r:
        assert r.members()[0].compressed_size is None
    p = tmp_path / "x.gz"
    p.write_bytes(raw)
    with open_archive(p) as r:
        assert r.members()[0].compressed_size == len(raw)


def test_reader_close_then_open_is_usage_error() -> None:
    """Settled #225 path — not F3; pins the correct half."""
    r = open_archive(io.BytesIO(_zip_bytes()))
    member = next(m for m in r.members() if m.type.name == "FILE")
    r.close()
    with pytest.raises(ArchiveyUsageError):
        r.open(member)

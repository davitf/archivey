"""A wrong-typed ``format=`` argument is a usage error, on every public entry point.

Four public functions take a format argument, and three of them accepted a
``StreamFormat`` — the codec half of an ``ArchiveFormat``'s ``(container, stream)``
pair — without noticing: ``format_availability()`` fabricated a ``FormatAvailability``
whose ``format`` field violated its own declared type, and ``open_archive()`` /
``extract()`` let an ``AttributeError`` naming a private attribute cross the public
boundary. ``open_stream()`` accepts both types by design, but silently *ignored* a
value that was neither and auto-detected instead.

The type checkers already reject all four calls (both run over ``src/`` only, so an
untyped caller is the exposure). These tests cover what happens when the call is made
anyway: ``ArchiveyUsageError``, outside the ``ArchiveyError`` tree per ADR 0012.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyError,
    ArchiveyUsageError,
    FormatSupport,
    StreamFormat,
    extract,
    format_availability,
    open_archive,
    open_stream,
)

CONTENT = b"the quick brown fox jumps over the lazy dog\n"


@pytest.fixture
def zip_path(tmp_path: Path) -> Path:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("member.txt", CONTENT)
    return path


@pytest.fixture
def gz_path(tmp_path: Path) -> Path:
    path = tmp_path / "member.txt.gz"
    path.write_bytes(gzip.compress(CONTENT))
    return path


# --- format_availability ----------------------------------------------------------


def test_format_availability_rejects_a_stream_format() -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        format_availability(StreamFormat.ZSTD)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "StreamFormat.ZSTD" in message
    assert "ArchiveFormat" in message


def test_format_availability_names_the_formats_that_contain_the_codec() -> None:
    """The message ends the mistake: a raw ``.zst`` stream, or a tar compressed with it."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        format_availability(StreamFormat.ZSTD)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "ArchiveFormat.ZST" in message
    assert "ArchiveFormat.TAR_ZST" in message


@pytest.mark.parametrize("value", ["zip", None, 7, StreamFormat.UNCOMPRESSED])
def test_format_availability_rejects_any_non_archive_format(value: object) -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        format_availability(value)  # type: ignore[arg-type]

    assert "ArchiveFormat" in str(exc_info.value)


def test_format_availability_usage_error_escapes_archiveyerror_handlers() -> None:
    """ADR 0012: a caller mistake sits outside the tree, so this handler must not catch."""
    with pytest.raises(ArchiveyUsageError):
        try:
            format_availability(StreamFormat.ZSTD)  # type: ignore[arg-type]
        except ArchiveyError:  # pragma: no cover - the point is that it does not fire
            pytest.fail("ArchiveyUsageError was swallowed by `except ArchiveyError`")


def test_unknown_format_is_still_a_legitimate_hintless_none() -> None:
    """A real ``ArchiveFormat`` answering NONE with an empty ``missing`` is an answer.

    The fix keys on the argument's *type*, not on the shape of the verdict, so this
    genuine hintless NONE must survive it.
    """
    availability = format_availability(ArchiveFormat.UNKNOWN)

    assert availability.format is ArchiveFormat.UNKNOWN
    assert availability.support is FormatSupport.NONE
    assert availability.missing == ()


def test_every_archive_format_still_answers() -> None:
    for fmt in (ArchiveFormat.ZIP, ArchiveFormat.TAR_GZ, ArchiveFormat.ZST):
        assert format_availability(fmt).format is fmt


# --- open_archive -----------------------------------------------------------------


def test_open_archive_rejects_a_stream_format(zip_path: Path) -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        open_archive(zip_path, format=StreamFormat.ZSTD)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "StreamFormat.ZSTD" in message
    assert "ArchiveFormat.ZST" in message


def test_open_archive_does_not_leak_an_attribute_error(zip_path: Path) -> None:
    """The old failure was ``'StreamFormat' object has no attribute 'container'``."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        open_archive(zip_path, format=StreamFormat.ZSTD)  # type: ignore[arg-type]

    assert not isinstance(exc_info.value, AttributeError)
    assert "has no attribute" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_open_archive_rejects_before_it_reads_the_source() -> None:
    """The spec requires the refusal before the source is resolved, peeked or read."""
    source = io.BytesIO(b"PK\x03\x04not really a zip")
    with pytest.raises(ArchiveyUsageError):
        open_archive(source, format=StreamFormat.ZSTD)  # type: ignore[arg-type]

    assert source.tell() == 0


@pytest.mark.parametrize("value", ["zip", 7])
def test_open_archive_rejects_any_non_archive_format(
    value: object, zip_path: Path
) -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        open_archive(zip_path, format=value)  # type: ignore[arg-type]

    assert "ArchiveFormat" in str(exc_info.value)


def test_open_archive_still_accepts_an_archive_format_and_none(zip_path: Path) -> None:
    for fmt in (None, ArchiveFormat.ZIP):
        with open_archive(zip_path, format=fmt) as reader:
            assert [m.name for m in reader.members()] == ["member.txt"]


# --- extract ----------------------------------------------------------------------


def test_extract_rejects_a_stream_format(zip_path: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    with pytest.raises(ArchiveyUsageError) as exc_info:
        extract(zip_path, dest, format=StreamFormat.ZSTD)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "StreamFormat.ZSTD" in message
    assert "ArchiveFormat.ZST" in message


def test_extract_rejects_before_it_touches_the_source(
    zip_path: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    with pytest.raises(ArchiveyUsageError):
        extract(zip_path, dest, format=StreamFormat.ZSTD)  # type: ignore[arg-type]

    assert not dest.exists()


def test_extract_still_accepts_an_archive_format(
    zip_path: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    extract(zip_path, dest, format=ArchiveFormat.ZIP)

    assert (dest / "member.txt").read_bytes() == CONTENT


# --- open_stream: the deliberate exception ----------------------------------------


def test_open_stream_keeps_accepting_both_format_types(gz_path: Path) -> None:
    """A raw stream has no container, so ``StreamFormat`` is a first-class argument here."""
    for fmt in (StreamFormat.GZIP, ArchiveFormat.GZ, None):
        with open_stream(gz_path, format=fmt) as stream:
            assert stream.read() == CONTENT


def test_open_stream_rejects_a_format_of_neither_type(gz_path: Path) -> None:
    """It used to ignore the argument and auto-detect, which is the same dishonesty."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        open_stream(gz_path, format="zst")  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "StreamFormat" in message
    assert "ArchiveFormat" in message


def test_open_stream_rejects_a_format_of_neither_type_before_reading() -> None:
    source = io.BytesIO(gzip.compress(CONTENT))
    with pytest.raises(ArchiveyUsageError):
        open_stream(source, format=7)  # type: ignore[arg-type]

    assert source.tell() == 0

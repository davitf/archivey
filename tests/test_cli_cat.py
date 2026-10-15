"""``archivey cat``: member contents, or the decompressed outer stream, to stdout."""

from __future__ import annotations

import gzip
import io
import lzma
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from archivey.cli.exit_codes import (
    EXIT_BROKEN_PIPE,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
)
from archivey.cli.main import main

_MEMBERS = {"a.txt": b"hello\n", "b.log": b"world\n", "d/c.log": b"deep\n"}


def _tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        directory = tarfile.TarInfo("d")
        directory.type = tarfile.DIRTYPE
        tf.addfile(directory)
        for name, data in _MEMBERS.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "a.txt"
        tf.addfile(link)
    return buf.getvalue()


@pytest.fixture
def tar_xz(tmp_path: Path) -> Path:
    path = tmp_path / "t.tar.xz"
    path.write_bytes(lzma.compress(_tar_bytes()))
    return path


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    path = tmp_path / "z.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in _MEMBERS.items():
            zf.writestr(name, data)
    return path


class _Pipe(io.RawIOBase):
    """Bytes that read like a pipe: forward only, no seek."""

    def __init__(self, data: bytes) -> None:
        self._data = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        return self._data.readinto(buffer)


class _Run:
    """One ``main()`` call with a binary-backed stdout and a captured stderr."""

    def __init__(self, argv: list[str], stdin: bytes | None = None) -> None:
        self._stdout = io.BytesIO()
        out = io.TextIOWrapper(self._stdout, encoding="utf-8")
        err = io.StringIO()
        saved = sys.stdin
        if stdin is not None:
            sys.stdin = io.TextIOWrapper(io.BufferedReader(_Pipe(stdin)))
        try:
            self.code = main(argv, out=out, err=err)
        finally:
            sys.stdin = saved
        out.flush()
        self.stdout = self._stdout.getvalue()
        self.stderr = err.getvalue()


def test_cat_writes_file_members_in_archive_order(tar_xz: Path) -> None:
    run = _Run(["cat", str(tar_xz)])
    assert run.code == EXIT_OK, run.stderr
    # The directory and the symlink have no body of their own.
    assert run.stdout == b"hello\nworld\ndeep\n"


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (["b.log"], b"world\n"),
        (["*.log"], b"world\n"),  # ``*`` stays in the top folder
        (["**/*.log"], b"world\ndeep\n"),
        (["d"], b"deep\n"),  # a folder brings its contents
        (["--exclude", "a.txt"], b"world\ndeep\n"),
    ],
)
def test_cat_selects_members_like_the_other_verbs(
    sample_zip: Path, patterns: list[str], expected: bytes
) -> None:
    run = _Run(["cat", str(sample_zip), *patterns])
    assert run.code == EXIT_OK, run.stderr
    assert run.stdout == expected


def test_cat_pattern_that_matches_nothing_fails_before_writing(
    sample_zip: Path,
) -> None:
    run = _Run(["cat", str(sample_zip), "nomatch"])
    assert run.code == EXIT_FAIL
    assert "pattern matched no members: 'nomatch'" in run.stderr
    # ZIP has a free index, so the patterns are judged before any byte is written.
    assert run.stdout == b""


def test_cat_warns_about_one_unmatched_pattern_among_several(
    sample_zip: Path,
) -> None:
    # As for test and extract: the run fails only when the patterns select nothing.
    run = _Run(["cat", str(sample_zip), "a.txt", "nomatch"])
    assert run.code == EXIT_OK
    assert "pattern matched no members: 'nomatch'" in run.stderr
    assert run.stdout == b"hello\n"


def test_cat_pattern_that_matches_nothing_fails_after_a_pass(tar_xz: Path) -> None:
    # A compressed tar has no index: the pass itself finds the unmatched pattern.
    run = _Run(["cat", str(tar_xz), "nomatch"])
    assert run.code == EXIT_FAIL
    assert "pattern matched no members: 'nomatch'" in run.stderr


def test_cat_verbose_names_members_on_stderr(tar_xz: Path) -> None:
    run = _Run(["cat", str(tar_xz), "-v"])
    assert run.code == EXIT_OK
    assert run.stdout == b"hello\nworld\ndeep\n"
    lines = run.stderr.splitlines()
    assert "a.txt" in lines
    assert "skip d/" in lines or "skip d" in lines
    assert "skip link.txt" in lines


def test_cat_reads_an_archive_from_stdin(tar_xz: Path) -> None:
    run = _Run(["cat", "-", "**/*.log"], stdin=tar_xz.read_bytes())
    assert run.code == EXIT_OK, run.stderr
    assert run.stdout == b"world\ndeep\n"


def test_cat_from_stdin_names_stdin_when_the_format_must_seek() -> None:
    rar = Path(__file__).parent / "fixtures" / "rar" / "wildcard_names__.rar"
    run = _Run(["cat", "-"], stdin=rar.read_bytes())
    assert run.code == EXIT_FAIL
    assert "stdin: the rar format cannot be read from a pipe" in run.stderr


def test_the_stdin_token_stays_reserved_for_the_other_verbs(tar_xz: Path) -> None:
    run = _Run(["list", "-"], stdin=tar_xz.read_bytes())
    assert run.code == EXIT_USAGE
    assert "'-' token for stdin is reserved" in run.stderr


def test_cat_stops_at_a_damaged_member(tmp_path: Path) -> None:
    data = bytearray(gzip.compress(b"abc" * 1000))
    data[-8] ^= 1  # the CRC32 in the trailer
    path = tmp_path / "bad.gz"
    path.write_bytes(bytes(data))
    run = _Run(["cat", str(path)])
    assert run.code == EXIT_FAIL
    assert run.stderr.startswith("archivey: bad: ")


def test_cat_outer_removes_only_the_compression_layer(tar_xz: Path) -> None:
    run = _Run(["cat", str(tar_xz), "--outer"])
    assert run.code == EXIT_OK, run.stderr
    assert run.stdout == _tar_bytes()


def test_cat_outer_from_stdin(tar_xz: Path) -> None:
    run = _Run(["cat", "-", "--outer"], stdin=tar_xz.read_bytes())
    assert run.code == EXIT_OK, run.stderr
    assert run.stdout == _tar_bytes()


def test_cat_and_outer_agree_on_a_single_compressed_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt.gz"
    path.write_bytes(gzip.compress(b"notes\n"))
    assert _Run(["cat", str(path)]).stdout == b"notes\n"
    assert _Run(["cat", str(path), "--outer"]).stdout == b"notes\n"


def test_cat_outer_on_a_container_says_to_leave_the_flag_out(
    sample_zip: Path,
) -> None:
    run = _Run(["cat", str(sample_zip), "--outer"])
    assert run.code == EXIT_FAIL
    assert "is a zip archive, which has no compression layer" in run.stderr
    assert "leave out --outer" in run.stderr
    assert "open_archive" not in run.stderr


def test_cat_outer_on_a_directory_is_a_usage_error(tmp_path: Path) -> None:
    run = _Run(["cat", str(tmp_path), "--outer"])
    assert run.code == EXIT_USAGE
    assert "is a directory, which has no compression layer" in run.stderr


@pytest.mark.parametrize("extra", [["a.txt"], ["--exclude", "a.txt"], ["--track-io"]])
def test_cat_outer_refuses_member_options(tar_xz: Path, extra: list[str]) -> None:
    run = _Run(["cat", str(tar_xz), "--outer", *extra])
    assert run.code == EXIT_USAGE
    assert run.stdout == b""


def test_cat_needs_a_binary_stdout(sample_zip: Path) -> None:
    err = io.StringIO()
    assert main(["cat", str(sample_zip)], out=io.StringIO(), err=err) == EXIT_USAGE
    assert "binary standard output" in err.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_cat_into_a_closed_pipe_exits_141(tmp_path: Path) -> None:
    path = tmp_path / "big.gz"
    path.write_bytes(gzip.compress(b"y\n" * 4_000_000, compresslevel=1))
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    with os.fdopen(write_fd, "w") as out:
        assert main(["cat", str(path)], out=out, err=io.StringIO()) == (
            EXIT_BROKEN_PIPE
        )

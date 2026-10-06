"""7z member names that hold a lone UTF-16 surrogate (U+D800-U+DFFF).

A 7z name is a sequence of UTF-16 code units, and NTFS allows any 16-bit unit in a
file name, so a 7z made on Windows can carry a surrogate that has no partner. The
names decode with ``surrogatepass`` and keep the surrogate, as 7-Zip does. On POSIX,
extraction writes the surrogate as its three-byte UTF-8 form (``ed a0 80`` for
U+D800), which is what 7-Zip 23.01 writes on Linux. On Windows the name is used as
it is.

U+DC80-U+DCFF is the exception: in a Python ``str`` that range also means an
undecodable byte (``surrogateescape``), and archivey reads it that way when it
extracts. ``raw_name`` keeps the stored code units for every name.
"""

from __future__ import annotations

import errno
import io
import os
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

import archivey
from archivey.cli.exit_codes import EXIT_OK
from archivey.cli.main import main
from archivey.exceptions import ExtractionError, FilterRejectionError
from archivey.internal.filters import disk_spelling
from archivey.types import ExtractionPolicy, ExtractionStatus
from tests.conftest import requires_binary

_POSIX = sys.platform != "win32"
# APFS refuses a name that is not valid UTF-8 (EILSEQ), so the bytes 7-Zip writes on
# Linux cannot land on the macOS runner's disk.
_BYTE_NAMES = _POSIX and sys.platform != "darwin"


def _u(value: int) -> bytes:
    assert 0 <= value < 0x80
    return bytes([value])


def _prop(prop: int, payload: bytes) -> bytes:
    return bytes([prop]) + _u(len(payload)) + payload


def _surrogate_7z(
    files: list[tuple[str, bytes]], *, comment: str | None = None
) -> bytes:
    """A plain-header 7z with one COPY folder per file and the names as given.

    The names, and the archive ``comment`` when one is given, are encoded with
    ``surrogatepass``, so a lone surrogate is stored as that code unit. Every member
    must have data.
    """
    packed = b"".join(data for _, data in files)
    count = len(files)
    sizes = b"".join(_u(len(data)) for _, data in files)
    crcs = b"".join(
        (zlib.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little") for _, data in files
    )
    pack_info = b"\x06" + _u(0) + _u(count) + b"\x09" + sizes + b"\x00"
    folder = _u(1) + b"\x00"  # one COPY coder
    unpack_info = (
        (b"\x07\x0b" + _u(count) + b"\x00" + folder * count + b"\x0c" + sizes)
        + b"\x0a\x01"
        + crcs
        + b"\x00"
    )
    streams_info = b"\x04" + pack_info + unpack_info + b"\x00"
    names = bytearray(b"\x00")
    for name, _ in files:
        names += name.encode("utf-16le", "surrogatepass") + b"\x00\x00"
    props = _prop(0x11, bytes(names))
    if comment is not None:
        text = comment.encode("utf-16le", "surrogatepass") + b"\x00\x00"
        props += _prop(0x16, b"\x00" + text)  # kComment, not external
    files_info = b"\x05" + _u(count) + props + b"\x00"
    header = b"\x01" + streams_info + files_info + b"\x00"
    start_header = (
        len(packed).to_bytes(8, "little")
        + len(header).to_bytes(8, "little")
        + (zlib.crc32(header) & 0xFFFFFFFF).to_bytes(4, "little")
    )
    return (
        b"7z\xbc\xaf'\x1c\x00\x04"
        + (zlib.crc32(start_header) & 0xFFFFFFFF).to_bytes(4, "little")
        + start_header
        + packed
        + header
    )


_FILES = [
    ("hi\ud800.txt", b"high"),
    ("ok.txt", b"ok"),
    ("z\udfff", b"last"),
    ("dir\udbff/in.txt", b"nested"),
]


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "surrogate.7z"
    path.write_bytes(_surrogate_7z(_FILES))
    return path


def _tree(root: Path) -> dict[bytes, bytes]:
    """Every regular file under ``root`` as its relative path in bytes → contents."""
    out: dict[bytes, bytes] = {}
    broot = os.fsencode(root)
    for dirpath, _dirs, filenames in os.walk(broot):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, broot).replace(os.sep.encode(), b"/")
            with open(full, "rb") as f:
                out[rel] = f.read()
    return out


def test_every_member_lists_with_its_surrogate(archive: Path) -> None:
    with archivey.open_archive(archive) as reader:
        members = reader.members()
    assert [m.name for m in members] == [name for name, _ in _FILES]
    assert [m.raw_name for m in members] == [
        name.encode("utf-16le", "surrogatepass") for name, _ in _FILES
    ]


def test_a_member_with_a_surrogate_reads(archive: Path) -> None:
    with archivey.open_archive(archive) as reader:
        assert reader.read("hi\ud800.txt") == b"high"
        assert reader.read("dir\udbff/in.txt") == b"nested"
        assert [(m.name, s.read()) for m, s in reader.stream_members()] == _FILES


@pytest.mark.skipif(not _BYTE_NAMES, reason="needs a filesystem that takes any bytes")
@pytest.mark.parametrize("policy", list(ExtractionPolicy))
def test_posix_extraction_writes_the_surrogate_as_utf8(
    archive: Path, tmp_path: Path, policy: ExtractionPolicy
) -> None:
    dest = tmp_path / "out"
    results = archivey.extract(archive, dest, policy=policy)
    assert [r.status for r in results] == [ExtractionStatus.EXTRACTED] * len(_FILES)
    assert _tree(dest) == {
        b"hi\xed\xa0\x80.txt": b"high",
        b"ok.txt": b"ok",
        b"z\xed\xbf\xbf": b"last",
        b"dir\xed\xaf\xbf/in.txt": b"nested",
    }
    by_name = {r.member.name: r for r in results}
    written = by_name["hi\ud800.txt"]
    assert written.path is not None
    assert os.fsencode(written.path.name) == b"hi\xed\xa0\x80.txt"
    # The on-disk spelling is not a rename: the stored name is the name.
    assert written.presented_name is None
    assert written.requested_path == written.path


@pytest.mark.skipif(not _BYTE_NAMES, reason="needs a filesystem that takes any bytes")
@requires_binary("7z")
def test_posix_extraction_matches_the_7z_tool(archive: Path, tmp_path: Path) -> None:
    """7-Zip 23.01 on Linux writes each lone surrogate as its UTF-8 form."""
    seven_zip = shutil.which("7z")
    assert seven_zip is not None
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    subprocess.run(
        [seven_zip, "x", "-y", str(archive)],
        cwd=theirs,
        check=True,
        capture_output=True,
    )
    ours = tmp_path / "ours"
    archivey.extract(archive, ours)
    assert _tree(ours) == _tree(theirs)


@pytest.mark.skipif(not _BYTE_NAMES, reason="needs a filesystem that takes any bytes")
@requires_binary("7z")
def test_a_unit_in_the_escape_range_is_the_one_difference_from_the_7z_tool(
    tmp_path: Path,
) -> None:
    """U+DC80-U+DCFF: 7-Zip writes its three UTF-8 bytes, archivey the one byte.

    Measured with 7-Zip 23.01 on Linux. archivey writes the byte the unit stands for
    in a ``str``, which the default policy escapes to ``%80``. Every other member is
    written as 7-Zip writes it.
    """
    archive = tmp_path / "low.7z"
    archive.write_bytes(_surrogate_7z([*_FILES, ("lo\udc80.txt", b"low")]))
    seven_zip = shutil.which("7z")
    assert seven_zip is not None
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    subprocess.run(
        [seven_zip, "x", "-y", str(archive)],
        cwd=theirs,
        check=True,
        capture_output=True,
    )
    ours = tmp_path / "ours"
    archivey.extract(archive, ours)
    ours_tree, theirs_tree = _tree(ours), _tree(theirs)
    assert ours_tree.pop(b"lo%80.txt") == b"low"
    assert theirs_tree.pop(b"lo\xed\xb2\x80.txt") == b"low"
    assert ours_tree == theirs_tree
    assert len(ours_tree) == len(_FILES)


@pytest.mark.skipif(sys.platform != "darwin", reason="APFS refuses the bytes")
def test_a_utf8_only_filesystem_refusal_is_a_typed_failure(
    archive: Path, tmp_path: Path
) -> None:
    """On APFS the name 7-Zip writes on Linux is refused: a typed failure, not a crash."""
    dest = tmp_path / "out"
    results = archivey.extract(archive, dest, on_error="continue")
    by_name = {r.member.name: r for r in results}
    assert by_name["ok.txt"].status is ExtractionStatus.EXTRACTED
    for name in ("hi\ud800.txt", "z\udfff"):
        result = by_name[name]
        if result.status is ExtractionStatus.EXTRACTED:
            continue  # a volume that takes any bytes
        assert result.status is ExtractionStatus.FAILED
        assert isinstance(result.error, ExtractionError)
        cause = result.error.__cause__
        assert isinstance(cause, OSError)
        assert cause.errno == errno.EILSEQ


@pytest.mark.skipif(_POSIX, reason="Windows keeps the exact name")
def test_windows_extraction_uses_the_exact_name(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    archivey.extract(archive, dest)
    assert (dest / "hi\ud800.txt").read_bytes() == b"high"
    assert (dest / "z\udfff").read_bytes() == b"last"
    assert (dest / "dir\udbff" / "in.txt").read_bytes() == b"nested"


@pytest.mark.skipif(not _BYTE_NAMES, reason="needs a filesystem that takes any bytes")
def test_a_surrogate_name_and_its_utf8_bytes_collide(tmp_path: Path) -> None:
    """``hi\\ud800`` and a name whose undecodable bytes are ``ed a0 80`` name one file.

    The second is resolved by the overwrite policy, not written over the first.
    """
    archive = tmp_path / "pair.7z"
    archive.write_bytes(
        _surrogate_7z([("hi\ud800", b"first"), ("hi\udced\udca0\udc80", b"second")])
    )
    dest = tmp_path / "out"
    results = archivey.extract(
        archive, dest, policy=ExtractionPolicy.TRUSTED, overwrite="skip"
    )
    assert results[0].status is ExtractionStatus.EXTRACTED
    assert results[1].status is ExtractionStatus.NOT_OVERWRITTEN
    assert _tree(dest) == {b"hi\xed\xa0\x80": b"first"}


def test_a_lone_surrogate_in_the_comment_does_not_refuse_the_archive(
    tmp_path: Path,
) -> None:
    """The comment is UTF-16 code units too, and decodes as the names do."""
    archive = tmp_path / "comment.7z"
    archive.write_bytes(_surrogate_7z([("ok.txt", b"ok")], comment="note\ud800"))
    with archivey.open_archive(archive) as reader:
        assert reader.info.comment == "note\ud800"
        assert [m.name for m in reader.members()] == ["ok.txt"]
        assert reader.read("ok.txt") == b"ok"


def test_a_rejection_names_the_stored_member(tmp_path: Path) -> None:
    """The checks see the name that reaches disk; the error reports ``member.name``."""
    archive = tmp_path / "escape.7z"
    archive.write_bytes(_surrogate_7z([("\ud800/../x", b"x"), ("ok.txt", b"ok")]))
    dest = tmp_path / "out"
    results = archivey.extract(archive, dest, on_error="continue")
    blocked, ok = results
    assert ok.status is ExtractionStatus.EXTRACTED
    assert blocked.status is ExtractionStatus.BLOCKED
    assert isinstance(blocked.error, FilterRejectionError)
    assert blocked.error.member_name == blocked.member.name == "\ud800/../x"
    assert "member='\\ud800/../x'" in str(blocked.error)


def test_a_low_surrogate_in_the_escape_range_is_a_byte(tmp_path: Path) -> None:
    """U+DC80-U+DCFF lists as stored, and extracts as the byte it stands for.

    In a ``str`` the unit cannot be told apart from a ``surrogateescape`` byte, so
    extraction treats it as one, as it does for every format: under ``STRICT`` it is
    written ``%80``. 7-Zip writes ``ed b2 80`` on Linux; ``raw_name`` keeps the unit.
    """
    archive = tmp_path / "low.7z"
    archive.write_bytes(_surrogate_7z([("lo\udc80.txt", b"low")]))
    with archivey.open_archive(archive) as reader:
        (member,) = reader.members()
    assert member.name == "lo\udc80.txt"
    assert member.raw_name == "lo\udc80.txt".encode("utf-16le", "surrogatepass")
    dest = tmp_path / "out"
    (result,) = archivey.extract(archive, dest)
    assert result.status is ExtractionStatus.EXTRACTED
    assert result.presented_name == "lo\udc80.txt"
    assert (dest / "lo%80.txt").read_bytes() == b"low"


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI with strict UTF-8 streams, which raise on a lone surrogate."""
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(raw_out, encoding="utf-8", errors="strict")
    err = io.TextIOWrapper(raw_err, encoding="utf-8", errors="strict")
    code = main(argv, out=out, err=err)
    out.flush()
    err.flush()
    return code, raw_out.getvalue().decode(), raw_err.getvalue().decode()


def test_cli_list_escapes_the_surrogate(archive: Path) -> None:
    code, out, _ = _run_cli(["list", str(archive)])
    assert code == EXIT_OK
    assert "hi\\ud800.txt" in out
    assert "z\\udfff" in out
    assert "dir\\udbff/in.txt" in out
    assert "ok.txt" in out


@pytest.mark.parametrize("verb", ["test", "extract"])
def test_cli_member_lines_escape_the_surrogate(
    archive: Path, tmp_path: Path, verb: str
) -> None:
    dest = tmp_path / "out"
    argv = [verb, "-v", str(archive)] + (["-d", str(dest)] if verb == "extract" else [])
    code, out, err = _run_cli(argv)
    assert "hi\\ud800.txt" in out + err
    if verb == "test" or _BYTE_NAMES or sys.platform == "win32":
        assert code == EXIT_OK
    if verb == "extract":
        assert (dest / "ok.txt").read_bytes() == b"ok"


@pytest.mark.parametrize(
    ("name", "posix"),
    [
        ("plain.txt", "plain.txt"),
        ("hi\ud800", "hi\udced\udca0\udc80"),
        ("a\udc7fb", "a\udced\udcb1\udcbfb"),
        ("caf\udce9", "caf\udce9"),  # a surrogateescape byte stays one byte
        ("\U00010000", "\U00010000"),  # a paired surrogate is a character
    ],
)
def test_disk_spelling(name: str, posix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert disk_spelling(name) == posix
    monkeypatch.setattr(sys, "platform", "win32")
    assert disk_spelling(name) == name

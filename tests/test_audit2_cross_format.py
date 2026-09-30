"""Second cross-format audit: the public reading API, parity between backends, and the
``list`` / ``info`` / ``test`` CLI verbs.

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` with the
defect it pins, so a fix turns it into an XPASS failure and the marker has to go.
Fixtures are built in the test from bytes, from the declarative corpus, or from the
committed RAR fixtures; nothing new is committed.
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import subprocess
import tarfile
import time
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, open_archive
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.main import main
from archivey.diagnostics import DiagnosticCode, DiagnosticDisposition, DiagnosticPolicy
from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    DiagnosticRaisedError,
)
from tests.conftest import requires, requires_binary
from tests.sample_archives import CORPUS, corpus_archive_path

_RAR_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


# ---------------------------------------------------------------------------
# C1: a truncated RAR lists a silent prefix
# ---------------------------------------------------------------------------


def _vint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _rar5_blocks(data: bytes) -> list[tuple[int, int, int, int]]:
    """``(block_pos, block_type, header_end, data_size)`` for every RAR5 block."""
    pos = 8
    blocks = []
    while pos < len(data):
        header_size, start = _vint(data, pos + 4)
        p = start
        block_type, p = _vint(data, p)
        flags, p = _vint(data, p)
        data_size = 0
        if flags & 0x01:
            _, p = _vint(data, p)
        if flags & 0x02:
            data_size, p = _vint(data, p)
        blocks.append((pos, block_type, start + header_size, data_size))
        pos = start + header_size + data_size
    return blocks


def _rar4_blocks(data: bytes) -> list[tuple[int, int, int, int]]:
    """``(block_pos, block_type, header_end, data_size)`` for every RAR 2.9/4 block."""
    pos = 7
    blocks = []
    while pos < len(data):
        _crc, block_type, flags, header_size = struct.unpack_from("<HBHH", data, pos)
        add = struct.unpack_from("<I", data, pos + 7)[0] if flags & 0x8000 else 0
        blocks.append((pos, block_type, pos + header_size, add))
        pos += header_size + add
    return blocks


def _cut_inside_third_file_data(
    data: bytes, blocks: list[tuple[int, int, int, int]], file_type: int
) -> bytes:
    files = [b for b in blocks if b[1] == file_type and b[3] > 0]
    _pos, _type, header_end, data_size = files[1]
    return data[: header_end + data_size // 2]


@pytest.mark.parametrize(
    ("fixture", "walker", "file_type"),
    [
        pytest.param("basic_nonsolid__.rar", _rar5_blocks, 2, id="rar5"),
        pytest.param("basic_nonsolid__rar4.rar", _rar4_blocks, 0x74, id="rar4"),
    ],
)
def test_rar_cut_inside_member_data_reports_truncation(
    fixture: str,
    walker: Callable[[bytes], list[tuple[int, int, int, int]]],
    file_type: int,
) -> None:
    data = (_RAR_FIXTURES / fixture).read_bytes()
    cut = _cut_inside_third_file_data(data, walker(data), file_type)
    with open_archive(io.BytesIO(data)) as reader:
        full = len(reader.members())
    with open_archive(io.BytesIO(cut)) as reader:
        report = reader.members_report()
    # Precondition: the listing really is a strict prefix.
    assert len(report) < full
    # TAR parity: a member whose data runs past the end is a truncated archive.
    assert isinstance(report.error, CorruptionError)


def _tar_two_members() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in ("a", "b"):
            info = tarfile.TarInfo(name)
            info.size = 10
            tf.addfile(info, io.BytesIO(b"x" * 10))
    return buf.getvalue()


def test_tar_cut_at_header_boundary_is_reported_control() -> None:
    """Control for the RAR test below: TAR reports the same cut."""
    with open_archive(io.BytesIO(_tar_two_members()[:1024])) as reader:
        reader.members_report()
        counts = reader.diagnostics.counts
    assert counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "C1: a RAR5 cut at a header boundary (no ENDARC, which RAR5 always writes) "
        "lists as complete with no error and no ARCHIVE_EOF_MARKER_MISSING"
    ),
)
def test_rar5_cut_at_header_boundary_is_not_silent() -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    files = [b for b in _rar5_blocks(data) if b[1] == 2]
    cut = data[: files[1][0]]  # MAIN + the first FILE block, nothing after
    with open_archive(io.BytesIO(cut)) as reader:
        report = reader.members_report()
        counts = reader.diagnostics.counts
    assert [m.name for m in report] == ["file1.txt"]  # precondition
    assert (
        report.error is not None
        or counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) >= 1
    )


# ---------------------------------------------------------------------------
# C2: lzip listing runs a pure-Python GF(2) matrix exponentiation per member
# ---------------------------------------------------------------------------


def _lzip_member(payload: bytes) -> bytes:
    body = lzma_raw(payload)
    header = b"LZIP\x01\x0c"  # version 1, 4 KiB dictionary
    size = len(header) + len(body) + 20
    return header + body + struct.pack("<IQQ", zlib.crc32(payload), len(payload), size)


def lzma_raw(payload: bytes) -> bytes:
    import lzma

    return lzma.compress(
        payload,
        format=lzma.FORMAT_RAW,
        filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 12, "lc": 3, "lp": 0, "pb": 2}
        ],
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "C2: open_archive on a multi-member .lz costs ~4.5 ms per 26-byte trailer "
        "(crc32_combine rebuilds 32x32 GF(2) matrices per call, log2 of an "
        "attacker-chosen data_size); a 1 MiB file takes minutes before any decode"
    ),
)
def test_lzip_listing_cost_does_not_scale_with_declared_sizes() -> None:
    # One real member, then 400 trailer-only members (header + trailer, 26 bytes)
    # each declaring 2**62 bytes. The backward index scan reads only trailers and
    # member magics, so these parse; nothing is decoded.
    fake = b"LZIP\x01\x0c" + struct.pack("<IQQ", 0, 1 << 62, 26)
    blob = _lzip_member(b"hello\n") + fake * 400  # 10 KiB
    start = time.perf_counter()
    with open_archive(io.BytesIO(blob)) as reader:
        reader.members()
    elapsed = time.perf_counter() - start
    # Linear work over 10 KiB of trailers is milliseconds; today it is ~1.8 s here.
    assert elapsed < 0.5, f"listing a 10 KiB .lz took {elapsed:.2f} s"


# ---------------------------------------------------------------------------
# C3: DiagnosticPolicy takes any value and fails open
# ---------------------------------------------------------------------------


def _tar_with_trailing_garbage() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("a")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue() + b"GARBAGE" * 10


def test_diagnostic_policy_raise_control() -> None:
    """Control: the enum spelling raises on the trailing data."""
    policy = DiagnosticPolicy(default=DiagnosticDisposition.RAISE)
    config = ArchiveyConfig(diagnostic_policy=policy)
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(io.BytesIO(_tar_with_trailing_garbage()), config=config) as r:
            r.members()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "C3: DiagnosticPolicy stores a string disposition or code name as given and "
        "never raises for it, so strict mode is silently off (ArchiveyConfig coerces "
        "the accelerator fields for exactly this reason)"
    ),
)
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"default": "raise"}, id="default-str"),
        pytest.param(
            {"overrides": {DiagnosticCode.ARCHIVE_TRAILING_DATA: "raise"}},
            id="override-value-str",
        ),
        pytest.param(
            # The member name, a spelling every coerced enum argument accepts. The
            # lower-case value happens to work only because the enum mixes in str.
            {"overrides": {"ARCHIVE_TRAILING_DATA": DiagnosticDisposition.RAISE}},
            id="override-key-name",
        ),
    ],
)
def test_diagnostic_policy_string_spelling_is_refused_or_honoured(
    kwargs: dict[str, object],
) -> None:
    try:
        policy = DiagnosticPolicy(**kwargs)  # type: ignore[arg-type]
    except ArchiveyUsageError:
        return  # refused at construction: acceptable
    config = ArchiveyConfig(diagnostic_policy=policy)
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(io.BytesIO(_tar_with_trailing_garbage()), config=config) as r:
            r.members()


# ---------------------------------------------------------------------------
# C4: a seek back to the start never re-arms the CRC
# ---------------------------------------------------------------------------


def _damaged_stored_zip(path: Path) -> bytes:
    payload = b"LINE-abcde\n" * 3000
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("m.txt", payload)
    data = bytearray(path.read_bytes())
    at = data.find(payload[:200])
    data[at + 5000] ^= 0x20  # the stored CRC no longer matches
    path.write_bytes(bytes(data))
    return payload


def _seek_end_then_start(stream: io.RawIOBase) -> bytes:
    stream.seek(0, io.SEEK_END)  # the size-probe idiom
    stream.seek(0)
    return stream.read()


def _partial_then_start(stream: io.RawIOBase) -> bytes:
    stream.read(10)
    stream.seek(0)
    return stream.read()


# Only the partial-read rewind: newer 3.12 and 3.13+ zipfile seek a stored member on
# the file directly and stop checking its CRC after seek-to-end, so that case is not a
# stable control across patch releases.
def test_stdlib_zipfile_rechecks_crc_after_rewind_control(tmp_path: Path) -> None:
    """Control: stdlib ``zipfile`` resets its running CRC on a rewind and raises."""
    path = tmp_path / "damaged.zip"
    _damaged_stored_zip(path)
    with zipfile.ZipFile(path) as zf, zf.open("m.txt") as stream:
        with pytest.raises(zipfile.BadZipFile):
            _partial_then_start(stream)  # type: ignore[arg-type]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "C4: after any seek the digest is off for the stream's life, even when the "
        "caller rewinds to 0 and reads every byte; damaged data returns clean, "
        "unlike zipfile and against errors-and-diagnostics.md ('damage it reaches "
        "still raises')"
    ),
)
@pytest.mark.parametrize(
    "ops",
    [
        pytest.param(_seek_end_then_start, id="seek-end-then-0"),
        pytest.param(_partial_then_start, id="read-then-seek-0"),
    ],
)
def test_full_read_after_rewind_to_start_is_verified(
    ops: Callable[[io.RawIOBase], bytes], tmp_path: Path
) -> None:
    path = tmp_path / "damaged.zip"
    _damaged_stored_zip(path)
    with open_archive(path, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            with pytest.raises(CorruptionError):
                ops(stream)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# X4: `archivey test` re-opens every 7z / RAR4 symlink and follows it
# ---------------------------------------------------------------------------


def _corpus_symlinks_7z(tmp_path: Path) -> Path:
    entry = next(e for e in CORPUS if e.id == "symlinks")
    return corpus_archive_path(entry, "7z", tmp_path)


def _fixture_rar4_symlinks(tmp_path: Path) -> Path:
    del tmp_path
    return _RAR_FIXTURES / "symlinks_solid__rar4.rar"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "X4: `archivey test` treats every 7z/RAR4 symlink as unverified (its target "
        "is read at pass end), re-opens it, follows it to a directory, and dies on "
        "an uncaught ArchiveyUsageError traceback"
    ),
)
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_corpus_symlinks_7z, id="7z-corpus", marks=requires("py7zr")),
        pytest.param(
            _fixture_rar4_symlinks, id="rar4-fixture", marks=requires_binary("unrar")
        ),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="the 7z corpus builder needs symlinks")
def test_cli_test_on_intact_symlink_to_directory(
    build: Callable[[Path], Path], tmp_path: Path
) -> None:
    archive = build(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    assert main(["test", str(archive)], out=out, err=err) == EXIT_OK


def _symlink_cycle_tree(root: Path) -> list[str]:
    root.mkdir()
    (root / "f").write_bytes(b"hi\n")
    os.symlink("b", root / "a")
    os.symlink("a", root / "b")
    return ["f", "a", "b"]


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_cli_test_zip_symlink_cycle_control(tmp_path: Path) -> None:
    """Control: the same content as a ZIP tests clean."""
    names = _symlink_cycle_tree(tmp_path / "src")
    archive = tmp_path / "loop.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in names:
            src = tmp_path / "src" / name
            if src.is_symlink():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                zf.writestr(info, os.readlink(src))
            else:
                zf.write(src, name)
    assert main(["test", str(archive)], out=io.StringIO(), err=io.StringIO()) == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "X4: `archivey test` FAILs an intact 7z whose symlinks form a cycle "
        "(re-open raises ReadError 'Link cycle'); the same content as a ZIP is OK"
    ),
)
@requires_binary("7z")
@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_cli_test_7z_symlink_cycle_is_not_a_failure(tmp_path: Path) -> None:
    names = _symlink_cycle_tree(tmp_path / "src")
    archive = tmp_path / "loop.7z"
    subprocess.run(
        ["7z", "a", "-snl", "-bd", "-bso0", str(archive), *names],
        cwd=tmp_path / "src",
        check=True,
    )
    err = io.StringIO()
    assert main(["test", str(archive)], out=io.StringIO(), err=err) == EXIT_OK, (
        err.getvalue()
    )


# ---------------------------------------------------------------------------
# X5: an archive-chosen name crashes `list` / `info` on a non-UTF-8 stdout
# ---------------------------------------------------------------------------


def _zip_with_cjk_name_and_comment(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("日本語.txt", b"hi")
        zf.writestr("b.txt", b"x")
        zf.comment = "日本語".encode()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "X5: `list` / `info` print printable non-ASCII archive text raw, so a stdout "
        "that cannot encode it (cp1252 redirect on Windows, PYTHONIOENCODING=ascii) "
        "raises an uncaught UnicodeEncodeError mid-listing"
    ),
)
@pytest.mark.parametrize("verb", ["list", "info"])
def test_cli_output_survives_unencodable_member_text(verb: str, tmp_path: Path) -> None:
    archive = tmp_path / "jp.zip"
    _zip_with_cjk_name_and_comment(archive)
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii")  # errors="strict", like a real stdout
    code = main([verb, str(archive)], out=out, err=io.StringIO())
    out.flush()
    assert code in (EXIT_OK, EXIT_FAIL)
    if verb == "list":
        assert b"b.txt" in raw.getvalue()


# ---------------------------------------------------------------------------
# X6: `archivey test` exits 0 when it could not check the stored digest
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "X6: `archivey test` exits 0 ('1 OK, 0 failed') on a .tar.gz whose member "
        "bytes are damaged, when >1 MiB of padding keeps the gzip CRC from being "
        "checked (DIGEST_UNVERIFIABLE is only logged); gzip -t fails it"
    ),
)
def test_cli_test_fails_when_the_stream_digest_went_unchecked(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        payload = b"hello world\n" * 100
        info = tarfile.TarInfo("a.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    raw = buf.getvalue() + b"\0" * (2 << 20)
    # Stored DEFLATE blocks, so a flipped content byte still decodes; only the
    # gzip trailer's CRC-32 can see it.
    data = bytearray(gzip.compress(raw, compresslevel=0))
    at = data.find(b"hello world\nhello")
    data[at + 100] ^= 0x01
    archive = tmp_path / "damaged.tar.gz"
    archive.write_bytes(bytes(data))
    with pytest.raises(OSError):  # precondition: the stdlib sees the damage
        gzip.decompress(bytes(data))
    err = io.StringIO()
    assert main(["test", str(archive)], out=io.StringIO(), err=err) == EXIT_FAIL, (
        err.getvalue()
    )


# ---------------------------------------------------------------------------
# C5: three TAR combinations archivey reads have no ArchiveFormat name
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "C5: tar.lz, tar.zz and tar.br are detected and read but have no named "
        "ArchiveFormat, so display_name, repr and every error's format= field print "
        "'ArchiveFormat(<ContainerFormat.TAR: ...>, ...)'"
    ),
)
@pytest.mark.parametrize("stream", ["LZIP", "ZLIB", "BROTLI"])
def test_every_readable_tar_combination_has_a_display_name(stream: str) -> None:
    from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat

    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat[stream])
    assert not fmt.display_name.startswith("ArchiveFormat(")

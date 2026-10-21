"""libarchive's published vulnerabilities, replayed against archivey where they apply.

Most libarchive CVEs are memory-safety bugs in its C parsers (overflows, use after
free, out-of-bounds reads). archivey's parsers are Python, so those do not carry over;
their Python equivalent, an untyped exception from a malformed field, is the fuzzers'
job. The tests here cover the ones whose cause is logic and could be repeated in any
language: where extraction writes, which link it follows, which permissions it keeps,
and whether hostile structure ends in a typed error rather than a hang. Each test
names the CVE it replays. The full classification of libarchive's advisories is in the
CVE report, not here.
"""

from __future__ import annotations

import gzip
import io
import os
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path

import pytest

from archivey import ArchiveFormat, ExtractionPolicy, open_archive
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
    UnsupportedFeatureError,
)
from tests.conftest import requires
from tests.extract_util import open_and_extract

_POSIX = pytest.mark.skipif(os.name == "nt", reason="needs POSIX links and modes")
_POLICIES = pytest.mark.parametrize(
    "policy", list(ExtractionPolicy), ids=lambda p: p.name
)
_MODES = pytest.mark.parametrize("streaming", [False, True], ids=["random", "stream"])


def _tar_header(
    name: bytes,
    *,
    size: int = 0,
    typeflag: bytes = b"0",
    linkname: bytes = b"",
    mtime_field: bytes = b"00000000000\0",
    mode: int = 0o644,
) -> bytes:
    """One ustar header block with a correct checksum. Raw, so a test can declare
    what ``tarfile`` would refuse to write."""
    block = bytearray(512)
    block[0 : len(name)] = name
    block[100:108] = b"%07o\0" % mode
    block[108:116] = b"0000000\0"
    block[116:124] = b"0000000\0"
    block[124:136] = b"%011o\0" % size
    block[136:148] = mtime_field
    block[156:157] = typeflag
    block[157 : 157 + len(linkname)] = linkname
    block[257:265] = b"ustar\x0000"
    block[148:156] = b" " * 8
    block[148:156] = b"%06o\0 " % sum(block)
    return bytes(block)


def _data_blocks(data: bytes) -> bytes:
    return data + bytes(-len(data) % 512)


_END = bytes(1024)


def _tar(entries: list[tuple[str, str, object]]) -> bytes:
    """A PAX tar of ``(kind, name, value)``: ``file`` holds bytes, ``dir`` takes a
    mode, ``sym`` links to a name."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for kind, name, value in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(value, bytes)
                info.size = len(value)
                tf.addfile(info, io.BytesIO(value))
            elif kind == "dir":
                assert isinstance(value, int)
                info.type = tarfile.DIRTYPE
                info.mode = value
                tf.addfile(info)
            else:
                assert isinstance(value, str)
                info.type = tarfile.SYMTYPE
                info.linkname = value
                tf.addfile(info)
    return buf.getvalue()


def _zip(entries: list[tuple[bytes, bytes]]) -> bytes:
    """A stored ZIP of ``(raw name, data)``. Raw, since ``zipfile`` refuses an empty
    name."""
    out = bytearray()
    central = bytearray()
    for name, data in entries:
        crc, offset = zlib.crc32(data), len(out)
        out += struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(data), len(data),
            len(name), 0,
        ) + name + data  # fmt: skip
        central += struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0, crc, len(data),
            len(data), len(name), 0, 0, 0, 0, 0, offset,
        ) + name  # fmt: skip
    offset = len(out)
    out += central
    out += struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central),
        offset, 0,
    )  # fmt: skip
    return bytes(out)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    """``tmp_path/dest``, empty, and ``tmp_path/outside`` (mode 0o700) holding
    ``victim``."""
    dest = tmp_path / "dest"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o700)
    (outside / "victim").write_bytes(b"original")
    (outside / "victim").chmod(0o600)
    return dest, outside


def _assert_outside_untouched(tmp_path: Path, outside: Path) -> None:
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dest", "outside"]
    assert [p.name for p in outside.iterdir()] == ["victim"]
    assert (outside / "victim").read_bytes() == b"original"
    if os.name != "nt":
        assert stat.S_IMODE(outside.stat().st_mode) == 0o700
        assert stat.S_IMODE((outside / "victim").stat().st_mode) == 0o600


def _extract_ignoring_errors(data: bytes, dest: Path, **kwargs: object) -> None:
    """Extract; a typed refusal is as good an answer as a skip."""
    try:
        open_and_extract(io.BytesIO(data), dest, **kwargs)
    except ArchiveyError:
        pass


# --- Names -----------------------------------------------------------------------------


@_POLICIES
@_MODES
@pytest.mark.parametrize("name", [b"", b".", b"/", b"./", b"//"])
def test_cve_2024_37407_empty_zip_name(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, name: bytes
) -> None:
    """libarchive before 3.7.4 read out of bounds on a ZIP member with an empty name.
    The logic half: a name that means the destination itself writes nothing."""
    dest, outside = _layout(tmp_path)
    data = _zip([(name, b"data"), (b"ok", b"x")])
    _extract_ignoring_errors(data, dest, policy=policy, streaming=streaming)
    _assert_outside_untouched(tmp_path, outside)
    assert [p.name for p in dest.iterdir()] == ["ok"]


@_POLICIES
@pytest.mark.parametrize(
    "name",
    [
        "..\\..\\evil",
        "a\\..\\..\\evil",
        "C:\\evil",
        "C:evil",
        "\\evil",
        "\\\\server\\share\\evil",
        "..\\evil/../x",
    ],
)
def test_cve_2026_69968_windows_spellings_stay_inside(
    tmp_path: Path, policy: ExtractionPolicy, name: str
) -> None:
    """CVE-2026-69968 and CVE-2026-69973: path traversal in the libarchive shipped
    with Windows. Backslashes, drive letters and UNC prefixes are separators or roots
    there. Every policy keeps what it writes inside the destination, on any OS."""
    dest, outside = _layout(tmp_path)
    _extract_ignoring_errors(_tar([("file", name, b"x")]), dest, policy=policy)
    _assert_outside_untouched(tmp_path, outside)
    for path in dest.rglob("*"):
        assert path.resolve().is_relative_to(dest.resolve())


# --- Links -----------------------------------------------------------------------------


def _hard_link_with_data(target: str) -> bytes:
    return (
        _tar_header(b"h", size=6, typeflag=b"1", linkname=target.encode())
        + _data_blocks(b"pwned!")
        + _END
    )


@_POSIX
@_POLICIES
@_MODES
@pytest.mark.parametrize("absolute", [False, True], ids=["dotdot", "absolute"])
def test_cve_2016_5418_hard_link_with_data_outside(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, absolute: bool
) -> None:
    """libarchive 3.2.0 and earlier wrote a hard link entry's data through the link,
    so a link to a file outside the destination overwrote it."""
    dest, outside = _layout(tmp_path)
    target = str(outside / "victim") if absolute else "../outside/victim"
    _extract_ignoring_errors(
        _hard_link_with_data(target),
        dest,
        policy=policy,
        streaming=streaming,
        overwrite="replace",
    )
    _assert_outside_untouched(tmp_path, outside)


@_POSIX
@_POLICIES
@_MODES
@pytest.mark.parametrize("order", ["dir-then-link", "link-then-dir"])
def test_cve_2021_23177_late_fixup_does_not_follow_a_link(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, order: str
) -> None:
    """CVE-2021-23177 and CVE-2021-31566: libarchive set a directory's mode, ACLs
    and flags after extraction, through a path a later entry had replaced with a
    symlink, and so changed whatever the link pointed at."""
    dest, outside = _layout(tmp_path)
    directory = ("dir", "d", 0o777)
    link = ("sym", "d", "../outside")
    entries: list[tuple[str, str, object]] = (
        [directory, link] if order == "dir-then-link" else [link, directory]
    )
    _extract_ignoring_errors(
        _tar(entries), dest, policy=policy, streaming=streaming, overwrite="replace"
    )
    _assert_outside_untouched(tmp_path, outside)


@_POSIX
@_POLICIES
@_MODES
def test_cve_2020_11736_file_under_a_link_to_outside(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool
) -> None:
    """CVE-2020-11736 and CVE-2020-36314 (file-roller on libarchive): a file whose
    parent is a symlink to a directory outside is written there."""
    dest, outside = _layout(tmp_path)
    archive = _tar([("sym", "a", "../outside"), ("file", "a/victim", b"pwned")])
    _extract_ignoring_errors(
        archive, dest, policy=policy, streaming=streaming, overwrite="replace"
    )
    _assert_outside_untouched(tmp_path, outside)


# --- Permissions -----------------------------------------------------------------------


@_POSIX
@_MODES
def test_cve_2023_30571_default_policy_leaves_nothing_world_writable(
    tmp_path: Path, streaming: bool
) -> None:
    """libarchive through 3.6.2 could leave directories world-writable (a umask
    race). Under the default policy no extracted entry is group- or world-writable,
    whatever the archive asks for."""
    dest = tmp_path / "dest"
    dest.mkdir()
    archive = _tar(
        [("dir", "open", 0o777), ("file", "open/f", b"x"), ("dir", "sticky", 0o1777)]
    )
    open_and_extract(io.BytesIO(archive), dest, streaming=streaming)
    for path in [dest, *dest.rglob("*")]:
        assert stat.S_IMODE(path.lstat().st_mode) & 0o7022 == 0, path


# --- Timestamps ------------------------------------------------------------------------

_HUGE_BASE256 = bytes([0x80, 0x7F]) + b"\xff" * 10
_NEGATIVE_BASE256 = bytes([0xFF, 0x80]) + bytes(10)


@pytest.mark.parametrize(
    "mtime_field",
    [b"77777777777\0", _HUGE_BASE256, _NEGATIVE_BASE256],
    ids=["octal-max", "base256-huge", "base256-negative"],
)
def test_cve_2025_25724_out_of_range_mtime(tmp_path: Path, mtime_field: bytes) -> None:
    """CVE-2025-25724 (an unchecked ``strftime`` in bsdtar's verbose listing) and
    CVE-2015-8931 (overflow in its time limits): a timestamp outside what the
    platform can show. Listing, the CLI's listing and extraction all finish."""
    archive = (
        _tar_header(b"f", size=1, mtime_field=mtime_field) + _data_blocks(b"x") + _END
    )
    with open_archive(io.BytesIO(archive)) as reader:
        assert [m.name for m in reader.members()] == ["f"]
    path = tmp_path / "a.tar"
    path.write_bytes(archive)
    listing = subprocess.run(
        [sys.executable, "-m", "archivey", "list", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert listing.returncode == 0, listing.stderr
    assert listing.stdout.rstrip().endswith(" f")
    dest = tmp_path / "dest"
    open_and_extract(path, dest)
    assert (dest / "f").read_bytes() == b"x"


# --- Hostile structure ends ------------------------------------------------------------


@_MODES
@pytest.mark.parametrize(
    "archive",
    [
        _tar_header(b"././@PaxHeader", size=1000, typeflag=b"x") + b"30 path=a\n",
        _tar_header(b"././@LongLink", size=600, typeflag=b"L") + b"A" * 512,
        _tar_header(b"././@LongLink", size=600, typeflag=b"K") + b"A" * 512,
    ],
    ids=["pax", "gnu-longname", "gnu-longlink"],
)
def test_cve_2007_3644_archive_ending_inside_an_extended_header(
    archive: bytes, streaming: bool
) -> None:
    """CVE-2007-3644: libarchive looped forever on an archive that ends inside a PAX
    extended header. CVE-2024-57970: it read past a GNU long-link header cut short.
    Both are a truncated archive, and say so."""
    start = time.monotonic()
    with pytest.raises(TruncatedError):
        with open_archive(io.BytesIO(archive), streaming=streaming) as reader:
            if streaming:
                for _ in reader.stream_members():
                    pass
            else:
                reader.members()
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("flags", [0x80, 0x88, 0x91, 0x9F, 0xFF])
def test_cve_2015_8932_compress_header_with_bad_code_width(flags: int) -> None:
    """libarchive before 3.2.0 shifted by the .Z header's code width unchecked. A
    width outside 9..16 bits, or reserved flags, is refused before any decoding."""
    data = b"\x1f\x9d" + bytes([flags]) + b"\x41\x00" * 10
    with pytest.raises((CorruptionError, UnsupportedFeatureError)) as info:
        with open_archive(io.BytesIO(data), format=ArchiveFormat.Z) as reader:
            with reader.open(reader.members()[0]) as stream:
                stream.read()
    assert "code width" in str(info.value) or "reserved flags" in str(info.value)


def test_cve_2016_7166_nested_compression_is_one_layer() -> None:
    """libarchive before 3.2.0 did not limit recursive decompression, so gzip inside
    gzip inside gzip ran without end. A compressed file is one layer here: its
    member is the next layer's bytes, undecoded."""
    data = b"x"
    for _ in range(200):
        data = gzip.compress(data)
    inner = gzip.decompress(data)
    with open_archive(io.BytesIO(data)) as reader:
        members = reader.members()
        assert len(members) == 1
        with reader.open(members[0]) as stream:
            assert stream.read() == inner


@requires("pycdlib")
@pytest.mark.parametrize("entry", [b"CL", b"PL"])
@pytest.mark.parametrize("target", ["/D0", "/D0/D1/D2", "root"])
def test_cve_2015_8930_rock_ridge_relocation_loop(entry: bytes, target: str) -> None:
    """CVE-2015-8930 and CVE-2019-1000020: bsdtar looped forever on an ISO image
    whose directories form a cycle. Rock Ridge relocation links (``CL`` child link,
    ``PL`` parent link) pointed back at an ancestor or the root close one. Listing
    ends, with members or a typed error. Plain directory-record cycles are in
    test_iso.py (``_pycdlib_directory_cycle_image``)."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    path = ""
    # Deeper than ISO 9660's eight levels, so pycdlib relocates the deepest one.
    for depth in range(10):
        path += f"/D{depth}"
        iso.add_directory(path, rr_name=f"d{depth}")
    iso.add_fp(io.BytesIO(b"hi"), 2, path + "/F.TXT;1", rr_name="f.txt")
    if target == "root":
        extent = iso.get_record(iso_path="/").extent_location()
    else:
        extent = iso.get_record(iso_path=target).extent_location()
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    data = bytearray(out.getvalue())
    marker = entry + b"\x0c\x01"
    at = data.find(marker)
    assert at > 0 and data.find(marker, at + 1) < 0
    data[at + 4 : at + 12] = struct.pack("<I", extent) + struct.pack(">I", extent)

    start = time.monotonic()
    try:
        with open_archive(io.BytesIO(bytes(data))) as reader:
            assert len(reader.members()) < 20
    except ArchiveyError:
        pass
    assert time.monotonic() - start < 10

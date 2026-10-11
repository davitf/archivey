"""The native ZIP structure parser, checked against stdlib ``zipfile`` and by hand.

The differential test is the parity bar from the native-zip-reader design: wherever
``zipfile`` opens an archive, the parser's entries carry the same fields. The unit tests
cover what ``zipfile`` refuses or does differently on purpose.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from archivey.exceptions import CorruptionError, UnsupportedFeatureError
from archivey.internal.backends import zip_reader
from archivey.internal.backends.zip_parser import (
    CentralDirectoryWalk,
    CentralEntry,
    CommentCutShort,
    EndRecord,
    EntryCountMismatch,
    EntryOverrun,
    ReadAt,
    find_end_record,
    read_local_header,
)
from tests.create_adversarial import adversarial_archives
from tests.sample_archives import CORPUS, build_archive

FIXTURES = Path(__file__).parent / "fixtures"


def _read_at(data: bytes) -> ReadAt:
    def read_at(offset: int, n: int) -> bytes:
        return data[offset : offset + n]

    return read_at


def _parse(data: bytes) -> tuple[EndRecord, list[CentralEntry], CentralDirectoryWalk]:
    end = find_end_record(_read_at(data), len(data))
    walk = CentralDirectoryWalk(_read_at(data), end)
    return end, list(walk), walk


def _stdlib_infos(data: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, UnicodeError, ValueError):
        return None


def _assert_matches_zipfile(data: bytes) -> bool:
    """Compare the parser with ``zipfile`` field by field; False when zipfile refuses."""
    zf = _stdlib_infos(data)
    if zf is None:
        return False
    end, entries, _walk = _parse(data)
    infos = zf.infolist()
    assert end.cd_start == zf.start_dir
    assert end.comment == zf.comment
    assert len(entries) == len(infos)
    for entry, info in zip(entries, infos, strict=True):
        stored = info.orig_filename.encode(
            "utf-8" if info.flag_bits & 0x800 else "cp437"
        )
        assert entry.name == stored
        assert entry.header_offset == info.header_offset
        assert entry.compressed_size == info.compress_size
        assert entry.file_size == info.file_size
        assert entry.crc == info.CRC
        assert entry.flags == info.flag_bits
        assert entry.method == info.compress_type
        assert entry.create_system == info.create_system
        assert entry.version_made_by & 0xFF == info.create_version
        assert entry.version_needed & 0xFF == info.extract_version
        assert entry.external_attr == info.external_attr
        assert entry.internal_attr == info.internal_attr
        assert entry.extra == info.extra
        assert entry.comment == info.comment
        assert entry.date_time == info.date_time
        assert entry.dos_time == info._raw_time  # type: ignore[attr-defined]
        assert entry.is_dir == info.orig_filename.endswith("/")
        if info.volume != 0xFFFF:
            assert entry.disk_start == info.volume
    return True


def _corpus_zips(tmp_path: Path) -> Iterator[tuple[str, bytes]]:
    for path in sorted(FIXTURES.rglob("*.zip")):
        yield str(path.relative_to(FIXTURES)), path.read_bytes()
    for entry, data in adversarial_archives():
        if entry.fmt == "zip":
            yield f"adversarial:{entry.id}", data
    for corpus_entry in CORPUS:
        for key in ("zip", "zip-aes"):
            if key not in corpus_entry.formats:
                continue
            path = tmp_path / f"{corpus_entry.id}.{key}.zip"
            try:
                build_archive(corpus_entry, key, path)
            except ImportError:
                continue
            yield f"corpus:{corpus_entry.id}:{key}", path.read_bytes()


def test_entries_match_zipfile_over_the_corpus(tmp_path: Path) -> None:
    compared = []
    for label, data in _corpus_zips(tmp_path):
        if _assert_matches_zipfile(data):
            compared.append(label)
    # The bar is meaningless if most of the corpus silently fell out.
    assert len(compared) >= 25, compared


def test_end_record_findings_match_the_reader(tmp_path: Path) -> None:
    """The walk's findings are the ones ``_end_record_findings`` reports today."""
    for label, data in _corpus_zips(tmp_path):
        zf = _stdlib_infos(data)
        if zf is None:
            continue
        today = zip_reader._end_record_findings(
            io.BytesIO(data),
            start_dir=zf.start_dir,
            infos=zf.infolist(),
            archive_name=None,
        )
        _end, _entries, walk = _parse(data)
        assert len(walk.findings) == len(today), label


def _zip(members: dict[str, bytes], *, comment: bytes = b"", **kwargs: object) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", **kwargs) as zf:  # type: ignore[arg-type]
        for name, data in members.items():
            zf.writestr(name, data)
        zf.comment = comment
    return buf.getvalue()


def test_zip64_sizes_offsets_and_record() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in ("a.txt", "b.txt"):
            with zf.open(name, "w", force_zip64=True) as member:
                member.write(name.encode() * 100)
    data = buf.getvalue()
    assert _assert_matches_zipfile(data)
    _end, entries, _walk = _parse(data)
    assert [e.file_size for e in entries] == [500, 500]


def test_zip64_end_record_for_more_than_65535_entries() -> None:
    data = _zip({f"{i}": b"" for i in range(65_537)})
    assert _assert_matches_zipfile(data)
    end, entries, walk = _parse(data)
    assert end.zip64
    assert end.entries_declared == len(entries) == 65_537
    assert walk.findings == []


def _with_central_extra(data: bytes, index: int, extra: bytes, **fields: int) -> bytes:
    """``data`` with directory entry ``index`` given ``extra`` and some fixed fields."""
    offsets = {"crc": 16, "compressed_size": 20, "file_size": 24, "header_offset": 42}
    eocd = data.rindex(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<LL", data, eocd + 12)
    out = bytearray()
    pos = cd_offset
    for i in range(struct.unpack_from("<H", data, eocd + 10)[0]):
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        end = pos + 46 + name_len + extra_len + comment_len
        entry = bytearray(data[pos:end])
        if i == index:
            fixed_and_name = entry[: 46 + name_len]
            comment = entry[46 + name_len + extra_len :]
            struct.pack_into("<H", fixed_and_name, 30, len(extra))
            for field, value in fields.items():
                struct.pack_into("<L", fixed_and_name, offsets[field], value)
            entry = fixed_and_name + extra + comment
        out += entry
        pos = end
    record = bytearray(data[eocd:])
    struct.pack_into("<L", record, 12, len(out))
    return data[:cd_offset] + bytes(out) + bytes(record)


def test_zip64_extra_field_supplies_a_deferred_offset() -> None:
    """A header offset of 0xFFFFFFFF is read from the ZIP64 extra field."""
    plain = _zip({"a.txt": b"hello", "b.txt": b"world"})
    second_offset = plain.index(b"PK\x03\x04", 1)
    extra = struct.pack("<HHQ", 0x0001, 8, second_offset)
    data = _with_central_extra(plain, 1, extra, header_offset=0xFFFF_FFFF)
    assert _assert_matches_zipfile(data)
    _end, entries, _walk = _parse(data)
    assert entries[1].header_offset == second_offset


def test_zip64_extra_field_order_is_uncompressed_compressed_offset() -> None:
    """Every value deferred at once: APPNOTE's order, distinct values so a swap shows."""
    plain = _zip({"a.txt": b"hello", "b.txt": b"world"}, compression=zipfile.ZIP_STORED)
    second_offset = plain.index(b"PK\x03\x04", 1)
    extra = struct.pack("<HHQQQ", 0x0001, 24, 5, 5, second_offset)
    data = _with_central_extra(
        plain,
        1,
        extra,
        compressed_size=0xFFFF_FFFF,
        file_size=0xFFFF_FFFF,
        header_offset=0xFFFF_FFFF,
    )
    assert _assert_matches_zipfile(data)
    # Values that differ, so reading the fields in another order changes the result.
    extra = struct.pack("<HHQQQ", 0x0001, 24, 11, 7, second_offset)
    data = _with_central_extra(
        plain,
        1,
        extra,
        compressed_size=0xFFFF_FFFF,
        file_size=0xFFFF_FFFF,
        header_offset=0xFFFF_FFFF,
    )
    _end, entries, _walk = _parse(data)
    assert (entries[1].file_size, entries[1].compressed_size) == (11, 7)
    assert entries[1].header_offset == second_offset
    info = _stdlib_infos(data)
    assert info is not None
    assert (info.infolist()[1].file_size, info.infolist()[1].compress_size) == (11, 7)


def test_zip64_extra_field_missing_a_deferred_value_is_corruption() -> None:
    plain = _zip({"a.txt": b"hello", "b.txt": b"world"})
    # The offset is deferred, and the ZIP64 field holds 4 bytes where 8 are needed.
    short = struct.pack("<HH", 0x0001, 4) + b"\x00" * 4
    data = _with_central_extra(plain, 1, short, header_offset=0xFFFF_FFFF)
    assert _stdlib_infos(data) is None
    walk = CentralDirectoryWalk(
        _read_at(data), find_end_record(_read_at(data), len(data))
    )
    seen = []
    with pytest.raises(CorruptionError, match="ZIP64 extra field"):
        for entry in walk:
            seen.append(entry.name)
    assert seen == [b"a.txt"]


@pytest.mark.parametrize("adjusted", [False, True], ids=["unadjusted", "adjusted"])
def test_stub_prefix(tmp_path: Path, adjusted: bool) -> None:
    stub = b"MZ" + b"\x00" * 1000
    if adjusted:
        # zipfile appending to a file that already holds the stub writes offsets that
        # count the stub, as an SFX builder that adjusts them does.
        path = tmp_path / "sfx.zip"
        path.write_bytes(stub)
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("a.txt", b"hello")
        data = path.read_bytes()
    else:
        data = stub + _zip({"a.txt": b"hello"})
    assert _assert_matches_zipfile(data)
    end, entries, _walk = _parse(data)
    assert end.base == (0 if adjusted else len(stub))
    local = read_local_header(_read_at(data), entries[0].header_offset)
    assert data[local.data_start : local.data_start + 5] == b"hello"


def test_comment_with_a_decoy_end_record_signature() -> None:
    """The last signature wins, as in stdlib: a cut decoy record finds nothing."""
    data = _zip({"a.txt": b"x"}, comment=b"before PK\x05\x06 after")
    assert _stdlib_infos(data) is None
    with pytest.raises(CorruptionError):
        find_end_record(_read_at(data), len(data))
    # A decoy at the start of the comment, followed by enough bytes for a record,
    # is read as the record by both: an empty directory.
    data = _zip({"a.txt": b"x"}, comment=b"PK\x05\x06" + b"\x00" * 18)
    assert _assert_matches_zipfile(data)


@pytest.mark.parametrize("comment", [b"", b"note"], ids=["no-comment", "comment"])
def test_trailing_bytes_are_counted(comment: bytes) -> None:
    plain = _zip({"a.txt": b"hello"}, comment=comment)
    assert _parse(plain)[0].trailing == 0
    data = plain + b"junk" * 4
    assert _assert_matches_zipfile(data)
    end, entries, _walk = _parse(data)
    assert end.trailing == 16
    assert end.comment == comment
    assert [e.name for e in entries] == [b"a.txt"]


def test_comment_cut_short_is_not_trailing() -> None:
    plain = _zip({"a.txt": b"hello"}, comment=b"a longer comment")
    end = _parse(plain[:-4])[0]
    assert (end.trailing, end.comment_declared - len(end.comment)) == (0, 4)


def test_empty_archive() -> None:
    data = _zip({})
    assert _assert_matches_zipfile(data)
    end, entries, _walk = _parse(data)
    assert entries == [] and end.cd_size == 0


def test_no_end_record_is_corruption() -> None:
    data = _zip({"a.txt": b"hello"})
    with pytest.raises(CorruptionError, match="no end of central directory"):
        find_end_record(_read_at(data[:-22]), len(data) - 22)
    with pytest.raises(CorruptionError):
        find_end_record(_read_at(b"PK"), 2)


@pytest.mark.parametrize("field_offset", [4, 6], ids=["this_disk", "cd_start_disk"])
def test_classic_disk_fields_refuse_a_spanned_part(field_offset: int) -> None:
    data = bytearray(_zip({"a.txt": b"hello"}))
    eocd = data.rindex(b"PK\x05\x06")
    struct.pack_into("<H", data, eocd + field_offset, 2)
    with pytest.raises(UnsupportedFeatureError, match="multi-volume|spanned|split"):
        find_end_record(_read_at(bytes(data)), len(data))


def test_zip64_sentinel_disk_field_is_not_a_disk() -> None:
    data = bytearray(_zip({"a.txt": b"hello"}))
    eocd = data.rindex(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 4, 0xFFFF, 0xFFFF)
    find_end_record(_read_at(bytes(data)), len(data))


def test_zip64_locator_with_several_disks_refuses() -> None:
    data = bytearray(_zip({f"{i}": b"" for i in range(65_537)}))
    locator = data.rindex(b"PK\x06\x07")
    struct.pack_into("<L", data, locator + 16, 3)
    with pytest.raises(UnsupportedFeatureError):
        find_end_record(_read_at(bytes(data)), len(data))


@pytest.mark.parametrize("zip64", [False, True], ids=["classic", "zip64"])
def test_archive_extra_data_record_is_strong_encryption(zip64: bool) -> None:
    members = {f"{i}": b"" for i in range(65_537)} if zip64 else {"a.txt": b"x"}
    data = bytearray(_zip(members))
    cd = data.index(b"PK\x01\x02")
    data[cd : cd + 4] = b"PK\x06\x08"
    with pytest.raises(UnsupportedFeatureError, match="Strong Encryption"):
        find_end_record(_read_at(bytes(data)), len(data))


def test_damaged_entry_yields_the_entries_before_it() -> None:
    data = bytearray(_zip({"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"}))
    third = data.index(
        b"PK\x01\x02c"[:4], data.index(b"b.txt", data.index(b"PK\x01\x02"))
    )
    data[third : third + 4] = b"XXXX"
    walk = CentralDirectoryWalk(
        _read_at(bytes(data)), find_end_record(_read_at(bytes(data)), len(data))
    )
    seen = []
    with pytest.raises(CorruptionError, match="Bad magic number for central directory"):
        for entry in walk:
            seen.append(entry.name)
    assert seen == [b"a.txt", b"b.txt"]


def test_a_cut_extra_field_does_not_refuse_the_archive() -> None:
    """stdlib refuses the whole archive ("Corrupt extra field"); the walk keeps it."""
    cut = struct.pack("<HH", 0x5455, 9) + b"\x01\x00"  # declares 9 bytes, holds 2
    data = _with_central_extra(_zip({"a.txt": b"hello"}), 0, cut)
    assert _stdlib_infos(data) is None
    _end, entries, _walk = _parse(data)
    assert entries[0].extra == cut


def test_findings_count_comment_and_overrun() -> None:
    data = bytearray(_zip({"a.txt": b"1", "b.txt": b"2"}))
    eocd = data.rindex(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 8, 5, 5)  # entry counts
    struct.pack_into("<H", data, eocd + 20, 40)  # comment longer than the file
    _end, entries, walk = _parse(bytes(data))
    assert len(entries) == 2
    assert walk.findings == [
        EntryCountMismatch(declared=5, read=2, zip64=False),
        CommentCutShort(declared=40, available=0),
    ]

    data = bytearray(_zip({"a.txt": b"1"}))
    cd = data.index(b"PK\x01\x02")
    struct.pack_into("<H", data, cd + 32, 30)  # a comment past the directory's end
    _end, entries, walk = _parse(bytes(data))
    (overrun,) = [f for f in walk.findings if isinstance(f, EntryOverrun)]
    assert overrun.field == "comment" and overrun.index == 0


def test_the_walk_reads_forward_in_large_pieces() -> None:
    data = _zip({f"member-{i:05d}.txt": b"" for i in range(3000)})
    calls: list[tuple[int, int]] = []
    base = _read_at(data)

    def counting(offset: int, n: int) -> bytes:
        calls.append((offset, n))
        return base(offset, n)

    end = find_end_record(counting, len(data))
    calls.clear()
    assert len(list(CentralDirectoryWalk(counting, end))) == 3000
    offsets = [offset for offset, _n in calls]
    assert offsets == sorted(offsets)
    assert len(calls) <= end.cd_size // (1 << 16) + 2


def test_local_header() -> None:
    data = _zip({"dir/a.txt": b"hello"})
    _end, entries, _walk = _parse(data)
    local = read_local_header(_read_at(data), entries[0].header_offset)
    assert local.name == b"dir/a.txt"
    assert data[local.data_start : local.data_start + 5] == b"hello"
    with pytest.raises(CorruptionError, match="Bad magic number for file header"):
        read_local_header(_read_at(data), entries[0].header_offset + 1)
    with pytest.raises(CorruptionError, match="Absurd"):
        read_local_header(_read_at(data), 1 << 41)


def test_local_header_cut_by_end_of_file_is_truncation() -> None:
    from archivey.exceptions import TruncatedError

    data = _zip({"a.txt": b"hello"})
    with pytest.raises(TruncatedError):
        read_local_header(_read_at(data[:20]), 0)
    with pytest.raises(TruncatedError):
        read_local_header(_read_at(data[:32]), 0)


@pytest.mark.skipif(shutil.which("zip") is None, reason="Info-ZIP zip not installed")
def test_info_zip_output_matches(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_bytes(b"hello " * 100)
    (src / "link").symlink_to("sub/f.txt")
    out = tmp_path / "plain.zip"
    subprocess.run(["zip", "-q", "-r", "-y", str(out), "."], cwd=src, check=True)
    assert _assert_matches_zipfile(out.read_bytes())
    # Written to a pipe: data descriptors on every member. Info-ZIP refuses to store a
    # member on a pipe, so only the compressible file goes, without directory entries.
    piped = subprocess.run(
        ["zip", "-q", "-D", "-", "sub/f.txt"], cwd=src, check=True, capture_output=True
    ).stdout
    assert zipfile.ZipFile(io.BytesIO(piped)).infolist()[0].flag_bits & 0x8
    assert _assert_matches_zipfile(piped)


@pytest.mark.skipif(shutil.which("7z") is None, reason="7-Zip not installed")
def test_7zip_output_matches(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_bytes(b"hello " * 100)
    (src / "ü.txt").write_bytes(b"x")
    out = tmp_path / "out.zip"
    subprocess.run(
        ["7z", "a", "-tzip", "-bd", "-y", str(out), "."],
        cwd=src,
        check=True,
        capture_output=True,
    )
    assert _assert_matches_zipfile(out.read_bytes())

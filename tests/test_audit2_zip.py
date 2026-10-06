"""Regression tests for the ZIP backend's findings of the second audit (Z5 onward).

Each test asserts the promised behaviour that the finding in its name or section
heading broke.

Fixtures are built byte by byte with :func:`_build_zip`, so a test can set any central
or local header field independently of the other.
"""

from __future__ import annotations

import bz2
import hashlib
import io
import os
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest

import archivey
from archivey.config import AcceleratorMode, ArchiveyConfig
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    UnsupportedFeatureError,
)
from tests.conftest import requires
from tests.extract_util import open_and_extract


@dataclass
class _Entry:
    """One member: ``data`` is the stored payload (already compressed)."""

    name: bytes
    data: bytes
    method: int = 0
    plain: bytes | None = None  # the content the CRC and size describe; default data
    crc: int | None = None
    usize: int | None = None
    extra: bytes = b""  # central-directory extra field
    local_extra: bytes = b""  # local-header extra field
    header_offset: int | None = None  # central-directory value; default the real one
    extract_version: int = 20
    external_attr: int = 0o100644 << 16


def _build_zip(entries: list[_Entry]) -> bytes:
    """A single-disk ZIP: local headers and data, central directory, classic EOCD."""
    out = bytearray()
    offsets = []
    for e in entries:
        plain = e.plain if e.plain is not None else e.data
        crc = e.crc if e.crc is not None else zlib.crc32(plain)
        usize = e.usize if e.usize is not None else len(plain)
        offsets.append(len(out))
        out += struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            e.extract_version,
            0,  # flags
            e.method,
            0,  # time
            0x21,  # date: 1980-01-01
            crc,
            len(e.data),
            min(usize, 0xFFFFFFFF),
            len(e.name),
            len(e.local_extra),
        )
        out += e.name + e.local_extra + e.data
    cd_start = len(out)
    cd = bytearray()
    for e, offset in zip(entries, offsets, strict=True):
        plain = e.plain if e.plain is not None else e.data
        crc = e.crc if e.crc is not None else zlib.crc32(plain)
        usize = e.usize if e.usize is not None else len(plain)
        header_offset = e.header_offset if e.header_offset is not None else offset
        cd += struct.pack(
            "<4sBBHHHHHIIIHHHHHII",
            b"PK\x01\x02",
            20,  # version made by
            3,  # create system: Unix
            e.extract_version,
            0,  # flags
            e.method,
            0,
            0x21,
            crc,
            len(e.data),
            min(usize, 0xFFFFFFFF),
            len(e.name),
            len(e.extra),
            0,  # comment length
            0,  # disk number start
            0,  # internal attributes
            e.external_attr,
            min(header_offset, 0xFFFFFFFF),
        )
        cd += e.name + e.extra
    out += cd
    out += struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06",
        0,
        0,
        len(entries),
        len(entries),
        len(cd),
        cd_start,
        0,
    )
    return bytes(out)


def _raw_deflate(data: bytes, level: int = 9) -> bytes:
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _outcome(blob: bytes, **open_kwargs: object) -> tuple[str, object]:
    """Read the only member in 1 MiB chunks: ``("ok", sha256)`` or ``("raise", type)``.

    The digest keeps a failed comparison cheap for pytest to explain."""
    with archivey.open_archive(io.BytesIO(blob), **open_kwargs) as ar:  # type: ignore[arg-type]
        (member,) = ar.members()
        try:
            with ar.open(member) as stream:
                chunks = []
                while chunk := stream.read(1 << 20):
                    chunks.append(chunk)
        except ArchiveyError as exc:
            return ("raise", type(exc))
    return ("ok", hashlib.sha256(b"".join(chunks)).hexdigest())


def _open_and_list(blob: bytes) -> list[str]:
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        return [m.name for m in ar.members()]


# ---------------------------------------------------------------------------------------
# Z5: a ZIP64 local-header offset of 2**63 or more escapes as a raw OverflowError.
# ---------------------------------------------------------------------------------------


def _zip64_header_offset_zip(offset: int, *, symlink: bool = False) -> bytes:
    zip64_extra = struct.pack("<HHQ", 0x0001, 8, offset)
    mode = 0o120777 if symlink else 0o100644
    return _build_zip(
        [
            _Entry(
                b"a.txt",
                b"hello",
                extra=zip64_extra,
                header_offset=0xFFFFFFFF,  # "see the ZIP64 extra"
                external_attr=mode << 16,
            )
        ]
    )


@pytest.mark.parametrize("offset", [2**63, 2**64 - 1])
def test_zip64_header_offset_past_ssize_max_is_typed(offset: int) -> None:
    blob = _zip64_header_offset_zip(offset)
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()  # the central directory itself is fine
        with pytest.raises(CorruptionError):
            ar.read(member)


def test_zip64_header_offset_past_ssize_max_extract_is_typed(tmp_path: Path) -> None:
    blob = _zip64_header_offset_zip(2**63)
    with pytest.raises(ArchiveyError):
        open_and_extract(io.BytesIO(blob), tmp_path / "out")


def test_zip64_header_offset_past_ssize_max_symlink_lists() -> None:
    blob = _zip64_header_offset_zip(2**63, symlink=True)
    # A damaged link target leaves the link listed without a target
    # (zip.md §6, SYMLINK_TARGET_UNAVAILABLE reason="target_data_damaged").
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
    assert member.link_target is None
    assert [
        (d.code, getattr(d.context, "reason", None)) for d in member.diagnostics
    ] == [(DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE, "target_data_damaged")]


# ---------------------------------------------------------------------------------------
# Z6: the Info-ZIP Unicode Path extra field (0x7075) is ignored.
# ---------------------------------------------------------------------------------------


_UNICODE_PATH_REAL = "Привет.txt"
_UNICODE_PATH_STORED = _UNICODE_PATH_REAL.encode("cp866")  # OEM code page, flag clear


def _unicode_path_field(
    stored: bytes = _UNICODE_PATH_STORED,
    name: bytes = _UNICODE_PATH_REAL.encode("utf-8"),
    *,
    version: int = 1,
) -> bytes:
    field = struct.pack("<BI", version, zlib.crc32(stored)) + name
    return struct.pack("<HH", 0x7075, len(field)) + field


def _unicode_path_zip(
    *, extra: bytes | None = None, local_extra: bytes = b""
) -> tuple[bytes, str]:
    field = _unicode_path_field() if extra is None else extra
    entry = _Entry(_UNICODE_PATH_STORED, b"hi", extra=field, local_extra=local_extra)
    return _build_zip([entry]), _UNICODE_PATH_REAL


def test_unicode_path_extra_field_names_the_member() -> None:
    blob, real = _unicode_path_zip()
    # 7-Zip and Info-ZIP unzip present the 0x7075 name; so does stdlib zipfile on
    # 3.12+ (ZipInfo.filename). The field's CRC covers the stored name, so it is
    # an in-band oracle for that name.
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.infolist()[0].orig_filename == real.encode("cp866").decode("cp437")
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.name == real
        # name is raw_name decoded: raw_name is the field's UTF-8 bytes, and the
        # header's legacy bytes are kept as the alternate.
        assert member.raw_name == real.encode("utf-8")
        assert member.extra["alternate_raw_name"] == real.encode("cp866")
        # The field declares the encoding: nothing was inferred or normalized.
        assert member.diagnostics == ()
        assert ar.read(real) == b"hi"


def test_unicode_path_extra_field_wins_over_encoding() -> None:
    # The field is UTF-8 tied to the stored bytes by their CRC, so it names the
    # member even when the caller passes a (here wrong) encoding=.
    blob, real = _unicode_path_zip()
    with archivey.open_archive(io.BytesIO(blob), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == real
        assert member.raw_name == real.encode("utf-8")
        assert member.extra["alternate_raw_name"] == real.encode("cp866")


@pytest.mark.parametrize(
    "field",
    [
        # Stale: the entry was renamed and the field kept the old name's CRC.
        pytest.param(_unicode_path_field(stored=b"old.txt"), id="crc_mismatch"),
        pytest.param(_unicode_path_field(version=2), id="unknown_version"),
        pytest.param(
            _unicode_path_field(name=b""),
            id="empty_name",
            # stdlib zipfile 3.12+ warns about it while reading the directory.
            marks=pytest.mark.filterwarnings("ignore:Empty unicode path extra field"),
        ),
    ],
)
def test_unicode_path_extra_field_that_does_not_vouch_is_ignored(field: bytes) -> None:
    blob, _ = _unicode_path_zip(extra=field)
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        # The header bytes decode as before (not UTF-8, so cp437).
        assert member.name == _UNICODE_PATH_STORED.decode("cp437")
        assert member.raw_name == _UNICODE_PATH_STORED
        assert "alternate_raw_name" not in member.extra
    with archivey.open_archive(io.BytesIO(blob), encoding="cp866") as ar:
        (member,) = ar.members()
        assert member.name == _UNICODE_PATH_REAL
        assert member.raw_name == _UNICODE_PATH_STORED


def test_unicode_path_extra_field_with_invalid_utf8() -> None:
    blob, _ = _unicode_path_zip(extra=_unicode_path_field(name=b"\xff.txt"))
    if sys.version_info >= (3, 12):
        # stdlib zipfile refuses the whole directory over it; archivey types that.
        with pytest.raises(CorruptionError, match="0x7075"):
            archivey.open_archive(io.BytesIO(blob))
        return
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.raw_name == _UNICODE_PATH_STORED
        assert "alternate_raw_name" not in member.extra


def test_unicode_path_extra_field_is_read_from_the_central_directory() -> None:
    # The listing comes from the central directory, as in stdlib zipfile; a field in
    # the local header alone does not rename the member.
    blob, _ = _unicode_path_zip(extra=b"", local_extra=_unicode_path_field())
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.raw_name == _UNICODE_PATH_STORED
        assert member.name == _UNICODE_PATH_STORED.decode("cp437")
        assert ar.read(member) == b"hi"
    # A central field names it whatever the local header carries.
    blob, real = _unicode_path_zip(local_extra=_unicode_path_field(stored=b"x"))
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.name == real
        assert ar.read(member) == b"hi"


# ---------------------------------------------------------------------------------------
# Z7: the rapidgzip accelerator on a raw DEFLATE member with bytes after its stream.
#
# zlib ends the member at the stream's final block; rapidgzip reads on. Where its output
# would pass the declared size, or it fails on the bytes after the stream, it hands over to
# zlib, so the verdicts agree. A second stream whose output matches the declared size and
# CRC reads under rapidgzip: the compressed-streams spec lets the declared checks decide
# on such stream-boundary malformations.
# ---------------------------------------------------------------------------------------

_DEFLATE_PAYLOAD = bytes(range(256)) * 400


def _deflate_member_variants() -> dict[str, bytes]:
    stream = _raw_deflate(_DEFLATE_PAYLOAD)
    p = _DEFLATE_PAYLOAD
    return {
        # Two DEFLATE streams back to back, CRC and size covering both.
        "two_streams_declared_both": _build_zip(
            [_Entry(b"a", stream + stream, method=8, plain=p + p)]
        ),
        # Two streams, CRC and size covering the first only (what 7-Zip and
        # Info-ZIP read, reporting or ignoring the rest).
        "two_streams_declared_first": _build_zip(
            [_Entry(b"a", stream + stream, method=8, plain=p)]
        ),
        # Junk bytes after the stream's end, inside compress_size.
        "trailing_junk": _build_zip(
            [_Entry(b"a", stream + b"GARBAGE" * 10, method=8, plain=p)]
        ),
        # Two streams, size covering both but the CRC of the first only.
        "two_streams_crc_of_first": _build_zip(
            [_Entry(b"a", stream + stream, method=8, plain=p + p, crc=zlib.crc32(p))]
        ),
    }


def _is_corruption(outcome: tuple[str, object]) -> bool:
    verdict, error = outcome
    return (
        verdict == "raise"
        and isinstance(error, type)
        and issubclass(error, CorruptionError)
    )


@requires("rapidgzip")
@pytest.mark.parametrize("case", ["two_streams_declared_first", "trailing_junk"])
def test_rapidgzip_on_and_off_agree_on_a_zip_deflate_member(case: str) -> None:
    # Output past the declared size, or a data error, hands the member to zlib from the
    # position already delivered: both read the first stream's payload.
    blob = _deflate_member_variants()[case]
    expected = ("ok", hashlib.sha256(_DEFLATE_PAYLOAD).hexdigest())
    for mode in (AcceleratorMode.OFF, AcceleratorMode.ON):
        assert _outcome(blob, config=ArchiveyConfig(use_rapidgzip=mode)) == expected


@requires("rapidgzip")
def test_rapidgzip_reads_a_second_deflate_stream_the_declared_crc_covers() -> None:
    blob = _deflate_member_variants()["two_streams_declared_both"]
    # zlib stops after the first stream, short of the declared size.
    off = _outcome(blob, config=ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF))
    assert off == ("raise", archivey.exceptions.TruncatedError)
    # rapidgzip reads both, and they are exactly the bytes the size and CRC declare.
    on = _outcome(blob, config=ArchiveyConfig(use_rapidgzip=AcceleratorMode.ON))
    assert on == ("ok", hashlib.sha256(_DEFLATE_PAYLOAD * 2).hexdigest())


@requires("rapidgzip")
@pytest.mark.parametrize("mode", [AcceleratorMode.OFF, AcceleratorMode.ON])
def test_second_deflate_stream_that_breaks_the_crc_raises(
    mode: AcceleratorMode,
) -> None:
    blob = _deflate_member_variants()["two_streams_crc_of_first"]
    assert _is_corruption(_outcome(blob, config=ArchiveyConfig(use_rapidgzip=mode)))


@requires("rapidgzip")
def test_seekable_members_reads_a_large_deflate_member_the_crc_covers() -> None:
    # 9 MiB of random data twice: > RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE compressed, so
    # under the default AUTO, seekable_members=True engages rapidgzip.
    payload = os.urandom(9 * 2**20)
    stream = _raw_deflate(payload, level=1)
    blob = _build_zip([_Entry(b"a", stream + stream, method=8, plain=payload * 2)])
    assert _outcome(blob) == ("raise", archivey.exceptions.TruncatedError)  # zlib
    assert _outcome(blob, seekable_members=True) == (
        "ok",
        hashlib.sha256(payload * 2).hexdigest(),
    )


# ---------------------------------------------------------------------------------------
# Z8: a bzip2 member and the bytes after its bzip2 stream.
#
# The standard-library path ends the member at its first end-of-stream marker, as every
# other ZIP codec and other readers do. The accelerator reads a second stream as content;
# the declared size and CRC then decide, as for DEFLATE above.
# ---------------------------------------------------------------------------------------

_BZ_PAYLOAD = b"hello world " * 20


def _two_bzip2_streams(plain: bytes, crc: int | None = None) -> bytes:
    stream = bz2.compress(_BZ_PAYLOAD)
    entry = _Entry(
        b"a", stream + stream, method=12, plain=plain, crc=crc, extract_version=46
    )
    return _build_zip([entry])


def test_bzip2_member_ends_at_its_first_stream() -> None:
    blob = _two_bzip2_streams(_BZ_PAYLOAD * 2)
    # Every other reader ends the member at the first end-of-stream marker, so the
    # CRC (of both streams) fails: `unzip -t` "bad CRC", `7z t` "CRC Failed".
    with zipfile.ZipFile(io.BytesIO(blob)) as zf, pytest.raises(zipfile.BadZipFile):
        zf.read("a")
    # Here the member ends 240 bytes short of its declared size, which the verifier
    # reports as TruncatedError (a CorruptionError), as for two DEFLATE streams (Z7).
    assert _outcome(blob) == ("raise", archivey.exceptions.TruncatedError)


def test_bzip2_member_with_a_second_stream_after_its_end_reads() -> None:
    blob = _two_bzip2_streams(_BZ_PAYLOAD)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.read("a") == _BZ_PAYLOAD
    # Bytes after a codec's end inside a ZIP member end it silently for DEFLATE,
    # LZMA and PPMd here (internal/config.py StreamConfig.report_trailing_data).
    assert _outcome(blob) == ("ok", hashlib.sha256(_BZ_PAYLOAD).hexdigest())


_BZ_ACCELERATED = [
    pytest.param(
        {"config": ArchiveyConfig(use_indexed_bzip2=AcceleratorMode.ON)}, id="on"
    ),
    # The default AUTO engages the accelerator on declared seeking, at any size.
    pytest.param({"seekable_members": True}, id="auto-seekable"),
]


@requires("rapidgzip")
@pytest.mark.parametrize("open_kwargs", _BZ_ACCELERATED)
def test_bzip2_accelerator_reads_a_second_stream_the_declared_crc_covers(
    open_kwargs: dict[str, object],
) -> None:
    blob = _two_bzip2_streams(_BZ_PAYLOAD * 2)
    assert _outcome(blob, **open_kwargs) == (
        "ok",
        hashlib.sha256(_BZ_PAYLOAD * 2).hexdigest(),
    )


@requires("rapidgzip")
@pytest.mark.parametrize("open_kwargs", _BZ_ACCELERATED)
def test_bzip2_accelerator_raises_on_output_past_the_declared_size(
    open_kwargs: dict[str, object],
) -> None:
    # Size and CRC cover the first stream only. The stdlib path stops there and reads
    # (test above); the accelerator reads on, past the declared size, and raises.
    blob = _two_bzip2_streams(_BZ_PAYLOAD)
    assert _is_corruption(_outcome(blob, **open_kwargs))


@requires("rapidgzip")
@pytest.mark.parametrize("open_kwargs", [{}, *_BZ_ACCELERATED])
def test_bzip2_second_stream_that_breaks_the_crc_raises(
    open_kwargs: dict[str, object],
) -> None:
    # The size covers both streams, the CRC only the first: the stdlib path is short,
    # the accelerator's output fails the CRC.
    blob = _two_bzip2_streams(_BZ_PAYLOAD * 2, crc=zlib.crc32(_BZ_PAYLOAD))
    assert _is_corruption(_outcome(blob, **open_kwargs))


# ---------------------------------------------------------------------------------------
# Z9: a member declaring size 0 is never verified on a chunked read.
# ---------------------------------------------------------------------------------------


def test_zero_size_member_with_wrong_crc_raises_on_chunked_read() -> None:
    blob = _build_zip([_Entry(b"a", b"", crc=0x12345678)])
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        with ar.open(member) as stream:
            with pytest.raises(CorruptionError):
                stream.read()  # read(-1) does raise: fixture sanity
        with ar.open(member) as stream:
            with pytest.raises(CorruptionError):
                stream.read(8192)


def test_zero_size_member_with_wrong_crc_does_not_extract_clean(tmp_path: Path) -> None:
    blob = _build_zip([_Entry(b"a", b"", crc=0x12345678)])
    with pytest.raises(CorruptionError):
        open_and_extract(io.BytesIO(blob), tmp_path / "out")


@pytest.mark.parametrize("method", [0, 8])
def test_zero_declared_size_with_data_raises_rather_than_serving_it(
    method: int,
) -> None:
    data = b"hello" if method == 0 else _raw_deflate(b"hello")
    blob = _build_zip([_Entry(b"a", data, method=method, plain=b"", usize=0)])
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.size == 0
        with ar.open(member) as stream:
            # read(-1) raises "exceeds its declared size of 0 bytes"; a bounded read
            # must reach the same verdict rather than b"" followed by b"hello".
            with pytest.raises(CorruptionError):
                stream.read(8192)


def test_zero_declared_size_with_data_does_not_extract_clean(tmp_path: Path) -> None:
    blob = _build_zip(
        [_Entry(b"a", _raw_deflate(b"hello"), method=8, plain=b"", usize=0)]
    )
    with pytest.raises(CorruptionError):
        open_and_extract(io.BytesIO(blob), tmp_path / "out")


# ---------------------------------------------------------------------------------------
# Z10: the split-set check reads the wrong EOCD when its disk fields spell PK\x05\x06.
# ---------------------------------------------------------------------------------------


def test_eocd_disk_fields_spelling_the_signature_are_still_refused() -> None:
    base = bytearray(_build_zip([_Entry(b"a", b"hi")]))
    eocd = base.rfind(b"PK\x05\x06")
    # this_disk = 0x4B50, cd_start_disk = 0x0605: not the ZIP64 sentinel, so the
    # archive declares a spanned set, which the reader refuses...
    control = bytearray(base)
    control[eocd + 4 : eocd + 8] = struct.pack("<HH", 0x4B50, 0x0606)
    with pytest.raises(UnsupportedFeatureError):
        _open_and_list(bytes(control))
    # ...unless those four bytes are b"PK\x05\x06" and the entry counts after them
    # are zero: stdlib parses the record at the end of the file (its no-comment fast
    # path), the check reads the counts as disk numbers.
    crafted = bytearray(base)
    crafted[eocd + 4 : eocd + 12] = b"PK\x05\x06" + bytes(4)
    with zipfile.ZipFile(io.BytesIO(bytes(crafted))) as zf:
        assert zf.namelist() == ["a"]  # stdlib parsed the record at the end
    with pytest.raises(UnsupportedFeatureError):
        _open_and_list(bytes(crafted))

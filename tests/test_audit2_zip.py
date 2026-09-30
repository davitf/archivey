"""Audit reproducers for the ZIP backend (second extraction audit, Z5 onward).

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` while the
bug stands; the ``reason`` names the defect. Remove the marker when the fix lands.

Fixtures are built byte by byte with :func:`_build_zip`, so a test can set any central
or local header field independently of the other.
"""

from __future__ import annotations

import bz2
import hashlib
import io
import os
import struct
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
            0,  # local extra length
        )
        out += e.name + e.data
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
        archivey.extract(io.BytesIO(blob), tmp_path / "out")


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


def _unicode_path_zip() -> tuple[bytes, str]:
    real = "Привет.txt"
    stored = real.encode("cp866")  # an OEM code page name, UTF-8 flag clear
    field = struct.pack("<BI", 1, zlib.crc32(stored)) + real.encode("utf-8")
    extra = struct.pack("<HH", 0x7075, len(field)) + field
    return _build_zip([_Entry(stored, b"hi", extra=extra)]), real


@pytest.mark.xfail(
    strict=True,
    reason="Z6: a CRC-matching Unicode Path extra (0x7075) is ignored; the name is "
    "the cp437 garble of the legacy bytes",
)
def test_unicode_path_extra_field_names_the_member() -> None:
    blob, real = _unicode_path_zip()
    # 7-Zip and Info-ZIP unzip present the 0x7075 name; so does stdlib zipfile on
    # 3.12+ (ZipInfo.filename). The field's CRC covers the stored name, so it is
    # the in-band oracle zip.md §5 says the format lacks.
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.infolist()[0].orig_filename == real.encode("cp866").decode("cp437")
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        assert member.raw_name == real.encode("cp866")
        assert member.name == real


# ---------------------------------------------------------------------------------------
# Z7: the rapidgzip accelerator changes the verdict on a raw DEFLATE member.
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
    }


_Z7_UNDETECTED = pytest.mark.xfail(
    strict=True,
    reason="Z7: use_rapidgzip=ON reads past the end of a raw DEFLATE member's stream; "
    "when the output still matches the declared size and CRC, nothing short of a "
    "second decode shows that zlib would have stopped earlier",
)


@requires("rapidgzip")
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("two_streams_declared_both", marks=_Z7_UNDETECTED),
        # Fixed: output past the declared size, or a data error, hands the member to
        # zlib from the position already delivered.
        "two_streams_declared_first",
        "trailing_junk",
    ],
)
def test_rapidgzip_on_and_off_agree_on_a_zip_deflate_member(case: str) -> None:
    blob = _deflate_member_variants()[case]
    off = _outcome(blob, config=ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF))
    on = _outcome(blob, config=ArchiveyConfig(use_rapidgzip=AcceleratorMode.ON))
    # compressed-streams spec: "Accelerator mode is a performance choice and SHALL
    # NOT be observable as a difference in whether a corrupt source raises."
    assert on == off


@requires("rapidgzip")
@pytest.mark.xfail(
    strict=True,
    reason="Z7: under the default AUTO, seekable_members=True engages rapidgzip on a "
    ">=16 MiB DEFLATE member and a member that raises without it reads clean",
)
def test_seekable_members_does_not_change_whether_a_zip_member_raises() -> None:
    # 9 MiB of random data twice: > RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE compressed.
    payload = os.urandom(9 * 2**20)
    stream = _raw_deflate(payload, level=1)
    blob = _build_zip([_Entry(b"a", stream + stream, method=8, plain=payload * 2)])
    plain = _outcome(blob)
    assert plain == ("raise", archivey.exceptions.TruncatedError)  # zlib stops at end
    # compressed-streams spec: "A capability flag (seekable_members) never changes
    # whether a corrupt source raises."
    assert _outcome(blob, seekable_members=True) == plain


# ---------------------------------------------------------------------------------------
# Z8: a bzip2 member is decoded past the end of its bzip2 stream.
# ---------------------------------------------------------------------------------------

_BZ_PAYLOAD = b"hello world " * 20


def test_bzip2_member_ends_at_its_first_stream() -> None:
    stream = bz2.compress(_BZ_PAYLOAD)
    blob = _build_zip(
        [
            _Entry(
                b"a",
                stream + stream,
                method=12,
                plain=_BZ_PAYLOAD * 2,
                extract_version=46,
            )
        ]
    )
    # Every other reader ends the member at the first end-of-stream marker, so the
    # CRC (of both streams) fails: `unzip -t` "bad CRC", `7z t` "CRC Failed".
    with zipfile.ZipFile(io.BytesIO(blob)) as zf, pytest.raises(zipfile.BadZipFile):
        zf.read("a")
    # Here the member ends 240 bytes short of its declared size, which the verifier
    # reports as TruncatedError (a CorruptionError), as for two DEFLATE streams (Z7).
    assert _outcome(blob) == ("raise", archivey.exceptions.TruncatedError)


def test_bzip2_member_with_a_second_stream_after_its_end_reads() -> None:
    stream = bz2.compress(_BZ_PAYLOAD)
    blob = _build_zip(
        [
            _Entry(
                b"a", stream + stream, method=12, plain=_BZ_PAYLOAD, extract_version=46
            )
        ]
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.read("a") == _BZ_PAYLOAD
    # Bytes after a codec's end inside a ZIP member end it silently for DEFLATE,
    # LZMA and PPMd here (internal/config.py StreamConfig.report_trailing_data).
    assert _outcome(blob) == ("ok", hashlib.sha256(_BZ_PAYLOAD).hexdigest())


@pytest.mark.parametrize("declared", ["both", "first"])
def test_bzip2_member_seekable_members_gives_the_same_verdict(declared: str) -> None:
    # Under AUTO, seekable_members=True must not hand the member to rapidgzip's bzip2
    # decoder, which reads on into the second stream.
    stream = bz2.compress(_BZ_PAYLOAD)
    plain = _BZ_PAYLOAD * 2 if declared == "both" else _BZ_PAYLOAD
    blob = _build_zip(
        [_Entry(b"a", stream + stream, method=12, plain=plain, extract_version=46)]
    )
    assert _outcome(blob, seekable_members=True) == _outcome(blob)


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
        archivey.extract(io.BytesIO(blob), tmp_path / "out")


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
        archivey.extract(io.BytesIO(blob), tmp_path / "out")


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

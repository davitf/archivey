"""Self-extracting archives: the shared forward scan and the 7z start offset.

A self-extracting archive is an executable stub with a real archive appended, so the
archive magic sits at some offset ``N > 0``. Three pieces have to agree about that
offset — the shared scanner in ``archivey.internal.sfx``, each parser's own scan, and
``detect_format`` — so they are exercised together here rather than once per backend.

The detection half is the one with teeth: before it, a low-entropy ``MZ`` stub in front
of a real RAR/7z/ZIP was reported as ``BROTLI`` and ``open_archive`` returned a
fabricated ``*.uncompressed`` member. A test that only asserted ``FormatDetectionError``
would have stayed green through that, so the cases below assert the *members*.
"""

from __future__ import annotations

import io
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from archivey import DEFAULT_ARCHIVEY_CONFIG, detect_format, open_archive
from archivey.exceptions import (
    CorruptionError,
    FormatDetectionError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_parser import MAGIC_7Z, find_signature_offset
from archivey.internal.backends.sevenzip_reader import SevenZipReadBackend
from archivey.internal.password import _PasswordCandidates
from archivey.internal.sfx import (
    SFX_MAX,
    ExecutableCue,
    MagicHit,
    executable_cue,
    scan_for_magic,
)
from archivey.internal.streams.brotli_framing import (
    BrotliBlock,
    parse_metablock,
)
from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools.slice import SlicingStream
from archivey.types import ArchiveFormat
from tests.conftest import requires, requires_binary
from tests.streams_util import brotli_compressed_metablock_header

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
    assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,)) == MagicHit(400, MAGIC_7Z, 0)


def test_scan_returns_none_when_absent() -> None:
    assert scan_for_magic(io.BytesIO(b"\x00" * 10_000), (MAGIC_7Z,)) is None


def test_scan_finds_magic_straddling_a_chunk_boundary() -> None:
    # The overlap carried between reads is what makes this work; without it a magic
    # split across two 64 KiB reads is invisible.
    for split in range(1, len(MAGIC_7Z)):
        offset = 65536 - split
        data = b"\x00" * offset + MAGIC_7Z + b"\x00" * 128
        assert scan_for_magic(io.BytesIO(data), (MAGIC_7Z,)) == MagicHit(
            offset, MAGIC_7Z, 0
        )


def test_scan_returns_the_earliest_of_several_needles() -> None:
    data = b"\x00" * 50 + b"SECOND" + b"\x00" * 50 + b"FIRST"
    # Earliest position wins, not the order the needles were passed in.
    assert scan_for_magic(io.BytesIO(data), (b"FIRST", b"SECOND")) == MagicHit(
        50, b"SECOND", 0
    )


def test_scan_requires_the_whole_magic_inside_the_limit() -> None:
    data = b"\x00" * 10 + MAGIC_7Z
    assert scan_for_magic(
        io.BytesIO(data), (MAGIC_7Z,), limit=10 + len(MAGIC_7Z)
    ) == MagicHit(10, MAGIC_7Z, 0)
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


# --- detection: executable cues ---------------------------------------------------------


def _pe_stub(size: int = 8192, filler: bytes = b"\x90") -> bytes:
    """A DOS header whose ``e_lfanew`` really points at a ``PE\\0\\0`` signature."""
    header = bytearray(b"\x00" * 0x40)
    header[0:2] = b"MZ"
    header[0x3C:0x40] = struct.pack("<I", 0x80)
    body = bytearray(filler * (size - 0x40))
    body[0x80 - 0x40 : 0x80 - 0x40 + 4] = b"PE\x00\x00"
    return bytes(header) + bytes(body)


def test_executable_cue_grades_the_evidence() -> None:
    assert executable_cue(b"not an executable at all") is ExecutableCue.NONE
    # `MZ` alone is two bytes — enough to look for a payload, not enough to overrule a
    # content probe.
    assert executable_cue(_STUB) is ExecutableCue.WEAK
    assert executable_cue(_pe_stub()) is ExecutableCue.STRONG
    assert executable_cue(b"\x7fELF" + b"\x00" * 64) is ExecutableCue.WEAK
    assert executable_cue(b"\x7fELF\x02\x01\x01" + b"\x00" * 64) is ExecutableCue.STRONG


def test_a_real_elf_binary_is_a_strong_cue() -> None:
    """The strong cue against a shipped binary, not a hand-built header.

    Sampling the platform's own binaries is the point — a synthetic ELF header only
    proves the parser reads what this file wrote. Skipped where those binaries are not
    ELF: macOS ships Mach-O (`0xcafebabe` / `0xfeedfacf`) and Windows PE, and
    `executable_cue` knows only the two shapes `format-detection` names, so there is
    nothing to assert on those platforms.
    """
    for candidate in (Path("/usr/bin/env"), Path("/bin/ls"), Path("/usr/bin/python3")):
        if not candidate.is_file():
            continue
        prefix = candidate.read_bytes()[:4096]
        if prefix.startswith(b"\x7fELF"):
            assert executable_cue(prefix) is ExecutableCue.STRONG
            return
    pytest.skip("no ELF binary on this platform to sample")


def test_a_mach_o_binary_is_not_a_cue_today() -> None:
    """Records the known gap rather than leaving it to a platform-dependent surprise.

    A macOS self-extracting stub is Mach-O, which no cue matches, so an archive behind
    one still falls through to the content probes. `format-detection` names `MZ` and ELF
    only; widening it is a spec change, not an implementation detail.
    """
    fat_universal = b"\xca\xfe\xba\xbe" + b"\x00" * 4092
    mach_o_64 = b"\xcf\xfa\xed\xfe" + b"\x00" * 4092
    assert executable_cue(fat_universal) is ExecutableCue.NONE
    assert executable_cue(mach_o_64) is ExecutableCue.NONE


# --- detection: the SFX matrix ----------------------------------------------------------


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in _FILES.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _rar_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "rar-src"
    for name, data in _FILES.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    out = tmp_path / "payload.rar"
    subprocess.run(
        ["rar", "a", "-r", "-ep1", str(out), "."],
        cwd=source,
        check=True,
        capture_output=True,
    )
    return out.read_bytes()


def _7z_bytes(tmp_path: Path) -> bytes:
    inner = tmp_path / "payload.7z"
    _write_7z(inner)
    return inner.read_bytes()


def _assert_sfx_opens(path: Path, expected: ArchiveFormat, offset: int) -> None:
    detected = detect_format(path)
    assert detected.format == expected
    assert detected.payload_offset == offset
    assert detected.detected_by == "sfx_scan"
    with open_archive(path) as archive:
        members = {m.name: archive.read(m) for m in archive.members() if m.is_file}
    assert members == _FILES


@requires("py7zr")
def test_sfx_7z_behind_a_low_entropy_stub_is_not_brotli(tmp_path: Path) -> None:
    path = tmp_path / "installer.exe"
    path.write_bytes(_STUB + _7z_bytes(tmp_path))
    _assert_sfx_opens(path, ArchiveFormat.SEVEN_Z, len(_STUB))


def test_sfx_zip_behind_a_low_entropy_stub_is_not_brotli(tmp_path: Path) -> None:
    path = tmp_path / "installer.exe"
    path.write_bytes(_STUB + _zip_bytes())
    _assert_sfx_opens(path, ArchiveFormat.ZIP, len(_STUB))


@requires_binary("rar")
def test_sfx_rar_behind_a_low_entropy_stub_is_not_brotli(tmp_path: Path) -> None:
    path = tmp_path / "installer.exe"
    path.write_bytes(_STUB + _rar_bytes(tmp_path))
    _assert_sfx_opens(path, ArchiveFormat.RAR, len(_STUB))


@requires_binary("rar")
def test_a_real_sfx_archive_auto_opens(tmp_path: Path) -> None:
    """`rar a -sfx` output: a real ~250 KB stub, not a hand-rolled one.

    Before the scan this raised FormatDetectionError — the loud half of the same defect.
    """
    source = tmp_path / "sfx-src"
    source.mkdir()
    (source / "alpha.txt").write_bytes(_FILES["alpha.txt"])
    path = tmp_path / "real.exe"
    subprocess.run(
        ["rar", "a", "-ep", "-sfx", str(path), "alpha.txt"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    detected = detect_format(path)
    assert detected.format == ArchiveFormat.RAR
    assert detected.payload_offset > 0
    with open_archive(path) as archive:
        assert archive.read("alpha.txt") == _FILES["alpha.txt"]


def test_executable_prefix_with_a_pe_header_never_becomes_a_stream_codec(
    tmp_path: Path,
) -> None:
    """A confirmed PE with no archive in the window: an error, never a fake member."""
    path = tmp_path / "plain.exe"
    path.write_bytes(_pe_stub(200_000))
    with pytest.raises(FormatDetectionError):
        detect_format(path)


def test_a_weak_cue_still_lets_a_content_probe_answer(tmp_path: Path) -> None:
    """`MZ` is two bytes: a probe-matched stream that starts with them stays detectable.

    The short A-34 stub (`MZ` + ``\\x90`` × 4094) is no longer a probe hit — the framing
    gate rejects its declared meta-block overrun. A longer stream with the same weak cue
    and a *fitting* first block still answers ``BROTLI``, which is what proves the weak
    cue does not suppress content probes.
    """
    brotli = pytest.importorskip("brotli")
    del brotli
    # Smallest MZ-leading uncompressed first-block the bit layout allows (~1 MiB body).
    header = bytes.fromhex("4d5a0088")
    framing = parse_metablock(header)
    assert framing.outcome is BrotliBlock.UNCOMPRESSED
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    # Pad a compressed second link so the chain walk stops rather than rejecting
    # exact EOF-without-ISLAST (same residual shape as OLE/COFF fixtures).
    second = brotli_compressed_metablock_header(first=False)
    path = tmp_path / "weak.bin"
    path.write_bytes(header + b"\x90" * (need - len(header)) + second + b"\x00" * 8)
    assert executable_cue(path.read_bytes()[:8]) is ExecutableCue.WEAK
    detected = detect_format(path)
    assert detected.format == ArchiveFormat.BROTLI
    assert detected.detected_by == "content_probe"


def test_a_real_brotli_stream_is_unaffected(tmp_path: Path) -> None:
    brotli = pytest.importorskip("brotli")
    path = tmp_path / "payload.bin"
    path.write_bytes(brotli.compress(b"hello world\n" * 500))
    detected = detect_format(path)
    assert detected.format == ArchiveFormat.BROTLI
    assert detected.detected_by == "content_probe"
    assert detected.payload_offset == 0


def test_no_archive_needle_in_the_window_falls_through_to_the_extension(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mislabelled.zip"
    path.write_bytes(_pe_stub(100_000))
    detected = detect_format(path)
    assert detected.format == ArchiveFormat.ZIP
    assert detected.detected_by == "extension"
    assert detected.payload_offset == 0


def test_the_scan_does_not_reach_past_the_shared_bound(tmp_path: Path) -> None:
    path = tmp_path / "far.exe"
    path.write_bytes(b"MZ" + b"\x00" * SFX_MAX + _zip_bytes())
    with pytest.raises(FormatDetectionError):
        detect_format(path)


def test_detection_leaves_a_non_seekable_stream_replayable(tmp_path: Path) -> None:
    """The scan peeks; it must not eat the bytes the backend still needs.

    A pipe cannot be rewound, so this is what stops the SFX window from being a
    destructive read for the streaming ZIP reader this repo is heading towards.
    """
    payload = _STUB + _zip_bytes()
    source = PeekableStream(io.BytesIO(payload))
    detected = detect_format(source)
    assert detected.format == ArchiveFormat.ZIP
    assert detected.payload_offset == len(_STUB)
    assert source.read() == payload


# --- the payload_offset hand-off --------------------------------------------------------


@pytest.mark.parametrize(
    "builder,expected",
    [
        pytest.param(lambda tmp: _zip_bytes(), ArchiveFormat.ZIP, id="zip"),
        pytest.param(
            _7z_bytes,
            ArchiveFormat.SEVEN_Z,
            id="7z",
            marks=requires("py7zr"),
        ),
        pytest.param(
            _rar_bytes,
            ArchiveFormat.RAR,
            id="rar",
            marks=requires_binary("rar"),
        ),
    ],
)
def test_auto_open_matches_an_explicitly_sliced_stream(
    tmp_path: Path, builder, expected: ArchiveFormat
) -> None:
    """Auto-open of an SFX file equals opening a view whose byte 0 is the payload.

    The hand-off is an argument rather than a slice so a path source stays a path; this
    is the assertion that the two are nonetheless the same archive.
    """
    payload = builder(tmp_path)
    path = tmp_path / "installer.exe"
    path.write_bytes(_STUB + payload)

    with open_archive(path) as auto:
        assert auto.format == expected
        auto_read = {m.name: auto.read(m) for m in auto.members() if m.is_file}

    with path.open("rb") as handle:
        with open_archive(SlicingStream(handle, start=len(_STUB))) as sliced:
            sliced_read = {
                m.name: sliced.read(m) for m in sliced.members() if m.is_file
            }

    assert auto_read == sliced_read == _FILES


def test_a_stub_carrying_a_decoy_zip_header_does_not_move_the_answer(
    tmp_path: Path,
) -> None:
    """ZIP is sliced at the detected offset rather than left to self-adjust.

    zipfile would find the EOCD from the tail and correct for a stub on its own, so this
    only fails if the slice is dropped — the point being that ``start_offset`` decides
    where the archive starts, not the file's own tail arithmetic.
    """
    payload = _zip_bytes()
    path = tmp_path / "installer.exe"
    path.write_bytes(_STUB + payload)

    detected = detect_format(path)
    assert detected.payload_offset == len(_STUB)
    with open_archive(path) as archive:
        assert {
            m.name: archive.read(m) for m in archive.members() if m.is_file
        } == _FILES


@requires("py7zr")
def test_a_decoy_needle_in_the_stub_wins_and_fails_loudly(tmp_path: Path) -> None:
    """A known scanner limit, pinned: earliest match wins, even against a real payload.

    ``detect_format`` takes the first needle in the window, so a stub carrying one
    decides ``payload_offset`` and the backend opens *there* rather than at a later real
    archive. What makes it acceptable for now is that the failure is loud — 7z rejects
    the signature CRC — rather than the fabricated-member silence this change exists to
    close. Validate-and-continue would turn detection into a trial-open loop; if that
    ever lands, this test is the one to rewrite.
    """
    inner = tmp_path / "real-inner.7z"
    _write_7z(inner)
    stub = b"MZ" + b"\x90" * 512 + MAGIC_7Z + b"\x90" * 3576
    path = tmp_path / "decoy-auto.exe"
    path.write_bytes(stub + inner.read_bytes())

    detected = detect_format(path)
    assert detected.format == ArchiveFormat.SEVEN_Z
    assert detected.payload_offset == 514, "the decoy, not the real payload at 4096"

    with pytest.raises(CorruptionError):
        open_archive(path)

    # The real payload is reachable the moment something supplies the right offset.
    with _open_with(path, start_offset=len(stub)) as archive:
        assert {m.name for m in archive.members() if m.is_file} == set(_FILES)


def test_a_decoy_zip_needle_still_opens_via_the_tail(tmp_path: Path) -> None:
    """ZIP survives the same decoy, because zipfile locates the EOCD from the tail.

    Recorded next to the 7z case so the asymmetry is visible: the scanner limit bites
    per-format, and only where the backend trusts the offset as the archive origin.
    """
    stub = b"MZ" + b"\x90" * 512 + b"PK\x03\x04" + b"\x90" * 3576
    path = tmp_path / "decoy-zip.exe"
    path.write_bytes(stub + _zip_bytes())

    assert detect_format(path).payload_offset == 514
    with open_archive(path) as archive:
        assert {
            m.name: archive.read(m) for m in archive.members() if m.is_file
        } == _FILES

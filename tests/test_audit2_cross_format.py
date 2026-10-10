"""Second cross-format audit: the public reading API, parity between backends, and the
``list`` / ``info`` / ``test`` CLI verbs.

These are regression tests for the second audit's findings: each test asserts the
promised behaviour, and the section headings name the finding (``C<n>``, ``X<n>``).
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
from archivey.diagnostics import (
    ArchiveEofContext,
    Diagnostic,
    DiagnosticCode,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSummary,
)
from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    DiagnosticRaisedError,
    EncryptionError,
    LinkTargetNotFoundError,
    PackageNotInstalledError,
    ReadError,
    TruncatedError,
)
from archivey.internal.backends.rar_parser import (
    parse_rar_archive,
    parse_rar_volumes,
)
from archivey.reader import ForwardArchiveReader
from archivey.types import ArchiveMember, MemberType
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


def _eof_marker_diagnostics(summary: DiagnosticSummary) -> list[Diagnostic]:
    return [
        d
        for d in summary.retained
        if d.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING
    ]


def _rar5_without_endarc(data: bytes) -> bytes:
    blocks = _rar5_blocks(data)
    assert blocks[-1][1] == 5  # precondition: the writer put ENDARC last
    return data[: blocks[-1][0]]


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_rar5_without_endarc_warns_once_and_lists(streaming: bool) -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        full = [m.name for m in reader.members()]
    cut = _rar5_without_endarc(data)
    with open_archive(io.BytesIO(cut), streaming=streaming) as reader:
        names = [m.name for m, _ in reader.stream_members()]
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert names == full
    assert len(diagnostics) == 1
    context = diagnostics[0].context
    assert isinstance(context, ArchiveEofContext)
    assert context.format == "rar"
    assert context.expected_marker == "end_of_archive_block"
    assert context.observed_kind == "absent"


def test_rar5_complete_archive_emits_no_eof_warning() -> None:
    with open_archive(_RAR_FIXTURES / "basic_nonsolid__.rar") as reader:
        reader.members_report()
        counts = reader.diagnostics.counts
    assert counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) == 0


def test_rar5_cut_at_header_boundary_refused_under_strict() -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(io.BytesIO(_rar5_without_endarc(data)), config=config) as reader:
        with pytest.raises(DiagnosticRaisedError):
            reader.members()


def test_rar5_volume_set_endarc(tmp_path: Path) -> None:
    parts = [(_RAR_FIXTURES / f"tinyvol.part{n}.rar").read_bytes() for n in (1, 2)]
    for n, part in enumerate(parts, 1):
        (tmp_path / f"tinyvol.part{n}.rar").write_bytes(part)
    with open_archive(tmp_path / "tinyvol.part1.rar") as reader:
        full = [m.name for m in reader.members()]
        assert (
            reader.diagnostics.counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0)
            == 0
        )
    # Volume 1 cut before its ENDARC: the split member still continues into
    # volume 2, so the set lists in full, and the missing block is reported.
    (tmp_path / "tinyvol.part1.rar").write_bytes(_rar5_without_endarc(parts[0]))
    with open_archive(tmp_path / "tinyvol.part1.rar") as reader:
        assert [m.name for m in reader.members()] == full
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 1
    assert "volume(s) 1 " in diagnostics[0].message


# With header encryption (``rar a -hp``) every header is a salt (RAR3, 8 bytes) or IV
# (RAR5, 16 bytes) and then whole 16-byte AES blocks. The last block of both
# fixtures is the end-of-archive header: RAR5 16 + 16 bytes, RAR3 8 + 16 bytes.
# Cutting 1-16 bytes left a partial AES block that the walk took as a clean end.
_HP_PASSWORD = "header_password"


def _hp_fixture(name: str) -> bytes:
    return (_RAR_FIXTURES / name).read_bytes()


def _assert_truncated_listing(
    data: bytes,
    members: int,
    streaming: bool,
    *,
    password: str | None = _HP_PASSWORD,
    message: str = "encrypted header",
) -> None:
    with open_archive(
        io.BytesIO(data), password=password, streaming=streaming
    ) as reader:
        report = reader.members_report()
        assert len(report.members) == members
        assert isinstance(report.error, TruncatedError)
        assert message in str(report.error)
        assert not _eof_marker_diagnostics(reader.diagnostics)
    with open_archive(
        io.BytesIO(data), password=password, streaming=streaming
    ) as reader:
        # members() is random-access only; scan_members() is the streaming listing.
        with pytest.raises(TruncatedError):
            reader.scan_members() if streaming else reader.members()


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    "cut",
    # 1-16: inside the end block's ciphertext; 17-31: inside its IV.
    [1, 2, 8, 15, 16, 17, 24, 31],
)
def test_rar5_header_encrypted_cut_in_last_header_is_truncated(
    cut: int, streaming: bool
) -> None:
    data = _hp_fixture("encrypted_header__.rar")
    _assert_truncated_listing(data[:-cut], members=6, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("cut", [33, 40, 48, 63])
def test_rar5_header_encrypted_cut_inside_a_verified_member_header_is_truncated(
    cut: int,
) -> None:
    """Past the end block, the cut lands in the last directory's 64-byte header. The
    fixture's password check value proves the key, so a header that runs out of
    bytes part-way is a cut, not a wrong password."""
    data = _hp_fixture("encrypted_header__.rar")
    _assert_truncated_listing(data[:-cut], members=5, streaming=False)


@requires("cryptography")
def test_rar5_header_encrypted_cut_at_a_header_boundary_warns() -> None:
    """Without any of the end block's 32 bytes, the walk ends at a header boundary:
    the same warning as a plain RAR5 without its end block."""
    data = _hp_fixture("encrypted_header__.rar")
    with open_archive(io.BytesIO(data[:-32]), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert report.error is None
        assert len(report.members) == 6
        assert len(_eof_marker_diagnostics(reader.diagnostics)) == 1


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    "cut",
    # 1-16: inside the end block's ciphertext; 17-23: inside its salt.
    [1, 8, 16, 17, 23],
)
def test_rar4_header_encrypted_cut_in_last_header_is_truncated(
    cut: int, streaming: bool
) -> None:
    data = _hp_fixture("encrypted_header__rar4.rar")
    _assert_truncated_listing(data[:-cut], members=6, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    ("length", "members"),
    # Each length ends inside a FILE header past its first cipher block, after
    # encrypted headers whose CRC16 matched. 160 is in the second FILE header, the
    # first one after the key is proven.
    [(160, 1), (250, 2), (480, 4)],
)
def test_rar4_header_encrypted_cut_after_a_proven_key_is_truncated(
    length: int, members: int, streaming: bool
) -> None:
    """RAR3 has no password check value, but a decrypted header whose CRC16 matches
    proves the key: the walk already takes a mismatch as a wrong password. From then
    on, a header that runs out of bytes part-way is a cut, as in RAR5 with a verified
    check value."""
    data = _hp_fixture("encrypted_header__rar4.rar")
    _assert_truncated_listing(data[:length], members=members, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("length", [44, 60, 91])
def test_rar4_header_encrypted_cut_inside_the_first_encrypted_header_is_a_wrong_password(
    length: int,
) -> None:
    """Inside the first encrypted header (bytes 44-91 of the fixture) no header has
    decrypted yet, so nothing proves the key, and a garbage header size from a wrong
    key also runs to the end of the file. That stays a wrong-password error."""
    data = _hp_fixture("encrypted_header__rar4.rar")
    with pytest.raises(EncryptionError, match="wrong password"):
        with open_archive(io.BytesIO(data[:length]), password=_HP_PASSWORD) as reader:
            reader.members()


def _rar3_reencrypt_header(
    data: bytes, salt_at: int, length: int, edit: Callable[[bytearray], None]
) -> bytes:
    """Decrypt the ``length`` ciphertext bytes of the RAR3 header whose salt starts at
    ``salt_at``, apply ``edit`` to the plaintext and encrypt it again with the same
    key, so a test can change a field the cipher would otherwise scramble."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from archivey.internal.backends.rar_parser import RarKdfCache

    salt = data[salt_at : salt_at + 8]
    start = salt_at + 8
    key, iv = RarKdfCache().rar3(_HP_PASSWORD, salt)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    plain = bytearray(cipher.decryptor().update(data[start : start + length]))
    edit(plain)
    encrypted = cipher.encryptor().update(bytes(plain))
    return data[:start] + encrypted + data[start + length :]


# encrypted_header__rar4.rar: the first encrypted header (file1.txt) is its salt at
# 20 and 64 ciphertext bytes; the second (empty_file.txt) is its salt at 124 and 64
# ciphertext bytes; the end block is the last 24 bytes.
_RAR4_HP_SECOND_HEADER = 124


# The same damaged bytes with a wrong password: nothing proves that key, so the
# failure is still a wrong password and a password list goes on to the next one.
_WRONG_PASSWORD_CASE = pytest.param(
    "nope", EncryptionError, r"wrong password\?", id="wrong-password"
)


@requires("cryptography")
@pytest.mark.parametrize(
    ("password", "error", "message"),
    [
        pytest.param(
            _HP_PASSWORD,
            CorruptionError,
            "RAR3 FILE header CRC mismatch",
            id="right-password",
        ),
        _WRONG_PASSWORD_CASE,
    ],
)
def test_rar4_header_encrypted_damage_after_a_proven_key_is_corruption(
    password: str, error: type[Exception], message: str
) -> None:
    """Once a CRC16 match has proved the password, a later header whose CRC16 does
    not match is damage. Bit 0 of byte 164 is in the second FILE header's
    ciphertext, past the first encrypted header that proved the key."""
    data = bytearray(_hp_fixture("encrypted_header__rar4.rar"))
    data[164] ^= 1
    with pytest.raises(error, match=message):
        parse_rar_archive(io.BytesIO(bytes(data)), password=password)


@requires("cryptography")
@pytest.mark.parametrize(
    ("password", "error", "message"),
    [
        pytest.param(
            _HP_PASSWORD,
            CorruptionError,
            "Invalid RAR3 header size: 3",
            id="right-password",
        ),
        _WRONG_PASSWORD_CASE,
    ],
)
def test_rar4_header_encrypted_bad_size_after_a_proven_key_is_corruption(
    password: str, error: type[Exception], message: str
) -> None:
    """A proven password and a header size below the 7-byte minimum that does not
    run to the end of the file: the structural error, not a wrong password."""

    def shrink(plain: bytearray) -> None:
        struct.pack_into("<H", plain, 5, 3)

    data = _rar3_reencrypt_header(
        _hp_fixture("encrypted_header__rar4.rar"),
        _RAR4_HP_SECOND_HEADER,
        64,
        shrink,
    )
    with pytest.raises(error, match=message):
        parse_rar_archive(io.BytesIO(data), password=password)


def _rar3_endarc_next_volume(plain: bytearray) -> None:
    """Set ENDARC_NEXT_VOLUME in a decrypted RAR3 end block and fix its CRC16."""
    flags = struct.unpack_from("<H", plain, 3)[0] | 0x0001
    struct.pack_into("<H", plain, 3, flags)
    struct.pack_into("<H", plain, 0, zlib.crc32(plain[2:7]) & 0xFFFF)


@requires("cryptography")
def test_rar4_header_encrypted_later_volume_cut_in_its_first_header_is_truncated() -> (
    None
):
    """A set has one password, so a CRC16 match in volume 1 proves it for volume 2.
    Volume 2 cut inside its own first encrypted header is then a cut, after volume
    1's members.

    The set is built from the single-volume fixture: volume 1 is that archive with its
    end block re-encrypted to carry the next-volume flag, and volume 2 is the same
    archive cut at byte 60. ``rar`` 7 cannot write RAR 1.5-4 (no ``-ma4``), so no
    committed RAR 1.5-4 ``-hp`` volume set exists."""
    complete = _hp_fixture("encrypted_header__rar4.rar")
    volume1 = _rar3_reencrypt_header(
        complete, len(complete) - 24, 16, _rar3_endarc_next_volume
    )
    archive = parse_rar_volumes(
        [io.BytesIO(volume1), io.BytesIO(complete[:60])], password=_HP_PASSWORD
    )
    assert len(archive.members) == 6
    assert archive.truncated is not None
    # Byte 20 of volume 2, not of the set: volume 1 is 588 bytes.
    assert (
        "encrypted header that starts at byte 20 (volume 2 of the set"
        in archive.truncated
    )


@requires("cryptography")
@pytest.mark.parametrize(
    ("password", "error", "message"),
    [
        pytest.param(
            _HP_PASSWORD,
            CorruptionError,
            "RAR3 FILE header CRC mismatch",
            id="right-password",
        ),
        _WRONG_PASSWORD_CASE,
    ],
)
def test_rar4_header_encrypted_end_block_proves_the_key_for_the_next_volume(
    password: str, error: type[Exception], message: str
) -> None:
    """Volume 1's only encrypted header is its end block. Its CRC16 match proves the
    password like any other header's, so damage in volume 2's first encrypted header
    is corruption, not a wrong password.

    Volume 1 is the fixture's MARK and MAIN (20 bytes) and its end block, re-encrypted
    to carry the next-volume flag. Volume 2 is the fixture with bit 0 of byte 60
    flipped, inside its first encrypted header's ciphertext (salt at 20)."""
    complete = _hp_fixture("encrypted_header__rar4.rar")
    volume1 = _rar3_reencrypt_header(
        complete[:20] + complete[-24:], 20, 16, _rar3_endarc_next_volume
    )
    volume2 = bytearray(complete)
    volume2[60] ^= 1
    with pytest.raises(error, match=message):
        parse_rar_volumes(
            [io.BytesIO(volume1), io.BytesIO(bytes(volume2))], password=password
        )


@requires("cryptography")
@pytest.mark.parametrize(
    "name", ["encrypted_header__.rar", "encrypted_header__rar4.rar"]
)
def test_header_encrypted_cut_inside_packed_data_is_truncated(name: str) -> None:
    """A cut inside the first member's packed data leaves the walk past the end of
    the file where the next header's salt or IV should be: the truncated listing
    a plain RAR gives, not an error at open."""
    data = _hp_fixture(name)
    with open_archive(io.BytesIO(data), password=_HP_PASSWORD) as reader:
        first = reader.members()[0]
    # The fixtures' first member, file1.txt, has 32 bytes of packed data; keep 10.
    assert first.name == "file1.txt"
    offset = {"encrypted_header__.rar": 190, "encrypted_header__rar4.rar": 92}[name]
    with open_archive(io.BytesIO(data[: offset + 10]), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert [m.name for m in report.members] == ["file1.txt"]
        assert isinstance(report.error, TruncatedError)
        assert "packed data" in str(report.error)


# A plain (unencrypted) RAR cut part-way through a header used to raise
# CorruptionError at open, with no listing. unrar 7.00 lists the members before the
# cut and then reports "Unexpected end of archive" (measured on
# basic_nonsolid__rar4.rar cut to 140 and 190 bytes).
@pytest.mark.parametrize(
    ("fixture", "walker", "file_type"),
    [
        pytest.param("basic_nonsolid__.rar", _rar5_blocks, 2, id="rar5"),
        pytest.param("basic_nonsolid__rar4.rar", _rar4_blocks, 0x74, id="rar4"),
    ],
)
def test_rar_plain_cut_inside_any_header_lists_the_prefix(
    fixture: str,
    walker: Callable[[bytes], list[tuple[int, int, int, int]]],
    file_type: int,
) -> None:
    data = (_RAR_FIXTURES / fixture).read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        full = [m.name for m in reader.members()]
    blocks = walker(data)
    assert len(blocks) > len(full)  # precondition: MAIN, every FILE, the end block
    checked = 0
    for block_pos, _type, header_end, _size in blocks:
        listed = full[
            : sum(1 for b in blocks if b[1] == file_type and b[0] < block_pos)
        ]
        # Every byte after the header's first and before its last: for RAR5 that
        # includes the CRC and the header-size vint.
        for cut in range(block_pos + 1, header_end):
            with open_archive(io.BytesIO(data[:cut])) as reader:
                report = reader.members_report()
                assert [m.name for m in report.members] == listed, cut
                assert isinstance(report.error, TruncatedError), cut
                assert f"header that starts at byte {block_pos}" in str(report.error)
                assert not _eof_marker_diagnostics(reader.diagnostics), cut
            checked += 1
    assert checked > 100


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    ("fixture", "walker"),
    [
        pytest.param("basic_nonsolid__.rar", _rar5_blocks, id="rar5"),
        pytest.param("basic_nonsolid__rar4.rar", _rar4_blocks, id="rar4"),
    ],
)
def test_rar_plain_cut_inside_a_header_raises_from_members(
    fixture: str,
    walker: Callable[[bytes], list[tuple[int, int, int, int]]],
    streaming: bool,
) -> None:
    """One cut through the shared helper: inside the fourth FILE header, after
    three whole members, so ``members()`` raises and streaming lists the same."""
    data = (_RAR_FIXTURES / fixture).read_bytes()
    block_pos, _type, header_end, _size = walker(data)[4]  # MAIN, then FILE blocks
    _assert_truncated_listing(
        data[: (block_pos + header_end) // 2],
        members=3,
        streaming=streaming,
        password=None,
        message=f"header that starts at byte {block_pos}",
    )


@pytest.mark.parametrize(
    ("fixture", "walker", "file_type", "warns"),
    [
        pytest.param("basic_nonsolid__.rar", _rar5_blocks, 2, True, id="rar5"),
        pytest.param("basic_nonsolid__rar4.rar", _rar4_blocks, 0x74, False, id="rar4"),
    ],
)
def test_rar_plain_cut_at_a_header_boundary_is_not_a_cut(
    fixture: str,
    walker: Callable[[bytes], list[tuple[int, int, int, int]]],
    file_type: int,
    warns: bool,
) -> None:
    """Unchanged: a cut exactly where a header starts is a clean end for RAR 1.5-4,
    and for RAR5 the missing end block is a warning, not an error."""
    data = (_RAR_FIXTURES / fixture).read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        full = [m.name for m in reader.members()]
    blocks = walker(data)
    # blocks[0] is MAIN, so the first cut leaves only the signature.
    for block_pos, _type, _end, _size in blocks:
        listed = full[
            : sum(1 for b in blocks if b[1] == file_type and b[0] < block_pos)
        ]
        with open_archive(io.BytesIO(data[:block_pos])) as reader:
            report = reader.members_report()
            assert [m.name for m in report.members] == listed, block_pos
            assert report.error is None, block_pos
            assert bool(_eof_marker_diagnostics(reader.diagnostics)) is warns


# ---------------------------------------------------------------------------
# An end-of-archive block with a bad header CRC keeps the listing
# ---------------------------------------------------------------------------
# Maintainer ruling 2026-10-03. The block sits after the last member, so damage to
# it is terminal damage after the members: they list, open and read, the reader
# reports ARCHIVE_EOF_MARKER_MISSING after them (observed_kind="nonzero": a
# block is there, but it is not a valid end block), and a strict policy refuses.
# Measured on unrar 7.00 with one CRC byte of the end block flipped in
# basic_nonsolid__rar4.rar and basic_nonsolid__.rar: `unrar l` lists all six
# members and exits 0, and `unrar t` tests each member OK, then reports
# "Total errors" and exits 3. Before the ruling archivey raised CorruptionError at
# open and listed nothing.

_RAR4_ENDARC = 0x7B
_RAR5_ENDARC = 5


def _endarc_block(data: bytes, version: int) -> tuple[int, int]:
    """``(block_pos, header_end)`` of the end-of-archive block, the writer's last."""
    if version == 4:
        block_pos, block_type, header_end, _size = _rar4_blocks(data)[-1]
        assert block_type == _RAR4_ENDARC  # precondition
    else:
        block_pos, block_type, header_end, _size = _rar5_blocks(data)[-1]
        assert block_type == _RAR5_ENDARC  # precondition
    return block_pos, header_end


def _edit_endarc(
    data: bytes, version: int, *, damage_crc: bool = True, next_volume: bool = False
) -> bytes:
    """Optionally set the end block's next-volume flag, recompute its header CRC,
    and then optionally flip a byte of that CRC."""
    out = bytearray(data)
    block_pos, header_end = _endarc_block(data, version)
    if version == 4:
        if next_volume:
            flags = struct.unpack_from("<H", out, block_pos + 3)[0]
            struct.pack_into("<H", out, block_pos + 3, flags | 0x0001)
        crc = zlib.crc32(out[block_pos + 2 : header_end]) & 0xFFFF
        struct.pack_into("<H", out, block_pos, crc)
    else:
        if next_volume:
            # CRC32, size vint, type, block flags, then the end-of-archive flags;
            # every vint here is one byte in the fixtures.
            flags_at = block_pos + 4 + 1 + 1 + 1
            # precondition: no extra or data area
            assert not out[block_pos + 6] & 0x03
            out[flags_at] |= 0x01
        crc = zlib.crc32(out[block_pos + 4 : header_end])
        struct.pack_into("<I", out, block_pos, crc)
    if damage_crc:
        out[block_pos] ^= 0xFF
    return bytes(out)


_ENDARC_FIXTURES = [
    pytest.param("basic_nonsolid__rar4.rar", 4, id="rar4"),
    pytest.param("basic_nonsolid__.rar", 5, id="rar5"),
]


def _members_and_bytes(
    reader: ForwardArchiveReader,
) -> list[tuple[str, bytes | None]]:
    return [
        (member.name, stream.read() if stream is not None else None)
        for member, stream in reader.stream_members()
    ]


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_endarc_keeps_the_listing(
    fixture: str, version: int, streaming: bool
) -> None:
    data = (_RAR_FIXTURES / fixture).read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        expected = _members_and_bytes(reader)
    damaged = _edit_endarc(data, version)
    with open_archive(io.BytesIO(damaged), streaming=streaming) as reader:
        assert _members_and_bytes(reader) == expected
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 1
    context = diagnostics[0].context
    assert isinstance(context, ArchiveEofContext)
    assert context.format == "rar"
    assert context.expected_marker == "end_of_archive_block"
    assert context.observed_kind == "nonzero"
    assert context.observed_bytes == _endarc_block(data, version)[0]
    assert "header CRC" in diagnostics[0].message
    # A lone archive has no next volume, so the message does not mention one.
    assert "next-volume" not in diagnostics[0].message


@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_endarc_random_access_reads_every_member(
    fixture: str, version: int
) -> None:
    data = (_RAR_FIXTURES / fixture).read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        expected = {m.name: reader.read(m) for m in reader.members() if m.is_file}
    with open_archive(io.BytesIO(_edit_endarc(data, version))) as reader:
        report = reader.members_report()
        assert report.error is None
        assert {m.name: reader.read(m) for m in report if m.is_file} == expected


@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_endarc_refused_under_strict(fixture: str, version: int) -> None:
    data = _edit_endarc((_RAR_FIXTURES / fixture).read_bytes(), version)
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(io.BytesIO(data), config=config) as reader:
        with pytest.raises(DiagnosticRaisedError):
            reader.members()


@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_intact_endarc_emits_no_eof_warning(fixture: str, version: int) -> None:
    """Control: the same edit with the CRC left valid is a clean end block."""
    data = _edit_endarc(
        (_RAR_FIXTURES / fixture).read_bytes(), version, damage_crc=False
    )
    archive = parse_rar_archive(io.BytesIO(data), password=None)
    assert archive.end_block_damaged_volumes == {}
    with open_archive(io.BytesIO(data)) as reader:
        reader.members()
        assert not _eof_marker_diagnostics(reader.diagnostics)


@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_endarc_next_volume_flag_is_not_followed(
    fixture: str, version: int
) -> None:
    """A damaged block's flags are not data, so its next-volume flag does not chain
    the walk to another volume. Only a member header whose CRC matched can say that
    its data continues (see the volume-set test below)."""
    data = (_RAR_FIXTURES / fixture).read_bytes()
    intact = _edit_endarc(data, version, damage_crc=False, next_volume=True)
    assert parse_rar_archive(io.BytesIO(intact), password=None).needs_next_volume
    damaged = _edit_endarc(data, version, next_volume=True)
    archive = parse_rar_archive(io.BytesIO(damaged), password=None)
    assert not archive.needs_next_volume
    endarc_at = _endarc_block(data, version)[0]
    assert archive.end_block_damaged_volumes == {0: endarc_at}
    # A set ends at the damaged volume: the bytes passed as volume 2 are not read,
    # so they need not even be RAR.
    merged = parse_rar_volumes(
        [io.BytesIO(damaged), io.BytesIO(b"not a RAR volume")], password=None
    )
    assert [m.filename for m in merged.members] == [m.filename for m in archive.members]
    assert merged.end_block_damaged_volumes == {0: endarc_at}


@pytest.mark.parametrize(
    ("names", "version"),
    [
        pytest.param(("tinyvol.part1.rar", "tinyvol.part2.rar"), 5, id="rar5"),
        pytest.param(("tinyvol_rnn.rar", "tinyvol_rnn.r00"), 4, id="rar4"),
    ],
)
@requires_binary("unrar")
def test_rar_volume_set_damaged_endarc_continues_on_a_split_member(
    tmp_path: Path, names: tuple[str, str], version: int
) -> None:
    """Volume 1's end block is damaged, but its last member's header (CRC intact)
    says the data continues, so the set still reads in full and the damage is
    reported once, naming volume 1. unrar 7.00 on the same RAR5 set: `unrar t`
    follows the member into volume 2 and exits 0; `unrar l` reports "Corrupt
    header is found" and exits 3."""
    for name in names:
        (tmp_path / name).write_bytes((_RAR_FIXTURES / name).read_bytes())
    with open_archive(tmp_path / names[0]) as reader:
        expected = {m.name: reader.read(m) for m in reader.members() if m.is_file}
    first = tmp_path / names[0]
    first.write_bytes(_edit_endarc(first.read_bytes(), version))
    with open_archive(first) as reader:
        assert {m.name: reader.read(m) for m in reader.members() if m.is_file} == (
            expected
        )
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 1
    assert "volume 1," in diagnostics[0].message
    assert "next-volume flag was not followed" in diagnostics[0].message


@pytest.mark.parametrize(
    ("names", "version"),
    [
        pytest.param(("tinyvol.part1.rar", "tinyvol.part2.rar"), 5, id="rar5"),
        pytest.param(("tinyvol_rnn.rar", "tinyvol_rnn.r00"), 4, id="rar4"),
    ],
)
@requires_binary("unrar")
def test_rar_volume_set_damaged_endarc_in_every_volume_reports_each_volume(
    tmp_path: Path, names: tuple[str, str], version: int
) -> None:
    """Both volumes' end blocks are damaged: the set still reads in full and each
    volume gets its own diagnostic, in volume order, with the offset of its own end
    block within that volume."""
    for name in names:
        (tmp_path / name).write_bytes((_RAR_FIXTURES / name).read_bytes())
    with open_archive(tmp_path / names[0]) as reader:
        expected = {m.name: reader.read(m) for m in reader.members() if m.is_file}
    offsets = []
    for name in names:
        path = tmp_path / name
        data = path.read_bytes()
        offsets.append(_endarc_block(data, version)[0])
        path.write_bytes(_edit_endarc(data, version))
    with open_archive(tmp_path / names[0]) as reader:
        assert {m.name: reader.read(m) for m in reader.members() if m.is_file} == (
            expected
        )
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 2
    for number, (diagnostic, offset) in enumerate(zip(diagnostics, offsets), 1):
        assert f"volume {number}," in diagnostic.message
        assert diagnostic.context.observed_bytes == offset


def _rar3_hp_damaged_endarc(data: bytes) -> bytes:
    """Flip a CRC16 byte inside the encrypted end block, the file's last 24 bytes
    (8 salt bytes, then one cipher block)."""

    def flip(plain: bytearray) -> None:
        assert plain[2] == _RAR4_ENDARC  # precondition
        plain[0] ^= 0xFF

    return _rar3_reencrypt_header(data, len(data) - 24, 16, flip)


@requires("cryptography")
def test_rar4_header_encrypted_damaged_endarc_after_a_proven_key_keeps_the_listing() -> (
    None
):
    data = _rar3_hp_damaged_endarc(_hp_fixture("encrypted_header__rar4.rar"))
    with open_archive(io.BytesIO(data), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert report.error is None
        assert len(report.members) == 6
        assert len(_eof_marker_diagnostics(reader.diagnostics)) == 1


@requires("cryptography")
def test_rar4_header_encrypted_damaged_endarc_with_an_unproven_key_is_a_wrong_password() -> (
    None
):
    """With no member header before it, the end block is the first encrypted header,
    so nothing has proved the password: a CRC16 mismatch there reads the same as a
    wrong key, and stays the wrong-password error."""
    data = _hp_fixture("encrypted_header__rar4.rar")
    main_end = _rar4_blocks(data[:20])[0][2]
    assert main_end == 20  # precondition: signature + MAIN, then the first salt
    empty = data[:main_end] + data[-24:]
    # Control: intact, the end block's CRC16 proves the password; nothing is listed.
    with open_archive(io.BytesIO(empty), password=_HP_PASSWORD) as reader:
        assert reader.members() == []
    damaged = _rar3_hp_damaged_endarc(empty)
    with pytest.raises(EncryptionError, match=r"wrong password\?"):
        with open_archive(io.BytesIO(damaged), password=_HP_PASSWORD) as reader:
            reader.members()


def _rar5_hp_reencrypt_endarc(data: bytes, password: str = _HP_PASSWORD) -> bytes:
    """Flip a CRC32 byte inside the encrypted end block, the file's last 32 bytes
    (16 IV bytes, then one cipher block)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from archivey.internal.backends.rar_parser import RarKdfCache

    # The ENCRYPTION header follows the signature: CRC32, size, type 4, flags 0,
    # algorithm, encryption flags, KDF count, then the 16-byte salt.
    _size, p = _vint(data, 8 + 4)
    block_type, p = _vint(data, p)
    assert block_type == 4  # precondition
    _flags, p = _vint(data, p)
    _algo, p = _vint(data, p)
    _enc_flags, p = _vint(data, p)
    kdf_count = data[p]
    salt = data[p + 1 : p + 17]
    key = RarKdfCache().rar5(password, salt, 1 << kdf_count)
    iv = data[-32:-16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    plain = bytearray(cipher.decryptor().update(data[-16:]))
    assert plain[5] == _RAR5_ENDARC  # precondition: CRC32, size, then the type
    plain[0] ^= 0xFF
    return data[:-16] + cipher.encryptor().update(bytes(plain))


def _rar5_hp_without_check_value(data: bytes) -> bytes:
    """Drop the password check value from the plain ENCRYPTION header, as the gap
    report describes: clear its flag, cut its 12 bytes, fix the size and CRC32."""
    start = 8
    size, body_at = _vint(data, start + 4)
    body = bytearray(data[body_at : body_at + size])
    # type 4, flags 0, algorithm 0, encryption flags 1, KDF count, salt, check value
    assert body[:4] == b"\x04\x00\x00\x01"  # precondition: one-byte vints
    body[3] = 0
    body = body[: 4 + 1 + 16]
    header = bytes([len(body)]) + bytes(body)
    return (
        data[:start]
        + struct.pack("<I", zlib.crc32(header))
        + header
        + data[body_at + size :]
    )


@requires("cryptography")
def test_rar5_header_encrypted_damaged_endarc_after_a_verified_key_keeps_the_listing() -> (
    None
):
    data = _rar5_hp_reencrypt_endarc(_hp_fixture("encrypted_header__.rar"))
    with open_archive(io.BytesIO(data), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert report.error is None
        assert len(report.members) == 6
        assert len(_eof_marker_diagnostics(reader.diagnostics)) == 1


@requires("cryptography")
def test_rar5_header_encrypted_damaged_endarc_with_no_check_value_is_a_wrong_password() -> (
    None
):
    """Without a check value nothing proves the password, so a header CRC mismatch
    there, the end block's included, stays the wrong-password error."""
    data = _rar5_hp_without_check_value(_hp_fixture("encrypted_header__.rar"))
    # Control: intact, the archive without a check value lists in full.
    with open_archive(io.BytesIO(data), password=_HP_PASSWORD) as reader:
        assert len(reader.members()) == 6
        assert not _eof_marker_diagnostics(reader.diagnostics)
    damaged = _rar5_hp_reencrypt_endarc(data)
    with pytest.raises(EncryptionError, match=r"wrong password\?"):
        with open_archive(io.BytesIO(damaged), password=_HP_PASSWORD) as reader:
            reader.members()


def _flip_block_type_to_endarc(data: bytes, version: int, which: str) -> bytes:
    """Overwrite one block's type byte with the end block's type, leaving the rest
    of the header (and so its CRC) as it was. ``which`` is ``"main"`` or
    ``"second_file"``."""
    if version == 4:
        blocks, file_type, type_at, end_type = _rar4_blocks(data), 0x74, 2, 0x7B
        main_type = 0x73
    else:
        blocks, file_type, end_type, main_type = _rar5_blocks(data), 2, 5, 1
        type_at = 4 + 1  # CRC32, then a one-byte size vint in the fixtures
    if which == "main":
        block_pos = next(b[0] for b in blocks if b[1] == main_type)
    else:
        block_pos = [b[0] for b in blocks if b[1] == file_type][1]
    out = bytearray(data)
    assert out[block_pos + type_at] in (file_type, main_type)  # precondition
    out[block_pos + type_at] = end_type
    return bytes(out)


@pytest.mark.parametrize("which", ["main", "second_file"])
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_header_whose_type_byte_reads_as_endarc_stays_corruption(
    fixture: str, version: int, streaming: bool, which: str
) -> None:
    """The type of a header whose CRC failed is not data either: one flipped byte
    turns a MAIN or FILE header's type into the end block's. Such a header has
    members after it, so it is not taken for the end of the archive, and the
    walk raises CorruptionError as for any other damaged header. unrar 7.00 on
    the second_file cases lists only file1.txt and `unrar t` exits 3."""
    data = _flip_block_type_to_endarc(
        (_RAR_FIXTURES / fixture).read_bytes(), version, which
    )
    with pytest.raises(CorruptionError):
        with open_archive(io.BytesIO(data), streaming=streaming) as reader:
            _members_and_bytes(reader)


@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_endarc_followed_by_bytes_stays_corruption(
    fixture: str, version: int
) -> None:
    """A damaged end block is taken as one only where the file ends right after
    it, which a header with members after it cannot fake."""
    data = _edit_endarc((_RAR_FIXTURES / fixture).read_bytes(), version) + b"\0"
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(data), password=None)


@pytest.mark.parametrize("shape", ["data_area", "oversized"])
@pytest.mark.parametrize(("fixture", "version"), _ENDARC_FIXTURES)
def test_rar_damaged_last_block_not_shaped_as_an_end_block_stays_corruption(
    fixture: str, version: int, shape: str
) -> None:
    """At the end of the file, a damaged header typed as the end block is still
    refused when its shape is not an end block's: it declares a data area (RAR5
    data-area flag, RAR3 long-block flag, as a FILE header does), or its header
    is longer than an end block's."""
    out = bytearray(_edit_endarc((_RAR_FIXTURES / fixture).read_bytes(), version))
    block_pos, header_end = _endarc_block(bytes(out), version)
    assert header_end == len(out)  # precondition: the end block is last
    if version == 4:
        # long block: a 4-byte data size of 0 follows the 7-byte header
        size, extra = (11, 4) if shape == "data_area" else (40, 33)
        if shape == "data_area":
            out[block_pos + 4] |= 0x80
        struct.pack_into("<H", out, block_pos + 5, size)
        out += bytes(extra)
    elif shape == "data_area":
        out[block_pos + 6] |= 0x02  # block flags: data area
    else:
        out[block_pos + 4] += 1  # one-byte size vint: one byte past the end flags
        out += b"\0"
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(bytes(out)), password=None)


# The TAR twin of the rule above (maintainer ruling 2026-10-06): a zero block ends
# the members and the second end-of-archive block is damaged. GNU tar ("A lone zero
# block") and 7-Zip list every member with a warning and exit 0. archivey reports
# ARCHIVE_EOF_MARKER_MISSING with expected_marker="second_zero_block", so the context
# tells it from a rejected header, and a strict policy refuses it. tests/test_tar.py
# carries the detailed cases.


def _tar_with_damaged_second_eof_block() -> tuple[bytes, dict[str, bytes]]:
    members = {"a.txt": b"aaa", "b.txt": b"b" * 4000}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as t:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
    data = bytearray(buf.getvalue())
    end = len(data)
    while data[end - 512 : end] == bytes(512):
        end -= 512
    assert data[end : end + 1024] == bytes(1024)  # precondition: a two-block trailer
    data[end + 512 + 100] = 1  # one stray byte in the second trailer block
    return bytes(data), members


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_tar_damaged_second_eof_block_keeps_the_listing(streaming: bool) -> None:
    data, members = _tar_with_damaged_second_eof_block()
    with open_archive(io.BytesIO(data), streaming=streaming) as reader:
        assert dict(_members_and_bytes(reader)) == members
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 1
    context = diagnostics[0].context
    assert isinstance(context, ArchiveEofContext)
    assert context.format == "tar"
    assert context.expected_marker == "second_zero_block"
    assert context.observed_kind == "nonzero"


def test_tar_damaged_second_eof_block_refused_under_strict() -> None:
    data, _members = _tar_with_damaged_second_eof_block()
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(io.BytesIO(data), config=config) as reader:
        with pytest.raises(DiagnosticRaisedError):
            reader.members()


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
    # Linear work over 10 KiB of trailers is milliseconds (it was ~1.8 s when
    # crc32_combine rebuilt its GF(2) matrices per call).
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
# C4: a seek back to the start re-arms the CRC
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


def _bad_crc_deflated_zip(path: Path, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("m.txt", payload)
    data = bytearray(path.read_bytes())
    bad = struct.pack("<I", zlib.crc32(payload) ^ 1)
    data[14:18] = bad  # local header CRC
    central = data.find(b"PK\x01\x02")
    data[central + 16 : central + 20] = bad
    path.write_bytes(bytes(data))


def _damaged_copy_7z(path: Path, payload: bytes) -> None:
    import py7zr

    with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_COPY}]) as zf:
        zf.writestr(payload, "m.txt")
    data = bytearray(path.read_bytes())
    data[data.find(payload[:200]) + 5000] ^= 0x20
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    ("build", "name"),
    [
        pytest.param(_bad_crc_deflated_zip, "damaged.zip", id="zip-deflated"),
        pytest.param(
            _damaged_copy_7z, "damaged.7z", id="7z-copy", marks=requires("py7zr")
        ),
    ],
)
def test_rewind_after_a_mid_member_seek_rearms_the_check(
    build: Callable[[Path, bytes], None], name: str, tmp_path: Path
) -> None:
    """Sibling of C4: a seek into the member, then back to 0, then a chunked read."""
    payload = b"LINE-abcde\n" * 3000
    path = tmp_path / name
    build(path, payload)
    with open_archive(path, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            stream.seek(5000)
            stream.read(10)
            stream.seek(0)
            with pytest.raises(CorruptionError):
                while stream.read(4096):
                    pass


# ---------------------------------------------------------------------------
# X4: `archivey test` re-opens every 7z / RAR4 symlink and follows it
# ---------------------------------------------------------------------------


def _corpus_symlinks_7z(tmp_path: Path) -> Path:
    entry = next(e for e in CORPUS if e.id == "symlinks")
    return corpus_archive_path(entry, "7z", tmp_path)


def _fixture_rar4_symlinks(tmp_path: Path) -> Path:
    del tmp_path
    return _RAR_FIXTURES / "symlinks_solid__rar4.rar"


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


@requires_binary("7z")
@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_cli_test_7z_symlink_cycle_is_not_a_failure(tmp_path: Path) -> None:
    names = _symlink_cycle_tree(tmp_path / "src")
    archive = tmp_path / "loop.7z"
    made = subprocess.run(
        ["7z", "a", "-snl", "-bd", "-bso0", str(archive), *names],
        cwd=tmp_path / "src",
        check=False,
    )
    if made.returncode != 0:
        pytest.skip("this 7z cannot store a symlink cycle (macOS builds refuse it)")
    err = io.StringIO()
    assert main(["test", str(archive)], out=io.StringIO(), err=err) == EXIT_OK, (
        err.getvalue()
    )


class _LinkOpenStub:
    """A reader whose ``open()`` reads the link's target, then raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def open(self, member: ArchiveMember) -> None:
        member.link_target = "target.txt"
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(LinkTargetNotFoundError("Link target not found"), id="absent"),
        pytest.param(ReadError("Link cycle detected", member_name="a"), id="cycle"),
        pytest.param(
            ArchiveyUsageError(
                "Cannot open member 'd': type is 'directory' (not a file)"
            ),
            id="directory",
        ),
    ],
)
def test_cli_test_ignores_where_a_link_points(exc: Exception) -> None:
    from archivey.cli.test_cmd import _verify_link

    link = ArchiveMember(type=MemberType.SYMLINK, name="l")
    _verify_link(_LinkOpenStub(exc), link)  # type: ignore[arg-type]


def test_cli_test_ignores_a_real_link_cycle() -> None:
    """The CLI tells a cycle apart by the message the reader raises; pin it against
    the real reader so a change to that message cannot slip past the stub above."""
    from archivey.cli.test_cmd import _verify_link

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, target in (("a", "b"), ("b", "a")):
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    buf.seek(0)
    with open_archive(buf) as reader:
        link = reader.get("a")
        assert link is not None
        _verify_link(reader, link)


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(PackageNotInstalledError("pyppmd is not installed"), id="pkg"),
        pytest.param(CorruptionError("CRC mismatch"), id="corrupt"),
        pytest.param(ReadError("some other read error"), id="read"),
        pytest.param(ArchiveyUsageError("The reader is closed"), id="usage"),
    ],
)
def test_cli_test_raises_other_errors_from_a_link_open(exc: Exception) -> None:
    """Only the three errors about where a link points are ignored once its target
    is read; a target that cannot be opened, or a usage error, is not a clean link."""
    from archivey.cli.test_cmd import _verify_link

    link = ArchiveMember(type=MemberType.SYMLINK, name="l")
    with pytest.raises(type(exc)):
        _verify_link(_LinkOpenStub(exc), link)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# X5: an archive-chosen name crashes `list` / `info` on a non-UTF-8 stdout
# ---------------------------------------------------------------------------


def _zip_with_cjk_name_and_comment(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("日本語.txt", b"hi")
        zf.writestr("b.txt", b"x")
        zf.comment = "日本語".encode()


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
    # The summary says why the run failed: nothing failed, one digest went unchecked.
    assert "1 OK, 0 failed, 1 not verified" in err.getvalue()


# ---------------------------------------------------------------------------
# C5: three TAR combinations archivey reads have no ArchiveFormat name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", ["LZIP", "ZLIB", "BROTLI"])
def test_every_readable_tar_combination_has_a_display_name(stream: str) -> None:
    from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat

    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat[stream])
    assert fmt.display_name == f"TAR_{stream}"

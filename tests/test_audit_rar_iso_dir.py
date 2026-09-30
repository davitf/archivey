"""Audit reproducers for the RAR, ISO and directory backends.

Each test states the promised behaviour. A test marked ``xfail(strict=True)`` fails
today for the reason in its marker; it turns green (and so strict-fails) when the bug
is fixed, and the marker must then be removed.

Most archives here are derived from committed fixtures by rewriting headers, so only
``unrar`` is needed at runtime. The helpers rebuild each header with a fresh CRC.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import struct
import subprocess
import sys
import textwrap
import threading
import zlib
from pathlib import Path
from typing import Any

import pytest

from archivey import ArchiveyConfig, open_archive
from archivey.config import DecoderLimits
from archivey.exceptions import (
    ArchiveyError,
    PackageNotInstalledError,
    ResourceLimitError,
)
from archivey.internal.backends import rar_reader, rar_unrar
from archivey.internal.external import unar as unar_cli
from tests.conftest import requires_binary

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"
_UNRAR_ONLY = ArchiveyConfig(rar_decompressor="unrar")


def _fixture(name: str) -> Path:
    path = _FIXTURES / name
    if not path.is_file():
        pytest.skip(f"missing vendored fixture {name}")
    return path


# --- RAR5 header rewriting ----------------------------------------------------

_RAR5_SIG = b"Rar!\x1a\x07\x01\x00"


def _vint(value: int) -> bytes:
    out = bytearray()
    while True:
        low = value & 0x7F
        value >>= 7
        if value:
            out.append(low | 0x80)
        else:
            out.append(low)
            return bytes(out)


def _read_vint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, pos


def _rar5_parse(data: bytes) -> list[dict[str, Any]]:
    """Split a RAR5 archive into blocks; FILE/SERVICE bodies are decoded field by field."""
    assert data.startswith(_RAR5_SIG)
    pos = len(_RAR5_SIG)
    blocks: list[dict[str, Any]] = []
    while pos < len(data):
        size, body_start = _read_vint(data, pos + 4)
        header = data[body_start : body_start + size]
        q = 0
        block_type, q = _read_vint(header, q)
        flags, q = _read_vint(header, q)
        extra_size = data_size = 0
        if flags & 1:
            extra_size, q = _read_vint(header, q)
        if flags & 2:
            data_size, q = _read_vint(header, q)
        body = header[q : len(header) - extra_size]
        block: dict[str, Any] = {
            "type": block_type,
            "flags": flags,
            "body": body,
            "extra": header[len(header) - extra_size :] if extra_size else b"",
            "data": data[body_start + size : body_start + size + data_size],
        }
        if block_type in (2, 3):
            b = 0
            file_flags, b = _read_vint(body, b)
            unpacked, b = _read_vint(body, b)
            attr, b = _read_vint(body, b)
            mtime = crc = None
            if file_flags & 2:
                mtime = body[b : b + 4]
                b += 4
            if file_flags & 4:
                crc = body[b : b + 4]
                b += 4
            cinfo, b = _read_vint(body, b)
            host_os, b = _read_vint(body, b)
            name_len, b = _read_vint(body, b)
            block.update(
                file_flags=file_flags,
                unpacked=unpacked,
                attr=attr,
                mtime=mtime,
                crc=crc,
                cinfo=cinfo,
                host_os=host_os,
                name=body[b : b + name_len],
            )
        blocks.append(block)
        pos = body_start + size + data_size
    return blocks


def _rar5_build(blocks: list[dict[str, Any]]) -> bytes:
    out = bytearray(_RAR5_SIG)
    for block in blocks:
        if "file_flags" in block:
            file_flags = block["file_flags"] & ~6
            if block["mtime"] is not None:
                file_flags |= 2
            if block["crc"] is not None:
                file_flags |= 4
            body = _vint(file_flags) + _vint(block["unpacked"]) + _vint(block["attr"])
            if block["mtime"] is not None:
                body += block["mtime"]
            if block["crc"] is not None:
                body += block["crc"]
            body += (
                _vint(block["cinfo"])
                + _vint(block["host_os"])
                + _vint(len(block["name"]))
                + block["name"]
            )
        else:
            body = block["body"]
        flags = block["flags"] & ~3
        if block["extra"]:
            flags |= 1
        if block["data"] or block["flags"] & 2:
            flags |= 2
        header = _vint(block["type"]) + _vint(flags)
        if flags & 1:
            header += _vint(len(block["extra"]))
        if flags & 2:
            header += _vint(len(block["data"]))
        header += body + block["extra"]
        sized = _vint(len(header)) + header
        out += struct.pack("<I", zlib.crc32(sized)) + sized + block["data"]
    return bytes(out)


def _rar5_file_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [block for block in blocks if block["type"] == 2]


def _hostile_argv_payloads() -> dict[str, bytes]:
    """Member data of ``hostile_argv__.rar``, read through the untouched fixture."""
    with open_archive(_fixture("hostile_argv__.rar"), config=_UNRAR_ONLY) as archive:
        return {member.name: archive.read(member) for member in archive.members()}


# --- RAR3 header rewriting ----------------------------------------------------

_RAR3_SIG = b"Rar!\x1a\x07\x00"


def _rar3_parse(data: bytes) -> list[dict[str, Any]]:
    assert data.startswith(_RAR3_SIG)
    pos = len(_RAR3_SIG)
    blocks: list[dict[str, Any]] = []
    while pos + 7 <= len(data):
        _crc, block_type, flags, size = struct.unpack_from("<HBHH", data, pos)
        add = 0
        if flags & 0x8000 or block_type == 0x74:
            add = struct.unpack_from("<I", data, pos + 7)[0]
        blocks.append(
            {
                "type": block_type,
                "header": bytearray(data[pos : pos + size]),
                "data": data[pos + size : pos + size + add],
            }
        )
        pos += size + add
    return blocks


def _rar3_rename(block: dict[str, Any], name: bytes, *, extra_flags: int = 0) -> None:
    """Replace a FILE header's name; clear the Unicode flag and OR in ``extra_flags``."""
    header = block["header"]
    flags = struct.unpack_from("<H", header, 3)[0]
    old_len = struct.unpack_from("<H", header, 26)[0]
    offset = 32 + (8 if flags & 0x100 else 0)
    header[offset : offset + old_len] = name
    struct.pack_into("<H", header, 26, len(name))
    flags = (flags & ~0x0200) | extra_flags
    struct.pack_into("<H", header, 3, flags)
    struct.pack_into("<H", header, 5, len(header))


def _rar3_build(blocks: list[dict[str, Any]]) -> bytes:
    out = bytearray(_RAR3_SIG)
    for block in blocks:
        header = block["header"]
        struct.pack_into("<H", header, 0, zlib.crc32(bytes(header[2:])) & 0xFFFF)
        out += header + block["data"]
    return bytes(out)


def _renamed_rar4_hostile(tmp_path: Path, name: bytes, *, extra_flags: int = 0) -> Path:
    blocks = _rar3_parse(_fixture("hostile_argv__rar4.rar").read_bytes())
    first_file = next(block for block in blocks if block["type"] == 0x74)
    _rar3_rename(first_file, name, extra_flags=extra_flags)
    path = tmp_path / "renamed_rar4.rar"
    path.write_bytes(_rar3_build(blocks))
    return path


# --- multi-volume: an explicit path sequence ---------------------------------


def test_explicit_rar_volume_paths_in_separate_directories_open(
    tmp_path: Path,
) -> None:
    """docs/opening-and-listing.md: pass the volumes yourself when they are not siblings.

    "Do that and the order you give is the order used, with no discovery." The
    RAR branch of ``open_archive`` replaces the joined source with volume 1's path
    and rediscovers siblings beside it, so the second volume in another directory
    is never seen.
    """
    parts = []
    for index in (1, 2):
        folder = tmp_path / f"disk{index}"
        folder.mkdir()
        dest = folder / f"tinyvol.part{index}.rar"
        shutil.copyfile(_fixture(f"tinyvol.part{index}.rar"), dest)
        parts.append(dest)
    with open_archive(parts) as archive:
        names = [member.name for member in archive.members()]
    assert names == ["payload.bin"]


# --- the unrar -n include mask addresses by name ----------------------------


@requires_binary("unrar")
def test_duplicate_named_compressed_rar5_members_read_their_own_bytes(
    tmp_path: Path,
) -> None:
    """A valid archive whose later entry repeats a name (unrar t: All OK).

    ``unrar p -n./-inul`` emits both entries, in archive order; each read skips
    to its own.
    """
    payloads = _hostile_argv_payloads()
    blocks = _rar5_parse(_fixture("hostile_argv__.rar").read_bytes())
    files = _rar5_file_blocks(blocks)
    files[2]["name"] = files[1]["name"]  # "@atfile" -> "-inul"
    path = tmp_path / "dup.rar"
    path.write_bytes(_rar5_build(blocks))

    with open_archive(path, config=_UNRAR_ONLY) as archive:
        members = archive.members()
        assert [m.name for m in members] == ["canary.txt", "-inul", "-inul"]
        assert archive.read(members[2]) == payloads["@atfile"]
        assert archive.read(members[1]) == payloads["-inul"]


@requires_binary("unrar")
def test_invalid_utf8_name_never_reads_a_siblings_bytes(tmp_path: Path) -> None:
    """Bytes returned for a member must be that member's bytes.

    The target has no stored digest (RAR5 makes the CRC optional), so nothing
    downstream notices. ``unrar`` itself sees the invalid name as empty, and
    ``rar_decompressor='unar'`` (which addresses entries by index) reads it right.
    """
    payloads = _hostile_argv_payloads()
    assert len(payloads["-inul"]) == len(payloads["@atfile"])
    blocks = _rar5_parse(_fixture("hostile_argv__.rar").read_bytes())
    files = _rar5_file_blocks(blocks)
    files[1]["name"] = b"\xff"  # "-inul": not UTF-8
    files[1]["crc"] = None
    files[2]["name"] = "�".encode()  # "@atfile": literally U+FFFD
    path = tmp_path / "fffd.rar"
    path.write_bytes(_rar5_build(blocks))

    with open_archive(path, config=_UNRAR_ONLY) as archive:
        target = archive.members()[1]
        assert target.hashes == {}
        try:
            data = archive.read(target)
        except ArchiveyError:
            return  # refusing is acceptable; the sibling's bytes are not
    assert data != payloads["@atfile"]
    assert data == payloads["-inul"]


_C_LOCALE_READ = textwrap.dedent(
    """
    import sys
    from archivey import ArchiveyConfig, open_archive
    config = ArchiveyConfig(rar_decompressor="unrar")
    with open_archive(sys.argv[1], config=config) as archive:
        member = archive.members()[0]
        sys.stdout.buffer.write(archive.read(member))
    """
)


@requires_binary("unrar")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX locale behaviour")
def test_non_ascii_member_reads_under_the_c_locale(tmp_path: Path) -> None:
    """``LC_ALL=C`` is common in cron jobs, CI and containers; the archive is valid.

    Python coerces a bare C locale for its children, but an explicit ``LC_ALL``
    (or a ``LANG`` naming a missing locale) turns that off, and archivey passes the
    environment through unchanged.
    """
    payloads = _hostile_argv_payloads()
    blocks = _rar5_parse(_fixture("hostile_argv__.rar").read_bytes())
    _rar5_file_blocks(blocks)[0]["name"] = "cañary.txt".encode()
    path = tmp_path / "accent.rar"
    path.write_bytes(_rar5_build(blocks))

    env = {**os.environ, "LC_ALL": "C"}
    env.pop("PYTHONUTF8", None)
    completed = subprocess.run(
        [sys.executable, "-c", _C_LOCALE_READ, str(path)],
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-400:]
    assert completed.stdout == payloads["canary.txt"]


@requires_binary("unrar")
def test_rar3_8bit_name_member_is_readable(tmp_path: Path) -> None:
    """A RAR 2.x/3.x name without the Unicode flag, in a single-byte code page.

    ``unrar`` maps the stored bytes through the locale; archivey decodes them as
    windows-1252 and hands ``unrar`` the UTF-8 of that. Nine bytes, so the
    UTF-16 misdecode (next test) does not apply.
    """
    with open_archive(_fixture("hostile_argv__rar4.rar"), config=_UNRAR_ONLY) as ar:
        expected = ar.read("canary.txt")
    path = _renamed_rar4_hostile(tmp_path, b"caf\xe9s.txt")
    with open_archive(path, config=_UNRAR_ONLY) as archive:
        member = archive.members()[0]
        assert member.name == "cafés.txt"
        assert archive.read(member) == expected


def test_rar3_8bit_name_is_not_decoded_as_utf16(tmp_path: Path) -> None:
    """``caf\\xe9.txt`` is eight bytes; it must not list as ``'慣\\ue966琮瑸'``."""
    path = _renamed_rar4_hostile(tmp_path, b"caf\xe9.txt")
    with open_archive(path) as archive:
        member = archive.members()[0]
        # The fixture was written on Unix, so the fallback is windows-1252.
        assert member.name == "café.txt"
        assert member.raw_name == b"caf\xe9.txt"
    with open_archive(path, encoding="cp437") as archive:
        member = archive.members()[0]
        assert member.name == "cafΘ.txt"
        assert member.raw_name == b"caf\xe9.txt"


# --- non-ArchiveyError exceptions --------------------------------------------


@pytest.mark.parametrize(
    "suffix",
    ["²".encode(), b"1" * 5000],
    ids=["superscript-two", "5000-digits"],
)
def test_rar3_version_suffix_is_parsed_without_a_bare_value_error(
    tmp_path: Path, suffix: bytes
) -> None:
    """``open_archive`` on crafted input raises an ``ArchiveyError`` or succeeds."""
    path = _renamed_rar4_hostile(tmp_path, b"a;" + suffix, extra_flags=0x0800)
    try:
        with open_archive(path) as archive:
            archive.members()
    except ArchiveyError:
        pass


@requires_binary("unrar")
def test_nul_in_member_name_read_raises_an_archivey_error(tmp_path: Path) -> None:
    blocks = _rar5_parse(_fixture("hostile_argv__.rar").read_bytes())
    _rar5_file_blocks(blocks)[0]["name"] = b"nul\x00x.txt"
    path = tmp_path / "nul.rar"
    path.write_bytes(_rar5_build(blocks))
    with open_archive(path, config=_UNRAR_ONLY) as archive:
        member = archive.members()[0]
        with pytest.raises(ArchiveyError):
            archive.read(member)


# --- passwords on unrar's stdin ----------------------------------------------


@requires_binary("unrar")
def test_password_with_nul_is_not_silently_cut_by_unrar() -> None:
    """``_password_stdin_bytes`` refuses a line break for this reason; NUL is the same.

    RAR3/4 data has no password check value, so the member's CRC is the only
    judge, and it passes because ``unrar`` used ``password``.
    """
    with open_archive(
        _fixture("encryption__rar4.rar"),
        password="password\x00zz",
        config=_UNRAR_ONLY,
    ) as archive:
        member = archive.members()[0]
        with pytest.raises(ArchiveyError):
            archive.read(member)


@requires_binary("rar", "unrar")
def test_long_password_does_not_deadlock_the_unrar_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAR5 hashes only the first 127 characters, so this password is the right one.

    The PswCheck accepts it natively. Written whole to ``unrar``'s stdin before
    stdout is read, 200 KB fills the pipe while ``unrar`` fills stdout with the
    300 KB member, and both processes wait on each other. The read runs in a
    thread; after the deadline the test kills ``unrar`` to unblock it.
    """
    source = tmp_path / "rnd.bin"
    source.write_bytes(os.urandom(300_000))
    archive_path = tmp_path / "enc.rar"
    subprocess.run(
        [
            "rar",
            "a",
            "-idq",
            "-m1",
            "-ep",
            "-p" + "p" * 127,
            str(archive_path),
            str(source),
        ],
        check=True,
        timeout=30,
    )
    spawned: list[subprocess.Popen[bytes]] = []
    real_spawn = rar_unrar.spawn_for_stdout

    def recording_spawn(*args: Any, **kwargs: Any) -> Any:
        proc, stdout = real_spawn(*args, **kwargs)
        spawned.append(proc)
        return proc, stdout

    monkeypatch.setattr(rar_unrar, "spawn_for_stdout", recording_spawn)
    outcome: list[object] = []

    def read() -> None:
        try:
            with open_archive(
                archive_path, password="p" * 127 + "x" * 200_000, config=_UNRAR_ONLY
            ) as archive:
                outcome.append(archive.read(archive.members()[0]))
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome.append(exc)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(timeout=2.0)
    finished = not worker.is_alive()
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    worker.join(timeout=10)
    assert finished, "archive.read() was still blocked in open_unrar_p after 2.0 s"
    assert outcome and outcome[0] == source.read_bytes()


# --- the RAR dictionary counts against DecoderLimits.max_decoder_memory ------
#
# The rule differs by program (dev-docs/formats/rar.md section 7). unar writes to the
# whole declared dictionary, so the declared size counts. unrar fills pages only as it
# writes output, so it counts the declared size capped at the unpacked bytes it
# decodes: the member's own size, or in a solid archive everything up to the member.

_RAR5_DICT_4GIB = 15  # 128 KiB << 15


def _declaring_dictionary(
    tmp_path: Path, fixture: str, index: int, exponent: int
) -> Path:
    """``fixture`` with FILE header ``index`` declaring a ``128 KiB << exponent`` dictionary."""
    blocks = _rar5_parse(_fixture(fixture).read_bytes())
    target = _rar5_file_blocks(blocks)[index]
    assert (target["cinfo"] >> 7) & 7, "a stored member never reaches the check"
    target["cinfo"] = (target["cinfo"] & ~(0x3FF << 10)) | (exponent << 10)
    path = tmp_path / f"dict{exponent}_{fixture}"
    path.write_bytes(_rar5_build(blocks))
    return path


def _config(program: str, max_decoder_memory: int | None = None) -> ArchiveyConfig:
    if max_decoder_memory is None:
        return ArchiveyConfig(rar_decompressor=program)
    return ArchiveyConfig(
        rar_decompressor=program,
        decoder_limits=DecoderLimits(max_decoder_memory=max_decoder_memory),
    )


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a refused read starts unrar or unar anyway."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a decompressor was spawned for a refused read")

    monkeypatch.setattr(rar_unrar, "spawn_for_stdout", refuse)
    monkeypatch.setattr(unar_cli, "spawn_for_stdout", refuse)


def _stream_all(path: Path, config: ArchiveyConfig) -> dict[str, bytes]:
    with open_archive(path, config=config) as archive:
        # Closed before the archive is, also when a read raises.
        with contextlib.closing(archive.stream_members()) as members:
            return {
                member.name: stream.read()
                for member, stream in members
                if stream is not None
            }


@requires_binary("unrar")
def test_unrar_reads_a_small_nonsolid_member_declaring_4_gib(tmp_path: Path) -> None:
    """unrar sizes the window to the 1408-byte member, so 4 GiB declared is allowed."""
    expected = _hostile_argv_payloads()["canary.txt"]
    path = _declaring_dictionary(tmp_path, "hostile_argv__.rar", 0, _RAR5_DICT_4GIB)
    with open_archive(path, config=_UNRAR_ONLY) as archive:
        assert archive.read("canary.txt") == expected


@requires_binary("unar")
def test_unar_refuses_a_small_nonsolid_member_declaring_4_gib(
    tmp_path: Path, no_spawn: None
) -> None:
    """unar writes to the whole declared dictionary, whatever the member's size."""
    path = _declaring_dictionary(tmp_path, "hostile_argv__.rar", 0, _RAR5_DICT_4GIB)
    with open_archive(path, config=_config("unar")) as archive:
        with pytest.raises(ResourceLimitError, match="max_decoder_memory"):
            archive.read("canary.txt")


@requires_binary("unrar")
def test_unrar_refuses_a_nonsolid_member_over_a_lowered_limit(
    tmp_path: Path, no_spawn: None
) -> None:
    """The cap is the unpacked size: 1408 bytes is over a 1 KiB limit."""
    path = _declaring_dictionary(tmp_path, "hostile_argv__.rar", 0, _RAR5_DICT_4GIB)
    with open_archive(path, config=_config("unrar", 1024)) as archive:
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=1024"):
            archive.read("canary.txt")


@requires_binary("unrar")
def test_unrar_reads_a_small_solid_archive_whose_first_member_declares_4_gib(
    tmp_path: Path,
) -> None:
    """unrar keeps the 4 GiB window for the stream, but writes only 1 MiB into it."""
    fixture = "seek_respawn_solid__.rar"
    expected = _stream_all(_fixture(fixture), _UNRAR_ONLY)
    path = _declaring_dictionary(tmp_path, fixture, 0, _RAR5_DICT_4GIB)
    with open_archive(path, config=_UNRAR_ONLY) as archive:
        assert archive.read("tail.txt") == expected["tail.txt"]
    assert _stream_all(path, _UNRAR_ONLY) == expected


@requires_binary("unar")
def test_unar_refuses_every_member_of_a_solid_stream_declaring_4_gib(
    tmp_path: Path, no_spawn: None
) -> None:
    """Only the first header declares 4 GiB; unar allocates it for the whole stream."""
    path = _declaring_dictionary(
        tmp_path, "seek_respawn_solid__.rar", 0, _RAR5_DICT_4GIB
    )
    config = _config("unar")
    with open_archive(path, config=config) as archive:
        for name in ("prefix.bin", "tail.txt"):
            with pytest.raises(ResourceLimitError, match="max_decoder_memory"):
                archive.read(name)
    with pytest.raises(ResourceLimitError, match="max_decoder_memory"):
        _stream_all(path, config)


@requires_binary("unrar")
def test_unrar_refuses_a_solid_member_whose_decoded_prefix_is_over_the_limit(
    tmp_path: Path, no_spawn: None
) -> None:
    """``tail.txt`` is 12 bytes, but reading it decodes the 1 MiB ``prefix.bin`` first."""
    path = _declaring_dictionary(
        tmp_path, "seek_respawn_solid__.rar", 0, _RAR5_DICT_4GIB
    )
    config = _config("unrar", 512 * 1024)
    with open_archive(path, config=config) as archive:
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=524288"):
            archive.read("tail.txt")
    with pytest.raises(ResourceLimitError, match="max_decoder_memory=524288"):
        _stream_all(path, config)


@requires_binary("unrar")
def test_unrar_solid_count_is_capped_at_the_decoded_bytes(tmp_path: Path) -> None:
    """4 GiB declared, about 1 MiB decoded: a 2 MiB limit is enough."""
    fixture = "seek_respawn_solid__.rar"
    expected = _stream_all(_fixture(fixture), _UNRAR_ONLY)
    path = _declaring_dictionary(tmp_path, fixture, 0, _RAR5_DICT_4GIB)
    assert _stream_all(path, _config("unrar", 2 * 2**20)) == expected


@requires_binary("unrar", "unar")
@pytest.mark.parametrize("unrar_found", [True, False], ids=["unrar", "unar"])
def test_auto_counts_for_the_program_it_picks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unrar_found: bool
) -> None:
    expected = _hostile_argv_payloads()["canary.txt"]
    path = _declaring_dictionary(tmp_path, "hostile_argv__.rar", 0, _RAR5_DICT_4GIB)
    if not unrar_found:

        def missing() -> str:
            raise PackageNotInstalledError("no RARLAB unrar in this test")

        monkeypatch.setattr(rar_reader, "find_rarlab_unrar", missing)
    with open_archive(path, config=_config("auto")) as archive:
        if unrar_found:
            assert archive.read("canary.txt") == expected
        else:
            with pytest.raises(ResourceLimitError, match="unar needs"):
                archive.read("canary.txt")


@requires_binary("unrar")
def test_rar3_dictionary_counts_the_same_way(no_spawn: None) -> None:
    """RAR3's flag-byte dictionary (1 MiB here) is capped at the bytes decoded.

    ``file1.txt`` is 13 bytes into the solid stream, ``subdir/file2.txt`` ends at 29.
    """
    path = _fixture("basic_solid__rar4.rar")
    with open_archive(path, config=_config("unrar", 16)) as archive:
        with pytest.raises(ResourceLimitError, match="max_decoder_memory=16"):
            archive.read("subdir/file2.txt")


@pytest.mark.parametrize(
    "program",
    [
        pytest.param("unrar", marks=requires_binary("unrar")),
        pytest.param("unar", marks=requires_binary("unar")),
    ],
)
@pytest.mark.parametrize("fixture", ["hostile_argv__.rar", "seek_respawn_solid__.rar"])
def test_honest_dictionaries_read_under_the_default_limit(
    program: str, fixture: str
) -> None:
    config = _config(program)
    with open_archive(_fixture(fixture), config=config) as archive:
        for member in archive.members():
            if member.is_file:
                archive.read(member)
    assert _stream_all(_fixture(fixture), config)

"""Audit reproducers for the RAR, ISO and directory backends.

Each test states the promised behaviour. A test marked ``xfail(strict=True)`` fails
today for the reason in its marker; it turns green (and so strict-fails) when the bug
is fixed, and the marker must then be removed.

Most archives here are derived from committed fixtures by rewriting headers, so only
``unrar`` is needed at runtime. The helpers rebuild each header with a fresh CRC.
"""

from __future__ import annotations

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
from archivey.exceptions import ArchiveyError
from archivey.internal.backends import rar_unrar
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: an explicit RAR volume path sequence is dropped for volume 1's "
        "on-disk siblings (core.py reopens volume_paths[0] only)"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: -n./<name> matches every member with that name, so each of two "
        "same-named compressed members reads as over-long CorruptionError"
    ),
)
def test_duplicate_named_compressed_rar5_members_read_their_own_bytes(
    tmp_path: Path,
) -> None:
    """A valid archive whose later entry repeats a name (unrar t: All OK).

    ``unrar x`` extracts it (the last entry wins); archivey refuses both reads and
    the extraction of the current entry, because ``unrar p -n./-inul`` emits both.
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: an invalid-UTF-8 RAR5 name decodes to U+FFFD and the -n mask then "
        "selects a sibling literally named U+FFFD; its bytes are returned silently"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: under LC_ALL=C unrar cannot match a non-ASCII -n mask, so every "
        "compressed member with a non-ASCII name reads as TruncatedError"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: an 8-bit (non-Unicode) RAR3 name is sent to unrar re-encoded as "
        "UTF-8, which is not the name unrar sees, so the member reads truncated"
    ),
)
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: rar_parser._TRY_ENCODINGS tries utf-16le before windows-1252, so an "
        "even-length 8-bit RAR3 name lists as CJK/private-use garbage"
    ),
)
def test_rar3_8bit_name_is_not_decoded_as_utf16(tmp_path: Path) -> None:
    """``caf\\xe9.txt`` is eight bytes; it must not list as ``'慣\\ue966琮瑸'``.

    ``encoding=`` is dropped for RAR, so the caller has no way to correct it.
    """
    path = _renamed_rar4_hostile(tmp_path, b"caf\xe9.txt")
    with open_archive(path) as archive:
        name = archive.members()[0].name
    assert name.endswith(".txt"), repr(name)


# --- non-ArchiveyError exceptions --------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: _rar3_split_file_version uses str.isdigit() then int(), so a "
        "Unicode digit or a >4300-digit ;n suffix raises bare ValueError at open"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a NUL in a RAR member name reaches the unrar argv (-n mask) and "
        "subprocess raises bare ValueError('embedded null byte') from read()"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: unrar stops reading a stdin password at NUL, so 'password\\x00zz' "
        "decrypts a RAR4 member whose password is 'password'"
    ),
)
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: open_unrar_p writes the password to unrar's stdin before reading "
        "stdout; a password larger than the pipe buffer deadlocks with unrar "
        "blocked on a full stdout"
    ),
)
def test_long_password_does_not_deadlock_the_unrar_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAR5 hashes only the first 127 characters, so this password is the right one.

    The PswCheck accepts it natively; the whole 200 KB then goes to ``unrar``'s
    stdin. ``unrar`` reads one line's worth and starts writing a 300 KB member, and
    both processes wait on each other. The read runs in a thread; after the
    deadline the test kills ``unrar`` to unblock it.
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

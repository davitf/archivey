"""CLI wrapper around RARLAB ``unrar`` for member **payload** bytes.

No RAR structure knowledge beyond argv safety — metadata/listing is
:mod:`.rar_parser`. Locates RARLAB ``unrar`` on ``PATH`` (not ``unrar-free`` /
``unar`` / ``7z``) and spawns ``unrar p`` with the password on stdin (bare ``-p``,
secret not in argv) and optional ``-n./member`` include masks.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import BinaryIO, cast

from archivey.exceptions import (
    PackageNotInstalledError,
    ReadError,
)

# Successful probes only. A rejected lookalike is not cached, so it is
# re-probed on every attempted read rather than once per process.
_cached_unrar: str | None = None

_NOT_INSTALLED_MSG = (
    "RARLAB unrar is required to read RAR member data, but it was not found on PATH "
    "(or the unrar on PATH is not RARLAB unrar). Install RARLAB unrar — "
    "unrar-free / unar / 7z / 7zz are not supported as substitutes."
)

_RAR3_ID = b"Rar!\x1a\x07\x00"
_RAR3_MAIN = 0x73
_RAR3_FILE = 0x74
_RAR3_FILE_PASSWORD = 0x0004
_RAR3_FILE_SALT = 0x0400
_RAR3_FILE_DICTMASK = 0x00E0
_RAR3_LONG_BLOCK = 0x8000
_RAR3_M0 = 0x30
_RAR3_BLOCK_HEADER = struct.Struct("<HBHH")
_RAR3_FILE_HEADER = struct.Struct("<LLBLLBBHL")


def _is_rarlab_unrar(path: str) -> bool:
    """Return True when ``path`` prints a RARLAB unrar banner."""
    try:
        completed = subprocess.run(
            [path],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    banner = (completed.stdout or b"") + (completed.stderr or b"")
    text = banner.decode("utf-8", errors="replace")
    if "UNRAR" not in text:
        return False
    return "Alexander Roshal" in text or "RARLAB" in text


def find_rarlab_unrar() -> str:
    """Return path to RARLAB unrar, or raise PackageNotInstalledError naming RARLAB unrar."""
    global _cached_unrar
    if _cached_unrar is not None:
        return _cached_unrar

    candidate = shutil.which("unrar")
    if candidate is None or not _is_rarlab_unrar(candidate):
        raise PackageNotInstalledError(_NOT_INSTALLED_MSG)

    _cached_unrar = candidate
    return candidate


def _password_arg(password: str | bytes | None) -> str:
    """Return the ``unrar`` password switch.

    Empty/absent → ``-p-`` (no password). Otherwise bare ``-p``; the password itself
    is written to the child's stdin (see :func:`open_unrar_p`) so it never appears in
    ``argv`` / ``/proc/<pid>/cmdline``.
    """
    if password is None or password == b"" or password == "":
        return "-p-"
    return "-p"


def _password_stdin_bytes(password: str | bytes) -> bytes:
    if isinstance(password, bytes):
        return password
    return password.encode("utf-8", errors="surrogateescape")


def _member_include_switch(member: str) -> str:
    """Build a safe ``unrar`` include-mask switch for one member name.

    A hostile archive can name a member like a switch (``-inul``) or an ``@listfile``
    argument; passed positionally those are mis-parsed by ``unrar`` (a switch, or a
    read of an attacker-chosen local file). Passing the name as the value of the ``-n``
    include-mask switch, prefixed with ``./``, neutralizes both: the leading ``-`` is
    not a switch (it is inside ``-n``) and the leading ``@`` is not a listfile (the
    value starts with ``.``). For a name **without** wildcards, ``./`` also anchors
    the mask to the exact archive path rather than matching the basename at any
    depth.

    ``unrar`` masks treat ``*`` and ``?`` as wildcards with no escape (``[]`` are
    literal, ``\\`` does not escape). A name whose globs are confined to the
    basename and that contains no backslash is still passed as the mask;
    ``RarReader._open_member`` skips other matching members using the parsed
    member list and :func:`_unrar_mask_match`. A glob in a directory component,
    or a backslash in the presented name, raises ``UnsupportedFeatureError``
    instead — :func:`_unrar_mask_match` is not faithful there, and Windows
    ``unrar`` treats ``\\`` as a separator (see :func:`_unrar_glob_demux_ok`).
    """
    return "-n./" + member


def _unrar_glob_demux_ok(presented: str) -> bool:
    """True when archivey will demux this glob name from an ``unrar -n`` pipe.

    Only a glob confined to the basename, with no backslash. A glob in a
    directory component, or a ``\\`` anywhere, makes :func:`_unrar_mask_match`
    over-match unrar 7.00, so the skip would land inside the target and a valid
    archive would be reported truncated. Those names stay
    ``UnsupportedFeatureError`` until the matcher is a source-faithful port.
    """
    if "\\" in presented:
        return False
    parent, sep, _base = presented.rpartition("/")
    if not sep:
        return True
    return "*" not in parent and "?" not in parent


def _unrar_component_match(name: str, mask: str) -> bool:
    """Glob-match one path component: ``*``/``?`` wildcards, ``[]`` literal."""
    parts: list[str] = []
    for ch in mask:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.fullmatch("".join(parts), name, flags=re.DOTALL) is not None


def _unrar_mask_match(name: str, mask: str) -> bool:
    """Match ``name`` the way ``unrar -n`` does.

    No wildcards: exact path (``./`` already stripped by the caller of ``-n./``).
    With ``*``/``?``: ``MATCH_WILDSUBPATH`` — the last mask component matches the
    basename at any depth, and a non-wildcard directory prefix constrains which
    subtrees. ``[]`` are literal (unlike Python ``fnmatch``). On Windows, ``unrar``
    folds case; we do too so the skip stays aligned with the pipe.

    Not a source-faithful port: a glob in a directory component over-matches
    (``d*/x.txt`` vs ``aaa/x.txt``), and folding ``\\`` to ``/`` collides a
    Linux literal backslash with a separator. Callers must refuse those names
    via :func:`_unrar_glob_demux_ok` before using this to size a skip.
    """
    if mask.startswith("./"):
        mask = mask[2:]
    name = name.replace("\\", "/")
    mask = mask.replace("\\", "/")
    if sys.platform == "win32":
        name = name.casefold()
        mask = mask.casefold()
    if "*" not in mask and "?" not in mask:
        return name == mask
    mask_dir, mask_base = mask.rsplit("/", 1) if "/" in mask else ("", mask)
    name_base = name.rsplit("/", 1)[-1]
    if not _unrar_component_match(name_base, mask_base):
        return False
    if not mask_dir:
        return True
    if "*" not in mask_dir and "?" not in mask_dir:
        name_dir = name.rsplit("/", 1)[0] if "/" in name else ""
        return name_dir == mask_dir or name_dir.startswith(mask_dir + "/")
    return True


def decompress_rar3_blob(
    *,
    extract_version: int,
    compress_type: int,
    packed: bytes,
    unpacked_size: int,
    flags: int,
    crc16: int,
    password: str | bytes | None = None,
) -> bytes | None:
    """Decode a non-file RAR3 payload by wrapping it in a temporary RAR.

    RAR3 old-style comments hold compressed bytes inside a header, without a
    FILE block that ``unrar`` can address. A minimal one-file archive lets the
    existing RARLAB process decode that one blob. This is deliberately limited
    to metadata blobs; it does not change stream-source member reads.

    ``unrar`` can report a CRC error for the synthetic FILE because old comment
    blocks retain only a CRC16. The caller validates that CRC16 against the
    returned bytes, which is the integrity check the on-disk comment provides.
    """
    if unpacked_size < 0 or unpacked_size > 0xFFFF:
        return None
    if compress_type == _RAR3_M0 and not flags & _RAR3_FILE_PASSWORD:
        return packed if len(packed) == unpacked_size else None

    file_flags = flags & (_RAR3_FILE_PASSWORD | _RAR3_FILE_SALT | _RAR3_FILE_DICTMASK)
    file_flags |= _RAR3_LONG_BLOCK
    filename = b"data"
    file_body = (
        _RAR3_FILE_HEADER.pack(
            len(packed),
            unpacked_size,
            0,  # MS-DOS
            crc16,
            0,
            extract_version,
            compress_type,
            len(filename),
            0x20,  # DOS archive attribute
        )
        + filename
    )
    file_without_crc = (
        struct.pack(
            "<BHH", _RAR3_FILE, file_flags, _RAR3_BLOCK_HEADER.size + len(file_body)
        )
        + file_body
    )
    file_header = (
        struct.pack("<H", zlib.crc32(file_without_crc) & 0xFFFF) + file_without_crc
    )

    main_body = b"\0" * 6
    main_without_crc = (
        struct.pack("<BHH", _RAR3_MAIN, 0, _RAR3_BLOCK_HEADER.size + len(main_body))
        + main_body
    )
    main_header = (
        struct.pack("<H", zlib.crc32(main_without_crc) & 0xFFFF) + main_without_crc
    )

    fd, name = tempfile.mkstemp(suffix=".rar")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as archive:
            archive.write(_RAR3_ID + main_header + file_header + packed)
        proc, stdout = open_unrar_p(path, password=password)
        try:
            # A comment's declared unpacked length is a uint16. Bound the
            # process output so a malformed blob cannot turn archive listing
            # into an unbounded metadata read.
            data = stdout.read(unpacked_size + 1)
            if len(data) != unpacked_size:
                return None
            return data
        finally:
            try:
                stdout.close()
            finally:
                if proc.poll() is None:
                    terminate_unrar(proc)
    finally:
        path.unlink(missing_ok=True)


def open_unrar_p(
    archive_path: str | Path,
    *,
    password: str | bytes | None = None,
    member: str | None = None,
    version_control: bool = False,
) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    """Spawn ``unrar p -inul [-ver] [-p|-p-] [-n./member] archive``.

    ``version_control`` adds ``-ver`` so the pipe includes WinRAR file-version history
    payloads (needed for solid demux when versioned FILE rows are present, and for a
    named open of a ``path;n`` history member — the ``-n`` mask excludes history rows
    unless ``-ver`` is set).

    A named ``member`` is passed as a ``-n./`` include mask, never positionally, so a
    hostile member name cannot inject an ``unrar`` switch or ``@listfile`` argument
    (see :func:`_member_include_switch`).

    When a non-empty ``password`` is given, the switch is bare ``-p`` and the password
    (plus a trailing newline) is written to the child's stdin — ``unrar`` reads it from
    stdin when redirected, keeping the secret out of ``argv``.

    Returns ``(proc, stdout)``. Caller must terminate/wait/close.
    """
    unrar = find_rarlab_unrar()
    cmd = [unrar, "p", "-inul"]
    if version_control:
        cmd.append("-ver")
    pass_arg = _password_arg(password)
    cmd.append(pass_arg)
    if member is not None:
        cmd.append(_member_include_switch(member))
    cmd.append(str(archive_path))
    feed_password = pass_arg == "-p"
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if feed_password else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 1024,
        )
    except OSError as exc:
        raise PackageNotInstalledError(_NOT_INSTALLED_MSG) from exc
    if feed_password:
        assert password is not None and password != b"" and password != ""
        assert proc.stdin is not None
        try:
            proc.stdin.write(_password_stdin_bytes(password) + b"\n")
            proc.stdin.close()
        except BrokenPipeError:
            # unrar exited before consuming the password; surface via exit-code mapping.
            pass
    if proc.stdout is None:
        proc.kill()
        # Defensive: Popen was asked for stdout=PIPE, so this should be unreachable. Typed
        # anyway — every archive-read failure surfaces as an ArchiveyError, and a raw
        # RuntimeError here would cross open_archive untranslated.
        raise ReadError("unrar produced no stdout pipe")
    return proc, cast(BinaryIO, proc.stdout)


def terminate_unrar(proc: subprocess.Popen[bytes] | None) -> None:
    """Terminate an ``unrar`` process if it is still running."""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

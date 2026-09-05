"""Native RAR reader fixture coverage."""

from __future__ import annotations

import dataclasses
import errno
import io
import os
import shutil
import struct
import subprocess
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from archivey import ExtractionStatus, open_archive
from archivey.config import REWIND_REDECODE_WARN_BYTES
from archivey.cost import AccessCost
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    ArchiveyError,
    ConcurrentAccessError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends import rar_reader, rar_unrar
from archivey.internal.backends.rar_parser import (
    RAR5_ID,
    RAR_ID,
    RarMemberInfo,
    _decode_rar3_unicode_name,
    load_vint,
    parse_rar_archive,
)
from archivey.types import (
    EXTRA_RAR_CREATED_IS_CTIME,
    EXTRA_RAR_EXTRACT_VERSION,
    ArchiveMember,
    CompressionAlgorithm,
    HashAlgorithm,
    MemberType,
)
from tests.conftest import requires, requires_binary

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"

_BASIC_CONTENTS = {
    "file1.txt": b"Hello, world!",
    "empty_file.txt": b"",
    "subdir/file2.txt": b"Hello, universe!",
    "implicit_subdir/file3.txt": b"Hello there!",
}

# Compressed fixtures: stored members never reach unrar, so they would not pin this.
# ``subdir/aY.txt`` is an earlier basename match for ``a*.txt`` (prefix skip).
# Windows cannot create these names on disk — committed only.
_WILDCARD_PAD = b"0123456789abcdef" * 512
_WILDCARD_CONTENTS = {
    "subdir/aY.txt": b"nested-aY\n" + _WILDCARD_PAD,
    "a*.txt": b"target-star\n" + _WILDCARD_PAD,
    "aX.txt": b"other-aX\n" + _WILDCARD_PAD,
    "b?.txt": b"target-q\n" + _WILDCARD_PAD,
    "b1.txt": b"other-b1\n" + _WILDCARD_PAD,
    "only*.dat": b"unique\n" + _WILDCARD_PAD,
}
_WILDCARD_FIXTURES = (
    "wildcard_names__.rar",
    "wildcard_names_solid__.rar",
    "wildcard_names__rar4.rar",
)
_WILDCARD_DIRGLOB_CONTENTS = {
    "aaa/x.txt": b"aaa\n" + _WILDCARD_PAD,
    "dX/x.txt": b"dX\n" + _WILDCARD_PAD,
    "d*/x.txt": b"dstar\n" + _WILDCARD_PAD,
}
# ``member.name`` folds a stored backslash to ``/`` (RAR treats it as a separator).
_WILDCARD_BACKSLASH_SLASH = b"slashdir\n" + _WILDCARD_PAD
_WILDCARD_BACKSLASH_REFUSED = ("a/b_TGT.txt", "a/b*.txt")
_WILDCARD_VER_CONTENTS = {
    "data.bin;1": b"data-v1\n" + _WILDCARD_PAD,
    "data.bin": b"data-v2!!\n" + _WILDCARD_PAD,
    "data_TARGET": b"data-tgt\n" + _WILDCARD_PAD,
    "data*": b"data-star\n" + _WILDCARD_PAD,
}
# 1 MiB repeating pad so a solid later-member rewind crosses the diagnostic
# threshold without monkeypatching it, and so unrar is still writing when a
# test seeks mid-stream (the 64 KiB pipe buffer fills).
_SEEK_RESPAWN_PAD = b"0123456789abcdef" * (1024 * 1024 // 16)
_SEEK_RESPAWN_TAIL = b"tail-member\n"
_SEEK_RESPAWN_FIXTURE = "seek_respawn_solid__.rar"


def _fixture(name: str) -> Path:
    path = _FIXTURES / name
    if not path.is_file():
        pytest.skip(f"missing vendored fixture {name}")
    return path


def _named_unrar_p_bytes(path: Path, member: str) -> bytes:
    """Named ``unrar p`` stdout for ``member``.

    Goes through ``rar_unrar.open_unrar_p`` so this is the oracle, outside the
    ``rar_reader`` spy — a library spawn would otherwise be counted as ours.

    Requires exit 0: a no-match is rc 10 with empty stdout, which is not
    "the link emitted nothing".
    """
    proc, stdout = rar_unrar.open_unrar_p(path, member=member)
    try:
        data = stdout.read()
    finally:
        stdout.close()
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rar_unrar.terminate_unrar(proc)
            raise
    assert rc == 0, f"unrar p member={member!r} exited {rc} (10 is no-match)"
    return data


def _assert_solid_link_emission(
    path: Path, member: ArchiveMember, *, rar5: bool
) -> None:
    """Packed/unpacked sizes do not predict ``unrar p``; ``is_payload_file`` does."""
    raw = member._raw
    assert isinstance(raw, RarMemberInfo)
    assert raw.is_payload_file() is False
    assert member.size is not None and member.size > 0
    if rar5:
        assert member.compressed_size == 0
    else:
        assert member.compressed_size is not None and member.compressed_size > 0
    assert _named_unrar_p_bytes(path, member.name) == b""


@requires_binary("unrar")
def test_named_unrar_p_bytes_rejects_no_match() -> None:
    """A no-match is empty stdout at rc 10 — not "the link emitted nothing"."""
    with pytest.raises(AssertionError, match="exited 10"):
        _named_unrar_p_bytes(_fixture("symlinks_solid__.rar"), "NO_SUCH_MEMBER_xyz.txt")


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["basic_nonsolid__.rar", "basic_nonsolid__rar4.rar"],
)
def test_basic_nonsolid_list_and_read(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is False
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_BASIC_CONTENTS)
        for member_name, expected in _BASIC_CONTENTS.items():
            assert archive.read(files[member_name]) == expected


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["basic_solid__.rar", "basic_solid__rar4.rar"],
)
def test_basic_solid_stream_and_random(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is True
        # RAR exposes no per-solid-block boundaries, so solidity is one archive-level
        # flag and the block count is unknown rather than 1.
        assert archive.cost.access_cost is AccessCost.SOLID
        assert archive.cost.solid_block_count is None
        streamed = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert streamed == _BASIC_CONTENTS
        assert archive.read("file1.txt") == _BASIC_CONTENTS["file1.txt"]


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["basic_solid__.rar", "basic_solid__rar4.rar"],
)
def test_solid_pass_spawns_unrar_only_on_the_first_read(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solid pass nobody reads from costs no ``unrar`` process.

    ``stream_members`` promises unread members are not opened or decompressed and
    do not request passwords; a pipe spawned at pass start would break all three.
    """
    spawns: list[Path] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(path)
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is True
        for _member, _stream in archive.stream_members():
            pass
    assert spawns == []

    with open_archive(_fixture(name)) as archive:
        for member, stream in archive.stream_members():
            if member.name == "file1.txt":
                assert stream is not None
                assert stream.read() == _BASIC_CONTENTS["file1.txt"]
    assert len(spawns) == 1


@requires_binary("unrar")
def test_refused_second_open_does_not_spawn_unrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ConcurrentAccessError`` must mean the second ``unrar`` was never spawned.

    ``basic_solid__.rar`` is ``-m3`` (not stored): ``open()`` takes the named-unrar
    path. A spawn-then-cleanup fix still raises and leaves no live process, so this
    counts ``open_unrar_p`` calls rather than leftover children.
    """
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(_fixture("basic_solid__.rar")) as archive:
        files = [m for m in archive.members() if m.is_file]
        assert len(files) >= 2
        s1 = archive.open(files[0])
        assert len(spawns) == 1
        with pytest.raises(ConcurrentAccessError):
            archive.open(files[1])
        assert len(spawns) == 1
        s1.close()
        s2 = archive.open(files[1])
        assert len(spawns) == 2
        s2.close()


@requires_binary("unrar")
@pytest.mark.parametrize("name", ["basic_solid__.rar", "basic_solid__rar4.rar"])
def test_unrar_route_is_not_seekable_by_default(name: str) -> None:
    """Without ``seekable_members``, a compressed member stays a pipe."""
    with open_archive(_fixture(name)) as archive:
        with archive.open("file1.txt") as stream:
            assert stream.seekable() is False
            with pytest.raises(io.UnsupportedOperation):
                stream.seek(0)


@requires_binary("unrar")
@pytest.mark.parametrize("name", ["basic_solid__.rar", "basic_solid__rar4.rar"])
def test_seekable_members_respawns_unrar_on_backward_seek(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``seekable_members=True`` must honour backward seek on the named-unrar route.

    Direct-slice stored members already seek. This pins the pipe route: a backward
    seek closes the process; the following read respawns ``unrar``. A buffer-the-
    member fix would not call ``open_unrar_p`` a second time at all.
    """
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    expected = _BASIC_CONTENTS["file1.txt"]
    with open_archive(_fixture(name), seekable_members=True) as archive:
        with archive.open("file1.txt") as stream:
            assert stream.seekable() is True
            assert stream.read(5) == expected[:5]
            assert len(spawns) == 1
            stream.seek(0)
            assert len(spawns) == 1
            assert stream.read() == expected
            assert len(spawns) == 2
            stream.seek(5)
            assert stream.read() == expected[5:]
            stream.seek(len(expected) + 100)
            assert stream.read() == b""


def test_unrar_respawn_overrun_probe_sees_trailing_bytes() -> None:
    """Declared-size clamp must not hide extra pipe bytes from fused verify.

    ``read()`` at ``pos == size`` still reaches the inner stream so the one-byte
    overrun probe can fire. A buffer-and-clamp wrapper would return ``b""``.
    """
    payload = b"hello world!!extra"
    declared = 13

    def spawn() -> io.BytesIO:
        return io.BytesIO(payload)

    stream = rar_reader._UnrarRespawnStream(spawn, spawn(), size=declared)
    assert stream.read(declared) == payload[:declared]
    assert stream.read(1) == payload[declared : declared + 1]
    stream.seek(0)
    assert stream.read(declared) == payload[:declared]
    assert stream.read(1) == payload[declared : declared + 1]


def test_unrar_respawn_boundary_read_is_one_byte() -> None:
    """At declared size, ``read(-1)`` must not drain the rest of the pipe."""
    payload = b"hello world!!EXTRA"
    declared = 13

    def spawn() -> io.BytesIO:
        return io.BytesIO(payload)

    stream = rar_reader._UnrarRespawnStream(spawn, spawn(), size=declared)
    assert stream.read(declared) == payload[:declared]
    assert stream.read(-1) == payload[declared : declared + 1]


def test_unrar_respawn_failed_seek_leaves_position() -> None:
    """A raising ``seek()`` must not reset tell() to 0."""

    class _CloseBoom(io.BytesIO):
        def close(self) -> None:
            already = self.closed
            super().close()
            if not already:
                raise OSError("close failed")

    def spawn() -> io.BytesIO:
        return io.BytesIO(b"x" * 20)

    stream = rar_reader._UnrarRespawnStream(spawn, _CloseBoom(b"x" * 20), size=20)
    assert stream.read(10) == b"x" * 10
    assert stream.tell() == 10
    with pytest.raises(OSError, match="close failed"):
        stream.seek(2)
    assert stream.tell() == 10


def test_unrar_respawn_seek_end_does_not_drain_or_respawn() -> None:
    """``seek(0, SEEK_END); seek(0)`` before a read must not spawn a second process."""
    payload = b"hello world!"
    spawns = 0

    def spawn() -> io.BytesIO:
        nonlocal spawns
        spawns += 1
        return io.BytesIO(payload)

    stream = rar_reader._UnrarRespawnStream(spawn, spawn(), size=len(payload))
    assert spawns == 1
    assert stream.seek(0, io.SEEK_END) == len(payload)
    assert spawns == 1
    assert stream.seek(0) == 0
    assert spawns == 1
    assert stream.read() == payload
    assert spawns == 1


@requires_binary("unrar")
def test_seekable_unrar_emits_stream_rewind() -> None:
    """A small rewind of a later solid member is still loud: the prefix counts."""
    with open_archive(
        _fixture(_SEEK_RESPAWN_FIXTURE), seekable_members=True
    ) as archive:
        with archive.open("tail.txt") as stream:
            stream.read(8)
            stream.seek(0)
            assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES in dict(
                stream.diagnostics.counts
            )
            assert stream.read() == _SEEK_RESPAWN_TAIL


def test_rewind_warning_min_redecode_bytes_is_a_cost_floor() -> None:
    """A carrier-declared floor must fire even when discarded member progress is tiny."""
    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.internal.streams.archive_stream import ArchiveStream, RewindWarning

    collector = DiagnosticCollector()
    payload = b"abcdefghij"
    warning = RewindWarning(
        codec_name="rar",
        suggest_install=False,
        min_redecode_bytes=REWIND_REDECODE_WARN_BYTES,
    )
    stream = ArchiveStream(
        lambda: io.BytesIO(payload),
        translate=lambda _exc: None,
        rewind_warning=warning,
        collector=collector,
    )
    try:
        stream.read(5)
        stream.seek(0)
        assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES in dict(
            stream.diagnostics.counts
        )
    finally:
        stream.close()

    quiet = ArchiveStream(
        lambda: io.BytesIO(payload),
        translate=lambda _exc: None,
        rewind_warning=RewindWarning(codec_name="rar", suggest_install=False),
        collector=DiagnosticCollector(),
    )
    try:
        quiet.read(5)
        quiet.seek(0)
        assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES not in dict(
            quiet.diagnostics.counts
        )
    finally:
        quiet.close()


@requires_binary("unrar")
def test_seekable_unrar_respawns_while_process_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward seek must reap a live ``unrar``, not only an already-exited one."""
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(
        _fixture(_SEEK_RESPAWN_FIXTURE), seekable_members=True
    ) as archive:
        with archive.open("prefix.bin") as stream:
            first = stream.read(1024)
            assert first == _SEEK_RESPAWN_PAD[:1024]
            assert len(spawns) == 1
            stream.seek(0)
            assert stream.read(1024) == first
            assert len(spawns) == 2


@requires_binary("unrar")
def test_seekable_members_does_not_respawn_on_stored_direct_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored nonsolid members stay a file view; seeking must not spawn ``unrar``."""
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    expected = _BASIC_CONTENTS["file1.txt"]
    with open_archive(
        _fixture("basic_nonsolid__.rar"), seekable_members=True
    ) as archive:
        with archive.open("file1.txt") as stream:
            assert stream.seekable() is True
            assert stream.read(5) == expected[:5]
            stream.seek(0)
            assert stream.read() == expected
    assert spawns == []


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["symlinks_solid__.rar", "symlinks_solid__rar4.rar"],
)
def test_solid_symlink_demux_and_link_targets(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solid demux skips links; packed vs unpacked sizes differ by generation.

    Measured on this pair: RAR5 links are packed 0 / unpacked 6–12 / emit 0;
    RAR4 (old on-disk family) links are packed 6–12 / unpacked 6–12 / emit 0.
    ``is_payload_file()`` is the predictor. The ``__rar4`` archive is RAR3-family;
    its link members are stored M0 (``compress_size == file_size``). There is no
    RAR 1.5/2.x solid-symlink fixture in the tree.
    """
    path = _fixture(name)
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(archive_path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(archive_path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(path) as archive:
        assert archive.info.is_solid is True
        rar5 = archive.info.format_version == "5"
        by_name = {m.name: m for m in archive.members()}
        assert by_name["symlink_to_file1.txt"].type is MemberType.SYMLINK
        assert by_name["symlink_to_file1.txt"].link_target == "file1.txt"
        assert by_name["subdir/link_to_file1.txt"].link_target == "../file1.txt"
        # Listing and ``_ensure_link_target`` must not take the payload pipe.
        assert spawns == []

        links = [m for m in archive.members() if m.type is MemberType.SYMLINK]
        assert {m.name for m in links} == {
            "symlink_to_file1.txt",
            "subdir/link_to_file1.txt",
            "subdir_link",
            "subdir_link_with_slash",
        }
        # Positive control: the oracle can produce bytes, so empty link stdout
        # is not a silent no-match.
        assert _named_unrar_p_bytes(path, "file1.txt") == b"Hello, world!"
        for member in links:
            _assert_solid_link_emission(path, member, rar5=rar5)
            raw = member._raw
            assert isinstance(raw, RarMemberInfo)
            if not rar5:
                # Stored M0. Fails if a regeneration starts compressing targets.
                assert raw.compress_size == raw.file_size

        # Follow the link: named ``unrar p`` is the target file, not the symlink.
        assert archive.read("symlink_to_file1.txt") == b"Hello, world!"
        assert spawns == ["file1.txt"]

        payload_names: list[str] = []
        pipe_bytes = 0
        for member, stream in archive.stream_members():
            if member.is_file and stream is not None:
                data = stream.read()
                payload_names.append(member.name)
                pipe_bytes += len(data)
                assert data == b"Hello, world!"
            else:
                assert stream is None
                assert member.type in (
                    MemberType.SYMLINK,
                    MemberType.DIRECTORY,
                    MemberType.HARDLINK,
                )
        # Only payload FILE members advance the unrar p pipe.
        # None is the unnamed solid ALL-pipe the sequential pass takes.
        assert payload_names == ["file1.txt"]
        assert pipe_bytes == 13
        assert spawns == ["file1.txt", None]


@requires_binary("unrar")
def test_solid_hardlink_demux_and_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAR5 solid hardlinks match RAR5 symlinks: packed 0 / unpacked > 0 / emit 0."""
    path = _fixture("hardlinks_solid__.rar")
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(archive_path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(archive_path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(path) as archive:
        assert archive.info.is_solid is True
        assert archive.info.format_version == "5"
        by_name = {m.name: m for m in archive.members()}
        assert by_name["subdir/hardlink_to_file1.txt"].type is MemberType.HARDLINK
        assert by_name["subdir/hardlink_to_file1.txt"].link_target == "file1.txt"
        assert by_name["hardlink_to_file2.txt"].link_target == "subdir/file2.txt"
        assert spawns == []

        hardlinks = [m for m in archive.members() if m.type is MemberType.HARDLINK]
        assert {m.name for m in hardlinks} == {
            "subdir/hardlink_to_file1.txt",
            "hardlink_to_file2.txt",
        }
        assert _named_unrar_p_bytes(path, "file1.txt") == b"Hello 1!"
        for member in hardlinks:
            _assert_solid_link_emission(path, member, rar5=True)

        payloads = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert payloads == {
            "file1.txt": b"Hello 1!",
            "subdir/file2.txt": b"Hello 2!",
        }
        assert archive.read("subdir/hardlink_to_file1.txt") == b"Hello 1!"
        # None is the unnamed solid ALL-pipe; file1.txt is the followed target.
        assert spawns == [None, "file1.txt"]


@requires("cryptography")
@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_encrypted_header_lists_with_password(name: str) -> None:
    path = _fixture(name)
    with pytest.raises(EncryptionError):
        open_archive(path)
    with open_archive(path, password="header_password") as archive:
        assert archive.info.is_encrypted is True
        assert archive.read("file1.txt") == b"Hello, world!"


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["encryption__.rar", "encryption__rar4.rar"],
)
def test_encrypted_data_requires_password(name: str) -> None:
    path = _fixture(name)
    with open_archive(path) as archive:
        assert archive.info.is_encrypted is True
        with pytest.raises((EncryptionError, CorruptionError)):
            archive.read("secret.txt")
    with open_archive(path, password="password") as archive:
        assert archive.read("secret.txt") == b"This is secret"
        assert archive.read("also_secret.txt") == b"This is also secret"


@requires_binary("unrar")
def test_stored_m0_direct_read() -> None:
    with open_archive(_fixture("stored_m0.rar")) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert member.compression[0].algo is CompressionAlgorithm.STORED
        assert member.extra[EXTRA_RAR_EXTRACT_VERSION] in {15, 20, 29, 50}
        assert archive.read(member) == b"stored payload"


@pytest.mark.parametrize(
    "name",
    ["basic_solid__.rar", "basic_solid__rar4.rar"],
)
def test_compressed_member_reports_rar_algorithm(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        member = next(m for m in archive.members() if m.name == "file1.txt")
        method = member.compression[0]
        assert method.algo is CompressionAlgorithm.RAR
        assert method.level in {1, 2, 3, 4, 5}
        assert member.extra[EXTRA_RAR_EXTRACT_VERSION] in {15, 20, 29, 50}


_FILE_VERSION_CONTENTS = {
    "file.txt;1": b"version-one",
    "file.txt;2": b"version-two!!",
    "file.txt": b"version-three!!!",
}


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["file_version__.rar", "file_version__rar4.rar"],
)
def test_file_version_list_and_read(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_FILE_VERSION_CONTENTS)
        for member_name, expected in _FILE_VERSION_CONTENTS.items():
            member = files[member_name]
            if member_name == "file.txt":
                assert member.is_current is True
                assert "rar.file_version" not in member.extra
            else:
                assert member.is_current is False
                assert member.extra["rar.file_version"] == int(
                    member_name.rsplit(";", 1)[1]
                )
            assert archive.read(member) == expected
            assert archive.read(member_name) == expected


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["file_version__.rar", "file_version__rar4.rar"],
)
def test_file_version_extract_all_skips_history(name: str, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    with open_archive(_fixture(name)) as archive:
        results = archive.extract_all(dest).results
    by_name = {r.member.name: r for r in results if r.member.is_file}
    assert by_name["file.txt;1"].status is ExtractionStatus.SUPERSEDED
    assert by_name["file.txt;2"].status is ExtractionStatus.SUPERSEDED
    assert by_name["file.txt"].status is ExtractionStatus.EXTRACTED
    assert (dest / "file.txt").read_bytes() == _FILE_VERSION_CONTENTS["file.txt"]
    assert not (dest / "file.txt;1").exists()
    assert not (dest / "file.txt;2").exists()


@requires_binary("unrar")
def test_file_version_solid_demux_aligned() -> None:
    expected = {
        "a.txt;1": b"AAA-v1",
        "b.txt": b"BBB-payload",
        "a.txt": b"AAA-v2-longer",
    }
    with open_archive(_fixture("file_version_solid__.rar")) as archive:
        assert archive.info.is_solid is True
        streamed = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert streamed == expected
        for member_name, payload in expected.items():
            assert archive.read(member_name) == payload
        history = archive.get("a.txt;1")
        assert history is not None
        assert history.is_current is False
        assert history.extra["rar.file_version"] == 1


@requires_binary("unrar")
def test_blake2sp_only_hash() -> None:
    with open_archive(_fixture("blake2sp.rar")) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert HashAlgorithm.CRC32 not in member.hashes
        assert HashAlgorithm.BLAKE2SP in member.hashes
        assert archive.read(member) == b"stored payload"


@requires_binary("unrar")
def test_blake2sp_verified_no_unverifiable_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="archivey.integrity"):
        with open_archive(_fixture("blake2sp.rar")) as archive:
            member = next(m for m in archive.members() if m.is_file)
            assert archive.read(member) == b"stored payload"
            assert archive.diagnostics.total_count == 0
    assert not any(
        "Cannot verify digest 'blake2sp'" in rec.message for rec in caplog.records
    )


@requires_binary("unrar")
def test_blake2sp_corrupt_payload_raises(tmp_path: Path) -> None:
    raw = _fixture("blake2sp.rar").read_bytes()
    payload = b"stored payload"
    offset = raw.find(payload)
    assert offset >= 0
    mutated = bytearray(raw)
    mutated[offset] ^= 0x01
    corrupt = tmp_path / "blake2sp_corrupt.rar"
    corrupt.write_bytes(mutated)
    with open_archive(corrupt) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert HashAlgorithm.BLAKE2SP in member.hashes
        with pytest.raises(CorruptionError, match="blake2sp"):
            archive.read(member)


@requires_binary("unrar")
def test_blake2sp_unrar_oracle_crosscheck() -> None:
    import shutil
    import subprocess

    if shutil.which("unrar") is None:
        pytest.skip("unrar unavailable")
    fixture = _fixture("blake2sp.rar")
    with open_archive(fixture) as archive:
        member = next(m for m in archive.members() if m.is_file)
        native = archive.read(member)
    proc = subprocess.run(
        ["unrar", "p", "-inul", str(fixture)],
        check=True,
        capture_output=True,
    )
    assert proc.stdout == native == b"stored payload"


@requires_binary("unrar")
def test_multi_volume_roundtrip() -> None:
    part1 = _fixture("tinyvol.part1.rar")
    assert (_FIXTURES / "tinyvol.part2.rar").is_file()
    with open_archive(part1) as archive:
        assert archive.info.is_multivolume is True
        assert archive.info.extra.get("rar.volume_count") == 2
        data = archive.read("payload.bin")
        assert data == b"ABCDEFGH" * 200


@requires_binary("unrar")
def test_multi_volume_rnn_roundtrip() -> None:
    """Classic RAR4 volumes: ``name.rar`` + ``name.r00`` (``-vn`` naming)."""
    first = _fixture("tinyvol_rnn.rar")
    assert (_FIXTURES / "tinyvol_rnn.r00").is_file()
    with open_archive(first) as archive:
        assert archive.info.is_multivolume is True
        assert archive.info.extra.get("rar.volume_count") == 2
        assert archive.read("payload.bin") == b"ABCDEFGH" * 200


@requires_binary("unrar")
def test_multi_volume_stream_materialization() -> None:
    paths = [_fixture("tinyvol.part1.rar"), _FIXTURES / "tinyvol.part2.rar"]
    streams = [p.open("rb") for p in paths]
    try:
        with open_archive(streams) as archive:
            assert archive.info.is_multivolume is True
            assert archive.read("payload.bin") == b"ABCDEFGH" * 200
    finally:
        for stream in streams:
            stream.close()


def test_incomplete_multi_volume_raises() -> None:
    # Lone volume-1 sibling with volume/next flags and no part2.
    part1 = _fixture("tinyvol.part1.rar").read_bytes()
    with pytest.raises(TruncatedError, match="multi-volume"):
        open_archive(io.BytesIO(part1))


@requires_binary("unrar")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("rar15-comment.rar", {"FILE1.TXT": b"foooo\r\n", "FILE2.TXT": b"baaaar\r\n"}),
        (
            "rar202-comment-nopsw.rar",
            {"FILE1.TXT": b"file1\r\n", "FILE2.TXT": b"file2\r\n"},
        ),
    ],
)
def test_rar15_and_rar2_list_and_read(name: str, expected: dict[str, bytes]) -> None:
    """RAR 1.5 / 2.x archives list and read via native headers + unrar."""
    with open_archive(_fixture(name)) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(expected)
        for member_name, payload in expected.items():
            assert archive.read(files[member_name]) == payload


@requires("rarfile")
@requires_binary("unrar")
@pytest.mark.parametrize("name", ["rar15-comment.rar", "rar202-comment-nopsw.rar"])
def test_rar15_and_rar2_comments_match_rarfile(name: str) -> None:
    """Legacy embedded archive and member comments match the reference reader."""
    rarfile = pytest.importorskip("rarfile")
    path = _fixture(name)
    with rarfile.RarFile(path) as oracle:
        expected_archive_comment = oracle.comment
        expected_member_comments = {
            info.filename.replace("\\", "/").rstrip("/"): info.comment
            for info in oracle.infolist()
            if not info.is_dir()
        }

    with open_archive(path) as archive:
        assert archive.info.comment == expected_archive_comment
        assert {
            member.name: member.comment
            for member in archive.members()
            if member.is_file
        } == expected_member_comments


def _rar3_old_comment_subblock(text: bytes) -> bytes:
    """Build an old-style stored COMMENT subblock with its CRC16."""
    from archivey.internal.backends.rar_parser import _crc32

    comment_body = (
        struct.pack(
            "<HBBH",
            len(text),
            20,  # RAR 2.0 extract version
            0x30,  # M0 stored
            _crc32(text) & 0xFFFF,
        )
        + text
    )
    header_without_crc = (
        struct.pack(
            "<BHH",
            0x75,  # OLD_COMMENT
            0,
            7 + len(comment_body),
        )
        + comment_body
    )
    return struct.pack("<H", _crc32(header_without_crc) & 0xFFFF) + header_without_crc


def test_rar3_stored_old_style_main_comment_needs_no_unrar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stored legacy comment is listed natively with no binary on PATH."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_MAIN_COMMENT,
        _crc32,
        rar3_main_crc_end,
    )

    text = b"stored old-style comment"
    subblock = _rar3_old_comment_subblock(text)
    flags = _RAR3_MAIN_COMMENT
    main_body = b"\0" * 6 + subblock
    main_without_crc = struct.pack("<BHH", 0x73, flags, 7 + len(main_body)) + main_body
    # Old COMMENT subblocks are inside header_size but outside the MAIN CRC.
    main_crc = _crc32(main_without_crc[: rar3_main_crc_end(flags) - 2]) & 0xFFFF
    main_hdr = struct.pack("<H", main_crc) + main_without_crc
    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_hdr = struct.pack("<H", _crc32(end_without_crc) & 0xFFFF) + end_without_crc

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with open_archive(io.BytesIO(RAR_ID + main_hdr + end_hdr)) as archive:
        assert archive.info.comment == text.decode()


def test_rar3_service_comment_maps_to_member_comment() -> None:
    """RAR3's existing solid CMT attachment reaches ArchiveMember.comment."""
    from archivey.internal.backends.rar_parser import _RAR3_FILE_SOLID

    main_hdr, end_hdr = _rar3_main_and_end()
    comment = b"member comment"
    file_hdr = _rar3_file_block(b"item.txt", flags=0, pack_lo=0, unp_lo=0)
    service_hdr = _rar3_file_block(
        b"CMT",
        flags=_RAR3_FILE_SOLID,
        pack_lo=len(comment),
        unp_lo=len(comment),
        block_type=0x7A,
    )

    with open_archive(
        io.BytesIO(RAR_ID + main_hdr + file_hdr + service_hdr + comment + end_hdr)
    ) as archive:
        member = archive.get("item.txt")
        assert member is not None
        assert member.comment == comment.decode()


def test_rar5_comment_service_stays_archive_only() -> None:
    """RAR5 CMT is an archive comment, never an invented member comment."""
    with open_archive(_fixture("comment__.rar")) as archive:
        assert archive.info.comment == "This is a\nmulti-line comment"
        assert all(member.comment is None for member in archive.members())


def test_extract_version_20_payload_accepted() -> None:
    """Craft a RAR3 archive whose payload FILE declares extract version 20."""
    from archivey.internal.backends.rar_parser import _RAR3_LONG_BLOCK, _crc32

    name = b"x.txt"
    payload = b"hi"
    # FILE fields include pack_size as the first le32; with LONG_BLOCK that
    # field is also the block add_size so the parser skips the payload.
    file_fields = struct.pack(
        "<LLBLLBBHL",
        len(payload),
        len(payload),
        3,  # Unix
        zlib.crc32(payload) & 0xFFFFFFFF,
        0,
        20,  # extract version → also used by RAR2-era and some RAR3 stored members
        0x30,  # M0
        len(name),
        0o100644,
    )
    file_fields += name
    flags = _RAR3_LONG_BLOCK
    header_without_crc = struct.pack(
        "<BHH",
        0x74,
        flags,
        7 + len(file_fields),
    )
    header_without_crc += file_fields
    file_crc = _crc32(header_without_crc) & 0xFFFF
    file_hdr = struct.pack("<H", file_crc) + header_without_crc

    main_body = b"\x00" * 6  # reserved
    main_without_crc = struct.pack("<BHH", 0x73, 0, 7 + len(main_body)) + main_body
    main_crc = _crc32(main_without_crc) & 0xFFFF
    main_hdr = struct.pack("<H", main_crc) + main_without_crc

    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_crc = _crc32(end_without_crc) & 0xFFFF
    end_hdr = struct.pack("<H", end_crc) + end_without_crc

    blob = RAR_ID + main_hdr + file_hdr + payload + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert len(archive.members) == 1
    member = archive.members[0]
    assert member.filename == "x.txt"
    assert member.extract_version == 20
    assert member.file_size == 2
    assert member.compress_size == 2


def _rar3_file_block(
    name: bytes,
    *,
    flags: int,
    pack_lo: int,
    unp_lo: int,
    pack_hi: int = 0,
    unp_hi: int = 0,
    method: int = 0x30,
    block_type: int = 0x74,
) -> bytes:
    """Build one RAR3 FILE block with a valid 16-bit header CRC."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_LARGE,
        _RAR3_LONG_BLOCK,
        _crc32,
    )

    file_fields = struct.pack(
        "<LLBLLBBHL",
        pack_lo,
        unp_lo,
        3,  # Unix
        0,  # crc32
        0,  # dos time
        20,  # extract version
        method,
        len(name),
        0o100644,
    )
    if flags & _RAR3_FILE_LARGE:
        file_fields += struct.pack("<LL", pack_hi, unp_hi)
    file_fields += name
    flags |= _RAR3_LONG_BLOCK
    header_without_crc = struct.pack("<BHH", block_type, flags, 7 + len(file_fields))
    header_without_crc += file_fields
    file_crc = _crc32(header_without_crc) & 0xFFFF
    return struct.pack("<H", file_crc) + header_without_crc


def _rar3_main_and_end() -> tuple[bytes, bytes]:
    from archivey.internal.backends.rar_parser import _crc32

    main_without_crc = struct.pack("<BHH", 0x73, 0, 7 + 6) + b"\x00" * 6
    main_hdr = struct.pack("<H", _crc32(main_without_crc) & 0xFFFF) + main_without_crc
    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_hdr = struct.pack("<H", _crc32(end_without_crc) & 0xFFFF) + end_without_crc
    return main_hdr, end_hdr


def test_rar3_large_packed_member_skips_full_64bit_size() -> None:
    """F5: a RAR3 ``FILE_LARGE`` member's packed-data skip must use the full 64-bit
    size (HIGH_PACK_SIZE), not just the low 32 bits.

    The first member claims a 4 GiB packed size (low 32 = 0, high = 1) with no actual
    data, immediately followed by a second FILE header. Skipping only the low 32 bits
    (0 bytes) would misparse that second header as a member; skipping the full 4 GiB
    lands past EOF, so exactly one member is seen.
    """
    from archivey.internal.backends.rar_parser import _RAR3_FILE_LARGE

    main_hdr, end_hdr = _rar3_main_and_end()
    big = _rar3_file_block(
        b"big.bin", flags=_RAR3_FILE_LARGE, pack_lo=0, unp_lo=0, pack_hi=1, unp_hi=1
    )
    trailing = _rar3_file_block(b"sneaky.txt", flags=0, pack_lo=0, unp_lo=0)
    blob = RAR_ID + main_hdr + big + trailing + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert [m.filename for m in archive.members] == ["big.bin"]
    assert archive.members[0].compress_size == 1 << 32


def test_rar3_mismatched_split_continuation_is_corruption() -> None:
    """F6: a SPLIT_BEFORE continuation that names a different file after a non-split
    member must not be silently merged into the previous member."""
    from archivey.internal.backends.rar_parser import _RAR3_FILE_SPLIT_BEFORE

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(b"a.txt", flags=0, pack_lo=0, unp_lo=0)
    forged = _rar3_file_block(
        b"b.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + forged + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_same_name_split_before_without_split_after_is_corruption() -> None:
    """F6: same filename + SPLIT_BEFORE still rejects when the previous part was not
    marked SPLIT_AFTER (not a genuine volume continuation)."""
    from archivey.internal.backends.rar_parser import _RAR3_FILE_SPLIT_BEFORE

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(b"a.txt", flags=0, pack_lo=0, unp_lo=0)
    cont = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + cont + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_split_after_then_different_name_is_corruption() -> None:
    """F6: a SPLIT_AFTER part followed by SPLIT_BEFORE with a different name is not a
    continuation — reject rather than fold the unrelated member's size/CRC in."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_SPLIT_AFTER,
        _RAR3_FILE_SPLIT_BEFORE,
    )

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_AFTER, pack_lo=0, unp_lo=0
    )
    forged = _rar3_file_block(
        b"b.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + forged + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_matching_split_continuation_merges() -> None:
    """F6 positive path: same name + previous SPLIT_AFTER collapses into one member."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_SPLIT_AFTER,
        _RAR3_FILE_SPLIT_BEFORE,
    )

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_AFTER, pack_lo=3, unp_lo=3
    )
    cont = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=5, unp_lo=5
    )
    # Each FILE header's claimed pack size must be skipped before the next header.
    blob = RAR_ID + main_hdr + first + b"AAA" + cont + b"BBBBB" + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert [m.filename for m in archive.members] == ["a.txt"]
    assert archive.members[0].compress_size == 8
    assert archive.members[0].spanned_volumes is True


def test_rar5_hostile_packed_size_is_corruption() -> None:
    """Atheris: huge RAR5 vint add_size must not raise raw OverflowError on seek."""
    import zlib

    def vint(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    def block(body: bytes) -> bytes:
        header_wo_crc = vint(len(body)) + body
        crc = zlib.crc32(header_wo_crc) & 0xFFFFFFFF
        return struct.pack("<I", crc) + header_wo_crc

    main = block(vint(1) + vint(0) + vint(0))  # MAIN, no flags, main_flags=0
    hostile = block(vint(99) + vint(0x02) + vint(1 << 70))  # unknown + DATA + huge
    blob = RAR5_ID + main + hostile
    with pytest.raises(CorruptionError, match="seekable range|packed size"):
        parse_rar_archive(io.BytesIO(blob))


def test_rar5_out_of_range_windowstime_is_tolerated() -> None:
    """Atheris: hostile FILETIME must not raise raw ValueError from fromtimestamp."""
    from archivey.internal.backends.rar_parser import _load_windowstime

    # FILETIME ticks far beyond datetime's year range.
    buf = struct.pack("<II", 0xFFFFFFFF, 0x7FFFFFFF)
    dt, pos = _load_windowstime(buf, 0)
    assert dt is None
    assert pos == 8


def test_rar5_zero_windowstime_is_unset() -> None:
    """FILETIME ticks=0 follows the shared helper's unset rule (None, not 1601)."""
    from archivey.internal.backends.rar_parser import _load_windowstime

    dt, pos = _load_windowstime(struct.pack("<II", 0, 0), 0)
    assert dt is None
    assert pos == 8


def _pack_dos(year: int, month: int, day: int, hour: int, minute: int, sec: int) -> int:
    return (
        ((year - 1980) << 25)
        | (month << 21)
        | (day << 16)
        | (hour << 11)
        | (minute << 5)
        | (sec // 2)
    )


def test_parse_rar5_xtime_keeps_ctime_and_atime_with_ns() -> None:
    """RAR5 extra 0x03: HAS_CTIME / HAS_ATIME are stored, including unix-ns words."""
    from archivey.internal.backends.rar_parser import (
        _RAR5_XTIME_HAS_ATIME,
        _RAR5_XTIME_HAS_CTIME,
        _RAR5_XTIME_HAS_MTIME,
        _RAR5_XTIME_UNIXTIME,
        _RAR5_XTIME_UNIXTIME_NS,
        _parse_rar5_xtime,
    )

    tflags = (
        _RAR5_XTIME_UNIXTIME
        | _RAR5_XTIME_HAS_MTIME
        | _RAR5_XTIME_HAS_CTIME
        | _RAR5_XTIME_HAS_ATIME
        | _RAR5_XTIME_UNIXTIME_NS
    )
    mtime_s, ctime_s, atime_s = 1_600_000_000, 1_600_000_200, 1_600_000_100
    blob = bytes([tflags]) + struct.pack(
        "<IIIIII",
        mtime_s,
        ctime_s,
        atime_s,
        123_456_789,
        234_567_890,
        345_678_901,
    )
    mtime, ctime, atime = _parse_rar5_xtime(blob, 0, None)
    assert mtime == datetime(2020, 9, 13, 12, 26, 40, 123456, tzinfo=timezone.utc)
    assert ctime == datetime(2020, 9, 13, 12, 30, 0, 234567, tzinfo=timezone.utc)
    assert atime == datetime(2020, 9, 13, 12, 28, 20, 345678, tzinfo=timezone.utc)


def test_parse_rar5_xtime_mtime_only_leaves_ctime_atime_none() -> None:
    from archivey.internal.backends.rar_parser import (
        _RAR5_XTIME_HAS_MTIME,
        _RAR5_XTIME_UNIXTIME,
        _RAR5_XTIME_UNIXTIME_NS,
        _parse_rar5_xtime,
    )

    tflags = _RAR5_XTIME_UNIXTIME | _RAR5_XTIME_HAS_MTIME | _RAR5_XTIME_UNIXTIME_NS
    blob = bytes([tflags]) + struct.pack("<II", 1_600_000_000, 500_000_000)
    mtime, ctime, atime = _parse_rar5_xtime(blob, 0, None)
    assert mtime == datetime(2020, 9, 13, 12, 26, 40, 500000, tzinfo=timezone.utc)
    assert ctime is None
    assert atime is None


def test_parse_rar5_xtime_second_record_without_flags_keeps_ctime_atime() -> None:
    """A later 0x03 extra with tflags=0 must not wipe ctime/atime from an earlier one."""
    from archivey.internal.backends.rar_parser import (
        _RAR5_XTIME_HAS_ATIME,
        _RAR5_XTIME_HAS_CTIME,
        _RAR5_XTIME_HAS_MTIME,
        _RAR5_XTIME_UNIXTIME,
        _parse_rar5_xtime,
    )

    tflags = (
        _RAR5_XTIME_UNIXTIME
        | _RAR5_XTIME_HAS_MTIME
        | _RAR5_XTIME_HAS_CTIME
        | _RAR5_XTIME_HAS_ATIME
    )
    first = bytes([tflags]) + struct.pack(
        "<III", 1_600_000_000, 1_600_000_200, 1_600_000_100
    )
    mtime, ctime, atime = _parse_rar5_xtime(first, 0, None)
    mtime2, ctime2, atime2 = _parse_rar5_xtime(bytes([0]), 0, mtime, ctime, atime)
    assert (mtime2, ctime2, atime2) == (mtime, ctime, atime)
    assert ctime2 is not None
    assert atime2 is not None


def test_parse_rar5_xtime_truncated_ns_does_not_raise() -> None:
    """HAS_MTIME|UNIXTIME_NS with a FILETIME and no ns word lists, modified=None."""
    from archivey.internal.backends.rar_parser import (
        _RAR5_XTIME_HAS_MTIME,
        _RAR5_XTIME_UNIXTIME_NS,
        _parse_rar5_xtime,
    )

    tflags = _RAR5_XTIME_HAS_MTIME | _RAR5_XTIME_UNIXTIME_NS
    blob = bytes([tflags]) + struct.pack("<II", 0, 0)
    mtime, ctime, atime = _parse_rar5_xtime(blob, 0, None)
    assert mtime is None
    assert ctime is None
    assert atime is None


def test_parse_rar3_ext_time_slot_order_is_mtime_ctime_atime() -> None:
    """RAR3 EXTTIME nibbles: >>12 mtime, >>8 ctime, >>4 atime (UnRAR / rarfile)."""
    from archivey.internal.backends.rar_parser import (
        _parse_dos_time,
        _parse_rar3_ext_time,
    )

    dos_mtime = _pack_dos(2020, 1, 15, 12, 0, 0)
    dos_ctime = _pack_dos(2019, 6, 1, 8, 0, 0)
    dos_atime = _pack_dos(2021, 6, 20, 18, 30, 0)
    # Bit 3 of each nibble = present; no remnant bytes, so ctime/atime are DOS stamps.
    flags = (0x8 << 12) | (0x8 << 8) | (0x8 << 4)
    blob = struct.pack("<HII", flags, dos_ctime, dos_atime)
    mtime, ctime, atime, pos = _parse_rar3_ext_time(blob, 0, _parse_dos_time(dos_mtime))
    assert pos == len(blob)
    assert mtime == datetime(2020, 1, 15, 12, 0, 0)
    assert ctime == datetime(2019, 6, 1, 8, 0, 0)
    assert atime == datetime(2021, 6, 20, 18, 30, 0)
    assert mtime.tzinfo is None
    assert ctime.tzinfo is None
    assert atime.tzinfo is None


def test_rar5_xtime_fixture_surfaces_accessed_and_created() -> None:
    """Listing, no unrar: RAR5 ``-tsmca`` fills accessed/created as aware UTC."""
    with open_archive(_fixture("xtime__.rar")) as archive:
        member = archive.get("file.txt")
        assert member.modified == datetime(2020, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert member.accessed == datetime(2021, 6, 20, 18, 30, tzinfo=timezone.utc)
        assert member.created is not None
        assert member.created.tzinfo is timezone.utc
        assert member.created != member.modified
        assert member.created != member.accessed
        # Unix-built fixture: creation slot is st_ctime.
        assert member.extra[EXTRA_RAR_CREATED_IS_CTIME] is True


def test_rar4_xtime_fixture_surfaces_accessed_and_created() -> None:
    """Listing, no unrar: RAR4 EXTTIME fills accessed/created as naive wall-clock."""
    with open_archive(_fixture("xtime__rar4.rar")) as archive:
        member = archive.get("file.txt")
        assert member.modified == datetime(2020, 1, 15, 12, 0, 0)
        assert member.accessed == datetime(2021, 6, 20, 18, 30, 0)
        assert member.modified.tzinfo is None
        assert member.accessed.tzinfo is None
        assert member.created is not None
        assert member.created.tzinfo is None
        assert member.created != member.modified
        assert member.created != member.accessed
        assert member.extra[EXTRA_RAR_CREATED_IS_CTIME] is True


@pytest.mark.parametrize(
    "name",
    [
        "stored_m0.rar",  # RAR5 extra has mtime+ns only
        "basic_nonsolid__rar4.rar",  # EXTTIME mtime nibble only
        "rar15-comment.rar",  # no EXTTIME
    ],
)
def test_xtime_absent_accessed_created_are_none(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        files = [m for m in archive.members() if m.is_file]
        assert files
        for member in files:
            assert member.accessed is None
            assert member.created is None
            assert member.modified is not None
            assert EXTRA_RAR_CREATED_IS_CTIME not in member.extra


@pytest.mark.parametrize(
    ("host_os", "ctime", "expected"),
    [
        (3, datetime(2019, 6, 1, 8, 0, tzinfo=timezone.utc), True),
        (2, datetime(2019, 6, 1, 8, 0, tzinfo=timezone.utc), False),
        (0, datetime(2019, 6, 1, 8, 0, tzinfo=timezone.utc), False),
        (3, None, None),
        (None, datetime(2019, 6, 1, 8, 0, tzinfo=timezone.utc), None),
    ],
)
def test_created_is_ctime_extra_follows_host_os(
    host_os: int | None, ctime: datetime | None, expected: bool | None
) -> None:
    """``extra["rar.created_is_ctime"]`` is Unix-only and omitted without a ctime."""
    reader = object.__new__(rar_reader.RarReader)
    reader._diagnostics_collector = None
    reader._archive_name = "<test>"
    info = RarMemberInfo(
        filename="a.txt",
        orig_filename=b"a.txt",
        file_size=0,
        compress_size=0,
        compress_type=0x30,
        crc32=None,
        blake2sp_hash=None,
        mtime=None,
        ctime=ctime,
        atime=None,
        mode=None,
        host_os=host_os,
        flags=0,
        file_redir=None,
        file_encryption=None,
        header_offset=0,
        header_size=0,
        data_offset=0,
        extract_version=50,
        file_solid=False,
        is_directory=False,
        is_symlink=False,
        is_hardlink_or_copy=False,
        is_encrypted=False,
        volume_index=0,
        split_before=False,
        split_after=False,
    )
    member = rar_reader.RarReader._to_member(reader, info)
    if expected is None:
        assert EXTRA_RAR_CREATED_IS_CTIME not in member.extra
    else:
        assert member.extra[EXTRA_RAR_CREATED_IS_CTIME] is expected


def test_rar_reader_masks_hostile_unix_mode() -> None:
    """Atheris: huge RAR5 mode vint must not OverflowError in ``stat.S_IMODE``."""
    from archivey.internal.backends.rar_parser import RarMemberInfo
    from archivey.internal.backends.rar_reader import RarReader

    info = RarMemberInfo(
        filename="a.txt",
        orig_filename=b"a.txt",
        file_size=0,
        compress_size=0,
        compress_type=0x30,
        crc32=None,
        blake2sp_hash=None,
        mtime=None,
        ctime=None,
        atime=None,
        mode=(1 << 80) | 0o100644,
        host_os=3,
        flags=0,
        file_redir=None,
        file_encryption=None,
        header_offset=0,
        header_size=0,
        data_offset=0,
        extract_version=50,
        file_solid=False,
        is_directory=False,
        is_symlink=False,
        is_hardlink_or_copy=False,
        is_encrypted=False,
        volume_index=0,
        split_before=False,
        split_after=False,
    )
    # Build a reader without opening a real archive — call the mapper directly.
    reader = object.__new__(RarReader)
    reader._diagnostics_collector = None
    reader._archive_name = "<test>"
    member = RarReader._to_member(reader, info)
    assert member.mode == 0o0644

    # Win32 attrs are masked to 32 bits (FILE_ATTRIBUTE_* width).
    info_win = dataclasses.replace(info, host_os=2, mode=(1 << 40) | 0x20)
    member_win = RarReader._to_member(reader, info_win)
    assert member_win.mode is None
    assert member_win.windows_attrs == 0x20


def _stub_which_unrar(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make ``shutil.which('unrar')`` return ``path`` while the file exists.

    Windows ``which`` only returns names in ``PATHEXT`` (``.exe`` / ``.cmd`` / …).
    An extensionless ``unrar`` on PATH is invisible there, so a lookalike that
    only sets PATH never reaches the banner probe.

    Patches ``which`` on the ``shutil`` *module object* (not a local alias);
    ``monkeypatch`` restores it.
    """
    resolved = str(path)

    def which(command: str, path: str | None = None, **_kwargs: object) -> str | None:
        if command == "unrar" and Path(resolved).is_file():
            return resolved
        return None

    monkeypatch.setattr(rar_unrar.shutil, "which", which)


def test_non_rarlab_unrar_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_text("#!/bin/sh\necho 'unrar-free fake'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()


def test_non_rarlab_unrar_negative_probe_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_text("#!/bin/sh\necho 'unrar-free fake'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    _stub_which_unrar(monkeypatch, fake)

    probes = 0

    def count_probe(_path: str) -> bool:
        nonlocal probes
        probes += 1
        return False

    monkeypatch.setattr(rar_unrar, "_is_rarlab_unrar", count_probe)
    for _ in range(2):
        with pytest.raises(PackageNotInstalledError, match="RARLAB"):
            rar_unrar.find_rarlab_unrar()

    assert probes == 1


def test_unrar_on_path_is_the_rarlab_build() -> None:
    """Environment guard: an ``unrar`` the finder rejects makes RAR data tests fail confusingly.

    On CI this duplicates the ``Verify RARLAB unrar on PATH`` step. Unique value is
    developer machines: one named failure instead of a wall of
    ``PackageNotInstalledError``. Finder unit behaviour is
    ``test_non_rarlab_unrar_rejected``. This does not cover a binary installed
    off PATH (that is the macOS ``~/.local/bin`` trap).
    """
    if shutil.which("unrar") is None:
        pytest.skip("no unrar on PATH — RAR data tests skip, the documented state")
    rar_unrar.find_rarlab_unrar()


def test_missing_unrar_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()


def test_missing_unrar_rechecks_which_and_does_not_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``which`` miss is not cached; the expensive banner probe is never run."""
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)

    which_calls = 0
    probe_calls = 0
    which = rar_unrar.shutil.which

    def count_which(
        command: str, path: str | None = None, **kwargs: object
    ) -> str | None:
        nonlocal which_calls
        which_calls += 1
        return which(command, path=path, **kwargs)

    def count_probe(path: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return False

    monkeypatch.setattr(rar_unrar.shutil, "which", count_which)
    monkeypatch.setattr(rar_unrar, "_is_rarlab_unrar", count_probe)
    for _ in range(2):
        with pytest.raises(PackageNotInstalledError, match="RARLAB"):
            rar_unrar.find_rarlab_unrar()

    assert which_calls == 2
    assert probe_calls == 0


def test_path_change_invalidates_cached_unrar_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rarlab_unrar = shutil.which("unrar")
    if rarlab_unrar is None or not rar_unrar._is_rarlab_unrar(rarlab_unrar):
        pytest.skip("no RARLAB unrar on PATH — cannot test cache invalidation")

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()

    monkeypatch.setenv("PATH", str(Path(rarlab_unrar).parent))
    assert rar_unrar.find_rarlab_unrar() == os.path.abspath(rarlab_unrar)


def test_unrar_installed_after_miss_is_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``which`` re-runs on a miss: installing into an unchanged PATH is visible."""
    rarlab_unrar = shutil.which("unrar")
    if rarlab_unrar is None or not rar_unrar._is_rarlab_unrar(rarlab_unrar):
        pytest.skip("no RARLAB unrar on PATH — cannot test install-after-miss")

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()

    dest = tmp_path / ("unrar.exe" if os.name == "nt" else "unrar")
    shutil.copy(rarlab_unrar, dest)
    dest.chmod(0o755)
    # Windows ``which`` appends the PATHEXT spelling (``.EXE``), not the
    # filename casing we wrote. ``Path`` equality is case-insensitive there.
    assert Path(rar_unrar.find_rarlab_unrar()) == Path(os.path.abspath(str(dest)))


def test_transient_probe_oserror_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_bytes(b"x")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    _stub_which_unrar(monkeypatch, fake)

    calls = 0

    def flaky(_path: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EMFILE, "Too many open files")
        return True

    monkeypatch.setattr(rar_unrar, "_is_rarlab_unrar", flaky)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()
    assert rar_unrar.find_rarlab_unrar() == os.path.abspath(str(fake))
    assert calls == 2


def test_stat_error_on_candidate_is_package_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_bytes(b"x")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    # Do not use ``_stub_which_unrar``: it stats the candidate, and this test
    # patches ``os.stat``.
    monkeypatch.setattr(
        rar_unrar.shutil,
        "which",
        lambda command, path=None, **_k: str(fake) if command == "unrar" else None,
    )

    def boom(
        _path: str | bytes | os.PathLike[str], *_a: object, **_k: object
    ) -> object:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(rar_unrar.os, "stat", boom)
    with pytest.raises(PackageNotInstalledError, match="RARLAB") as info:
        rar_unrar.find_rarlab_unrar()
    assert isinstance(info.value.__cause__, PermissionError)


def test_replaced_cached_unrar_is_reprobed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stat-identity change (size/mtime) forces a new banner probe."""
    fake = tmp_path / "unrar"
    fake.write_bytes(b"a")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    _stub_which_unrar(monkeypatch, fake)

    probes = 0

    def count_probe(_path: str) -> bool:
        nonlocal probes
        probes += 1
        return True

    monkeypatch.setattr(rar_unrar, "_is_rarlab_unrar", count_probe)
    assert rar_unrar.find_rarlab_unrar() == os.path.abspath(str(fake))
    fake.write_bytes(b"bbbb")
    assert rar_unrar.find_rarlab_unrar() == os.path.abspath(str(fake))
    assert probes == 2


def test_deleted_cached_unrar_is_not_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_bytes(b"")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    _stub_which_unrar(monkeypatch, fake)
    # A shell script is not a Win32 executable; this test pins cache
    # invalidation, not banner sniffing.
    monkeypatch.setattr(rar_unrar, "_is_rarlab_unrar", lambda _path: True)

    assert rar_unrar.find_rarlab_unrar() == os.path.abspath(str(fake))
    fake.unlink()

    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()


def test_unrar_not_installed_message_names_lookalikes() -> None:
    """Copy of the not-installed message — behaviour tests only match ``RARLAB``."""
    msg = rar_unrar._NOT_INSTALLED_MSG
    for name in ("unrar-free", "unar", "7z", "7zz"):
        assert name in msg


def test_listing_and_stored_reads_need_no_unrar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The native-metadata split, end to end: no binary on PATH at all.

    The whole point of parsing headers ourselves is that listing an archive — and
    reading a member we can slice directly — never needs the external decompressor.
    Only a member that must go through ``unrar`` fails, and it fails by naming it.
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)

    with open_archive(_fixture("basic_nonsolid__.rar")) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_BASIC_CONTENTS)
        # Stored, unencrypted, nonsolid, unsplit -> sliced from the source directly.
        for member_name, expected in _BASIC_CONTENTS.items():
            assert archive.read(files[member_name]) == expected

    # The archive comment is compressed, so no binary leaves it unset without
    # turning a successful listing into an open failure. Its member comments
    # are stored old-style blocks and remain available natively.
    with open_archive(_fixture("rar15-comment.rar")) as archive:
        assert archive.info.comment is None
        assert {
            member.name: member.comment
            for member in archive.members()
            if member.is_file
        } == {
            "FILE1.TXT": "file1comment -----",
            "FILE2.TXT": "file2comment -----",
        }

    with open_archive(_fixture("symlinks_solid__rar4.rar")) as archive:
        assert [m.name for m in archive.members()]  # listing still works
        compressed = next(
            m for m in archive.members() if m.is_file and m.compressed_size > 0
        )
        with pytest.raises(PackageNotInstalledError, match="RARLAB"):
            archive.read(compressed)


def test_stored_nonsolid_archive_spawns_no_unrar_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading every member of a stored nonsolid archive costs zero subprocesses."""
    spawns: list[object] = []
    real_open = rar_reader.open_unrar_p

    def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        spawns.append(args)
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)
    with open_archive(_fixture("basic_nonsolid__.rar")) as archive:
        for member in archive.members():
            if member.is_file:
                archive.read(member)
    assert spawns == []


@requires("cryptography")
def test_header_crypto_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    from archivey.internal.streams import crypto

    monkeypatch.setattr(crypto, "_crypto_available", lambda: False)
    with pytest.raises(PackageNotInstalledError, match="cryptography"):
        open_archive(_fixture("encrypted_header__.rar"), password="header_password")


def test_rar_parser_bounds_member_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Member-table bombs must fail at parse, not OOM (mirrors 7z header-size bound)."""
    import archivey.internal.backends.rar_parser as rar_parser

    monkeypatch.setattr(rar_parser, "_MAX_ARCHIVE_MEMBERS", 2)
    with pytest.raises(CorruptionError, match="member count exceeds"):
        parse_rar_archive(_fixture("basic_nonsolid__.rar").open("rb"))


def test_rar_members_enforces_listing_limits() -> None:
    from archivey import ArchiveyConfig, ListingLimits, ResourceLimitError

    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(_fixture("basic_nonsolid__.rar"), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()


def test_fix_rar3_astral_truncation() -> None:
    """RAR3 compresses names as UTF-16, which truncates non-BMP chars to a PUA/surrogate
    code unit; the 8-bit name field is preferred when it recovers the real character."""
    from archivey.internal.backends.rar_parser import _fix_rar3_astral_truncation

    # U+1F600 truncated to U+F600 in the UTF-16 name; the UTF-8 8-bit name recovers it.
    truncated = "emoji_\uf600.txt"
    recovered = "emoji_\U0001f600.txt"
    assert _fix_rar3_astral_truncation(truncated, recovered.encode()) == recovered
    # No 8-bit/UTF-16 disagreement -> keep the decompressed name unchanged.
    assert _fix_rar3_astral_truncation("plain.txt", b"plain.txt") == "plain.txt"
    # A PUA char present in both fields (genuine, not a truncation) is preserved.
    pua = "\uf600.txt"
    assert _fix_rar3_astral_truncation(pua, pua.encode()) == pua
    # An 8-bit field that is not valid UTF-8 cannot override; keep the UTF-16 name.
    assert _fix_rar3_astral_truncation("name.txt", b"\xff\xfe") == "name.txt"


def test_rar3_non_bmp_filename_not_truncated() -> None:
    """Regression: an emoji in a RAR3 name must survive as U+1F600, not the PUA U+F600
    the raw UTF-16 field decodes to (external fixture from the v1 reader's bug)."""
    with open_archive(_fixture("encoding__rar4.rar")) as archive:
        names = {m.name for m in archive.members()}
    assert "emoji_😀.txt" in names
    # None of the recovered names retain a surrogate/PUA truncation artifact.
    for name in names:
        assert not any(
            0xE000 <= ord(c) <= 0xF8FF or 0xD800 <= ord(c) <= 0xDFFF for c in name
        )


def _rle_name_encdata(opcode_runs: int) -> bytes:
    """RAR3 compressed-name bytes that are all maximum-length RLE runs.

    RAR3 stores names compressed. The RLE opcode emits up to 129 UTF-16 code
    units per encoding byte by copying the 8-bit name field — so an *empty*
    8-bit field plus a long run of RLE opcodes is the worst case.
    """
    return b"\x00" + (b"\xff" + b"\x7f" * 4) * opcode_runs


def test_rar3_compressed_name_decode_is_bounded() -> None:
    """Empty-8-bit + RLE-heavy encdata must fail closed, not spend listing CPU.

    Bound: ``len(decoded) <= len(std_name) + len(encdata)`` — every output unit
    is either copied from the 8-bit field or paid for by an encoding byte. With
    an empty 8-bit field that collapses to ``len(encdata)``, which is what this
    vector checks; a compact RLE copy of a long 8-bit name is allowed to exceed
    ``len(encdata)`` alone.

    ``name_size`` is a ``uint16``, so the pre-fix buffer was at most ~13.5 MB
    (bounded, transient, discarded before any member existed). The lever was
    CPU: ~11 s for one header at that ceiling, not an unbounded vint-style
    allocation. This vector must return None rather than emit the amplified
    name.
    """
    encdata = _rle_name_encdata(1000)
    assert _decode_rar3_unicode_name(b"", encdata) is None

    # t=0 with a flags byte and no payload must not emit a leftover unit.
    assert _decode_rar3_unicode_name(b"", b"\x00\x00") is None


def test_rar3_rle_name_still_decodes_when_the_8bit_field_is_present() -> None:
    """Failing closed on overrun must not change a well-formed RLE copy from the 8-bit name.

    hi=0, flags=0xC0 (first opcode is RLE), n=1 → copy 3 bytes of std_name as ASCII.
    """
    assert _decode_rar3_unicode_name(b"abc", b"\x00\xc0\x01") == "abc"


def test_rar3_rle_name_may_be_longer_than_encdata() -> None:
    """A compact RLE copy of a long 8-bit name is well-formed.

    Output can exceed ``len(encdata)`` so long as it stays within
    ``len(std_name) + len(encdata)``.
    """
    std_name = b"a" * 50
    # hi=0, flags=0xC0 (RLE), n=48 → copy 50 ASCII bytes.
    decoded = _decode_rar3_unicode_name(std_name, b"\x00\xc0\x30")
    assert decoded == "a" * 50
    assert len(decoded) > len(b"\x00\xc0\x30")
    assert len(decoded) <= len(std_name) + 3


def test_rar3_rle_name_zero_correction_keeps_hi_byte() -> None:
    """``n & 0x80`` with correction byte 0 still uses ``hi``, not high-byte 0.

    The ASCII RLE path (no 0x80) emits high-byte 0; the correction path must
    not collapse into it just because the correction value is zero.
    """
    # hi=0x04, flags=0xC0 (RLE), n=0x80 (k=2, correction follows), correction=0.
    decoded = _decode_rar3_unicode_name(b"ab", b"\x04\xc0\x80\x00")
    assert decoded == "\u0461\u0462"


def _reference_decode_rar3_unicode_name(std_name: bytes, encdata: bytes) -> str | None:
    """Per-byte transcription of the pre-rewrite ``_UnicodeFilename.decode``.

    Independent oracle for the property test — keep this as the old class
    algorithm, not a copy of ``_decode_rar3_unicode_name``.
    """
    pos = 0
    encpos = 0
    buf = bytearray()
    failed = False

    def enc_byte() -> int:
        nonlocal encpos, failed
        try:
            c = encdata[encpos]
            encpos += 1
            return c
        except IndexError:
            failed = True
            return 0

    def std_byte() -> int:
        nonlocal failed
        try:
            return std_name[pos]
        except IndexError:
            failed = True
            return ord("?")

    def put(lo: int, hi_byte: int) -> None:
        nonlocal pos
        buf.append(lo)
        buf.append(hi_byte)
        pos += 1

    hi = enc_byte()
    flagbits = 0
    flags = 0
    while not failed and encpos < len(encdata):
        if flagbits == 0:
            flags = enc_byte()
            flagbits = 8
            if failed:
                break
        flagbits -= 2
        t = (flags >> flagbits) & 3
        if t == 0:
            lo = enc_byte()
            if failed:
                break
            put(lo, 0)
        elif t == 1:
            lo = enc_byte()
            if failed:
                break
            put(lo, hi)
        elif t == 2:
            lo = enc_byte()
            if failed:
                break
            c_hi = enc_byte()
            if failed:
                break
            put(lo, c_hi)
        else:
            n = enc_byte()
            if failed:
                break
            if n & 0x80:
                correction = enc_byte()
                if failed:
                    break
                for _ in range((n & 0x7F) + 2):
                    lo = (std_byte() + correction) & 0xFF
                    if failed:
                        break
                    put(lo, hi)
            else:
                for _ in range(n + 2):
                    lo = std_byte()
                    if failed:
                        break
                    put(lo, 0)
    if failed:
        return None
    return buf.decode("utf-16le", "replace")


def test_rar3_unicode_name_decode_matches_reference_and_stays_bounded() -> None:
    """Random ``(std_name, encdata)`` pairs match the pre-rewrite decoder and
    never exceed ``len(std_name) + len(encdata)`` on success.
    """
    pytest.importorskip("hypothesis")
    from hypothesis import example, given
    from hypothesis import strategies as st

    opcodeish = st.sampled_from([0x00, 0x40, 0x80, 0xC0, 0xFF, 0x7F, 0x01, 0x81])
    encdata_st = st.one_of(
        st.binary(max_size=13),
        st.tuples(
            st.binary(min_size=1, max_size=1), st.lists(opcodeish, max_size=12)
        ).map(lambda hi_and_rest: hi_and_rest[0] + bytes(hi_and_rest[1])),
    )

    @given(
        std_name=st.one_of(
            st.binary(max_size=11), st.binary(min_size=129, max_size=200)
        ),
        encdata=encdata_st,
    )
    @example(b"", _rle_name_encdata(8))
    @example(b"", b"\x00\x00")
    @example(b"abc", b"\x00\xc0\x01")
    @example(b"ab", b"\x04\xc0\x80\x00")
    @example(b"x" * 129, b"\x00\xc0\x7f")  # plain run k=129
    @example(b"x" * 129, b"\x04\xc0\xff\x05")  # correction run k=129, c=5
    @example(b"x" * 129, b"\x04\xc0\xff\x00")  # correction run k=129, c=0
    def inner(std_name: bytes, encdata: bytes) -> None:
        got = _decode_rar3_unicode_name(std_name, encdata)
        assert got == _reference_decode_rar3_unicode_name(std_name, encdata)
        if got is not None:
            assert len(got) <= len(std_name) + len(encdata)

    inner()


# Fixtures built by review/next/01-rar-reader-findings/make_hostile_fixtures.py:
# nonsolid, compressed members whose stored names are a bare unrar switch and an
# ``@listfile`` argument, alongside a normal control member.
_HOSTILE_ARGV_CONTENTS = {
    "canary.txt": b"CANARY-CANARY-CANARY-\n" * 64,
    "-inul": b"DASH-INUL-PAYLOAD-\n" * 64,
    "@atfile": b"AT-ATFILE-PAYLOAD-\n" * 64,
}


@requires_binary("unrar")
@pytest.mark.parametrize("name", ["hostile_argv__.rar", "hostile_argv__rar4.rar"])
def test_hostile_member_name_reads_its_own_bytes(name: str) -> None:
    """F3 (review/next/01-rar-reader-findings/unrar-boundary.md): a member whose
    stored name is a bare ``unrar`` switch (``-inul``) or an ``@listfile`` argument
    (``@atfile``) must be addressed to exactly that member.

    Fixed by passing the member via a ``-n./`` include mask instead of positionally,
    so ``unrar`` cannot parse the name as a switch or a local-file read. Each hostile
    member now returns its own bytes, and never another member's.
    """
    with open_archive(_fixture(name)) as archive:
        members = {m.name: m for m in archive.members() if m.is_file}
        assert {"canary.txt", "-inul", "@atfile"} <= set(members)
        for member_name, expected in _HOSTILE_ARGV_CONTENTS.items():
            assert archive.read(members[member_name]) == expected, (
                f"reading {member_name!r} did not return its own bytes (F3 argv injection)"
            )


def test_rar5_header_size_vint_is_bounded() -> None:
    """F2: the RAR5 header-size vint pre-read is length-capped, so a crafted run of
    continuation bytes cannot drive an unbounded, O(n^2) read of the source."""
    payload = RAR5_ID + b"\x00\x00\x00\x00" + b"\x80" * 2_000_000
    start = time.perf_counter()
    with pytest.raises(ArchiveyError):
        parse_rar_archive(io.BytesIO(payload))
    # Bounded work: the cap rejects after a handful of bytes rather than reading 2 MB.
    assert time.perf_counter() - start < 1.0


def test_load_vint_single_and_multi_byte() -> None:
    """RAR5 vint decode: single-byte hot path and multi-byte continuation agree."""
    assert load_vint(b"\x00", 0) == (0, 1)
    assert load_vint(b"\x7f", 0) == (127, 1)
    assert load_vint(bytes([0x80 | 1, 0x02]), 0) == (1 + (2 << 7), 2)
    # Non-zero start offset (rewrite bound is relative to ``pos``, not buffer start).
    assert load_vint(b"\xff" + bytes([0x80 | 1, 0x02]), 1) == (1 + (2 << 7), 3)
    # Maximum-length valid vint: 10 continuation bytes + terminator.
    assert load_vint(b"\x80" * 10 + b"\x01", 0) == (1 << 70, 11)
    with pytest.raises(CorruptionError):
        load_vint(b"", 0)
    with pytest.raises(CorruptionError):
        load_vint(b"\x80" * 11, 0)


def test_unrar_member_include_switch_builds_n_mask() -> None:
    """Every member name becomes a ``-n./`` include mask, including ``*``/``?``
    (no escape exists) and names that would be switches or ``@listfile`` if
    passed positionally."""
    from archivey.internal.backends.rar_unrar import _member_include_switch

    assert _member_include_switch("-inul") == "-n./-inul"
    assert _member_include_switch("@atfile") == "-n./@atfile"
    assert _member_include_switch("dir/normal.txt") == "-n./dir/normal.txt"
    assert _member_include_switch("weird*.txt") == "-n./weird*.txt"
    assert _member_include_switch("a?b.txt") == "-n./a?b.txt"


def test_unrar_mask_match_treats_brackets_as_literal() -> None:
    """``unrar -n`` does not treat ``[]`` as a character class; the skip must not
    either, or a name with both brackets and a glob would desync from the pipe."""
    from archivey.internal.backends.rar_unrar import _unrar_mask_match

    assert _unrar_mask_match("a*.txt", "a*.txt")
    assert _unrar_mask_match("aX.txt", "a*.txt")
    assert _unrar_mask_match("subdir/aY.txt", "a*.txt")
    assert not _unrar_mask_match("b1.txt", "a*.txt")
    assert _unrar_mask_match("b?.txt", "b?.txt")
    assert _unrar_mask_match("b1.txt", "b?.txt")
    assert _unrar_mask_match("foo[a].txt", "foo[a].txt")
    assert not _unrar_mask_match("fooa.txt", "foo[a].txt")
    assert _unrar_mask_match("foo[a]X.txt", "foo[a]*.txt")
    assert not _unrar_mask_match("fooaX.txt", "foo[a]*.txt")
    assert _unrar_mask_match("subdir/aY.txt", "subdir/a*.txt")
    assert not _unrar_mask_match("aX.txt", "subdir/a*.txt")
    assert not _unrar_mask_match("other/aY.txt", "subdir/a*.txt")
    assert _unrar_mask_match("aY.txt", "./aY.txt")
    assert _unrar_mask_match("aY.txt", "aY.txt")
    assert not _unrar_mask_match("subdir/aY.txt", "aY.txt")
    # Known over-match vs unrar 7.00 (F1). Directory-glob / backslash names are
    # refused before this function sizes a skip; these pins keep the divergence
    # visible rather than silently "fixed" without an oracle.
    assert _unrar_mask_match("aaa/x.txt", "d*/x.txt")
    assert _unrar_mask_match("a/b1.txt", r"a\b*.txt")


def test_unrar_glob_demux_ok_basename_only() -> None:
    """Demux is offered only for a glob confined to the basename, no backslash."""
    from archivey.internal.backends.rar_unrar import _unrar_glob_demux_ok

    assert _unrar_glob_demux_ok("a*.txt")
    assert _unrar_glob_demux_ok("subdir/a*.txt")
    assert _unrar_glob_demux_ok("b?.txt")
    assert not _unrar_glob_demux_ok("d*/x.txt")
    assert not _unrar_glob_demux_ok("*/x.txt")
    assert not _unrar_glob_demux_ok(r"a\b*.txt")
    assert not _unrar_glob_demux_ok(r"subdir\a*.txt")


@requires_binary("unrar")
@pytest.mark.parametrize("name", list(_WILDCARD_FIXTURES))
def test_wildcard_member_name_reads_its_own_bytes(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``unrar -n./a*.txt`` concatenates every match; we skip to the named member.

    ``subdir/aY.txt`` is an earlier basename match, so ``a*.txt`` is the prefix-skip
    case. The size bound still drops ``aX.txt`` after it. Nonsolid fixtures must
    actually compress (tiny ``-m3`` members store as M0 and never reach this path).
    """
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(_fixture(name)) as archive:
        files = [m for m in archive.members() if m.is_file]
        assert {m.name for m in files} == set(_WILDCARD_CONTENTS)
        names = [m.name for m in files]
        assert names.index("subdir/aY.txt") < names.index("a*.txt")
        assert archive.read("a*.txt") == _WILDCARD_CONTENTS["a*.txt"]
        assert "a*.txt" in spawns
        for member_name, expected in _WILDCARD_CONTENTS.items():
            assert archive.read(member_name) == expected


@requires_binary("unrar")
def test_wildcard_solid_stream_members_reads_all() -> None:
    """The unnamed ALL-pipe has no ``-n`` mask, so wildcard names already demux by
    size; this pins that the glob skip on the named route did not leak into it."""
    with open_archive(_fixture("wildcard_names_solid__.rar")) as archive:
        got = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
    assert got == _WILDCARD_CONTENTS


@requires_binary("unrar")
def test_seekable_wildcard_respawn_still_skips_glob_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backward seek on a glob name must respawn *and* re-skip the prefix.

    ``a*.txt`` is not first among matches (``subdir/aY.txt`` is), so a respawn
    that forgot the skip would return the nested file after ``seek(0)``.
    """
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    expected = _WILDCARD_CONTENTS["a*.txt"]
    with open_archive(
        _fixture("wildcard_names_solid__.rar"), seekable_members=True
    ) as archive:
        with archive.open("a*.txt") as stream:
            assert stream.seekable() is True
            # Prefix skip is eager at pipe construction, which for a respawn is
            # the following read — not seek(). A no-byte seek must not spawn.
            stream.seek(0, io.SEEK_END)
            stream.seek(0)
            assert spawns == ["a*.txt"]
            assert stream.read(6) == expected[:6]
            assert spawns == ["a*.txt"]
            stream.seek(0)
            assert spawns == ["a*.txt"]
            assert stream.read() == expected
            assert spawns == ["a*.txt", "a*.txt"]


@requires_binary("unrar")
def test_wildcard_dirglob_and_backslash_names_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A glob in a directory component, or a backslash in the stored name, stays a
    typed refusal — otherwise unrar's mask (and our skip) desyncs, including on
    Windows where ``\\`` is a separator and a non-glob ``a\\b_TGT.txt`` emits nothing."""
    spawns: list[object] = []
    original = rar_reader.open_unrar_p

    def spy(path: Path, **kwargs: object):
        spawns.append(kwargs.get("member"))
        return original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_reader, "open_unrar_p", spy)

    with open_archive(_fixture("wildcard_dirglob__.rar")) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert "d*/x.txt" in files
        for name, expected in _WILDCARD_DIRGLOB_CONTENTS.items():
            if name == "d*/x.txt":
                continue
            assert archive.read(name) == expected
        before = list(spawns)
        with pytest.raises(UnsupportedFeatureError):
            archive.read("d*/x.txt")
        assert spawns == before

    with open_archive(_fixture("wildcard_backslash__.rar")) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert archive.read("a/b1.txt") == _WILDCARD_BACKSLASH_SLASH
        before = list(spawns)
        for name in _WILDCARD_BACKSLASH_REFUSED:
            assert name in files
            with pytest.raises(UnsupportedFeatureError):
                archive.read(name)
        assert spawns == before


@requires_binary("unrar")
def test_wildcard_ver_live_glob_skips_history_rows() -> None:
    """``unrar p -n./data*`` without ``-ver`` omits history; the skip must too."""
    with open_archive(_fixture("wildcard_ver__.rar")) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_WILDCARD_VER_CONTENTS)
        for member_name, expected in _WILDCARD_VER_CONTENTS.items():
            assert archive.read(member_name) == expected


@requires("cryptography")
@pytest.mark.parametrize(
    "name", ["encrypted_header__.rar", "encrypted_header__rar4.rar"]
)
def test_header_encryption_wrong_password_is_encryption_error(name: str) -> None:
    """F1: a wrong header password surfaces as ``EncryptionError`` (not
    ``CorruptionError``) even without a check value (always RAR3, checkval-less RAR5),
    so password-candidate iteration keeps trying instead of aborting."""
    path = _fixture(name)
    with pytest.raises(EncryptionError):
        with open_archive(path, password="DEFINITELY_WRONG") as archive:
            archive.members()
    # A candidate list whose first entry is wrong must fall through to the correct one.
    with open_archive(
        path, password=["DEFINITELY_WRONG", "header_password"]
    ) as archive:
        assert len(archive.members()) > 0


class _FakeUnrarProc:
    """Minimal ``Popen`` stand-in for ``_UnrarOwnedStream`` exit-code tests."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class _CloseRaises(io.BytesIO):
    """Stdout stand-in whose close() fails after the BytesIO close succeeds."""

    def close(self) -> None:
        super().close()
        raise OSError("stdout close failed")


def _close_unrar_owned(
    *,
    rc: int,
    named_member: bool = False,
    has_verifiable_hash: bool = False,
    encrypted: bool = False,
    stdout: bytes = b"",
) -> None:
    from archivey.internal.backends.rar_reader import _UnrarOwnedStream

    stream = _UnrarOwnedStream(
        io.BytesIO(stdout),
        _FakeUnrarProc(rc),  # type: ignore[arg-type]
        named_member=named_member,
        has_verifiable_hash=has_verifiable_hash,
        encrypted=encrypted,
    )
    stream.close()


def test_unrar_owned_stream_maps_exit_11_to_encryption_error() -> None:
    """F4: unrar exit 11 (bad password) always maps, even when a hash is present."""
    with pytest.raises(EncryptionError):
        _close_unrar_owned(rc=11, has_verifiable_hash=True)


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_maps_fatal_crc_when_no_hash(rc: int) -> None:
    """F4: exits 2/3 map to CorruptionError when archivey has no verifiable hash."""
    with pytest.raises(CorruptionError, match="fatal or CRC"):
        _close_unrar_owned(rc=rc, named_member=True, has_verifiable_hash=False)


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_suppresses_fatal_crc_when_hash_present(rc: int) -> None:
    """F4: with a verifiable hash, archivey's digest check is authoritative — ignore
    unrar's sometimes-spurious CRC/fatal codes (legacy RAR 1.5 false positives)."""
    _close_unrar_owned(rc=rc, named_member=True, has_verifiable_hash=True)


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_encrypted_empty_maps_to_encryption_error_on_read(
    rc: int,
) -> None:
    """RAR4 wrong password: exit 2/3 + empty stdout → EncryptionError on the
    completing/empty read (eager finalize), including when a hash would suppress CRC."""
    from archivey.internal.backends.rar_reader import _UnrarOwnedStream

    stream = _UnrarOwnedStream(
        io.BytesIO(b""),
        _FakeUnrarProc(rc),  # type: ignore[arg-type]
        named_member=True,
        has_verifiable_hash=True,
        encrypted=True,
    )
    with pytest.raises(EncryptionError):
        stream.read()
    stream.close()  # already mapped on read — must not raise again


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_encrypted_empty_maps_to_encryption_error_on_close(
    rc: int,
) -> None:
    """Early-stop close still maps encrypted empty exit 2/3 when no completing read."""
    with pytest.raises(EncryptionError):
        _close_unrar_owned(
            rc=rc,
            named_member=True,
            has_verifiable_hash=True,
            encrypted=True,
        )


def test_unrar_owned_stream_maps_exit_10_for_named_open() -> None:
    """F4: exit 10 (no files matched) on a named ``-n`` open is CorruptionError."""
    with pytest.raises(CorruptionError, match="no matching member"):
        _close_unrar_owned(rc=10, named_member=True, has_verifiable_hash=False)


def test_unrar_owned_stream_suppresses_exit_10_when_hash_present() -> None:
    """F4: exit 10 is also suppressed when archivey verifies the member itself."""
    _close_unrar_owned(rc=10, named_member=True, has_verifiable_hash=True)


def test_unrar_owned_stream_ignores_exit_10_on_solid_all_pipe() -> None:
    """F4: exit 10 on the solid ALL-pipe is not an error (empty match is expected
    when no named filter is used)."""
    _close_unrar_owned(rc=10, named_member=False, has_verifiable_hash=False)


def test_unrar_owned_stream_success_and_warning_pass() -> None:
    """F4: exits 0 (success) and 1 (warning) close cleanly."""
    _close_unrar_owned(rc=0, named_member=True)
    _close_unrar_owned(rc=1, named_member=True)


def test_unrar_owned_stream_negative_rc_from_terminate_is_not_error() -> None:
    """F4: a negative return code means we terminated the process (early close)."""
    _close_unrar_owned(rc=-15, named_member=True, has_verifiable_hash=False)


def test_unrar_owned_stream_close_does_not_mask_inner_close_error() -> None:
    """inner.close() must not be replaced by the exit-mapped error in close()."""
    from archivey.internal.backends.rar_reader import _UnrarOwnedStream

    stream = _UnrarOwnedStream(
        _CloseRaises(b""),
        _FakeUnrarProc(11),  # type: ignore[arg-type]
        named_member=True,
        has_verifiable_hash=True,
    )
    with pytest.raises(OSError, match="stdout close failed") as caught:
        stream.close()
    assert isinstance(caught.value.__cause__, EncryptionError)


def test_unrar_owned_stream_close_raises_inner_error_on_quiet_unrar_exit() -> None:
    """inner.close() still raises when unrar already exited 0 (no mapped error)."""
    from archivey.internal.backends.rar_reader import _UnrarOwnedStream

    stream = _UnrarOwnedStream(
        _CloseRaises(b""),
        _FakeUnrarProc(0),  # type: ignore[arg-type]
        named_member=True,
        has_verifiable_hash=True,
    )
    with pytest.raises(OSError, match="stdout close failed") as caught:
        stream.close()
    assert caught.value.__cause__ is None


def test_open_unrar_p_missing_stdout_pipe_is_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `unrar` spawn that yields no stdout pipe must not cross as a raw RuntimeError.

    Defensive: `Popen` is asked for `stdout=PIPE`, so this should be unreachable. It is
    typed anyway because every archive-read failure surfaces as an `ArchiveyError`, and
    the raw error would otherwise escape `open_archive` untranslated (review F15).
    """

    class _NoStdout:
        stdout = None
        stdin = None

        def kill(self) -> None:
            self.killed = True

    proc = _NoStdout()
    monkeypatch.setattr(rar_unrar, "find_rarlab_unrar", lambda: "/bin/true")
    monkeypatch.setattr(rar_unrar.subprocess, "Popen", lambda *a, **k: proc)

    with pytest.raises(ArchiveyError):
        rar_unrar.open_unrar_p(tmp_path / "nonexistent.rar")
    assert proc.killed is True


def test_listing_walks_header_to_header_rather_than_reading_an_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """formats/rar.md §1: listing seeks once per member; there is no index region.

    Pinned as a *shape* (seeks scale with member count), not an exact count, because
    the point is the scaling: ZIP reads one contiguous central directory in a constant
    number of seeks whatever the member count. If RAR ever learns to read the ``QO``
    quick-open record (§10 #5) this stops being true and the page must change with it.
    """
    from archivey.internal.backends import rar_parser

    skips = 0
    original = rar_parser._seek_after_packed

    def counting(source: object, data_offset: int, add_size: int) -> object:
        nonlocal skips
        skips += 1
        return original(source, data_offset, add_size)  # type: ignore[arg-type]

    monkeypatch.setattr(rar_parser, "_seek_after_packed", counting)

    small = _fixture("basic_nonsolid__.rar")
    with small.open("rb") as handle:
        archive = rar_parser.parse_rar_archive(handle)
    assert skips >= len(archive.members), (
        f"{skips} data skips for {len(archive.members)} members — expected at least one "
        "per member, i.e. a header-to-header walk"
    )

    # And it scales: a 1000-member archive costs proportionally more, not a constant.
    small_skips = skips
    skips = 0
    large = _fixture("many_list_store__.rar")
    with large.open("rb") as handle:
        large_archive = rar_parser.parse_rar_archive(handle)
    assert len(large_archive.members) > 10 * len(archive.members)
    assert skips > 10 * small_skips

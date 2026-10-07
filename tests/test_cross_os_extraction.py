"""Extraction under ``STRICT`` and ``STANDARD`` gives the same tree on every OS.

The ``safe-extraction`` rule "Cross-platform name safety is deterministic across policy
levels": an archive extracted on Linux into an NTFS volume ends up as it would on
Windows on that volume. Each test here pins one place where the two used to differ.
The tests run on POSIX and exercise the shared rule; the Windows-only parts either run
only on Windows or simulate the Windows error with monkeypatch.
"""

from __future__ import annotations

import errno
import io
import os
import stat
import tarfile
from pathlib import Path

import pytest

from archivey import ExtractionPolicy, ExtractionStatus, OnError, OverwritePolicy
from archivey.exceptions import (
    ExtractionError,
    FilterRejectionError,
    LinkTargetNotFoundError,
)
from archivey.internal import extraction
from archivey.internal.filters import apply_name_policy
from archivey.types import ArchiveMember, MemberType
from tests.extract_util import open_and_extract

_PORTABLE = pytest.mark.parametrize(
    "policy", [ExtractionPolicy.STRICT, ExtractionPolicy.STANDARD]
)
_POSIX_SYMLINKS = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")


def _tar(specs: list[tuple[str, str, bytes | str | None]]) -> bytes:
    """A tar from ``(kind, name, payload)``: kind ``file`` or ``ro`` (a read-only file;
    bytes), ``dir`` or ``rodir`` (a read-only directory), ``sym`` or ``hard`` (link
    target)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for kind, name, payload in specs:
            info = tarfile.TarInfo(name)
            if kind in ("file", "ro"):
                assert isinstance(payload, bytes)
                info.size = len(payload)
                info.mode = 0o644 if kind == "file" else 0o444
                tf.addfile(info, io.BytesIO(payload))
                continue
            if kind in ("dir", "rodir"):
                info.type = tarfile.DIRTYPE
                info.mode = 0o755 if kind == "dir" else 0o555
            else:
                assert isinstance(payload, str)
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = payload
            tf.addfile(info)
    return buf.getvalue()


def _member(
    name: str, *, type: MemberType = MemberType.FILE, link_target: str | None = None
) -> ArchiveMember:
    return ArchiveMember(
        type=type, name=name, raw_name=name.encode(), link_target=link_target
    )


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# --- A backslash in a TAR name is a separator --------------------------------------


@_PORTABLE
def test_tar_backslash_is_written_as_a_separator(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """Windows writes ``a\\b`` as directory ``a`` and file ``b``; so does every OS."""
    report = open_and_extract(
        io.BytesIO(_tar([("file", "a\\b", b"x")])), tmp_path / "out", policy=policy
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.EXTRACTED
    assert _tree(tmp_path / "out") == ["a", "a/b"]
    assert (tmp_path / "out" / "a" / "b").read_bytes() == b"x"
    # The rewrite is recorded like any other portable rewrite.
    assert result.presented_name == "a\\b"
    assert result.member.name == "a\\b"


@pytest.mark.skipif(os.name == "nt", reason="Windows writes '\\' as a separator")
def test_tar_backslash_is_literal_under_trusted(tmp_path: Path) -> None:
    open_and_extract(
        io.BytesIO(_tar([("file", "a\\b", b"x")])),
        tmp_path / "out",
        policy=ExtractionPolicy.TRUSTED,
    )
    assert _tree(tmp_path / "out") == ["a\\b"]


@_PORTABLE
def test_tar_backslash_meets_a_file_of_the_same_name_as_a_slash_does(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """``a`` then ``a\\b``: the outcome is the one ``a`` then ``a/b`` gets."""

    def outcome(name: str, dest: Path) -> list[tuple[ExtractionStatus, str | None]]:
        report = open_and_extract(
            io.BytesIO(_tar([("file", "a", b"1"), ("file", name, b"2")])),
            dest,
            policy=policy,
            on_error=OnError.CONTINUE,
        )
        return [
            (r.status, type(r.error).__name__ if r.error else None)
            for r in report.results
        ]

    slash = outcome("a/b", tmp_path / "slash")
    backslash = outcome("a\\b", tmp_path / "backslash")
    assert backslash == slash
    assert slash[1][0] is ExtractionStatus.FAILED


@_PORTABLE
def test_hardlink_target_backslash_becomes_a_separator(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    for type in (MemberType.HARDLINK, MemberType.SYMLINK):
        member = _member("h", type=type, link_target="d\\f")
        assert apply_name_policy(member, policy).link_target == "d/f"

    archive = _tar([("file", "d\\f", b"data"), ("hard", "h\\g", "d\\f")])
    report = open_and_extract(io.BytesIO(archive), tmp_path / "out", policy=policy)
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert (tmp_path / "out" / "h" / "g").read_bytes() == b"data"


@_PORTABLE
def test_hardlink_resolves_by_its_stored_target(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """The target rewrite does not change which member a hard link names.

    The reader matches a hard link to a member by the stored spelling, before any
    policy runs, so ``d\\f`` does not name the member stored ``d/f`` on any OS. The
    rewrite only puts the target on the path the universal check sees.
    """
    archive = _tar([("file", "d/f", b"data"), ("hard", "h", "d\\f")])
    report = open_and_extract(
        io.BytesIO(archive), tmp_path / "out", policy=policy, on_error=OnError.CONTINUE
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.FAILED,
    ]
    assert isinstance(report.results[1].error, LinkTargetNotFoundError)


@_POSIX_SYMLINKS
@_PORTABLE
def test_symlink_to_a_member_named_with_a_backslash_resolves(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """The file ``a\\b`` is written at ``a/b``, so the link to it must follow."""
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("file", "a\\b", b"x"), ("sym", "l", "a\\b")])),
        dest,
        policy=policy,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert (dest / "l").read_bytes() == b"x"
    # The member still records the target as stored.
    assert report.results[1].member.link_target == "a\\b"


@_PORTABLE
def test_symlink_target_backslash_cannot_climb_out(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """``..\\x`` is ``../x`` once rewritten, which leaves the destination."""
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("sym", "l", "..\\x")])),
        dest,
        policy=policy,
        on_error=OnError.CONTINUE,
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.BLOCKED
    assert isinstance(result.error, FilterRejectionError)
    assert result.error.link_target == "..\\x"
    assert not (dest / "l").is_symlink()


@_POSIX_SYMLINKS
@pytest.mark.parametrize("name", ["foo\\x", "foo. /x"])
def test_rewritten_name_is_checked_where_it_is_written(
    tmp_path: Path, name: str
) -> None:
    """The universal check runs again on the rewritten name.

    ``foo\\x`` and ``foo. /x`` name no directory ``foo`` until the policy rewrites them
    to ``foo/x``. A ``foo`` symlink already in the destination that leaves it must
    refuse the member, as it refuses ``foo/x``.
    """
    dest = tmp_path / "out"
    outside = tmp_path / "outside"
    dest.mkdir()
    outside.mkdir()
    (dest / "foo").symlink_to(outside)
    report = open_and_extract(
        io.BytesIO(_tar([("file", name, b"x")])),
        dest,
        policy=ExtractionPolicy.STRICT,
        on_error=OnError.CONTINUE,
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.BLOCKED
    assert isinstance(result.error, FilterRejectionError)
    assert result.error.member_name == name
    assert list(outside.iterdir()) == []


# --- Characters Windows refuses are escaped ----------------------------------------


@pytest.mark.parametrize(
    "name,written",
    [
        ("a?b", "a%3Fb"),
        ('q"u<o>t|e*', "q%22u%3Co%3Et%7Ce%2A"),
        ("tab\there", "tab%09here"),
        ("line\nbreak\x1f", "line%0Abreak%1F"),
        ("bell\x01", "bell%01"),
        # A '%' in a name the escape rewrites is escaped too, so it reads back.
        ("50%?", "50%25%3F"),
        # Each segment, directories included.
        ("d?r/f*le", "d%3Fr/f%2Ale"),
    ],
)
@_PORTABLE
def test_windows_invalid_characters_are_escaped(
    name: str, written: str, policy: ExtractionPolicy
) -> None:
    out = apply_name_policy(_member(name), policy)
    assert out.name == written


@_PORTABLE
def test_windows_invalid_characters_are_escaped_on_disk(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("file", "what?.txt", b"x"), ("file", "a|b", b"y")])),
        dest,
        policy=policy,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert _tree(dest) == ["a%7Cb", "what%3F.txt"]
    assert [r.presented_name for r in report.results] == ["what?.txt", "a|b"]


def test_plain_names_and_percent_are_not_escaped() -> None:
    for name in ["50%.txt", "café", "a b", "x\x7fy", "#&;'"]:
        member = _member(name)
        assert apply_name_policy(member, ExtractionPolicy.STRICT) is member


@pytest.mark.skipif(os.name == "nt", reason="Windows refuses these names")
def test_windows_invalid_characters_are_written_under_trusted(tmp_path: Path) -> None:
    open_and_extract(
        io.BytesIO(_tar([("file", "what?.txt", b"x")])),
        tmp_path / "out",
        policy=ExtractionPolicy.TRUSTED,
    )
    assert _tree(tmp_path / "out") == ["what?.txt"]


@_PORTABLE
def test_hardlink_to_an_escaped_name_links_to_what_was_written(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("file", "a?b", b"x"), ("hard", "l", "a?b")])),
        dest,
        policy=policy,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert _tree(dest) == ["a%3Fb", "l"]
    assert (dest / "l").read_bytes() == b"x"


# --- Windows device names beyond CON/NUL/COMn/LPTn ----------------------------------


_MORE_DEVICES = ["COM¹", "com².txt", "LPT³", "lpt¹.log", "CONIN$", "conout$.txt"]


@pytest.mark.parametrize("name", _MORE_DEVICES)
@_PORTABLE
def test_more_windows_device_names_are_rejected(
    tmp_path: Path, name: str, policy: ExtractionPolicy
) -> None:
    """Win32 reads ¹²³ as port digits and opens the console for ``CONIN$``/``CONOUT$``,
    so these are refused on every OS, as ``COM1`` is."""
    report = open_and_extract(
        io.BytesIO(_tar([("file", f"d/{name}", b"x")])),
        tmp_path / "out",
        policy=policy,
        on_error=OnError.CONTINUE,
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.BLOCKED
    assert isinstance(result.error, FilterRejectionError)
    assert "Windows-reserved device name" in result.error.message


def test_similar_names_are_not_devices() -> None:
    for name in ["COM⁴", "COM0", "COM10", "CONIN", "CONOUT$x", "LPT¹x"]:
        member = _member(name)
        assert apply_name_policy(member, ExtractionPolicy.STRICT) is member


@pytest.mark.skipif(os.name == "nt", reason="these are devices on Windows")
def test_more_windows_device_names_are_written_under_trusted(tmp_path: Path) -> None:
    open_and_extract(
        io.BytesIO(_tar([("file", n, b"x") for n in _MORE_DEVICES])),
        tmp_path / "out",
        policy=ExtractionPolicy.TRUSTED,
    )
    assert _tree(tmp_path / "out") == sorted(_MORE_DEVICES)


# --- Windows name errors are typed --------------------------------------------------


def _win_error(winerror: int, err: int = errno.EINVAL) -> OSError:
    """An ``OSError`` as Windows raises it: ``winerror`` set beside a mapped errno."""
    exc = OSError(err, "simulated")
    setattr(exc, "winerror", winerror)  # noqa: B010 - POSIX OSError has no such slot
    return exc


@pytest.mark.parametrize(
    "winerror,err,message",
    [
        (123, errno.EINVAL, "cannot be represented"),
        (206, errno.ENOENT, "too long"),
        (1314, errno.EINVAL, "privilege"),
    ],
)
def test_windows_name_errors_are_typed(winerror: int, err: int, message: str) -> None:
    exc = _win_error(winerror, err)
    typed = extraction._typed_os_error(exc, "m")
    assert isinstance(typed, ExtractionError)
    assert message in typed.message
    assert typed.member_name == "m"
    assert typed.__cause__ is exc


def test_other_windows_errors_stay_raw() -> None:
    for exc in [
        _win_error(5, errno.EACCES),
        _win_error(87),
        OSError(errno.EINVAL, "x"),
    ]:
        assert extraction._typed_os_error(exc, "m") is exc


@pytest.mark.parametrize("on_error", [OnError.CONTINUE, OnError.STOP])
def test_symlink_privilege_refusal_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_error: OnError
) -> None:
    """Windows without Developer Mode or elevation refuses every symlink with 1314."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise _win_error(1314)

    monkeypatch.setattr(os, "symlink", refuse)
    archive = io.BytesIO(_tar([("file", "f", b"x"), ("sym", "l", "f")]))
    if on_error is OnError.STOP:
        with pytest.raises(ExtractionError, match="privilege") as info:
            open_and_extract(archive, tmp_path / "out", on_error=on_error)
        assert info.value.member_name == "l"
        return
    report = open_and_extract(archive, tmp_path / "out", on_error=on_error)
    result = report.results[1]
    assert result.status is ExtractionStatus.FAILED
    assert isinstance(result.error, ExtractionError)
    assert "Developer Mode" in result.error.message


# --- Symlink targets use Windows separators on Windows ------------------------------


@_POSIX_SYMLINKS
def test_symlink_target_gets_windows_separators_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated on POSIX: the link is created with ``\\``, which Windows resolves.

    On POSIX the resulting link dangles (``sub\\file`` is one name there), which is
    enough to see what target was written.
    """
    monkeypatch.setattr(extraction, "_WINDOWS", True)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(
            _tar(
                [
                    ("dir", "sub", None),
                    ("file", "sub/file", b"x"),
                    ("sym", "l", "sub/file"),
                ]
            )
        ),
        dest,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 3
    assert os.readlink(dest / "l") == "sub\\file"
    # The member still records the target as stored.
    assert report.results[2].member.link_target == "sub/file"


@pytest.mark.skipif(os.name != "nt", reason="Windows symlink resolution")
def test_symlink_with_slash_target_resolves_on_windows(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    try:
        probe.symlink_to("x")
    except OSError:
        pytest.skip("this process may not create symlinks")
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(
            _tar(
                [
                    ("dir", "sub", None),
                    ("file", "sub/file", b"x"),
                    ("sym", "l", "sub/file"),
                ]
            )
        ),
        dest,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 3
    assert (dest / "l").read_bytes() == b"x"


# --- A read-only file this run wrote can be replaced --------------------------------


def _refuse_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make POSIX refuse to replace or unlink a read-only file, or remove a read-only
    directory, as Windows does."""
    real_replace, real_unlink, real_rmdir = os.replace, os.unlink, os.rmdir

    def check(path: str | Path) -> None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return
        kept = stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)
        if kept and not st.st_mode & stat.S_IWRITE:
            raise PermissionError(errno.EACCES, "Access is denied", str(path))

    def replace(src: str | Path, dst: str | Path) -> None:
        check(dst)
        real_replace(src, dst)

    def unlink(path: str | Path) -> None:
        check(path)
        real_unlink(path)

    monkeypatch.setattr(extraction, "_WINDOWS", True)
    monkeypatch.setattr(os, "replace", replace)

    def rmdir(path: str | Path) -> None:
        check(path)
        real_rmdir(path)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "rmdir", rmdir)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("second", ["file", "sym"])
@_PORTABLE
def test_replace_over_a_read_only_file_this_run_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: ExtractionPolicy,
    second: str,
) -> None:
    """``README`` stored read-only, then ``readme``: the collision replaces it on every
    OS, by an atomic replace (a file) or an unlink first (a symlink)."""
    if second == "sym" and os.name == "nt":
        pytest.skip("needs POSIX symlinks")
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    payload = b"new" if second == "file" else "elsewhere"
    report = open_and_extract(
        io.BytesIO(_tar([("ro", "README", b"old"), (second, "readme", payload)])),
        dest,
        policy=policy,
        overwrite=OverwritePolicy.REPLACE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.OVERWRITTEN,
        ExtractionStatus.EXTRACTED,
    ]
    if second == "file":
        assert (dest / "README").read_bytes() == b"new"
    else:
        assert os.readlink(dest / "README") == "elsewhere"


def test_streaming_duplicate_of_a_read_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A streaming pass writes the first copy before it sees the second."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("ro", "f", b"old"), ("ro", "f", b"new")])),
        dest,
        streaming=True,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.SUPERSEDED,
        ExtractionStatus.EXTRACTED,
    ]
    assert (dest / "f").read_bytes() == b"new"
    assert _mode(dest / "f") == 0o444


def test_streaming_duplicate_that_is_refused_still_removes_the_read_only_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The later copy is blocked, so the parked earlier copy is removed, not replaced:
    random access would never have written it."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("ro", "f", b"old"), ("sym", "f", "../../outside")])),
        dest,
        streaming=True,
        on_error=OnError.CONTINUE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.SUPERSEDED,
        ExtractionStatus.BLOCKED,
    ]
    assert _tree(dest) == []


def test_anti_item_removes_a_read_only_file_this_run_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven on the coordinator, as the anti-item tests in test_extraction.py are: no
    test archive of ours carries an anti-item after a read-only file."""
    from archivey.internal.extraction import (
        BombTracker,
        ExtractionCoordinator,
        _Claim,
        _RunState,
    )

    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    dest.mkdir()
    written = dest / "f"
    written.write_bytes(b"x")
    os.chmod(written, 0o444)
    coordinator = ExtractionCoordinator(policy=ExtractionPolicy.STRICT)
    coordinator._state = _RunState(
        dest=dest,
        dest_root=dest.resolve(),
        tracker=BombTracker(None, None),
        written_paths={written},
        collision_map={"f": _Claim(written, 0, written)},
    )
    anti = ArchiveMember(type=MemberType.ANTI, name="f")
    result = coordinator._apply_anti_item(anti, written)
    assert result.status is ExtractionStatus.EXTRACTED
    assert not written.exists()


def test_replace_over_a_read_only_directory_this_run_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory stored ``0o555`` (kept under ``STANDARD``), then a file of the same
    name. Simulated: POSIX removes such a directory, so the refusal is patched in.
    What this shows is that the coordinator clears the mode before ``os.rmdir``, not
    that Windows refuses the call."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("rodir", "d", None), ("file", "d", b"x")])),
        dest,
        policy=ExtractionPolicy.STANDARD,
        overwrite=OverwritePolicy.REPLACE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.OVERWRITTEN,
        ExtractionStatus.EXTRACTED,
    ]
    assert (dest / "d").read_bytes() == b"x"


@_POSIX_SYMLINKS
def test_replace_over_a_read_only_directory_first_created_as_a_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``d`` is created as the parent of ``d/x``, emptied when the streaming pass
    removes the parked ``d/x`` for a blocked duplicate, then given ``0o555`` by a
    ``d`` directory member. It is in ``created_dirs``, never ``written_paths``, and
    the later file ``d`` still replaces it."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(
            _tar(
                [
                    ("file", "d/x", b"x"),
                    ("sym", "d/x", "../../outside"),
                    ("rodir", "d", None),
                    ("file", "d", b"y"),
                ]
            )
        ),
        dest,
        policy=ExtractionPolicy.STANDARD,
        streaming=True,
        on_error=OnError.CONTINUE,
        overwrite=OverwritePolicy.REPLACE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.SUPERSEDED,
        ExtractionStatus.BLOCKED,
        ExtractionStatus.OVERWRITTEN,
        ExtractionStatus.EXTRACTED,
    ]
    assert (dest / "d").read_bytes() == b"y"


def test_anti_item_removes_a_read_only_directory_this_run_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven on the coordinator, as the file case above is."""
    from archivey.internal.extraction import (
        BombTracker,
        ExtractionCoordinator,
        _RunState,
    )

    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    written = dest / "d"
    written.mkdir(parents=True)
    os.chmod(written, 0o555)
    coordinator = ExtractionCoordinator(policy=ExtractionPolicy.STANDARD)
    coordinator._state = _RunState(
        dest=dest,
        dest_root=dest.resolve(),
        tracker=BombTracker(None, None),
        written_paths={written},
    )
    anti = ArchiveMember(type=MemberType.ANTI, name="d")
    result = coordinator._apply_anti_item(anti, written)
    assert result.status is ExtractionStatus.EXTRACTED
    assert not written.exists()


def test_read_only_flag_stays_on_other_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing the flag clears it on the file, so the other names get it back."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(
            _tar([("ro", "a", b"old"), ("hard", "B", "a"), ("file", "b", b"new")])
        ),
        dest,
        policy=ExtractionPolicy.STRICT,
        overwrite=OverwritePolicy.REPLACE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.OVERWRITTEN,
        ExtractionStatus.EXTRACTED,
    ]
    assert (dest / "B").read_bytes() == b"new"
    assert (dest / "a").read_bytes() == b"old"
    assert _mode(dest / "a") == 0o444


def test_read_only_file_already_in_the_destination_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only what this run wrote is opened up; the caller's read-only file still
    refuses the replace, as it does on Windows."""
    _refuse_read_only(monkeypatch)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "f").write_bytes(b"mine")
    os.chmod(dest / "f", 0o444)
    report = open_and_extract(
        io.BytesIO(_tar([("file", "f", b"theirs")])),
        dest,
        overwrite=OverwritePolicy.REPLACE,
        on_error=OnError.CONTINUE,
    )
    assert report.results[0].status is ExtractionStatus.FAILED
    assert (dest / "f").read_bytes() == b"mine"
    assert _mode(dest / "f") == 0o444


@pytest.mark.skipif(
    os.name != "nt", reason="Windows refuses to replace read-only files"
)
def test_replace_over_a_read_only_file_on_windows(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("ro", "README", b"old"), ("file", "readme", b"new")])),
        dest,
        overwrite=OverwritePolicy.REPLACE,
    )
    assert [r.status for r in report.results] == [
        ExtractionStatus.OVERWRITTEN,
        ExtractionStatus.EXTRACTED,
    ]
    assert (dest / "README").read_bytes() == b"new"


# --- More hard links than NTFS allows ----------------------------------------------


@pytest.mark.parametrize("refusal", ["emlink", "winerror"])
def test_hard_link_past_the_link_limit_is_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    """NTFS allows 1024 names for one file; simulated here with a limit of 2 names.

    The next link is a copy, and the links after it link to the copy.
    """
    real_link = os.link

    def link(src: str | Path, dst: str | Path) -> None:
        if os.stat(src).st_nlink >= 2:
            if refusal == "emlink":
                raise OSError(errno.EMLINK, "Too many links")
            raise _win_error(1142)
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    dest = tmp_path / "out"
    specs: list[tuple[str, str, bytes | str | None]] = [("file", "f", b"data")]
    specs += [("hard", f"h{i}", "f") for i in range(3)]
    report = open_and_extract(io.BytesIO(_tar(specs)), dest)
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 4
    assert all((dest / n).read_bytes() == b"data" for n in ["f", "h0", "h1", "h2"])
    same = os.stat(dest / "f").st_ino
    assert os.stat(dest / "h0").st_ino == same
    assert os.stat(dest / "h1").st_ino != same
    assert os.stat(dest / "h2").st_ino == os.stat(dest / "h1").st_ino


def test_links_past_the_limit_cost_one_attempt_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every older name of a full file is full too, so none is tried again. Trying
    them made the number of ``os.link`` calls quadratic in the archive's link count."""
    real_link = os.link
    calls = 0

    def link(src: str | Path, dst: str | Path) -> None:
        nonlocal calls
        calls += 1
        if os.stat(src).st_nlink >= 4:
            raise OSError(errno.EMLINK, "Too many links")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    links = 40
    specs: list[tuple[str, str, bytes | str | None]] = [("file", "f", b"data")]
    specs += [("hard", f"h{i}", "f") for i in range(links)]
    report = open_and_extract(io.BytesIO(_tar(specs)), tmp_path / "out")
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * (
        links + 1
    )
    assert calls == links
    assert len({os.stat(p).st_ino for p in (tmp_path / "out").iterdir()}) == 11


def test_other_link_errors_still_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def link(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", link)
    report = open_and_extract(
        io.BytesIO(_tar([("file", "f", b"data"), ("hard", "h", "f")])),
        tmp_path / "out",
        on_error=OnError.CONTINUE,
    )
    assert report.results[1].status is ExtractionStatus.FAILED
    assert not (tmp_path / "out" / "h").exists()

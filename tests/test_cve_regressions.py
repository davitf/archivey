"""Published vulnerabilities in other archive readers, replayed against archivey.

Each test rebuilds the archive structure a CVE or advisory describes and checks that
archivey keeps the property the other reader lost: nothing is written, linked or
changed outside the destination, and hostile structure ends in a typed error rather
than a hang. A test names the CVE it replays, so a failure points at the published
write-up of the attack.
"""

from __future__ import annotations

import errno
import io
import os
import stat
import tarfile
import warnings
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from archivey import (
    ExtractionPolicy,
    ExtractionReport,
    ExtractionStatus,
    OnError,
    OverwritePolicy,
)
from archivey.exceptions import FilterRejectionError, LinkTargetNotFoundError
from tests.extract_util import open_and_extract

_POSIX_LINKS = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
_POLICIES = pytest.mark.parametrize(
    "policy", list(ExtractionPolicy), ids=lambda p: p.name
)
_MODES = pytest.mark.parametrize("streaming", [False, True], ids=["random", "stream"])


def _tar(entries: list[tuple[str, str, str | bytes]]) -> bytes:
    """A PAX tar of ``(kind, name, value)`` entries: ``file`` holds the bytes
    ``value``, ``dir`` ignores it, ``sym`` and ``hard`` link to the name ``value``,
    and ``hard-data`` is a hardlink to ``value`` that also carries a body."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for kind, name, value in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(value, bytes)
                info.size = len(value)
                tf.addfile(info, io.BytesIO(value))
                continue
            if kind == "hard-data":
                assert isinstance(value, str)
                info.type = tarfile.LNKTYPE
                info.linkname = value
                info.size = len(b"overwritten")
                tf.addfile(info, io.BytesIO(b"overwritten"))
                continue
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
            else:
                assert isinstance(value, str)
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = value
            tf.addfile(info)
    return buf.getvalue()


def _outside_layout(tmp_path: Path) -> tuple[Path, Path]:
    """``tmp_path/dest``, empty, and ``tmp_path/outside`` holding ``victim``."""
    dest = tmp_path / "dest"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim").write_bytes(b"original")
    return dest, outside


def _assert_outside_untouched(tmp_path: Path, outside: Path) -> None:
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dest", "outside"]
    assert [p.name for p in outside.iterdir()] == ["victim"]
    assert (outside / "victim").read_bytes() == b"original"


def _assert_no_link_leaves(
    dest: Path, planted: Mapping[str, str] | None = None
) -> None:
    """Every symlink under ``dest`` leads to something inside it. The kernel follows
    each link (``os.stat``), one component at a time, and the inode it lands on must
    be one the walk found under ``dest``; ``realpath`` would not do, since it has the
    blind spot CVE-2025-4517 used. A dangling link must name a path inside. A link
    the test ``planted`` before the run, still as planted, is not the run's."""
    st = dest.stat()
    inside = {(st.st_dev, st.st_ino)}
    links: list[tuple[str, int, str]] = []
    # By directory descriptor: the tree's real paths can be longer than PATH_MAX.
    fds: list[int] = []
    try:
        for parent, dirnames, filenames, dir_fd in os.fwalk(dest):
            fds.append(os.dup(dir_fd))
            for name in dirnames + filenames:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                rel = os.path.relpath(os.path.join(parent, name), dest)
                if stat.S_ISLNK(st.st_mode) and (planted or {}).get(rel) == os.readlink(
                    name, dir_fd=dir_fd
                ):
                    continue
                if stat.S_ISLNK(st.st_mode):
                    links.append((parent, fds[-1], name))
                else:
                    inside.add((st.st_dev, st.st_ino))
        for parent, dir_fd, name in links:
            try:
                st = os.stat(name, dir_fd=dir_fd)
            except FileNotFoundError:
                rel = os.path.relpath(parent, dest)
                target = os.path.join(rel, os.readlink(name, dir_fd=dir_fd))
                assert not os.path.isabs(target), (parent, name)
                first = os.path.normpath(target).split(os.sep)[0]
                assert first != "..", (parent, name)
                continue
            except OSError as exc:  # a loop, or a path too long: it leads nowhere
                assert exc.errno in (errno.ELOOP, errno.ENAMETOOLONG), (parent, name)
                continue
            assert (st.st_dev, st.st_ino) in inside, (parent, name)
    finally:
        for fd in fds:
            os.close(fd)


def _path_max_chain(dest: Path) -> list[tuple[str, str, str | bytes]]:
    """CPython's test for CVE-2025-4517 (gh-135034), as a member list.

    Sixteen directories, each with a short symlink to it, are sized so that the path
    through the short names fits in ``PATH_MAX`` and the path the links expand to
    does not. A link at the bottom points back up sixteen levels, so it resolves
    inside the destination. ``escape`` points at that link's parent. The kernel
    follows it one link at a time and lands one level above the destination, but a
    resolver that builds the whole expanded path gets ``ENAMETOOLONG`` at the long
    link, and ``os.path.realpath`` then keeps the name and drops it again at the
    ``..``, so the target looks like it stays inside."""
    steps = "abcdefghijklmnop"
    path_max = os.pathconf(dest, "PC_PATH_MAX")
    component = "d" * min(
        255, (path_max - len(str(dest.resolve())) - 1) // (len(steps) + 1)
    )
    # The expansion through the long link must pass PATH_MAX; if it fits, ``escape``
    # is refused as a plain escape and the test no longer covers the CVE.
    expanded = len(str(dest.resolve())) + len(steps) * (len(component) + 1) + 255
    assert len(component) < 255 and expanded > path_max, (len(component), expanded)
    entries: list[tuple[str, str, str | bytes]] = []
    path = ""
    for step in steps:
        entries.append(("dir", os.path.join(path, component), ""))
        entries.append(("sym", os.path.join(path, step), component))
        path = os.path.join(path, component)
    long_link = os.path.join(*steps, "l" * 254)
    entries.append(("sym", long_link, os.path.join(*[".."] * len(steps))))
    entries.append(("sym", "escape", os.path.join(long_link, "..")))
    entries.append(("hard", "flaglink", "escape/outside/victim"))
    entries.append(("file", "escape/newfile", b"new"))
    entries.append(("file", "escape/outside/victim", b"overwritten"))
    return entries


@_POSIX_LINKS
@_POLICIES
@_MODES
def test_cve_2025_4517_link_chain_past_path_max(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool
) -> None:
    # tarfile's "data" filter resolved link targets with os.path.realpath, which keeps
    # a component it cannot look up as a name and goes on. The same archive must not
    # get a link out of the destination here, or a file written through one.
    dest, outside = _outside_layout(tmp_path)
    report = open_and_extract(
        io.BytesIO(_tar(_path_max_chain(dest))),
        dest,
        streaming=streaming,
        policy=policy,
        on_error=OnError.CONTINUE,
        overwrite=OverwritePolicy.REPLACE,
    )
    _assert_outside_untouched(tmp_path, outside)
    _assert_no_link_leaves(dest)
    escape = next(r for r in report if r.member.name == "escape")
    assert escape.status is ExtractionStatus.BLOCKED
    assert isinstance(escape.error, FilterRejectionError)
    assert not os.path.lexists(dest / "escape") or not os.path.islink(dest / "escape")


def _zip(entries: list[tuple[str, str, str | bytes]]) -> bytes:
    """A ZIP of ``(kind, name, value)`` entries as :func:`_tar` takes them; ``sym``
    stores a Unix symlink, and ``hard`` is not a ZIP type."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf, warnings.catch_warnings():
        # A case may repeat a name on purpose.
        warnings.filterwarnings("ignore", "Duplicate name", UserWarning)
        for kind, name, value in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            if kind == "file":
                info.external_attr = 0o100644 << 16
            elif kind == "dir":
                # archivey, like zipfile, takes a directory from the name's slash.
                name = name if name.endswith("/") else name + "/"
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o040755 << 16) | 0x10
            elif kind == "sym":
                info.external_attr = 0o120777 << 16
            else:
                raise ValueError(kind)
            zf.writestr(info, value if isinstance(value, bytes) else value.encode())
    return buf.getvalue()


def _snapshot(outside: Path) -> tuple[int, int, int, int]:
    """The modes and times of ``outside`` and its ``victim``: a link that leads out
    must not reach them by a ``chmod`` or ``utime`` either."""
    d, v = outside.stat(), (outside / "victim").stat()
    return d.st_mode, d.st_mtime_ns, v.st_mode, v.st_mtime_ns


def _extract_and_check(
    tmp_path: Path,
    archive: bytes,
    *,
    policy: ExtractionPolicy,
    streaming: bool,
    planted: Mapping[str, str] | None = None,
) -> ExtractionReport:
    """Extract ``archive`` into ``tmp_path/dest`` past ``tmp_path/outside`` and check
    that nothing outside changed, its metadata included, and no link leads out.
    ``planted`` symlinks (name to target) are made in ``dest`` before the run."""
    dest, outside = _outside_layout(tmp_path)
    for name, target in (planted or {}).items():
        os.symlink(target, dest / name)
    # Modes and times no member carries, so a chmod or utime through a link shows.
    os.chmod(outside, 0o751)
    os.chmod(outside / "victim", 0o640)
    for path in (outside / "victim", outside):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = _snapshot(outside)
    report = open_and_extract(
        io.BytesIO(archive),
        dest,
        streaming=streaming,
        policy=policy,
        on_error=OnError.CONTINUE,
        overwrite=OverwritePolicy.REPLACE,
    )
    _assert_outside_untouched(tmp_path, outside)
    assert _snapshot(outside) == before
    if os.name != "nt":
        _assert_no_link_leaves(dest, planted)
    _assert_not_disclosed(dest)
    return report


def _assert_not_disclosed(dest: Path) -> None:
    """No regular file under ``dest`` holds the victim's bytes, so nothing outside
    was copied or linked in."""
    for parent, _, filenames in os.walk(dest):
        for name in filenames:
            path = os.path.join(parent, name)
            if not os.path.islink(path):
                with open(path, "rb") as f:
                    assert f.read() != b"original", path


def _assert_outcomes(
    report: ExtractionReport, expected: Mapping[str, ExtractionStatus]
) -> None:
    """Each member named in ``expected`` ended with that status, and the members a
    filter refused are exactly the ``BLOCKED`` ones there: a case whose hostile
    member is missing, or fails for another reason, does not pass."""
    outcomes = [(r.member.name.rstrip("/"), r.status, r.error) for r in report]
    for name, status in expected.items():
        assert any(n == name and s is status for n, s, _ in outcomes), (
            name,
            status,
            outcomes,
        )
    blocked = {n for n, s, _ in outcomes if s is ExtractionStatus.BLOCKED}
    assert blocked == {
        n for n, s in expected.items() if s is ExtractionStatus.BLOCKED
    }, outcomes
    for _, s, error in outcomes:
        if s is ExtractionStatus.BLOCKED:
            assert isinstance(error, FilterRejectionError), outcomes


_Case = tuple[list[tuple[str, str, str | bytes]], tuple[str, ...]]
"""A case's entries, and the names of the members a filter must refuse."""

# Name-only escapes: every OS, every policy.
_NAME_CASES: dict[str, _Case] = {
    # CVE-2007-4559 (tarfile), the original traversal.
    "dotdot": ([("file", "../outside/victim", b"overwritten")], ("../outside/victim",)),
    "dotdot-inside": (
        [("file", "a/../../outside/victim", b"overwritten")],
        ("a/../../outside/victim",),
    ),
    # GNU tar CVE-2016-6321: a ``..`` after a prefix the reader strips.
    "dot-then-dotdot": (
        [("file", "./../outside/victim", b"overwritten")],
        ("../outside/victim",),
    ),
    # Absolute names (bsdtar CVE-2015-2304, tarfile). STANDARD and TRUSTED re-root
    # them inside the destination (``_REROOTED``).
    "absolute": (
        [("file", "{outside}/victim", b"overwritten")],
        ("{outside}/victim",),
    ),
    # unrar CVE-2022-30333, WinRAR CVE-2025-6218: backslash separators.
    "backslash": (
        [("file", "..\\outside\\victim", b"overwritten")],
        ("..\\outside\\victim",),
    ),
    "backslash-inside": (
        [("file", "a\\..\\..\\outside\\victim", b"overwritten")],
        ("a\\..\\..\\outside\\victim",),
    ),
    # Drive letters and UNC roots, which Windows takes as absolute.
    "drive": (
        [("file", "C:..\\outside\\victim", b"overwritten")],
        ("C:..\\outside\\victim",),
    ),
    "drive-root": (
        [("file", "C:\\outside\\victim", b"overwritten")],
        ("C:\\outside\\victim",),
    ),
    "unc": (
        [("file", "\\\\server\\share\\victim", b"overwritten")],
        ("\\\\server\\share\\victim",),
    ),
    # WinRAR CVE-2025-8088: a name that is an alternate data stream on NTFS, its
    # stream name carrying a traversal.
    "ads": (
        [("file", "f.txt:..\\..\\outside\\victim", b"overwritten")],
        ("f.txt:..\\..\\outside\\victim",),
    ),
}

# Cases whose one member STANDARD and TRUSTED extract inside the destination.
_REROOTED = {"absolute", "drive-root", "unc"}

# Link escapes: a link that leads out, then a write through it. Each link that leads
# out is refused when it is created, so the write that follows lands in a real
# directory inside the destination.
_LINK_CASES: dict[str, _Case] = {
    # GNU tar CVE-2025-45582, 7-Zip CVE-2025-55188: a symlink out, then a member
    # under it.
    "symlink-out": (
        [("sym", "dir", "../outside"), ("file", "dir/victim", b"overwritten")],
        ("dir",),
    ),
    # unrar CVE-2022-48579: an absolute symlink target.
    "symlink-absolute": (
        [("sym", "dir", "{outside}"), ("file", "dir/victim", b"overwritten")],
        ("dir",),
    ),
    # unrar CVE-2022-30333, 7-Zip CVE-2025-11001/11002: a target with backslashes,
    # which Windows readers convert and others keep. On POSIX, TRUSTED keeps the
    # backslashes as part of one name, so its link dangles inside.
    "symlink-backslash": (
        [("sym", "dir", "..\\outside"), ("file", "dir/victim", b"overwritten")],
        ("dir",),
    ),
    # unrar CVE-2017-12938, 7-Zip CVE-2025-55188: a chain of links each inside on
    # its own, which together lead out.
    "symlink-chain": (
        [
            ("sym", "a", "."),
            ("sym", "b", "a/.."),
            ("file", "b/outside/victim", b"overwritten"),
        ],
        ("b",),
    ),
    "symlink-chain-nested": (
        [
            ("dir", "x", ""),
            ("sym", "x/up", ".."),
            ("sym", "x/up2", "up/.."),
            ("file", "x/up2/outside/victim", b"overwritten"),
        ],
        ("x/up2",),
    ),
    # tarfile CVE-2025-4138 / CVE-2025-4330: a link inside a directory whose target
    # was checked against the wrong directory.
    "symlink-in-subdir": (
        [
            ("dir", "a/b", ""),
            ("sym", "a/b/l", "../../../outside"),
            ("file", "a/b/l/victim", b"overwritten"),
        ],
        ("a/b/l",),
    ),
    # CVE-2024-12718 (tarfile): a member written over a link out has its mode and
    # times set through the link.
    "metadata-through-link": (
        [("sym", "victim", "../outside/victim"), ("file", "victim", b"new")],
        ("victim",),
    ),
    # tarfile CVE-2026-7774: a symlink whose name looks like a directory. The ZIP
    # spelling cannot be a link: its trailing slash makes it a directory.
    "symlink-named-dot": (
        [
            ("dir", "a", ""),
            ("sym", "a/.", "../../outside"),
            ("file", "a/victim", b"overwritten"),
        ],
        ("a",),
    ),
    # tarfile CVE-2026-19672 goes up through a link with a ``..`` in a member name.
    # archivey refuses any name with a ``..`` before it resolves anything, so the
    # difference between a lexical and the kernel's resolution never arises.
    "dotdot-after-a-link": (
        [
            ("dir", "x/y", ""),
            ("sym", "x/y/l", "../.."),
            ("dir", "x/y/l/../newdir", ""),
            ("file", "x/y/l/../newfile", b"new"),
        ],
        ("x/y/l/../newdir", "x/y/l/../newfile"),
    ),
}

_TAR_ONLY_CASES: dict[str, _Case] = {
    # tarfile CVE-2026-7774: the TAR spelling of a directory-like symlink name.
    "symlink-named-like-a-directory": (
        [("sym", "a/", "../outside"), ("file", "a/victim", b"overwritten")],
        ("a",),
    ),
    # The hardlink cases below need a symlink out on disk, which the archive cannot
    # create here; ``_PLANTED_CASES`` replays them against a planted one.
    # tarfile CVE-2025-4330 / CVE-2025-4517 family: a hardlink to a symlink that
    # leads out, then a write through the hardlink.
    "hardlink-to-symlink": (
        [
            ("sym", "s", "../outside"),
            ("hard", "h", "s"),
            ("file", "h/victim", b"overwritten"),
        ],
        ("s", "h"),
    ),
    # tarfile CVE-2025-4138: a hardlink to a file outside, through a symlink.
    "hardlink-through-symlink": (
        [
            ("sym", "s", "../outside"),
            ("hard", "h", "s/victim"),
            ("file", "h", b"overwritten"),
        ],
        ("s",),
    ),
    # tarfile CVE-2026-82049 / CVE-2026-11940: a hardlink to a symlink to a file
    # outside, which copies or links that file in (its contents disclosed) and
    # takes the member's mode and times onto it.
    "hardlink-to-symlink-to-file": (
        [("sym", "s", "../outside/victim"), ("hard", "h", "s")],
        ("s", "h"),
    ),
    # libarchive CVE-2016-5418: a hardlink to an absolute path, carrying data that
    # would be written through it. A hardlink target names an earlier member here,
    # never a path on disk, so the link fails as not found and nothing is written.
    "hardlink-absolute-with-data": ([("hard-data", "h", "{outside}/victim")], ()),
}

# A destination someone else prepared: a symlink out is already there, and the
# archive's members would go through it. This is how the second stage of the
# cases above (a deferred directory mode, a hardlink resolved through a link, a
# write under one) is reached, since no archive here can make that link itself.
_PLANTED_CASES: dict[str, tuple[dict[str, str], _Case]] = {
    # libarchive CVE-2021-31566: a directory's mode is applied at the end, through
    # what is by then a link out. The directory replaces the link instead.
    "deferred-mode-through-link": ({"d": "../outside"}, ([("dir", "d", "")], ())),
    # GNU tar CVE-2025-45582: a member written under a link out.
    "file-under-link": (
        {"d": "../outside"},
        ([("file", "d/victim", b"overwritten")], ("d/victim",)),
    ),
    # A member written over a link out replaces the link.
    "file-over-link": ({"s": "../outside/victim"}, ([("file", "s", b"new")], ())),
    # tarfile CVE-2025-4330, CVE-2026-82049: a hardlink to a link out, or through
    # one. A hardlink target must be an earlier member, so neither is followed.
    "hardlink-to-link": ({"s": "../outside/victim"}, ([("hard", "h", "s")], ())),
    "hardlink-through-link": ({"s": "../outside"}, ([("hard", "h", "s/victim")], ())),
}


def _fill(
    entries: list[tuple[str, str, str | bytes]], outside: Path
) -> list[tuple[str, str, str | bytes]]:
    """``entries`` with ``{outside}`` in names and targets filled in."""
    return [
        (kind, name.format(outside=outside), value.format(outside=outside))
        if isinstance(value, str)
        else (kind, name.format(outside=outside), value)
        for kind, name, value in entries
    ]


def _expected(case: _Case, outside: Path) -> dict[str, ExtractionStatus]:
    """The members ``case`` says a filter must refuse, all ``BLOCKED``."""
    return dict.fromkeys(
        (name.format(outside=outside) for name in case[1]), ExtractionStatus.BLOCKED
    )


@_POLICIES
@_MODES
@pytest.mark.parametrize("fmt", ["tar", "zip"])
@pytest.mark.parametrize("case", sorted(_NAME_CASES))
def test_name_that_leads_out(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, fmt: str, case: str
) -> None:
    outside = tmp_path / "outside"
    entries = _fill(_NAME_CASES[case][0], outside)
    archive = (_tar if fmt == "tar" else _zip)(entries)
    report = _extract_and_check(tmp_path, archive, policy=policy, streaming=streaming)
    if case in _REROOTED and policy is not ExtractionPolicy.STRICT:
        expected = {entries[0][1]: ExtractionStatus.EXTRACTED}
    else:
        expected = _expected(_NAME_CASES[case], outside)
    _assert_outcomes(report, expected)


@_POSIX_LINKS
@_POLICIES
@_MODES
@pytest.mark.parametrize("fmt", ["tar", "zip"])
@pytest.mark.parametrize("case", sorted(_LINK_CASES))
def test_link_that_leads_out(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, fmt: str, case: str
) -> None:
    outside = tmp_path / "outside"
    entries = _fill(_LINK_CASES[case][0], outside)
    archive = (_tar if fmt == "tar" else _zip)(entries)
    report = _extract_and_check(tmp_path, archive, policy=policy, streaming=streaming)
    if case == "symlink-backslash" and policy is ExtractionPolicy.TRUSTED:
        expected = {"dir": ExtractionStatus.EXTRACTED}
    else:
        expected = _expected(_LINK_CASES[case], outside)
    _assert_outcomes(report, expected)


@_POSIX_LINKS
@_POLICIES
@_MODES
@pytest.mark.parametrize("case", sorted(_TAR_ONLY_CASES))
def test_tar_link_that_leads_out(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, case: str
) -> None:
    outside = tmp_path / "outside"
    entries = _fill(_TAR_ONLY_CASES[case][0], outside)
    report = _extract_and_check(
        tmp_path, _tar(entries), policy=policy, streaming=streaming
    )
    _assert_outcomes(report, _expected(_TAR_ONLY_CASES[case], outside))
    if case == "hardlink-absolute-with-data":
        (outcome,) = report
        assert outcome.status is ExtractionStatus.FAILED
        assert isinstance(outcome.error, LinkTargetNotFoundError)


@_POSIX_LINKS
@_POLICIES
@_MODES
@pytest.mark.parametrize("case", sorted(_PLANTED_CASES))
def test_link_planted_in_the_destination(
    tmp_path: Path, policy: ExtractionPolicy, streaming: bool, case: str
) -> None:
    planted, expected = _PLANTED_CASES[case]
    report = _extract_and_check(
        tmp_path,
        _tar(expected[0]),
        policy=policy,
        streaming=streaming,
        planted=planted,
    )
    _assert_outcomes(report, _expected(expected, tmp_path / "outside"))
    dest = tmp_path / "dest"
    if case.startswith("hardlink"):
        (outcome,) = report
        assert outcome.status is ExtractionStatus.FAILED
        assert isinstance(outcome.error, LinkTargetNotFoundError)
    elif not expected[1]:
        # The member replaced the planted link.
        (name,) = planted
        assert not (dest / name).is_symlink()

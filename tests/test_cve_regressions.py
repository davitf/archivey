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
from pathlib import Path

import pytest

from archivey import ExtractionPolicy, ExtractionStatus, OnError, OverwritePolicy
from archivey.exceptions import FilterRejectionError
from tests.extract_util import open_and_extract

_POSIX_LINKS = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
_POLICIES = pytest.mark.parametrize(
    "policy", list(ExtractionPolicy), ids=lambda p: p.name
)
_MODES = pytest.mark.parametrize("streaming", [False, True], ids=["random", "stream"])


def _tar(entries: list[tuple[str, str, str | bytes]]) -> bytes:
    """A PAX tar of ``(kind, name, value)`` entries: ``file`` holds the bytes
    ``value``, ``dir`` ignores it, ``sym`` and ``hard`` link to the name ``value``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for kind, name, value in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(value, bytes)
                info.size = len(value)
                tf.addfile(info, io.BytesIO(value))
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


def _assert_no_link_leaves(dest: Path) -> None:
    """Every symlink under ``dest`` leads to something inside it. The kernel follows
    each link (``os.stat``), one component at a time, and the inode it lands on must
    be one the walk found under ``dest``; ``realpath`` would not do, since it has the
    blind spot CVE-2025-4517 used. A dangling link must name a path inside."""
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

"""Published vulnerabilities in other archive readers, replayed against archivey.

Each test rebuilds the archive structure a CVE or advisory describes and checks that
archivey keeps the property the other reader lost: nothing is written, linked or
changed outside the destination, and hostile structure ends in a typed error rather
than a hang. A test names the CVE it replays, so a failure points at the published
write-up of the attack.
"""

from __future__ import annotations

import io
import os
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
    """Every symlink under ``dest`` stays inside it, following links the way the
    kernel does: one at a time, from the link's own directory."""
    for parent, dirnames, filenames in os.walk(dest):
        for name in dirnames + filenames:
            path = os.path.join(parent, name)
            if not os.path.islink(path):
                continue
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fchdir(fd)
                assert Path(os.getcwd()).is_relative_to(dest.resolve()), path
            except NotADirectoryError:
                pass
            finally:
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

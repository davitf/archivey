"""Audit reproducers for the extraction coordinator (2026-09-28 security audit).

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` until the
bug is fixed; the ``reason`` names the defect. Triage is pending.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

import archivey
from archivey import ExtractionStatus


def _build_tar(path: Path, entries: list[tuple[str, str, object]]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, kind, extra in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(extra, bytes)
                info.size = len(extra)
                tf.addfile(info, io.BytesIO(extra))
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = str(extra)
                tf.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = str(extra)
                tf.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a symlink whose target runs through a path component created by a "
        "LATER member (l -> a/../secret, then a -> .) is re-validated only when it "
        "is created, so it is left on disk resolving outside the destination"
    ),
)
def test_symlink_made_escaping_by_a_later_member_is_not_left_on_disk(
    tmp_path: Path, streaming: bool
) -> None:
    (tmp_path / "secret").write_text("OUTSIDE")
    archive = tmp_path / "a.tar"
    _build_tar(archive, [("l", "sym", "a/../secret"), ("a", "sym", ".")])
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        reader.extract_all(dest, on_error="continue")

    # docs/extracting.md: "Escaping links are removed and rejected." After the run no
    # link under dest may resolve outside it.
    dest_root = dest.resolve()
    for root, dirs, files in os.walk(dest):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink():
                resolved = path.resolve()
                assert resolved == dest_root or resolved.is_relative_to(dest_root), (
                    f"{path} -> {os.readlink(path)} resolves to {resolved}"
                )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a hardlink to a DIRECTORY member fails with a misleading message "
        "('FILE member has no data stream' in random access, 'source was excluded' "
        "in streaming) although the directory was extracted"
    ),
)
@pytest.mark.parametrize("streaming", [False, True])
def test_hardlink_to_directory_member_reports_an_accurate_error(
    tmp_path: Path, streaming: bool
) -> None:
    archive = tmp_path / "a.tar"
    _build_tar(archive, [("d", "dir", None), ("h", "hard", "d")])

    with archivey.open_archive(archive, streaming=streaming) as reader:
        report = reader.extract_all(tmp_path / "out", on_error="continue")

    by_name = {r.member.name.rstrip("/"): r for r in report.results}
    assert by_name["d"].status is ExtractionStatus.EXTRACTED
    link = by_name["h"]
    assert link.status is ExtractionStatus.FAILED
    message = str(link.error)
    assert "excluded" not in message
    assert "FILE member" not in message
    assert "director" in message.lower()


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a symlink with an empty stored target reaches os.symlink('') and "
        "the archive-caused FileNotFoundError aborts a default extract() untyped"
    ),
)
def test_empty_symlink_target_is_not_an_untyped_os_error(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar"
    _build_tar(archive, [("s", "sym", ""), ("f", "file", b"data")])

    # docs/extracting.md: "genuine I/O errors propagate unchanged". An empty target is
    # a property of the archive, not of the filesystem, so it must be reported as a
    # typed outcome (LINK_TARGET_UNAVAILABLE or an ArchiveyError), not an OSError.
    report = archivey.extract(archive, tmp_path / "out")

    by_name = {r.member.name: r for r in report.results}
    link = by_name["s"]
    assert link.error is None or isinstance(link.error, archivey.ArchiveyError)
    assert by_name["f"].status is ExtractionStatus.EXTRACTED

"""The dry run's scratch tree: a private copy of the destination that a dry run extracts
into for real, and the translation of every path it reports back to the caller's dest.

``ExtractionCoordinator.run`` describes what a dry run shows and where it says less
than a real run would.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path, PurePath
from typing import BinaryIO, NamedTuple

from archivey.internal.filters import resolve_or_raise_on_loop
from archivey.internal.logs import extraction as logger
from archivey.internal.long_paths import long_path, plain
from archivey.types import ExtractionResult

# Prefix of the private scratch directory a dry run extracts into. Removed when the run
# ends, like the temp files atomic writes stage.
DRY_RUN_PREFIX = "archivey-dry-run-"


def scratch_tree(
    root: Path,
) -> tuple[tuple[tuple[str, bool], ...], tuple[tuple[str, str], ...]] | None:
    """Every entry under ``root``, as (its path relative to ``root`` with ``/``
    separators, whether it is a directory and not a symlink), and every symlink, as
    (that path, its target), each sorted; ``None`` when part of the tree could not be
    read.

    Directories are listed without following links. A directory that cannot be listed,
    or a link that cannot be read, makes the whole record ``None`` rather than leaving
    it out: the CLI's hoist keeps a tree it could not fully walk, and a partial record
    would let the prediction move it.
    """
    found = []
    links = []
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            # Listed in the long-path form, so a deep directory can be read; each
            # entry's path is named plain again to be placed under ``root``.
            with os.scandir(long_path(directory)) as entries:
                for entry in entries:
                    path = Path(plain(entry.path))
                    rel = path.relative_to(root).as_posix()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    found.append((rel, is_dir))
                    if entry.is_symlink():
                        links.append((rel, plain(os.readlink(entry.path))))
                    elif is_dir:
                        pending.append(path)
    except OSError:
        return None
    return tuple(sorted(found)), tuple(sorted(links))


def remove_scratch(root: Path) -> None:
    """Remove a dry run's scratch directory, whatever modes the archive gave its entries.

    A stored mode can leave a directory unwritable (or unreadable), and a file read-only,
    which Windows refuses to unlink. Each real entry is opened up first; symlinks are
    skipped, never followed. A failure is logged rather than raised: the run's own
    outcome is what the caller needs to see.
    """
    try:
        # In the long-path form throughout, so the walk can reach a deep entry.
        top = long_path(root)
        os.chmod(top, 0o700)
        for parent, dirnames, filenames in os.walk(top):
            for name in (*dirnames, *filenames):
                path = os.path.join(parent, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if name in dirnames else 0o600)
        shutil.rmtree(top)
    except OSError as exc:
        logger.warning(
            "Could not remove dry-run scratch directory %r: %s", str(root), exc
        )


class ScratchRecord(NamedTuple):
    """What a dry run's pass left in the scratch copy of dest (``DryRun.record``)."""

    # The entries directly under it: (name, whether a directory and not a symlink).
    top_level: tuple[tuple[str, bool], ...] | None
    # Every entry of the tree and every symlink with its target, by ``/``-separated
    # path (``scratch_tree``); recorded only when ``top_level`` holds one entry.
    entries: tuple[tuple[str, bool], ...] | None
    links: tuple[tuple[str, str], ...] | None


@dataclass
class DryRun:
    """Where a dry run extracts, and how its paths are shown under the caller's dest.

    Built by ``scratch_for`` for the length of one dry run.
    """

    # The directory the pass extracts into, inside a private scratch directory, and
    # the destination the caller named, which every path the run reports is
    # translated back to (``shown``).
    scratch: Path
    shown_dest: Path
    # The scratch directory spelled as the pass is handed it, and where a path under
    # its ``os.path.abspath`` spelling is shown. A real run reports paths built from
    # ``dest`` as given, but an OSError about a staging file names it as ``tempfile``
    # spells it, through ``os.path.abspath``: absolute, ``..`` collapsed, symlinks
    # kept. Where those two spellings of dest differ, the pass is handed a spelling
    # of the scratch directory that differs from its own ``abspath`` the same way, so
    # each path can be shown in the spelling a real run would give it.
    # (``dest.resolve()`` spells nothing a real run reports; it is only for matching
    # link targets, ``dest_spellings``.)
    scratch_given: Path
    shown_abspath: Path
    # The caller's destination as the absolute path they gave and as it resolves,
    # which an absolute link target is matched against (``target_on_disk``).
    dest_spellings: tuple[PurePath, PurePath]
    # Where FILE bodies go instead of the file (``os.devnull``).
    sink: BinaryIO

    def target_on_disk(self, target: str) -> str:
        """The target a link is created with: an absolute target that names a path
        under dest is rewritten to the same path under the scratch copy.

        Matched by name against dest as given and as it resolves, not resolved itself,
        so ``..`` components and symlinks on the way are left for the checks that
        resolve the link. Any other target is returned as it is.
        """
        pure = PurePath(target)
        if not pure.anchor:
            return target
        if not pure.drive:
            # Windows root-relative (a target like \x): pathlib joins it onto the
            # link's own drive, which is dest's. A no-op elsewhere, where drive is "".
            pure = PurePath(self.dest_spellings[0].drive + target)
        for spelling in self.dest_spellings:
            if pure.is_relative_to(spelling):
                return str(self.scratch / pure.relative_to(spelling))
        return target

    def shown(self, path: Path) -> Path:
        """``path`` as the caller sees it: a scratch path under their dest, spelled as
        a real run would spell it (see ``scratch_given``)."""
        for scratch, shown in (
            (self.scratch_given, self.shown_dest),
            (self.scratch, self.shown_abspath),
        ):
            try:
                return shown / path.relative_to(scratch)
            except ValueError:
                continue
        return path

    def rebase_result(self, result: ExtractionResult) -> ExtractionResult:
        if isinstance(result.error, OSError):
            self.rebase_os_error(result.error)
        return replace(
            result,
            path=None if result.path is None else self.shown(result.path),
            requested_path=(
                None
                if result.requested_path is None
                else self.shown(result.requested_path)
            ),
            collided_with=(
                None
                if result.collided_with is None
                else self.shown(result.collided_with)
            ),
        )

    def rebase_os_error(self, exc: OSError) -> None:
        """Point a filesystem error's file names at the caller's dest, without the
        long-path prefix, in place."""
        for attr in ("filename", "filename2"):
            value = getattr(exc, attr, None)
            if isinstance(value, str):
                setattr(exc, attr, str(self.shown(Path(plain(value)))))

    def record(self) -> ScratchRecord:
        """What the pass left in the scratch copy of dest, for ``ExtractionReport``:
        the entries directly under it, and, when there is exactly one, every entry and
        symlink of the tree (``scratch_tree``), which the CLI's hoist prediction walks.
        ``None`` for what could not be read."""
        top: tuple[tuple[str, bool], ...] | None = None
        tree = None
        with contextlib.suppress(OSError):
            with os.scandir(long_path(self.scratch)) as entries:
                top = tuple(
                    sorted(
                        (entry.name, entry.is_dir(follow_symlinks=False))
                        for entry in entries
                    )
                )
            if len(top) == 1:
                tree = scratch_tree(self.scratch)
        if tree is None:
            return ScratchRecord(top, None, None)
        return ScratchRecord(top, tree[0], tree[1])

    def check_creatable(self) -> None:
        """Raise what ``mkdir(parents=True)`` would raise for the absent ``shown_dest``,
        without creating it.

        The pass itself writes into the scratch directory, which exists already. Here
        the nearest part of dest that exists gets the questions ``mkdir`` would answer:
        that it can be resolved, is a directory, and can be written to. Each raises
        the error ``mkdir`` raises, naming the path it names. Whether it can be written
        to is asked with ``os.access``, which is a prediction, not the write: it checks
        the real user and group ids where ``mkdir`` uses the effective ones, the
        directory can change after it is asked, and on Windows it is not asked.
        """
        dest = self.shown_dest
        child, ancestor = dest, dest.parent
        while not os.path.lexists(long_path(ancestor)) and ancestor != ancestor.parent:
            child, ancestor = ancestor, ancestor.parent
        try:
            st = os.stat(long_path(ancestor))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                # A dangling symlink: mkdir meets it as an existing entry.
                raise FileExistsError(
                    errno.EEXIST, os.strerror(errno.EEXIST), str(ancestor)
                ) from None
            # ELOOP and the like, met while resolving dest itself.
            raise OSError(exc.errno, exc.strerror, str(dest)) from None
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(
                errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(dest)
            )
        if os.name == "nt":
            # os.access there reads the read-only attribute, which does not stop
            # creating entries in a directory.
            return
        if not os.access(long_path(ancestor), os.X_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(dest))
        if not os.access(long_path(ancestor), os.W_OK):
            # The first directory mkdir creates is the one it is refused.
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(child))


@contextlib.contextmanager
def scratch_for(dest: Path) -> Iterator[DryRun]:
    """A ``DryRun`` for ``dest``, its scratch copy created, and removed with everything
    under it when the block ends.

    The scratch copy is a directory named like dest, one level down in a private
    directory, so a relative link target that climbs out of dest by name
    (``../out/x``) comes back in. The private directory's path is resolved, so the
    pass's paths translate the same way whether they are built from the resolved dest
    (``ExtractionCoordinator._run``'s ``dest_root``) or from ``dest`` as given
    (macOS's /var -> /private/var).
    """
    given = Path(os.path.abspath(dest))
    try:
        resolved = resolve_or_raise_on_loop(given)
    except OSError:
        resolved = given
    scratch = Path(tempfile.mkdtemp(prefix=DRY_RUN_PREFIX)).resolve()
    work = scratch / (resolved.name or "dest")
    work_given = work
    detour: Path | None = None
    if str(dest) != str(given):
        if not dest.is_absolute():
            with contextlib.suppress(ValueError):  # Windows: on another drive
                work_given = Path(os.path.relpath(work))
        else:
            # Spelled with ``..``: so is the scratch directory, through a sibling.
            detour = scratch / ("_" if work.name != "_" else "__")
            work_given = detour / ".." / work.name
    try:
        with open(os.devnull, "wb") as sink:
            dry = DryRun(
                scratch=work,
                shown_dest=dest,
                scratch_given=work_given,
                shown_abspath=given if work_given != work else dest,
                dest_spellings=(given, resolved),
                sink=sink,
            )
            try:
                os.mkdir(long_path(work))
                if detour is not None:
                    os.mkdir(long_path(detour))
            except OSError as exc:
                dry.rebase_os_error(exc)
                raise
            yield dry
    finally:
        remove_scratch(scratch)

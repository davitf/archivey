"""The dry run's scratch tree: a private copy of the destination that a dry run extracts
into for real, and the translation of every path it reports back to the caller's dest.

The copy starts empty and is filled from dest as the pass goes (``DryRun.bring_in``):
each entry of dest that a member's path, link target or hardlink source names is copied
in before the pass looks at it, a file as an empty file and a symlink as a symlink.
dest itself is only read.

``ExtractionCoordinator.run`` describes what a dry run shows and where it says less
than a real run would.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath
from typing import BinaryIO, NamedTuple

from archivey.internal.filters import (
    resolve_or_raise_on_loop,
    strip_absolute_root,
)
from archivey.internal.logs import extraction as logger
from archivey.internal.long_paths import long_path, plain
from archivey.types import ArchiveMember, ExtractionResult, MemberType

# Prefix of the private scratch directory a dry run extracts into. Removed when the run
# ends, like the temp files atomic writes stage.
DRY_RUN_PREFIX = "archivey-dry-run-"

# How many symlinks ``DryRun.bring_in`` follows on one path, the bound Linux puts on a
# resolution (``MAXSYMLINKS``): past it the path does not resolve, so nothing further
# on it is looked at.
_MAX_LINK_HOPS = 40

# ``DryRun._walk`` components: restart at the scratch root, and stop (the path has left
# the scratch tree). No entry on disk has either name, as a name cannot hold a NUL; a
# member name that holds one is refused by ``check_universal``, and before that it can
# only restart or stop the walk inside the scratch tree.
_FROM_ROOT = "\0root"
_OUTSIDE = "\0outside"


# A name ``numbered_name`` can have spelled from another: ``a (2).txt`` from ``a.txt``,
# ``d (1)`` from ``d``. The name it came from is group 1 followed by group 3. A name
# that only looks numbered is matched too, which costs at most one extra copy.
_NUMBERED = re.compile(r"(.*) \(([1-9][0-9]*)\)(.*)", re.DOTALL)


def _dir_copy_mode(mode: int) -> int:
    """The mode of the scratch copy of a directory of dest that has ``mode``: the same
    bits, setgid and sticky included, plus the owner's read, write and search bits, so
    the copy can be filled and walked."""
    return stat.S_IMODE(mode) | stat.S_IRWXU


def _fold(name: str) -> str:
    """``name`` as a case-insensitive, normalizing filesystem compares it."""
    return unicodedata.normalize("NFC", name).casefold()


def _with_ancestors(into: set[str], rel: str) -> None:
    """Add ``rel``, a ``/``-separated path, and each of its ancestors to ``into``."""
    parts = rel.split("/")
    into.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))


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


class _Copy(NamedTuple):
    """An entry ``DryRun`` copied into the scratch tree from dest (``_copy_in``)."""

    # The entry of dest it copies.
    source: Path
    # The copy's inode when made, and whether it is a directory: a directory that
    # the pass removed and the run made again is not the copy (``DryRun._source_of``).
    ino: int
    is_dir: bool


class _Listing(NamedTuple):
    """The names in a directory of dest, read once (``DryRun._listing``)."""

    source: Path
    # Sorted, so the entry ``_bring_one`` copies is the same on every run.
    names: tuple[str, ...]
    present: frozenset[str]
    # Each name by its ``_fold`` key, and the names spelled from it as RENAME
    # spells a free name (``_NUMBERED``).
    folded: dict[str, str]
    numbered: dict[str, tuple[str, ...]]


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
    # What each scratch directory's counterpart in dest holds, once read; ``None``
    # for a directory that stands for none, or one that cannot be listed.
    _listings: dict[Path, _Listing | None] = field(default_factory=dict)
    # Each entry copied in from dest, by its scratch path (``_copy_in``).
    _copies: dict[Path, _Copy] = field(default_factory=dict)
    # The scratch paths whose counterpart in dest was looked for. Each is looked for
    # once, so an entry the pass removed is not copied in again.
    _considered: set[Path] = field(default_factory=set)

    def target_on_disk(self, target: str) -> str:
        """The target a link is created with: an absolute target that names a path
        under dest is rewritten to the same path under the scratch copy.

        Matched by name against dest as given and as it resolves. A target that
        spells dest some other way (``x/../out``, or through a symlink outside dest)
        is matched by the shortest leading part of it that resolves to dest, on the
        real filesystem: that part is outside dest, where a real run resolves it the
        same way. A component that does not exist is kept as a name and taken off by
        a later ``..``, as resolving the path does, so ``/srv/gone/../out/x`` matches
        too. The OS cannot follow such a target past ``gone``, so its rewrite passes
        through a name that does not exist either, in the private directory that holds
        the scratch copy (dest and its parents do not count: a real run creates them).
        What follows dest in the target is not resolved, so ``..`` components and
        symlinks there are left for the checks that resolve the link. Any other target
        is returned as it is.

        The cost is linear in the target's depth, which the archive chooses: the
        spellings are compared part by part, and the fallback resolves one component
        at a time onto the prefix resolved before it.
        """
        pure = PurePath(target)
        if not pure.anchor:
            return target
        if not pure.drive:
            # Windows root-relative (a target like \x): pathlib joins it onto the
            # link's own drive, which is dest's. A no-op elsewhere, where drive is "".
            pure = PurePath(self.dest_spellings[0].drive + target)
        parts = pure.parts
        for spelling in self.dest_spellings:
            # As ``is_relative_to`` compares (case folded on Windows), without its
            # walk over every parent of the target.
            leading = spelling.parts
            if len(parts) >= len(leading) and all(
                os.path.normcase(mine) == os.path.normcase(theirs)
                for mine, theirs in zip(parts, leading, strict=False)
            ):
                return str(self.scratch.joinpath(*parts[len(leading) :]))
        # ``resolved`` is canonical while ``missing`` is 0. Past a component that does
        # not exist it grows by name, as ``Path.resolve`` grows it, and the ``..`` that
        # takes the last such name off brings it back to the canonical prefix; only
        # steps that exist are resolved, each a resolved path plus one name.
        dest = self.dest_spellings[1]
        created = {dest, *dest.parents}
        resolved = PurePath(parts[0])
        missing = 0
        # Whether a missing component that a real run does not create (dest and its
        # parents) is on the way.
        detoured = False
        for end in range(2, len(parts) + 1):
            part = parts[end - 1]
            if missing and part == "..":
                resolved = resolved.parent
                missing -= 1
            elif missing:
                resolved = resolved / part
                missing += 1
            else:
                try:
                    step = os.path.join(resolved, part)
                    if os.path.lexists(long_path(step)):
                        resolved = PurePath(plain(os.path.realpath(long_path(step))))
                    else:
                        resolved = PurePath(step)
                        missing = 1
                except (OSError, ValueError):
                    return target
            if missing and part != ".." and resolved not in created:
                detoured = True
            if resolved != dest:
                continue
            if not detoured:
                return str(self.scratch.joinpath(*parts[end:]))
            # The OS does not take a missing name off with a later `..`: it stops
            # there, and a link through it leads nowhere, while the checks, which
            # resolve the path as ``Path.resolve`` does, find it inside dest. The
            # rewritten target keeps one missing name on the way, in the private
            # directory that holds the scratch copy, so both still hold.
            gap = "missing" if self.scratch.name != "missing" else "missing_"
            return str(
                self.scratch.parent.joinpath(gap, "..", self.scratch.name, *parts[end:])
            )
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

    def record(self, made: Iterable[Path]) -> ScratchRecord:
        """What the pass left in the scratch copy of dest, for ``ExtractionReport``:
        the entries directly under it, and, when there is exactly one, every entry and
        symlink of the tree (``scratch_tree``), which the CLI's hoist prediction walks.
        ``None`` for what could not be read.

        Only what the run made counts, as only that is in a real run's results.
        ``made`` is the coordinator's own record of it: every path it wrote or
        created, and every path an extracted result names (a directory member that
        merged into one of the caller's directories). An entry copied in from dest
        counts only when it is one of those or holds one."""
        copied = {rel for path in self._copies if (rel := self._rel(path))}
        ours: set[str] = set()
        for path in made:
            rel = self._rel(path)
            if rel:
                _with_ancestors(ours, rel)
        top: tuple[tuple[str, bool], ...] | None = None
        with contextlib.suppress(OSError):
            with os.scandir(long_path(self.scratch)) as entries:
                top = tuple(
                    sorted(
                        (entry.name, entry.is_dir(follow_symlinks=False))
                        for entry in entries
                        if entry.name not in copied or entry.name in ours
                    )
                )
        if top is None or len(top) != 1:
            return ScratchRecord(top, None, None)
        tree = scratch_tree(self.scratch)
        if tree is None:
            return ScratchRecord(top, None, None)
        entries, links = tree
        if copied:
            entries = tuple(e for e in entries if e[0] not in copied or e[0] in ours)
            links = tuple(e for e in links if e[0] not in copied or e[0] in ours)
        return ScratchRecord(top, entries, links)

    def _rel(self, path: Path) -> str:
        """``path``, a path of the pass in either spelling of the scratch directory,
        relative to it with ``/`` separators; ``""`` for the scratch directory itself
        or a path outside it."""
        for base in (self.scratch_given, self.scratch):
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                continue
            return "" if rel == "." else rel
        return ""

    # --- what dest holds already ---------------------------------------------------

    def bring_in(self, member: ArchiveMember) -> None:
        """Copy into the scratch tree the entries of dest that handling ``member`` can
        look at, so the pass meets them where a real run meets them in dest.

        ``member`` is the member as it reaches disk. Copied are the entry of dest at
        each component of its path, followed through symlinks as the OS resolves the
        path; the names a RENAME would try in place of each component
        (``numbered_name``); and, when the path names a directory, one entry of it, so
        the pass finds it not empty when it is not. The same is done along a
        symlink's target from the link's directory, and along a hardlink's source as
        the policy checks it, re-rooted. Each path is looked for once.
        """
        # Read first for every member: it gives the scratch root dest's mode, which a
        # `./` member, whose path has no component, keeps.
        self._listing(self.scratch)
        rel = PurePath(member.name.rstrip("/"))
        if rel.anchor:
            return  # ``check_universal`` refuses it, so nothing is placed
        self._walk(rel.parts, leaf=True)
        target = member.link_target
        if target is None:
            return
        if member.type is MemberType.HARDLINK:
            # ``_source_refused`` checks the source re-rooted, as a policy writes it.
            self._walk(PurePath(strip_absolute_root(target.rstrip("/"))).parts)
        elif member.type is MemberType.SYMLINK:
            on_disk = self._link_parts(self.target_on_disk(target))
            self._walk((*rel.parent.parts, *on_disk))

    def _link_parts(self, target: str) -> tuple[str, ...]:
        """The ``_walk`` components of a symlink of the scratch tree whose target is
        ``target``: from the link's directory, from the scratch root for an absolute
        target under it, and ``_OUTSIDE`` for any other absolute target."""
        pure = PurePath(target)
        if not pure.anchor:
            return pure.parts
        try:
            return (_FROM_ROOT, *pure.relative_to(self.scratch).parts)
        except ValueError:
            return (_OUTSIDE,)

    def _walk(self, parts: tuple[str, ...], *, leaf: bool = False) -> None:
        """Follow ``parts`` from the scratch root as the OS resolves a path, copying in
        the entry of dest at each component first (``_bring``). It stops where the
        path meets a file or nothing, or leaves the scratch tree. Above the scratch
        root it can only come back in by dest's name (``../out/x``), as the scratch
        root is the one entry there named like dest (``scratch_for``).

        With ``leaf``, ``parts`` is a member's own path: when it ends on a directory,
        one entry of it is copied in (``_bring_one``). A symlink on the way there is
        followed as the pass follows it; one at the last component ends the probe,
        as the pass meets the link itself there."""
        pending = list(reversed(parts))
        current = self.scratch
        above = self.scratch.parent
        ends_on_directory = False
        hops = 0
        while pending:
            part = pending.pop()
            ends_on_directory = False
            if part == _FROM_ROOT:
                current = self.scratch
                continue
            if part == _OUTSIDE or (current == above and part != self.scratch.name):
                return
            if part == "..":
                current = current.parent
                continue
            if current != above:
                self._bring(current, part)
            path = current / part
            try:
                st = os.lstat(long_path(path))
            except (OSError, ValueError):  # missing, or a name the OS does not take
                return
            if stat.S_ISDIR(st.st_mode):
                current = path
                ends_on_directory = True
                continue
            hops += 1
            if not pending:
                # The path's last component is a symlink: the pass meets the link
                # there, never a directory to find empty. A symlink on the way to
                # the last component leaves the probe on (``a -> sub``, ``a/b``).
                leaf = False
            if not stat.S_ISLNK(st.st_mode) or hops > _MAX_LINK_HOPS:
                return
            try:
                target = plain(os.readlink(long_path(path)))
            except OSError:
                return
            pending.extend(reversed(self._link_parts(target)))
        if leaf and ends_on_directory:
            self._bring_one(current)

    def _bring(self, directory: Path, name: str) -> None:
        """Copy in the entry of dest that ``directory / name`` stands for, once, with
        the names a RENAME would try in its place (``_NUMBERED``).

        Looked for as the caller's filesystem looks: a name that dest finds under
        another case or normalization is copied under the name dest holds it by."""
        path = directory / name
        if path in self._considered:
            return
        self._considered.add(path)
        listing = self._listing(directory)
        if listing is None:
            return
        held = name if name in listing.present else listing.folded.get(_fold(name))
        if held is None or (
            held != name and not os.path.lexists(long_path(listing.source / name))
        ):
            return
        for candidate in (held, *listing.numbered.get(held, ())):
            self._copy_named(directory, listing, candidate)

    def _bring_one(self, directory: Path) -> None:
        """Copy in one entry of the directory of dest that ``directory`` stands for,
        when the scratch copy holds none: REPLACE removes a directory only when it is
        empty, and one entry is enough to show it is not."""
        listing = self._listing(directory)
        if listing is None or not listing.names:
            return
        try:
            with os.scandir(long_path(directory)) as entries:
                if next(entries, None) is not None:
                    return
        except OSError:
            return
        for name in listing.names:
            if directory / name not in self._considered:
                self._copy_named(directory, listing, name)
                return

    def _copy_named(self, directory: Path, listing: _Listing, name: str) -> None:
        """Copy ``listing``'s entry ``name`` to ``directory / name``, unless that path
        was looked for already or is taken."""
        path = directory / name
        if path in self._copies:
            return
        self._considered.add(path)
        if not os.path.lexists(long_path(path)):
            self._copy_in(listing.source / name, path)

    def _listing(self, directory: Path) -> _Listing | None:
        """The names in the directory of dest that ``directory`` stands for
        (``_source_of``), read once."""
        if directory in self._listings:
            return self._listings[directory]
        listing = None
        source = self._source_of(directory)
        if source is not None:
            try:
                names = sorted(os.listdir(long_path(source)))
            except OSError as exc:
                # The pass then meets none of what it holds.
                logger.debug("Dry run could not list %r: %s", str(source), exc)
            else:
                numbered: dict[str, list[str]] = {}
                for name in names:
                    found = _NUMBERED.fullmatch(name)
                    if found is not None:
                        base = found.group(1) + found.group(3)
                        numbered.setdefault(base, []).append(name)
                listing = _Listing(
                    source,
                    tuple(names),
                    frozenset(names),
                    {_fold(name): name for name in names},
                    {base: tuple(found) for base, found in numbered.items()},
                )
        self._listings[directory] = listing
        return listing

    def _source_of(self, directory: Path) -> Path | None:
        """The directory of dest that the scratch directory ``directory`` stands for:
        dest for the scratch root, and for a directory copied in from dest the one it
        copies, while the pass has left it in place. A directory the run made stands
        for none."""
        if directory == self.scratch:
            try:
                st = os.stat(long_path(self.shown_dest))
            except (OSError, ValueError):
                return None
            if not stat.S_ISDIR(st.st_mode):
                return None
            # A `./` member keeps dest's mode, and reports it when it differs from
            # the member's own; the scratch root is given that mode.
            try:
                os.chmod(long_path(directory), _dir_copy_mode(st.st_mode))
            except OSError as exc:
                logger.debug("Dry run could not set the root's mode: %s", exc)
            return self.shown_dest
        copy = self._copies.get(directory)
        if copy is None or not copy.is_dir:
            return None
        try:
            st = os.lstat(long_path(directory))
        except OSError:
            return None
        if not stat.S_ISDIR(st.st_mode) or st.st_ino != copy.ino:
            return None
        return copy.source

    def _copy_in(self, source: Path, path: Path) -> None:
        """Create at ``path`` a copy of ``source``, an entry of dest: a directory, with
        its entries left for later; an empty file with the file's mode; or a symlink
        with its target, rewritten as ``target_on_disk`` rewrites a link member's.

        A directory's copy gets the owner's read, write and search bits added to its
        mode (``_dir_copy_mode``). Anything that is not a directory or a symlink (a
        FIFO, a device) is copied as an empty file, as the pass treats each such entry
        as a file. So is a symlink the OS will not create (Windows without the
        privilege). An entry that cannot be copied at all (it went away, the scratch
        filesystem is full, its name is too long there) is left out, so the pass
        meets nothing there, as it does in a directory that cannot be listed."""
        try:
            st = os.lstat(long_path(source))
            isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
            if stat.S_ISLNK(st.st_mode) or (
                isjunction is not None and isjunction(long_path(source))
            ):
                if not self._copy_link(source, path):
                    open(long_path(path), "xb").close()
            elif stat.S_ISDIR(st.st_mode):
                os.mkdir(long_path(path))
                os.chmod(long_path(path), _dir_copy_mode(st.st_mode))
            else:
                open(long_path(path), "xb").close()
                os.chmod(long_path(path), stat.S_IMODE(st.st_mode))
            made = os.lstat(long_path(path))
        except OSError as exc:
            logger.debug("Dry run could not copy %r: %s", str(source), exc)
            with contextlib.suppress(OSError):
                if os.path.isdir(long_path(path)) and not os.path.islink(
                    long_path(path)
                ):
                    os.rmdir(long_path(path))
                elif os.path.lexists(long_path(path)):
                    os.unlink(long_path(path))
            return
        self._copies[path] = _Copy(source, made.st_ino, stat.S_ISDIR(made.st_mode))

    def _copy_link(self, source: Path, path: Path) -> bool:
        """Create at ``path`` a symlink with ``source``'s target; whether it was
        made."""
        try:
            target = plain(os.readlink(long_path(source)))
            os.symlink(
                self.target_on_disk(target),
                long_path(path),
                target_is_directory=os.path.isdir(long_path(source)),
            )
        except OSError as exc:
            logger.debug("Dry run copies symlink %r as a file: %s", str(source), exc)
            return False
        return True

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

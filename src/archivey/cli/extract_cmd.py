"""``extract`` / ``x`` verb."""

from __future__ import annotations

import contextlib
import os
import stat
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import TextIO

from archivey import (
    AbortOn,
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionReport,
    ExtractionResult,
    ExtractionStatus,
    NameRewrite,
    OnError,
    OverwritePolicy,
)
from archivey.cli.choices import from_cli_choice
from archivey.cli.common import (
    open_for_cli,
    reject_empty_path,
)
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_POLICY
from archivey.cli.filters import MemberSelection
from archivey.cli.format import escape_member_name, escape_path, format_error_detail
from archivey.cli.password import resolve_password
from archivey.cli.progress import ProgressCallback, make_progress_callback
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError
from archivey.paths import numbered_name
from archivey.reader import ForwardArchiveReader
from archivey.types import (
    ArchiveFormat,
    ArchiveMember,
    ContainerFormat,
    MemberType,
)


def _archive_stem(path: Path, *, format: ArchiveFormat) -> str:
    """Stem used for the smart enclosing directory.

    Prefer the format's canonical extension (covers ``.tar.Z``, ``.tzst``, …); fall
    back to stripping a final suffix and a remaining ``.tar``. Never return empty,
    ``.`` or ``..``: the result becomes a destination path, so an empty stem (a file
    named exactly ``.tar.gz``) or ``.`` (``..zip``) would splatter into the cwd, and
    ``..`` (``...zip``, ``...tar.gz``) would write into the parent directory. Those
    names give ``"archive"`` instead.
    """
    name = path.name
    ext = format.file_extension()
    if ext:
        suffix = f".{ext}"
        if name.lower().endswith(suffix.lower()):
            return _safe_stem(name[: -len(suffix)])
    stem, suffix = _split_suffix(name)
    if suffix:
        stem, suffix = _split_suffix(stem)
        if suffix.lower() != ".tar":
            stem += suffix
    return _safe_stem(stem)


def _split_suffix(name: str) -> tuple[str, str]:
    """Split ``name`` into stem and final suffix, the same on every Python version.

    ``pathlib`` changed in 3.14 to skip all leading dots before looking for a suffix
    (``Path("...bin").suffix`` is ``".bin"`` up to 3.13 and ``""`` from 3.14), so it
    would name the wrapper folder differently by interpreter. This keeps the 3.11-3.13
    rule: a suffix starts at the last dot, which is not the first or last character.
    """
    i = name.rfind(".")
    if 0 < i < len(name) - 1:
        return name[:i], name[i:]
    return name, ""


def _safe_stem(stem: str) -> str:
    """``stem``, or ``"archive"`` when it is not a usable single-segment name."""
    return "archive" if stem in ("", ".", "..") else stem


def _top_level_names(members: list[ArchiveMember]) -> set[str]:
    tops: set[str] = set()
    for member in members:
        name = member.name.strip("/")
        if not name:
            continue
        tops.add(name.split("/", 1)[0])
    return tops


def _enclosing_dir(archive: Path, *, format: ArchiveFormat) -> Path:
    """The wrapper folder named after the archive: a name nothing has yet (D1).

    The CLI picks this folder on the user's behalf, so it is always one this run
    creates. Anything already at the stem name (a directory, a file, a symlink,
    dangling or live) makes the name taken under every overwrite policy, and the
    next free ``stem (N)`` is used. A user who wants to extract into an existing
    directory, or through a link, names it with ``-d``. Probes use ``lexists`` so a
    dangling link counts as present, matching :func:`_free_name`.
    """
    dest = Path(_archive_stem(archive, format=format))
    if not os.path.lexists(dest):
        return dest
    return _free_name(dest, is_dir=True)


def smart_dest(
    archive: Path,
    *,
    format: ArchiveFormat,
    members: list[ArchiveMember],
) -> Path:
    """Anti-tarbomb dest from an indexed member list (tops may already be filtered)."""
    if format.container == ContainerFormat.RAW_STREAM:
        return Path(".")
    tops = _top_level_names(members)
    if len(tops) <= 1:
        return Path(".")
    return _enclosing_dir(archive, format=format)


@dataclass(frozen=True)
class _SmartDestPlan:
    """Where to extract, and whether a post-extract single-root hoist may run."""

    target: Path
    may_hoist: bool


def resolve_smart_dest(
    reader: ForwardArchiveReader,
    archive: Path,
    *,
    pred: Callable[[ArchiveMember], bool] | None,
) -> _SmartDestPlan:
    """Choose the default dest without forcing a streaming metadata pass (D1).

    - Single-file / raw-stream → cwd.
    - Indexed archive → tops on the **filtered** member set (wrap / cwd).
    - No cheap index (tar, an archive read from a pipe, …) → always a new
      ``./<stem>/``, then :func:`maybe_hoist_single_root` may lift a single
      extracted top entry to cwd.
    """
    fmt = reader.format
    if fmt.container == ContainerFormat.RAW_STREAM:
        return _SmartDestPlan(Path("."), may_hoist=False)

    indexed = reader.members_report_if_available()
    if indexed is None:
        # The wrapper is always new, so its content is all this run's.
        return _SmartDestPlan(_enclosing_dir(archive, format=fmt), may_hoist=True)

    members = [m for m in indexed if pred is None or pred(m)]
    return _SmartDestPlan(
        smart_dest(archive, format=fmt, members=members),
        may_hoist=False,
    )


@dataclass
class _HoistResult:
    """Outcome of :func:`maybe_hoist_single_root` for reporting and exit code."""

    # Where the content is: the wrapper when not hoisted. Read only by
    # :func:`maybe_hoist_single_root`, for its own closing line.
    target: Path
    # Terminal-safe summary destination when the hoist decided it; ``None`` leaves it
    # to :func:`_summary_dest_label`.
    dest_label: str | None = None
    ok: bool = True  # False → collision/failure; caller exits nonzero
    renamed: int = 0
    skipped: int = 0
    # What the move did to each entry, so the report can name each member where it is
    # now; ``None`` when nothing moved.
    moves: _Moves | None = None


@dataclass(frozen=True)
class _Moves:
    """Where the hoist put each entry it moved out of the wrapper.

    ``names`` maps an entry's name in the wrapper (``/``-separated) to its name in the
    wrapper's parent after the move. A name that is not a key moved with its nearest
    ancestor that is. ``discarded`` holds the keys whose entry ``SKIP`` unlinked
    rather than moved: their ``names`` value is the operator's entry that was kept,
    not the member. ``left_in`` is the wrapper's name when the hoist stopped
    part-way: a name under no key is still in the wrapper.
    """

    names: dict[str, str]
    discarded: frozenset[str] = frozenset()
    left_in: str | None = None

    def place(self, relative: str) -> tuple[str, bool]:
        """``relative``'s name after the move, and whether the move discarded it."""
        parts = relative.split("/")
        for end in range(len(parts), 0, -1):
            key = "/".join(parts[:end])
            moved = self.names.get(key)
            if moved is not None:
                return "/".join([moved, *parts[end:]]), key in self.discarded
        if self.left_in is not None:
            return f"{self.left_in}/{relative}", False
        return relative, False

    @classmethod
    def of(
        cls,
        moved: dict[str, tuple[Path, bool]],
        wrapper: Path,
        *,
        stopped: bool = False,
    ) -> _Moves:
        """What the hoist recorded in ``moved``, named from ``wrapper``'s
        parent; ``stopped`` when the merge did not finish."""
        return cls(
            {
                name: path.relative_to(wrapper.parent).as_posix()
                for name, (path, _) in moved.items()
            },
            frozenset(name for name, (_, gone) in moved.items() if gone),
            wrapper.name if stopped else None,
        )


class _HoistConflict(Exception):
    """A collision the overwrite policy cannot resolve without deleting data."""

    def __init__(self, dest: Path) -> None:
        super().__init__(str(dest))
        self.dest = dest


def _free_name(dest: Path, *, is_dir: bool) -> Path:
    """First ``name (N)`` free on disk, spelled as extraction spells its renames."""
    n = 1
    while True:
        candidate = dest.parent / numbered_name(dest.name, n, is_dir=is_dir)
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def _open_up(path: Path) -> int | None:
    """Give the directory at ``path`` owner read, write and search permission for a
    move; return the mode to put back, or ``None`` when nothing was changed (it is
    not a directory, or already has them).

    Extraction applies a directory's stored mode when the run ends, so a hoisted
    directory can be ``0o555``. Moving entries out of it needs write permission on
    it, and so does moving it to another parent, because its ``..`` entry changes.
    Without this, a non-root user's hoist failed where tar would have extracted.
    """
    st = os.lstat(path)
    mode = stat.S_IMODE(st.st_mode)
    if not stat.S_ISDIR(st.st_mode) or mode & stat.S_IRWXU == stat.S_IRWXU:
        return None
    os.chmod(path, mode | stat.S_IRWXU)
    return mode


def _put_back(path: Path, mode: int | None) -> None:
    """Restore the mode :func:`_open_up` changed, at the directory's current path."""
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(path, mode)


def _rename(src: Path, dest: Path) -> None:
    """``os.rename``, for a directory too that lacks owner write (:func:`_open_up`).
    The directory keeps its mode."""
    mode = _open_up(src)
    try:
        os.rename(src, dest)
    except BaseException:
        _put_back(src, mode)
        raise
    _put_back(dest, mode)


def _merge_move(
    src: Path,
    dest: Path,
    overwrite: OverwritePolicy,
    result: _HoistResult,
    err: TextIO,
    name: str,
    moved: dict[str, tuple[Path, bool]],
) -> Path | None:
    """Move ``src`` to ``dest`` with the same per-file semantics as extracting
    directly into ``dest``'s parent: directories merge, file/symlink collisions
    resolve by the overwrite policy. Pre-existing files are never deleted — the
    only removal is our own just-extracted copy under SKIP, which a direct
    extraction would never have written. Symlinks are moved as links and never
    descended into (on either side).

    ``name`` is ``src``'s name in the wrapper. Each entry moved, merged or discarded
    is recorded in ``moved`` under its name in the wrapper, with the path it took (the
    operator's path that was kept, for a discard) and whether it was discarded. A
    merged directory is recorded once all of it has moved, so a name under one the
    merge stopped in is still in the wrapper.

    Returns where ``src`` landed — ``dest``, or the free name a rename chose — and
    ``None`` when SKIP discarded it."""
    if not os.path.lexists(dest):
        _rename(src, dest)
        moved[name] = (dest, False)
        return dest
    src_is_dir = src.is_dir() and not src.is_symlink()
    dest_is_dir = dest.is_dir() and not dest.is_symlink()
    if src_is_dir and dest_is_dir:
        # ``dest`` keeps its own mode, as a directory that was already there does
        # when extracting into it, and the line is the one extraction prints then.
        # ``src`` holds the archive's mode as extraction applied it, which is the
        # mode the library compares for ``-d .``: the member's, as the platform
        # stores it (on Windows only the read-only attribute, ``0o777`` or
        # ``0o555``; see ``_dir_mode_as_stored``).
        kept = stat.S_IMODE(os.stat(dest).st_mode)
        if kept != stat.S_IMODE(os.lstat(src).st_mode):
            print(
                f"kept existing directory's mode {kept:04o}: {escape_path(dest)}",
                file=err,
            )
        # ``src`` is opened up only to empty it.
        mode = _open_up(src)
        try:
            for entry in sorted(src.iterdir()):
                _merge_move(
                    entry,
                    dest / entry.name,
                    overwrite,
                    result,
                    err,
                    f"{name}/{entry.name}",
                    moved,
                )
            moved[name] = (dest, False)
            src.rmdir()
        except BaseException:
            _put_back(src, mode)
            raise
        return dest
    if overwrite is OverwritePolicy.RENAME:
        free = _free_name(dest, is_dir=src_is_dir)
        _rename(src, free)
        moved[name] = (free, False)
        result.renamed += 1
        print(
            f"renamed: {escape_path(dest)} -> {escape_path(free)}",
            file=err,
        )
        return free
    if overwrite is OverwritePolicy.REPLACE and not src_is_dir and not dest_is_dir:
        os.replace(src, dest)  # replaces exactly the file being extracted
        moved[name] = (dest, False)
        return dest
    if overwrite is OverwritePolicy.SKIP and not src_is_dir:
        src.unlink()
        moved[name] = (dest, True)
        result.skipped += 1
        print(f"skipped: {escape_path(dest)}", file=err)
        return None
    # ERROR policy — or a dir-vs-file shape that REPLACE/SKIP cannot express
    # without deleting pre-existing data. Stop; the caller keeps the remainder
    # under the wrapper and exits nonzero (direct extraction would have failed
    # on this same collision).
    raise _HoistConflict(dest)


_MAX_LINK_HOPS = 40  # the Linux ``MAXSYMLINKS``


def _is_absolute_target(target: str) -> bool:
    """Whether a symlink target is absolute, on either separator (a drive letter
    counts)."""
    return target[:1] in ("/", "\\") or target[1:2] == ":"


def _readlink_on_disk(path: PurePath) -> str | None:
    """``path``'s target when it is a symlink on disk, else ``None``."""
    return os.readlink(path) if Path(path).is_symlink() else None


def _walk_stays_inside(
    start: PurePath,
    target: str,
    root: PurePath,
    hops: list[int],
    readlink: Callable[[PurePath], str | None] = _readlink_on_disk,
) -> PurePath | None:
    """Follow ``target`` from the directory ``start`` one component at a time, through
    any symlink on the way, and return where it ends; ``None`` when a step leaves
    ``root`` or reaches ``root``'s parent. ``hops`` is the symlink budget left, shared
    by the nested walks. ``readlink`` says whether a path is a symlink and where it
    points: the disk by default, a dry run's record of its scratch tree otherwise."""
    if _is_absolute_target(target):
        return None
    current = start
    for part in target.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if current == root:
                return None
            current = current.parent
            continue
        step = current / part
        try:
            inner = readlink(step)
        except OSError:
            return None
        if inner is not None:
            hops[0] -= 1
            if hops[0] < 0:
                return None
            landed = _walk_stays_inside(current, inner, root, hops, readlink)
            if landed is None:
                return None
            current = landed
        else:
            current = step
    return current


def _disk_links(root: Path) -> Iterator[tuple[PurePath, str]]:
    """Each symlink under ``root`` with its target, listed from disk as it is read."""
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    yield Path(entry.path), os.readlink(entry.path)
                elif entry.is_dir():
                    pending.append(Path(entry.path))


def _links_stay_inside(
    root: PurePath,
    links: Iterable[tuple[PurePath, str]],
    readlink: Callable[[PurePath], str | None] = _readlink_on_disk,
) -> bool:
    """Whether every symlink under ``root`` reaches its target without leaving ``root``.

    The hoist moves ``root`` one level up after extraction checked its links against
    the wrapper. A link whose path stays inside ``root`` at every step means the same
    thing after the move, because the whole tree moves together. A step above ``root``
    is where the move changes the meaning: ``wrapper/top/k ->
    ../../.ssh/authorized_keys`` stays inside the wrapper when the archive is named
    ``.ssh.tar``, and names the operator's own ``.ssh`` once ``top`` is moved up. Even
    one step up into the wrapper and back down can land on a name the working directory
    has and the wrapper did not, so that blocks too.

    The walk follows each symlink on the way, so a chain cannot hide a climb, and an
    absolute target always blocks. A path that ends at nothing is walked by name. A
    directory that cannot be listed blocks too: what is under it was not checked.

    ``links`` is every symlink under ``root`` with its target, and ``readlink`` answers
    each step from the same source. An ``OSError`` raised while ``links`` is listed
    blocks: so a disk listing must be passed unconsumed, not as a list built first.
    """
    try:
        return all(
            _walk_stays_inside(path.parent, target, root, [_MAX_LINK_HOPS], readlink)
            is not None
            for path, target in links
        )
    except OSError:
        # A directory the walk cannot list could hold anything: keep the tree
        # where it is rather than move what was not looked at.
        return False


def _disk_links_stay_inside(root: Path) -> bool:
    """:func:`_links_stay_inside` for a tree on disk, listed and read as it is walked."""
    return _links_stay_inside(root, _disk_links(root))


def _recorded_links_stay_inside(top: str, links: tuple[tuple[str, str], ...]) -> bool:
    """:func:`_links_stay_inside` for a dry run, over the symlinks its scratch tree
    held (``ExtractionReport._dry_run_links``) rather than a tree on disk: the same
    walk, with a path a symlink exactly when the record lists it."""
    targets: dict[PurePath, str] = {
        PurePosixPath(name): target for name, target in links
    }
    root = PurePosixPath(top)
    under = ((path, t) for path, t in targets.items() if root in path.parents)
    return _links_stay_inside(root, under, targets.get)


def _keep_reason(is_symlink: bool, links_leave: Callable[[], bool]) -> str | None:
    """Why the hoist leaves the wrapper's single entry in place, or ``None`` to move it.

    ``links_leave`` says whether a symlink in the entry may leave it on the way to its
    target, which includes a tree that could not be fully walked
    (:func:`_links_stay_inside`); it runs only when nothing earlier settled it.
    """
    if is_symlink:
        # A link's relative target is read from its own directory, which the move
        # changes from the wrapper to the working directory: `b -> passwd` would then
        # name the operator's own `passwd`, and `b -> ../etc/passwd` one outside it.
        return "its only entry is a symlink, which the move would repoint"
    if links_leave():
        return (
            "it may hold a symlink that points outside it, which a move would repoint"
        )
    return None


def maybe_hoist_single_root(
    wrapper: Path,
    *,
    overwrite: OverwritePolicy,
    err: TextIO,
) -> _HoistResult:
    """If ``wrapper`` holds exactly one top-level entry, lift it to cwd (R4/D1).

    The entry stays in the wrapper, and a line says why, for the reasons
    :func:`_keep_reason` gives.

    Recovers unar-style single-root reuse (and filter-aware D1 for streaming)
    after an always-wrap extract, without a pre-extract metadata pass. The final
    layout matches extracting directly into the wrapper's parent: directories
    merge into existing ones and per-file collisions follow the overwrite policy
    (:func:`_merge_move`). When the sole root shares the wrapper's own name
    (``src.tar.gz`` containing ``src/``), the child is flattened in place — the
    wrapper *becomes* the root, so no collision logic applies to it.
    """
    if wrapper == Path(".") or wrapper.is_symlink() or not wrapper.is_dir():
        return _HoistResult(wrapper)
    try:
        children = list(wrapper.iterdir())
    except OSError:
        return _HoistResult(wrapper)
    if len(children) != 1:
        return _HoistResult(wrapper)
    child = children[0]
    reason = _keep_reason(
        child.is_symlink(),
        lambda: child.is_dir() and not _disk_links_stay_inside(child),
    )
    if reason is not None:
        print(f"kept in {escape_path(wrapper)}/: {reason}", file=err)
        return _HoistResult(wrapper)
    dest = wrapper.parent / child.name
    result = _HoistResult(dest)
    moved: dict[str, tuple[Path, bool]] = {}
    # The sole root shares the wrapper's name, so the wrapper is removed and the root
    # takes its place under that same name; otherwise the root is merged into the cwd.
    in_place = dest == wrapper
    try:
        if in_place:
            if child.is_dir() and not child.is_symlink():
                # Flatten: wrapper/src/* → wrapper/*. The wrapper held only this
                # child, so the moves cannot collide. The wrapper takes the child's
                # place, so it takes the child's mode and times too.
                st = os.stat(child)
                mode = _open_up(child)
                try:
                    for entry in sorted(child.iterdir()):
                        up = wrapper / entry.name
                        _rename(entry, up)
                        # Recorded for a failure part-way: an entry already moved is
                        # named where it is, and ``left_in`` covers only the rest.
                        moved[f"{child.name}/{entry.name}"] = (up, False)
                    child.rmdir()
                except BaseException:
                    _put_back(child, mode)
                    raise
                with contextlib.suppress(OSError):  # best-effort, as in extraction
                    os.chmod(wrapper, stat.S_IMODE(st.st_mode))
                    os.utime(wrapper, ns=(st.st_atime_ns, st.st_mtime_ns))
            else:
                # Sole non-dir entry named like the wrapper: step the wrapper
                # aside so the entry can take its place.
                side = _free_name(wrapper, is_dir=True)
                wrapper.rename(side)
                (side / child.name).rename(dest)
                side.rmdir()
        else:
            # The wrapper took the first free name for the archive's stem, which can
            # be the free name a rename of the root would take (``foo`` exists, so
            # ``foo.tar`` extracts into ``foo (1)`` and its root ``foo`` must be
            # renamed). Step it aside so the merge sees the cwd a direct extraction
            # sees. The aside name is ``<wrapper> (N)``, which no rename of the root
            # spells, as the root's name differs from the wrapper's here.
            side = _free_name(wrapper, is_dir=True)
            wrapper.rename(side)
            try:
                landed = _merge_move(
                    side / child.name, dest, overwrite, result, err, child.name, moved
                )
            except BaseException:
                # Leave the remainder where the messages say it is.
                with contextlib.suppress(OSError):
                    side.rename(wrapper)
                raise
            side.rmdir()
            result.moves = _Moves.of(moved, wrapper)
            if landed is None:
                # SKIP kept the operator's entry and discarded ours; ``skipped:``
                # already said so, and nothing was moved anywhere. ``dest`` is the
                # operator's own entry, so the summary may not name it.
                result.dest_label = "."
                return result
            result.target = landed
    except _HoistConflict as conflict:
        print(
            f"Destination already exists: {escape_path(conflict.dest)}",
            file=err,
        )
        print(
            f"hoist stopped; remaining files left in {escape_path(wrapper)}/",
            file=err,
        )
        return _HoistResult(
            wrapper,
            ok=False,
            renamed=result.renamed,
            skipped=result.skipped,
            moves=_Moves.of(moved, wrapper, stopped=True),
        )
    except OSError as exc:
        # The ``archivey: `` prefix: see main._parse_and_dispatch.
        print(f"archivey: hoist failed: {format_error_detail(exc)}", file=err)
        print(f"files left in {escape_path(wrapper)}/", file=err)
        return _HoistResult(
            wrapper,
            ok=False,
            renamed=result.renamed,
            skipped=result.skipped,
            moves=_Moves.of(moved, wrapper, stopped=True),
        )
    if result.moves is None:
        result.moves = _Moves({child.name: child.name})  # in place: name unchanged
    is_dir = result.target.is_dir() and not result.target.is_symlink()
    # The target is the sole root's own name, which the archive chose.
    label = f"{escape_path(result.target)}{'/' if is_dir else ''}"
    result.dest_label = label
    if in_place:
        # src.tar → src/ containing src/: the wrapper is gone, the name unchanged.
        print(f"removed wrapper; content at {label}", file=err)
    else:
        print(f"moved to {label}", file=err)
    return result


def predict_hoist(
    wrapper: Path,
    report: ExtractionReport,
    *,
    err: TextIO,
) -> _HoistResult:
    """What :func:`maybe_hoist_single_root` would do after a real run, for a dry run.

    A dry run writes nothing, so there is no wrapper to look into. Its scratch tree is
    the next best thing: the run created the same directories, links and (empty) files
    there, and the report carries the entries it left at the top. A single entry is
    lifted to the wrapper's parent under its own name, as the hoist lifts it. Where that
    name exists already, the hoist would merge into it, and the collisions that merge
    could meet are not checked. The entry stays in the wrapper for the reasons the hoist
    has, judged from the symlinks the scratch tree held, and a line says why.
    """
    tops = _dry_run_top_level(report)
    if len(tops) != 1:
        return _HoistResult(wrapper)
    ((name, is_dir),) = tops
    links = report._dry_run_links
    # ``links`` is ``None`` when part of the scratch tree could not be read, and the
    # hoist keeps a tree it cannot fully walk.
    reason = _keep_reason(
        links is not None and any(path == name for path, _ in links),
        lambda: (
            links is None or (is_dir and not _recorded_links_stay_inside(name, links))
        ),
    )
    if reason is not None:
        print(f"would keep in {escape_path(wrapper)}/: {reason}", file=err)
        return _HoistResult(wrapper)
    dest = wrapper.parent / name
    label = f"{escape_path(dest)}{'/' if is_dir else ''}"
    if dest == wrapper:
        print(f"would remove wrapper; content at {label}", file=err)
    elif os.path.lexists(dest):
        print(
            f"would move to {label}, which exists already: "
            "collisions with its contents are not checked",
            file=err,
        )
    else:
        print(f"would move to {label}", file=err)
    return _HoistResult(dest, dest_label=label, moves=_Moves({name: name}))


def _summary_dest_label(
    target: Path, report: ExtractionReport, *, dry_run: bool = False
) -> str:
    """Closing summary destination; prefer the single extracted top when dest is cwd.

    The single top is the name the run wrote, which a rename can move off the member's
    own name: naming the member's would point at the operator's file. Returned
    terminal-safe: the top, the operator's ``-d`` and a wrapper named after the archive
    file can all carry control bytes, and this is the last line the operator reads.

    A dry run wrote nothing to look at, so it answers from what its scratch tree held,
    which is what a real run's extracted results hold: the target is a directory the
    run would create, and the single top's kind is the one the scratch tree gave it."""
    if target != Path("."):
        if dry_run or target.is_dir():
            return f"{escape_path(target)}/"
        return escape_path(target)
    if dry_run:
        tops = dict(_dry_run_top_level(report))
    else:
        written = (
            _relative_name(r.path, target) or r.member.name.strip("/")
            for r in report
            if r.status is ExtractionStatus.EXTRACTED
        )
        # The kind is read from disk below, once there is a single top.
        tops = dict.fromkeys({name.split("/", 1)[0] for name in written} - {""}, False)
    if len(tops) != 1:
        return "."
    ((only, is_dir),) = tops.items()
    if not dry_run:
        on_disk = Path(only)
        is_dir = on_disk.is_dir() and not on_disk.is_symlink()
    return f"{escape_member_name(only)}/" if is_dir else escape_member_name(only)


def _dry_run_top_level(report: ExtractionReport) -> tuple[tuple[str, bool], ...]:
    """A dry run's top-level entries, from the report's private field.

    The one place the CLI reads a private library name; the ``cli`` spec's public-API
    requirement records it as an exception. A dry run writes nothing, so nothing else
    can say what a real run would leave at the top of ``dest``."""
    return report._dry_run_top_level or ()


def _follows_renamed_dir(
    requested: Path, path: Path, renamed_dirs: dict[Path, Path]
) -> bool:
    """Whether ``requested`` -> ``path`` only moved with its nearest renamed ancestor
    directory: the same tail under that directory's written name."""
    for ancestor in requested.parents:
        renamed_to = renamed_dirs.get(ancestor)
        if renamed_to is not None:
            return path == renamed_to / requested.relative_to(ancestor)
    return False


def _report_extraction(
    report: ExtractionReport,
    *,
    target: Path,
    verbose: bool,
    err: TextIO,
    extra_renamed: int = 0,
    extra_skipped: int = 0,
    dest_label: str | None = None,
    moves: _Moves | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Print rename notices + a closing summary from the library report (F3/D2).

    Per-member paths are named relative to ``target``, the directory extracted into.
    ``moves`` is what the hoist did to the entries it moved out of ``target``
    (:class:`_Moves`): a path under one is named where the move put it, relative to
    ``target``'s parent. A member the hoist discarded under ``SKIP`` gets no line of its
    own (the hoist's ``skipped:`` line is its line), as its path names the operator's
    entry, not the member.

    ``extra_renamed`` / ``extra_skipped`` fold in collisions resolved during the
    post-extract hoist (the library report covers only the wrapper extraction,
    which is collision-free by construction). A hoist skip also comes off
    ``extracted``: the report counted that file before the hoist discarded it. ``dest_label`` is the hoist's own
    account of where the content landed, which the report — written before the hoist
    moved anything — cannot know.

    Under ``dry_run`` the per-member lines are the same, since they say what the
    extraction decided, and the summary says that nothing was written. Its destination
    is the target as named: the disk cannot say more, because nothing landed there.

    Returns ``(blocked_count, failed_count)`` for exit-code selection (Q1).
    """
    extracted = 0
    renamed = extra_renamed
    skipped = extra_skipped
    blocked = 0
    failed = 0
    rerooted = 0
    # Every directory rename in the run: requested path -> written path. A member
    # inside a renamed directory follows it (``dd/f`` -> ``dd (1)/f``) and so also
    # reports ``requested_path != path``; that is the directory's one rename, not a new
    # one. Collected before the walk, as the result that carries the rename is not
    # always first: with the directory stored twice, it is the later copy, after the
    # members written between the two.
    renamed_dirs = {
        r.requested_path: r.path
        for r in report.results
        if r.member.type is MemberType.DIRECTORY
        and r.requested_path is not None
        and r.path is not None
        and r.requested_path != r.path
    }

    def shown(path: Path | None) -> str:
        """``path`` as the line names it."""
        return escape_member_name(_relative_name(path, target, moves))

    for result in report:
        status = result.status
        if status is ExtractionStatus.EXTRACTED:
            extracted += 1
            if _discarded(result.path, target, moves):
                # The hoist discarded this member under ``SKIP``. Its ``skipped:`` line
                # is the member's only line: it was not extracted, and not re-rooted
                # either. ``extracted -= extra_skipped`` below takes it off the count.
                continue
            was_renamed = (
                result.requested_path is not None
                and result.path is not None
                and result.requested_path != result.path
                # An anti-item deletes; where it deleted is not a rename.
                and not result.member.is_anti
                and not _follows_renamed_dir(
                    result.requested_path, result.path, renamed_dirs
                )
            )
            landed = shown(result.path)
            if was_renamed:
                renamed += 1
                # Renames change where data lives — always report them. Neither side
                # names a discarded entry: a discard implies ``skip``, which implies no
                # rename.
                print(
                    f"renamed: {shown(result.requested_path)} -> {landed}",
                    file=err,
                )
            elif verbose and not _hoist_renamed(result.path, target, moves):
                # A member the hoist renamed has the hoist's ``renamed:`` line instead,
                # as a member that extraction renamed has its own.
                print(
                    f"extracted: {escape_member_name(result.member.name)}",
                    file=err,
                )
            if result.kept_mode is not None:
                # The directory was already there: it kept its own mode, not the
                # archive's. Reported because the tree differs from the archive.
                print(
                    f"kept existing directory's mode {result.kept_mode:04o}: "
                    f"{landed or '.'}",
                    file=err,
                )
            # A portable rewrite is a different event from a collision rename: the member
            # landed where it asked to, under a different spelling. Always reported, for
            # the same reason as a rename — the on-disk name is not the archive's.
            # A re-root is the exception: a ``tar -P`` backup re-roots every member, so
            # re-roots are counted and reported once, as GNU tar does, and listed per
            # member only under --verbose. A portable rewrite on top of a re-root goes
            # with it: the result carries one ``presented_name`` for both, so the
            # verbose line shows both under the re-root's label.
            if result.presented_name is not None:
                rerooted_name = NameRewrite.REROOTED in result.rewrites
                if rerooted_name:
                    rerooted += 1
                if not rerooted_name or verbose:
                    # ``presented_name`` is the full relative name, so the arrow's other
                    # side must be too: a basename would print ``dir/foo. -> foo`` and
                    # invent a destination the member never had.
                    label = "re-rooted" if rerooted_name else "name rewritten"
                    print(
                        f"{label}: {escape_member_name(result.presented_name)} -> "
                        f"{landed}",
                        file=err,
                    )
        elif status is ExtractionStatus.OVERWRITTEN:
            # Written, then clobbered by a later member. Not counted as extracted: its
            # content is not what is on disk. Always reported — data was lost.
            skipped += 1
            where = _escaped_where(result, target, moves)
            print(f"overwritten: {where}", file=err)
        elif status is ExtractionStatus.NOT_OVERWRITTEN:
            skipped += 1
            # Overwrite-skips change outcomes under --overwrite skip; always note.
            where = _escaped_where(result, target, moves)
            print(f"not overwritten: {where}", file=err)
        elif status is ExtractionStatus.LINK_TARGET_UNAVAILABLE:
            skipped += 1
            # The archive described a link it never recorded a target for, so there was
            # nothing to write. Always reported: the tree the user gets is missing an
            # entry the listing showed them.
            where = _escaped_where(result, target, moves)
            print(f"link target unavailable: {where}", file=err)
        elif status is ExtractionStatus.SUPERSEDED:
            skipped += 1  # count superseded entries alongside skipped in summary
            if verbose:
                print(
                    f"superseded: {escape_member_name(result.member.name)}",
                    file=err,
                )
        elif status is ExtractionStatus.BLOCKED:
            blocked += 1
            detail = (
                f": {format_error_detail(result.error)}"
                if result.error is not None
                else ""
            )
            print(
                f"blocked: {escape_member_name(result.member.name)}{detail}",
                file=err,
            )
        elif status is ExtractionStatus.FAILED:
            failed += 1
            detail = (
                f": {format_error_detail(result.error)}"
                if result.error is not None
                else ""
            )
            print(
                f"failed: {escape_member_name(result.member.name)}{detail}",
                file=err,
            )

    # Every hoist skip unlinked one file the report counted as extracted — our own copy,
    # set aside for the operator's — so it is not on disk and not counted, exactly as
    # a direct extraction's ``NOT_OVERWRITTEN`` is not.
    extracted -= extra_skipped
    if rerooted:
        print(
            f"re-rooted {rerooted} absolute member name{'s' if rerooted != 1 else ''}"
            " inside the destination",
            file=err,
        )
    if dest_label is None:
        dest_label = _summary_dest_label(target, report, dry_run=dry_run)
    print(
        f"{'dry run, nothing written: ' if dry_run else ''}"
        f"{extracted} extracted, {renamed} renamed, {skipped} skipped"
        f"{f', {blocked} blocked' if blocked else ''}"
        f"{f', {failed} failed' if failed else ''}"
        f" → {dest_label}",
        file=err,
    )
    return blocked, failed


def _escaped_where(result: ExtractionResult, target: Path, moves: _Moves | None) -> str:
    """The destination to report for a member that did not keep it, terminal-safe.

    ``requested_path`` is built from the member's own name. STRICT and STANDARD write the
    controls 0x01-0x1F as ``%XX``, but other non-printable characters (DEL, the C1
    controls, U+2028) pass through, and TRUSTED writes every name as stored. Printing it
    raw is the line-spoofing vector ``escape_member_name`` exists to close, so both the
    path and the name fallback go through it.

    Rendered relative to the extraction root first, matching ``name rewritten:``. That is
    not only for brevity: ``escape_member_name`` escapes backslashes, so handing it a
    native Windows path would double every separator (``C:\\Users\\...``). The relative
    name is ``/``-separated, so a backslash surviving into it is a real character in a
    member name — which is exactly what should be escaped."""
    if result.requested_path is not None:
        return escape_member_name(_relative_name(result.requested_path, target, moves))
    return escape_member_name(result.member.name)


def _relative_name(
    path: PurePath | None,
    target: PurePath,
    moves: _Moves | None = None,
) -> str:
    """The on-disk name relative to the extraction root, for reporting.

    With ``moves``, a name the hoist moved out of ``target`` is given where the move
    put it, relative to ``target``'s parent (:meth:`_Moves.place`); for an entry the
    hoist discarded, that is the operator's entry it kept. Falls back to the full path,
    still ``/``-separated, when the member landed outside ``target``, and to ``""`` when
    nothing was written."""
    if path is None:
        return ""
    try:
        relative = path.relative_to(target).as_posix()
    except ValueError:
        return path.as_posix()
    if moves is not None:
        return moves.place(relative)[0]
    return relative


def _in_target(path: PurePath | None, target: PurePath) -> str | None:
    """``path`` relative to ``target``, ``/``-separated; ``None`` when nothing was
    written or the entry is not under ``target``, so no hoist moved it."""
    if path is None:
        return None
    try:
        return path.relative_to(target).as_posix()
    except ValueError:
        return None


def _hoist_renamed(
    path: PurePath | None, target: PurePath, moves: _Moves | None
) -> bool:
    """Whether the hoist moved the entry at ``path`` itself to a new name. An entry
    inside a renamed directory only follows it, as in :func:`_follows_renamed_dir`.
    ``False`` when no hoist ran (``moves`` is ``None``) or nothing was written."""
    relative = _in_target(path, target)
    if relative is None or moves is None:
        return False
    return moves.names.get(relative, relative) != relative


def _discarded(path: PurePath | None, target: PurePath, moves: _Moves | None) -> bool:
    """Whether the hoist discarded the entry at ``path`` under ``SKIP``. ``False``
    when no hoist ran (``moves`` is ``None``) or nothing was written."""
    relative = _in_target(path, target)
    if relative is None or moves is None:
        return False
    return moves.place(relative)[1]


def _missing_dirs(target: Path) -> list[Path]:
    """``target`` and those of its parents that do not exist yet, deepest first."""
    missing: list[Path] = []
    path = target
    while not os.path.lexists(path) and path != path.parent:
        missing.append(path)
        path = path.parent
    return missing


def _remove_empty_dirs(paths: list[Path]) -> None:
    """Remove each directory in ``paths``, deepest first, while it is empty."""
    for path in paths:
        try:
            path.rmdir()
        except OSError:
            return


def _exit_for_outcomes(*, blocked: int, failed: int, hoist_ok: bool) -> int:
    """Map extract outcomes to exit codes (Q8 Option A): FAILED→1, policy-only BLOCKED→3."""
    if not hoist_ok or failed:
        return EXIT_FAIL
    if blocked:
        return EXIT_POLICY
    return EXIT_OK


def run_extract(
    *,
    archive: str,
    dest: str | None,
    patterns: list[str],
    exclude: list[str],
    policy: str,
    overwrite: str,
    password: str | None,
    track_io: bool,
    hide_progress: bool,
    verbose: bool,
    stop_on_error: bool = False,
    abort_on: list[str] | None = None,
    dry_run: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    del out  # extract reports to stderr; files go to the filesystem
    # On the strings, before Path() turns "" into ".". The stdin token "-" is
    # refused by open_for_cli below.
    reject_empty_path(archive, arg="archive")
    if dest is not None:
        reject_empty_path(dest, arg="--dest")
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    selection = MemberSelection(patterns, exclude)
    pred = selection.predicate
    policy_enum = from_cli_choice(ExtractionPolicy, policy)
    overwrite_enum = from_cli_choice(OverwritePolicy, overwrite)
    on_error = OnError.STOP if stop_on_error else OnError.CONTINUE
    abort_on_enum = frozenset(from_cli_choice(AbortOn, item) for item in abort_on or ())
    archive_path = Path(archive)

    with open_for_cli(archive_path, password=pwd, track_io=track_io, err=err) as reader:
        # A complete free index settles the patterns before anything is written.
        # Without one, or with one that ends in damage, the extraction's own pass
        # offers each member to them, and they are judged after it: a separate pass
        # would decompress the archive a second time.
        indexed = reader.members_report_if_available() if pred is not None else None
        if indexed is not None:
            selection.settle_from(indexed, err=err, dest_hint=True)
            if selection.settled and selection.selects_nothing:
                return EXIT_FAIL

        may_hoist = False
        if dest is not None:
            target = Path(dest)
        else:
            plan = resolve_smart_dest(
                reader,
                archive_path,
                pred=pred,
            )
            target = plan.target
            may_hoist = plan.may_hoist
            if target != Path("."):
                verb = "would extract" if dry_run else "extracting"
                print(f"{verb} into {escape_path(target)}/", file=err)

        base_progress: ProgressCallback | None = make_progress_callback(
            hide_progress=hide_progress, stream=err
        )
        # Under STOP, extract_all raises without returning a report — track how
        # many members were extracted / blocked before the stop via progress (Q1.5).
        members_extracted = 0
        members_blocked = 0

        # The directories the extraction may create; removed again when it turns
        # out the patterns selected nothing.
        missing_dirs = _missing_dirs(target)

        def on_progress(progress: ExtractionProgress) -> None:
            nonlocal members_extracted, members_blocked
            members_extracted = progress.members_extracted
            members_blocked = progress.members_blocked
            if base_progress is not None:
                base_progress(progress)

        try:
            try:
                report = reader.extract_all(
                    target,
                    members=pred,
                    policy=policy_enum,
                    overwrite=overwrite_enum,
                    on_error=on_error,
                    abort_on=abort_on_enum,
                    on_progress=on_progress,
                    dry_run=dry_run,
                )
            except BrokenPipeError:
                # The progress bar's stream lost its reader. That must reach
                # main(), which exits 141 with no message; the handler below would
                # report it as a failed extraction and exit 1.
                raise
            except (ArchiveyError, OSError) as exc:
                # STOP-path member failure / always-stop (bomb guards,
                # DiagnosticRaisedError): report what was already written, then
                # the stop notice. Exit 1 always on abort (Q8 Option A): exit 3
                # is reserved for a *completed* run with policy blocks and safe
                # members on disk (blocks never abort under STOP). The
                # ``archivey: `` prefix: see main._parse_and_dispatch.
                # Under CONTINUE this line can repeat the detail of the
                # ``WARNING: Skipping …`` line printed just above it, which ``test``
                # suppresses. That is kept on purpose: the warning says a member was
                # skipped, this line says why the run ended, and ``extract`` has no
                # failure counter, so nothing is counted twice.
                print(f"archivey: {format_error_detail(exc)}", file=err)
                parts: list[str] = []
                if members_extracted:
                    parts.append(f"{members_extracted} member(s) extracted")
                if members_blocked:
                    parts.append(f"{members_blocked} blocked")
                if parts:
                    print(f"{', '.join(parts)} before the stop", file=err)
                print(
                    "extraction stopped; remaining members were not extracted",
                    file=err,
                )
                if dry_run:
                    print("dry run: nothing was written", file=err)
                return EXIT_FAIL
            if pred is not None and not selection.settled:
                if len(report) == 0 and selection.selects_nothing:
                    # Removed before the warning, whose -d hint looks for a directory
                    # named like the pattern.
                    _remove_empty_dirs(missing_dirs)
                    selection.report(err=err, dest_hint=True)
                    return EXIT_FAIL
                selection.report(err=err, dest_hint=True)
            hoist = _HoistResult(target)
            if may_hoist and dry_run:
                # The hoist moves what the extraction wrote; a dry run wrote nothing.
                hoist = predict_hoist(target, report, err=err)
            elif may_hoist:
                hoist = maybe_hoist_single_root(
                    target, overwrite=overwrite_enum, err=err
                )
            blocked, failed = _report_extraction(
                report,
                target=target,
                verbose=verbose,
                err=err,
                extra_renamed=hoist.renamed,
                extra_skipped=hoist.skipped,
                dest_label=hoist.dest_label,
                moves=hoist.moves,
                dry_run=dry_run,
            )
            return _exit_for_outcomes(blocked=blocked, failed=failed, hoist_ok=hoist.ok)
        finally:
            if base_progress is not None:
                base_progress.close()

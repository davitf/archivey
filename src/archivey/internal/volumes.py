"""Multi-volume path discovery and joining (concatenation; RAR keeps volume-1 path)."""

from __future__ import annotations

import errno
import io
import os
import re
import stat
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeGuard

from archivey.escaping import display_path
from archivey.exceptions import (
    ArchiveyUsageError,
    OpenError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.streams.streamtools import (
    ensure_full_count_reads,
    is_stream,
    reject_source,
    source_name,
)

SourceItem = str | Path | BinaryIO
SourceSequence = Sequence[SourceItem]

# 7-Zip's ``-v`` writes ``name.7z.001``/``name.zip.001`` — in both cases a *raw byte
# split* of one finished archive, so the parts concatenate back into the original and
# one pattern serves both. An SFX module replaces the archive extension with ``.exe``
# (``7z a -sfx … -v`` → ``vol.exe.001`` … ``.00N``); the stub ``vol.exe`` has no
# ``.NNN`` suffix and is not a sibling. After detection misses, ``open_archive``
# follows that stub to the split first volume beside it (``first_volume_for_stub``).
# ``.sfx`` is not in this pattern: 7-Zip does not emit
# ``name.sfx.001``, and arbitrary ``name.foo.001`` is not a 7-Zip split. Info-ZIP's
# ``name.z01 … name.zip`` deliberately does not match: that is a true spanned set
# addressed by (disk, offset). A linear join of one lists correctly and then reads
# only whichever members happen to sit on the last disk, so it is refused in the ZIP
# backend instead.
#
# **Three digits minimum, not ``\d+``.** 7-Zip numbers from ``.001`` and widens past
# part 999, so nothing it emits needs fewer. Accepting one or two would swallow
# ``name.zip.1`` / ``name.zip.2`` — what wget and naive rotation produce for two
# downloads of the *same* file — and concatenate two independent complete archives,
# handing back the wrong file's contents with no error. The completeness check cannot
# catch it either: ``[1, 2]`` is exactly ``1..N``. ``.exe`` is format-agnostic (7-Zip
# writes it for a 7z or ZIP payload), so this pattern is not kept in step with
# ``is_zip_split_segment_name`` (still ``zip\.\d{3,}`` / ``.zNN``). A lone
# numbered part (``.7z.001`` / ``.zip.001`` / ``.exe.001``) is refused at
# ``open_archive`` as an incomplete set, naming the missing parts — not as a ZIP
# spanned-set error. Info-ZIP ``.zNN`` stays that ZIP refusal.
_NUMBERED_VOLUME_RE = re.compile(
    r"^(?P<base>.+\.(?:7z|zip|exe))\.(?P<part>\d{3,})$", re.IGNORECASE
)
# WinRAR ``-v`` writes ``name.partN.rar``. An SFX first volume keeps the ``partN``
# marker and changes only the last extension: ``name.part1.sfx`` (Linux rar) or
# ``name.part1.exe`` (Windows), with later volumes still ``.partN.rar``. The stem
# before ``.part`` is the set's base, so mixed extensions on one stem are one set.
_RAR_PART_RE = re.compile(
    r"^(?P<base>.+)\.part(?P<part>\d+)\.(?:rar|sfx|exe)$", re.IGNORECASE
)
_RAR_RNN_RE = re.compile(r"^(?P<base>.+)\.r(?P<part>\d{2})$", re.IGNORECASE)


# Each ordering key reads the part number back out of the pattern that classified the
# name, so a base that happens to contain another pattern's marker cannot capture it.
# A scan for the first ``.partN`` anywhere in the name used to, and sorted every part of
# ``my.part1.zip.001 … .003`` under the same key 1 — leaving the concatenation order to
# ``iterdir``, which is arbitrary.
def _numbered_part_number(name: str) -> int:
    match = _NUMBERED_VOLUME_RE.match(name)
    return int(match.group("part")) if match is not None else 0


def _rar_part_number(name: str) -> int:
    match = _RAR_PART_RE.match(name)
    return int(match.group("part")) if match is not None else 0


def _pick_rar_part(candidates: list[Path], named_part: int, named_lower: str) -> Path:
    """One file per part number. Prefer the name the caller opened."""
    part = _rar_part_number(candidates[0].name)
    if part == named_part:
        for candidate in candidates:
            if candidate.name.lower() == named_lower:
                return candidate
    for candidate in candidates:
        if candidate.suffix.lower() == ".rar":
            return candidate
    return candidates[0]


def _rnn_part_number(name: str) -> int:
    match = _RAR_RNN_RE.match(name)
    return int(match.group("part")) if match is not None else 0


def _is_old_scheme_first_volume_name(name: str) -> bool:
    """``<base>.rar`` / ``.exe`` / ``.sfx`` as old-scheme volume 1, not a partN/NNN name."""
    if (
        _NUMBERED_VOLUME_RE.match(name) is not None
        or _RAR_PART_RE.match(name) is not None
    ):
        return False
    lower = name.lower()
    return lower.endswith(".rar") or lower.endswith(".exe") or lower.endswith(".sfx")


def _old_rar_rnn_first_volume(parent: Path, base: str) -> Path | None:
    """Volume 1 of an old-scheme set: ``.rar``, else ``.exe``, else ``.sfx``."""
    for suffix in (".rar", ".exe", ".sfx"):
        candidate = parent / f"{base}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _collect_old_rar_rnn_volumes(parent: Path, base: str) -> list[Path] | None:
    first = _old_rar_rnn_first_volume(parent, base)
    if first is None:
        return None
    continuations = sorted(
        (
            candidate
            for candidate in parent.iterdir()
            if candidate.is_file()
            and (rnn_match := _RAR_RNN_RE.match(candidate.name)) is not None
            and rnn_match.group("base").lower() == base.lower()
        ),
        key=lambda candidate: _rnn_part_number(candidate.name),
    )
    if not continuations:
        return None
    return [first, *continuations]


def discover_volume_siblings(path: Path) -> list[Path] | None:
    """Return ordered sibling paths when ``path`` is part of a volume set, else ``None``."""
    name = path.name
    lower = name.lower()
    # Fast reject before any filesystem op: most opens (ZIP/TAR/gz/plain .7z) are
    # not volume-shaped. Saves a ``stat`` per open_archive (perf review L3).
    # SFX first members (``*.exe.001``, ``*.part1.sfx``) match the patterns above.
    # A stub ``*.exe`` / ``*.sfx`` is maybe-volume only when ``<stem>.r00`` exists
    # (one ``is_file``, no ``iterdir``) so 7-Zip ``vol.exe`` + ``vol.exe.001``
    # still falls through to stub-follow.
    maybe_volume = (
        _NUMBERED_VOLUME_RE.match(name) is not None
        or _RAR_PART_RE.match(name) is not None
        or _RAR_RNN_RE.match(name) is not None
        or lower.endswith(".rar")
    )
    if (
        not maybe_volume
        and _is_old_scheme_first_volume_name(name)
        and (path.parent / f"{path.stem}.r00").is_file()
    ):
        maybe_volume = True
    if not maybe_volume:
        return None
    if not path.is_file():
        return None
    parent = path.parent

    match = _NUMBERED_VOLUME_RE.match(name)
    if match is not None:
        base = match.group("base")
        siblings = sorted(
            (
                candidate
                for candidate in parent.iterdir()
                if candidate.is_file()
                and (vol_match := _NUMBERED_VOLUME_RE.match(candidate.name)) is not None
                and vol_match.group("base").lower() == base.lower()
            ),
            key=lambda candidate: _numbered_part_number(candidate.name),
        )
        return siblings if len(siblings) > 1 else None

    match = _RAR_PART_RE.match(name)
    if match is not None:
        base = match.group("base")
        grouped: dict[int, list[Path]] = {}
        for candidate in parent.iterdir():
            if not candidate.is_file():
                continue
            part_match = _RAR_PART_RE.match(candidate.name)
            if part_match is None or part_match.group("base").lower() != base.lower():
                continue
            grouped.setdefault(int(part_match.group("part")), []).append(candidate)
        if len(grouped) <= 1:
            return None
        named_part = int(match.group("part"))
        named_lower = name.lower()
        return [
            _pick_rar_part(grouped[part], named_part, named_lower)
            for part in sorted(grouped)
        ]

    rnn_base: str | None = None
    rnn_match = _RAR_RNN_RE.match(name)
    if rnn_match is not None:
        rnn_base = rnn_match.group("base")
    elif _is_old_scheme_first_volume_name(name):
        rnn_base = name[: name.rfind(".")]
    if rnn_base is not None:
        # Volume 1 is `<base>.rar`, or an SFX `<base>.exe` / `<base>.sfx` when no
        # `.rar` is beside the `.rNN` files. A bare `.rNN` with none of those is
        # a lone file — siblings[0] must be volume 1.
        return _collect_old_rar_rnn_volumes(parent, rnn_base)

    return None


def is_sfx_stub_name(name: str) -> bool:
    """True for ``*.exe`` / ``*.sfx`` names that are not already volume-shaped."""
    if (
        _NUMBERED_VOLUME_RE.match(name) is not None
        or _RAR_PART_RE.match(name) is not None
    ):
        return False
    lower = name.lower()
    return lower.endswith(".exe") or lower.endswith(".sfx")


def first_volume_for_stub(path: Path) -> Path | None:
    """Return the split first volume beside a stub-only ``.exe`` / ``.sfx``, if any.

    7-Zip's ``-sfx -v`` always writes a standalone stub with no archive magic, then
    names the volumes three different ways:

    - Linux: ``vol.exe.001`` (the stub's own name plus ``.001``)
    - Windows 7z: ``vol.7z.001``
    - Windows ZIP: ``vol.zip.001``

    Opening the stub should work for every one of those, otherwise ``open_archive``
    on ``vol.exe`` succeeds on Linux and fails on Windows for the same producer
    flag. The stub is still not a volume sibling — this only names the file to
    hand to :func:`discover_volume_siblings`.

    A path that is already volume-shaped (``vol.exe.001``, ``rv.part1.exe``) is not
    a stub. Two matching first volumes beside the stub is a refusal, not a guess.
    """
    name = path.name
    if not is_sfx_stub_name(name):
        return None
    if not path.is_file():
        return None
    # Exact names, not ``iterdir``. A random ``.exe`` with no volumes beside it
    # is the common miss; walking a large directory there is wasted work.
    # Windows ``is_file`` is case-insensitive; Linux is not, so ``VOL.ZIP.001``
    # beside ``vol.exe`` is only found there if the caller used that casing.
    stem = name[: name.rfind(".")]
    candidates = (
        path.parent / f"{name}.001",
        path.parent / f"{stem}.7z.001",
        path.parent / f"{stem}.zip.001",
    )
    found = [candidate for candidate in candidates if candidate.is_file()]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        names = ", ".join(sorted(candidate.name for candidate in found))
        raise UnsupportedFeatureError(
            f"{display_path(path)} has no archive magic, and more than one split "
            f"first volume sits beside it ({names}). Open one of those files.",
            archive_name=path.as_posix(),
        )
    return None


# LRU of open Path volume handles. Two or three absorb SharedView pairs that
# straddle a part boundary (concurrent_members=True). Not a refusal: a 100-part
# set still reads. Past this many distinct Path volumes in the working set, each
# cache miss is an open()+close() on that read. Growing the cache to the volume
# count would hold one descriptor per part again.
_PATH_HANDLE_CACHE_SIZE = 3


def _fd_limit_text() -> str:
    try:
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, AttributeError, OSError, ValueError):
        return "the process file-descriptor limit"
    return f"the process file-descriptor limit is {soft}"


def _volume_open_error(path: Path, exc: OSError) -> OpenError:
    if exc.errno in (errno.EMFILE, errno.ENFILE):
        return OpenError(
            f"Too many open files to open volume; {_fd_limit_text()}",
            archive_name=path.as_posix(),
        )
    return OpenError(
        "Cannot open volume",
        archive_name=path.as_posix(),
    )


@dataclass
class _CachedPathHandle:
    stream: BinaryIO
    synced: bool = False


class ConcatenatedFile(io.RawIOBase, BinaryIO):
    """Seekable read-only concatenation of volume streams.

    Path volumes are sized with ``stat`` and opened lazily into a small LRU of
    handles (``_PATH_HANDLE_CACHE_SIZE``). Sequential drains and a pair of
    alternating views stay inside that cache; a working set larger than the
    constant evicts on every miss, and those reads pay ``open``+``close``.
    Bytes are sampled at ``open()`` / ``read()``, not pinned by a
    construction-time descriptor: replacing a part after construction is
    visible on the next open of that part.
    Caller-supplied streams stay open, are never closed here, and are re-seeked
    before every read (the caller may have moved them).
    """

    def __init__(self, sources: Sequence[Path | BinaryIO]) -> None:
        super().__init__()
        if not sources:
            raise ArchiveyUsageError("at least one volume is required")
        # Retained so format-specific openers (RAR) can recover real volume paths —
        # unrar needs sibling files on disk, not a concatenated byte stream.
        self._volume_paths: list[Path] = [
            source for source in sources if isinstance(source, Path)
        ]
        self._volume_items: list[Path | BinaryIO] = list(sources)
        offsets = [0]
        total = 0
        for source in sources:
            if isinstance(source, Path):
                # No handle: a 100-part set must not hold 100 descriptors from
                # construction, and sizing does not need one.
                try:
                    st = os.stat(source)
                except OSError as exc:
                    # A path the caller listed is an open failure. A numbering
                    # gap among paths that exist is TruncatedError in
                    # _validate_numbered_volume_sequence — discovery expected a
                    # part that is not in the set.
                    raise _volume_open_error(source, exc) from exc
                if not stat.S_ISREG(st.st_mode):
                    raise OpenError(
                        "volume is not a regular file",
                        archive_name=source.as_posix(),
                    )
                size = st.st_size
            else:
                try:
                    pos = source.tell()
                    size = source.seek(0, os.SEEK_END)
                    source.seek(pos)
                except (OSError, AttributeError, io.UnsupportedOperation) as exc:
                    # Same refusal as a non-seekable *single* source, so it gets
                    # the same type: a volume set is concatenated by offset and
                    # cannot be joined from a forward-only stream. Paths are
                    # always seekable; this check is for caller-supplied streams
                    # only. (Keep "type:" out of a comment's first position;
                    # tests/test_no_stray_type_comments.py.)
                    raise StreamNotSeekableError(
                        "all volume streams must be seekable"
                    ) from exc
            total += size
            offsets.append(total)
        self._offsets = offsets
        self._size = total
        self._pos = 0
        self.volume_count = len(sources)
        self._vol_index = 0
        self._vol_offset = 0
        self._path_handles: OrderedDict[int, _CachedPathHandle] = OrderedDict()
        self._recompute_cursor()

    @property
    def volume_paths(self) -> list[Path]:
        """Path volumes in order, when every volume was a ``Path``; else empty."""
        if len(self._volume_paths) != self.volume_count:
            return []
        return list(self._volume_paths)

    @property
    def volume_items(self) -> list[Path | BinaryIO]:
        """Original volume sources in order (paths and/or streams)."""
        return list(self._volume_items)

    @property
    def volume_ranges(self) -> list[tuple[int, int]]:
        """``(start, size)`` of each volume in the concatenated byte space.

        Lets a format opener read one volume at a time through whatever it
        already holds over the concatenation — a ``SharedSource`` view, say —
        instead of reopening the originals. RAR's header walk needs each volume
        as an independent stream positioned at its start, which the whole
        concatenation cannot provide.
        """
        return [
            (self._offsets[index], self._offsets[index + 1] - self._offsets[index])
            for index in range(self.volume_count)
        ]

    @property
    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._checkClosed()
        return self._pos

    def _close_all_path_handles(self) -> None:
        handles = list(self._path_handles.values())
        self._path_handles.clear()
        for handle in handles:
            handle.stream.close()

    def _open_path_volume(self, path: Path) -> BinaryIO:
        try:
            return open(path, "rb")
        except OSError as exc:
            raise _volume_open_error(path, exc) from exc

    def _recompute_cursor(self) -> None:
        """Set volume index/offset from ``_pos``. Sequential ``read`` advances instead."""
        if self._pos >= self._size:
            self._vol_index = self.volume_count
            self._vol_offset = 0
            return
        index = bisect_right(self._offsets, self._pos) - 1
        self._vol_index = index
        self._vol_offset = self._pos - self._offsets[index]
        cached = self._path_handles.get(index)
        if cached is not None:
            cached.synced = False

    def _advance_volume(self) -> None:
        self._vol_index += 1
        self._vol_offset = 0
        n = self.volume_count
        while (
            self._vol_index < n
            and self._offsets[self._vol_index + 1] == self._offsets[self._vol_index]
        ):
            self._vol_index += 1
        cached = self._path_handles.get(self._vol_index)
        if cached is not None:
            # Sequential advance into a cached volume: the handle still sits
            # wherever the last user left it. Clear synced so the next read
            # seeks it; do not assume it is at 0 just because we did not seek.
            cached.synced = False

    def _ensure_current_stream(self) -> BinaryIO:
        source = self._volume_items[self._vol_index]
        if not isinstance(source, Path):
            # Caller-owned: never close it. ``_path_handles`` is Path-only, so a
            # mixed set can keep a Path handle cached while this stream is current.
            return source
        cached = self._path_handles.get(self._vol_index)
        if cached is not None:
            self._path_handles.move_to_end(self._vol_index)
            return cached.stream
        while len(self._path_handles) >= _PATH_HANDLE_CACHE_SIZE:
            _old_index, old = self._path_handles.popitem(last=False)
            old.stream.close()
        stream = self._open_path_volume(source)
        self._path_handles[self._vol_index] = _CachedPathHandle(stream)
        return stream

    def _seek_current_stream(self, stream: BinaryIO) -> None:
        source = self._volume_items[self._vol_index]
        if isinstance(source, Path):
            cached = self._path_handles[self._vol_index]
            if cached.synced:
                return
            stream.seek(self._vol_offset)
            cached.synced = True
            return
        # Borrowed: the caller may have moved it. Seek every time, as we did
        # when every volume was an already-open handle. ``synced`` is Path-only
        # because that invariant is not enforceable on a stream we do not own.
        stream.seek(self._vol_offset)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._checkClosed()
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == os.SEEK_END:
            new_pos = self._size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        if new_pos < 0:
            raise ValueError("Negative seek position")
        self._pos = new_pos
        self._recompute_cursor()
        return self._pos

    def read(self, n: int = -1) -> bytes:
        self._checkClosed()
        if self._pos >= self._size:
            return b""
        if n is None or n < 0:
            n = self._size - self._pos
        else:
            n = min(n, self._size - self._pos)
        # A raise discards ``out``. Put ``_pos`` back so a retry re-delivers
        # the prefix instead of skipping it.
        start_pos = self._pos
        out = bytearray()
        try:
            while n > 0 and self._pos < self._size:
                vol_end = self._offsets[self._vol_index + 1]
                available = vol_end - self._pos
                to_read = min(n, available)
                stream = self._ensure_current_stream()
                self._seek_current_stream(stream)
                chunk = stream.read(to_read)
                if not chunk:
                    source = self._volume_items[self._vol_index]
                    archive_name = (
                        source.as_posix() if isinstance(source, Path) else None
                    )
                    raise TruncatedError(
                        "volume ended before its recorded size",
                        archive_name=archive_name,
                    )
                got = len(chunk)
                out.extend(chunk)
                self._pos += got
                self._vol_offset += got
                n -= got
                if self._pos >= vol_end:
                    self._advance_volume()
        except BaseException:  # noqa: BLE001 - restore cursor; the original error re-raises
            self._pos = start_pos
            self._recompute_cursor()
            raise
        return bytes(out)

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._close_all_path_handles()
        finally:
            super().close()


def _validate_numbered_volume_sequence(paths: Sequence[Path]) -> None:
    """Require ``name.EXT.001 … .00N`` parts to be 1..N with no gaps.

    Concatenating a set with a hole produces bytes that are neither the original
    archive nor recognisably broken at the join, so the missing part is caught here
    by name rather than left to surface as corruption somewhere in the middle.
    """
    base = ""
    numbered: list[int] = []
    for path in paths:
        match = _NUMBERED_VOLUME_RE.match(path.name)
        if match is None:
            return
        base = base or match.group("base")
        numbered.append(int(match.group("part")))
    expected = list(range(1, len(numbered) + 1))
    if numbered != expected:
        raise TruncatedError(
            f"Incomplete multi-volume set for {base}: "
            f"expected parts {expected}, got {numbered}"
        )


def incomplete_lone_numbered_volume_error(
    archive_name: str | None,
) -> TruncatedError | None:
    """``TruncatedError`` when ``archive_name`` is a numbered volume part.

    Discovery returns ``None`` for a single matching file, so a lone
    ``name.7z.001`` / ``name.zip.001`` / ``name.exe.001`` would otherwise fall
    through to detection and surface as ``CorruptionError``. The missing parts
    are a truncated set, not a format-specific spanned-ZIP refusal. Callers
    skip this when ``format=`` is an explicit non-ZIP / non-7z (P8).
    """
    if not archive_name:
        return None
    filename = Path(archive_name).name
    match = _NUMBERED_VOLUME_RE.match(filename)
    if match is None:
        return None
    base = match.group("base")
    part = int(match.group("part"))
    missing = [f"{base}.{n:03d}" for n in range(1, part)]
    missing.append(f"{base}.{part + 1:03d}")
    missing_text = ", ".join(missing) + ", …"
    return TruncatedError(
        f"Incomplete multi-volume set for {base}: "
        f"found part {part} only; missing {missing_text}"
    )


def join_volumes(paths: Sequence[Path]) -> BinaryIO:
    """Concatenate an ordered volume set into one seekable file-like object."""

    if not paths:
        raise ArchiveyUsageError("volume path sequence must not be empty")
    _validate_numbered_volume_sequence(paths)
    return ConcatenatedFile(paths)


OpenSourceInput = SourceItem | SourceSequence


@dataclass(frozen=True)
class ResolvedSource:
    """Single source to hand to detection/backends plus multi-volume metadata."""

    open_source: Path | BinaryIO
    archive_name: str | None
    volume_count: int


def _coerce_path_or_stream(item: SourceItem) -> Path | BinaryIO:
    if isinstance(item, (str, Path)):
        return Path(item)
    return ensure_full_count_reads(item)


def _is_source_sequence(source: OpenSourceInput) -> TypeGuard[SourceSequence]:
    if isinstance(source, (str, Path, bytes)):
        return False
    if is_stream(source):
        return False
    return isinstance(source, Sequence)


def resolve_source(source: OpenSourceInput) -> ResolvedSource:
    """Normalize ``source`` to one open target and record multi-volume detection.

    Normalizing includes making every caller-supplied stream **full-count** on ``read(n)``
    (``ensure_full_count_reads``) before it reaches detection or a backend: this is the one
    boundary every archive source crosses, and the header parsers downstream — archivey's
    and the stdlib's alike — read fixed-size structures with a single ``read(n)``. Volume
    items are normalized individually, so :class:`ConcatenatedFile` (whose own ``read``
    already coalesces across volumes) stays the resolved source that the RAR/7z volume
    handling recognizes.
    """
    if _is_source_sequence(source):
        items = [_coerce_path_or_stream(item) for item in source]
        if not items:
            raise ArchiveyUsageError("source sequence must not be empty")
        if len(items) == 1:
            return _resolve_single(items[0])
        first = items[0]
        if all(isinstance(item, Path) for item in items):
            paths = [item for item in items if isinstance(item, Path)]
            return ResolvedSource(join_volumes(paths), source_name(first), len(paths))
        return ResolvedSource(ConcatenatedFile(items), source_name(first), len(items))
    if isinstance(source, str):
        return _resolve_single(Path(source))
    if isinstance(source, Path):
        return _resolve_single(source)
    if not is_stream(source):
        # str/Path are handled above, so anything left that is not a stream is a
        # source type archivey does not take. ``reject_source`` is NoReturn, which
        # is what keeps ``source`` narrowed to ``BinaryIO`` below.
        reject_source(source)
    return _resolve_single(ensure_full_count_reads(source))


def _resolve_single(source: Path | BinaryIO) -> ResolvedSource:
    if isinstance(source, Path):
        if source.is_dir():
            return ResolvedSource(source, str(source), 1)
        siblings = discover_volume_siblings(source)
        if siblings is not None:
            # Numbered parts are byte slices, so they are joined into one stream.
            # RAR falls through with volume 1's path instead: unrar walks the set
            # itself and needs the sibling files on disk.
            if _NUMBERED_VOLUME_RE.match(siblings[0].name):
                return ResolvedSource(
                    join_volumes(siblings), source_name(siblings[0]), len(siblings)
                )
            return ResolvedSource(siblings[0], source_name(siblings[0]), len(siblings))
        return ResolvedSource(source, source_name(source), 1)
    return ResolvedSource(source, source_name(source), 1)

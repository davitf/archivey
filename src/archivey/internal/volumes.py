"""Multi-volume path discovery and joining (concatenation; RAR keeps volume-1 path)."""

from __future__ import annotations

import bisect
import io
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeGuard

from archivey.exceptions import (
    ArchiveyUsageError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.streams.streamtools import (
    ensure_full_count_reads,
    is_stream,
    source_name,
)

SourceItem = str | Path | BinaryIO
SourceSequence = Sequence[SourceItem]

# 7-Zip's ``-v`` writes ``name.7z.001``/``name.zip.001`` — in both cases a *raw byte
# split* of one finished archive, so the parts concatenate back into the original and
# one pattern serves both. An SFX module replaces the archive extension with ``.exe``
# (``7z a -sfx … -v`` → ``vol.exe.001`` … ``.00N``); the stub ``vol.exe`` has no
# ``.NNN`` suffix and is not a sibling — resolving it to ``.001`` is a separate
# detection question. ``.sfx`` is not in this pattern: 7-Zip does not emit
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
# catch it either: ``[1, 2]`` is exactly ``1..N``. This also keeps the pattern in step
# with ``is_zip_split_segment_name``, which already required ``zip\.\d{3,}``; the two
# disagreeing about the same name is what let it through.
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


def _rnn_part_number(name: str) -> int:
    match = _RAR_RNN_RE.match(name)
    return int(match.group("part")) if match is not None else 0


def discover_volume_siblings(path: Path) -> list[Path] | None:
    """Return ordered sibling paths when ``path`` is part of a volume set, else ``None``."""
    name = path.name
    lower = name.lower()
    # Fast reject before any filesystem op: most opens (ZIP/TAR/gz/plain .7z) are
    # not volume-shaped. Saves a ``stat`` per open_archive (perf review L3).
    # SFX first members (``*.exe.001``, ``*.part1.sfx``) match the patterns above;
    # a stub ``*.exe`` / ``*.sfx`` with no part marker still returns here.
    maybe_volume = (
        _NUMBERED_VOLUME_RE.match(name) is not None
        or _RAR_PART_RE.match(name) is not None
        or _RAR_RNN_RE.match(name) is not None
        or lower.endswith(".rar")
    )
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
        siblings = sorted(
            (
                candidate
                for candidate in parent.iterdir()
                if candidate.is_file()
                and (part_match := _RAR_PART_RE.match(candidate.name)) is not None
                and part_match.group("base").lower() == base.lower()
            ),
            key=lambda candidate: _rar_part_number(candidate.name),
        )
        return siblings if len(siblings) > 1 else None

    if lower.endswith(".rar") and _RAR_PART_RE.match(name) is None:
        base = name[:-4]
        r00 = parent / f"{base}.r00"
        if r00.is_file():
            siblings = [path]
            siblings.extend(
                sorted(
                    (
                        candidate
                        for candidate in parent.iterdir()
                        if candidate.is_file()
                        and (rnn_match := _RAR_RNN_RE.match(candidate.name)) is not None
                        and rnn_match.group("base").lower() == base.lower()
                    ),
                    key=lambda candidate: _rnn_part_number(candidate.name),
                )
            )
            return siblings if len(siblings) > 1 else None

    match = _RAR_RNN_RE.match(name)
    if match is not None:
        base = match.group("base")
        first = parent / f"{base}.rar"
        # The first volume of an old-scheme set is always `<base>.rar`; the `.rNN`
        # files are its continuation volumes. Without the first volume present we can't
        # anchor the set at its head (siblings[0] must be volume 1), so a bare `.rNN`
        # with no `.rar` is treated as a lone file — mirrors the `.rar` branch above,
        # which requires `.r00` to exist.
        if not first.is_file():
            return None
        siblings: list[Path] = [first]
        siblings.extend(
            sorted(
                (
                    candidate
                    for candidate in parent.iterdir()
                    if candidate.is_file()
                    and (rnn_match := _RAR_RNN_RE.match(candidate.name)) is not None
                    and rnn_match.group("base").lower() == base.lower()
                ),
                key=lambda candidate: _rnn_part_number(candidate.name),
            )
        )
        return siblings if len(siblings) > 1 else None

    return None


class ConcatenatedFile(io.RawIOBase, BinaryIO):
    """Seekable read-only concatenation of volume streams."""

    def __init__(self, sources: Sequence[Path | BinaryIO]) -> None:
        super().__init__()
        if not sources:
            raise ArchiveyUsageError("at least one volume is required")
        self._streams: list[BinaryIO] = []
        self._owned: list[BinaryIO] = []
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
                stream = open(source, "rb")
                self._owned.append(stream)
            else:
                stream = source
            try:
                pos = stream.tell()
                size = stream.seek(0, os.SEEK_END)
                stream.seek(pos)
            except (OSError, AttributeError, io.UnsupportedOperation) as exc:
                # Same refusal as a non-seekable *single* source, so it gets the same
                # type: a volume set is concatenated by offset and cannot be joined
                # from a forward-only stream.
                raise StreamNotSeekableError(
                    "all volume streams must be seekable"
                ) from exc
            self._streams.append(stream)
            total += size
            offsets.append(total)
        self._offsets = offsets
        self._size = total
        self._pos = 0
        self.volume_count = len(sources)

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
    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
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
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if self._pos >= self._size:
            return b""
        if n is None or n < 0:
            n = self._size - self._pos
        else:
            n = min(n, self._size - self._pos)
        out = bytearray()
        while n > 0 and self._pos < self._size:
            index = bisect.bisect_right(self._offsets, self._pos) - 1
            stream = self._streams[index]
            volume_offset = self._pos - self._offsets[index]
            available = self._offsets[index + 1] - self._pos
            to_read = min(n, available)
            stream.seek(volume_offset)
            chunk = stream.read(to_read)
            if not chunk:
                break
            out.extend(chunk)
            self._pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def close(self) -> None:
        if self.closed:
            return
        try:
            for stream in self._owned:
                stream.close()
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
        raise TypeError(f"unsupported source type: {type(source)!r}")
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

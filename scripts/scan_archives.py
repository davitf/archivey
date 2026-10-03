"""Dry-run every archive under a directory: statistics, and a list of files to check.

    python scripts/scan_archives.py ~/backups -o scan.csv [--password-file FILE]

Each archive goes through ``extract_all(dry_run=True)``, the real extraction pass with
file bodies discarded, so nothing is kept on disk. Files are found by content, not by
extension; a file ``detect_format`` cannot place is skipped.

Three outputs:

- ``scan.csv``: one short row per archive. The ``flags`` column says why an archive is
  worth a closer look (``bug``, ``open_error``, ``failed_members``, ``over:max_ratio``,
  ``diag:archive_trailing_data``, ...); filter on it. Empty flags means nothing stood
  out.
- ``scan.log``: the detail for every flagged archive (error messages, the members
  that failed, what declared the largest decoder allocation, archivey's own warnings,
  tracebacks), then the summary.
- The summary, also printed at the end: counts by format and outcome, how many
  archives carry each flag and diagnostic, and for every configurable limit the
  largest value seen, the 99th and 50th percentiles, and how many archives are over
  the default or past half of it without being over. Ctrl-C stops the scan and still
  writes the summary for the archives done so far.

**Resuming.** ``scan.progress`` records every file the scan has finished with, archive
or not, and every directory whose whole subtree is done. ``--resume`` continues an
interrupted scan from it: finished directories are not listed again, finished files
are not opened again, and the CSV and log are appended to. The summary covers the
whole scan, earlier rows read back from the CSV. A resume does not look for changes
in what was already scanned; a changed tree is a new scan. A file that was being
scanned when the previous run stopped is scanned again, and named on stderr in case
it is what stopped the run.

**Limits.** By default the scan turns off the limits that only count something (bytes,
entries, ratio, members, metadata, key-derivation rounds, spool), so the columns show
the true value rather than stopping at the cap. The two that protect the scanning
machine, ``max_decoder_memory`` and ``max_ppmd_in_process_input``, stay at their
defaults. ``--default-limits`` scans under the full default config instead.

**Where a dry run says less than a real run.** Two of the divergences the dry run
documents (``extract`` in ``src/archivey/internal/extraction.py`` has the full list)
land on this scan's columns. A link whose target leaves the destination and comes back
through a symlink outside it, or climbs above the directory holding it, is refused in
the scratch tree where a real run could accept it, so ``blocked_members`` can name a
member a real extraction would write. And the scratch tree is one filesystem, so a
hardlink never falls back to a copy: ``bytes_written`` is a floor for
``max_extracted_bytes`` when a real destination spans a mount point.

**Best-effort columns.** ``decoder_memory``, ``kdf_rounds`` and ``spool_bytes`` have no
public API yet. The script reads them by wrapping three internal functions for the
duration of the scan, and ``metadata_bytes`` reads the reader's private listing tracker.
Any of them can stop working when those internals change; the script then prints which
on its ``warning: probes not installed`` line rather than leaving a column silently
empty. ``decoder_memory`` is the largest allocation an archive *declared* that archivey
checks itself; xz and zstd hand the limit to liblzma / libzstd and are not counted.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import importlib
import json
import logging
import os
import pkgutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import archivey
from archivey import (
    ArchiveyConfig,
    ArchiveyError,
    DecoderLimits,
    DiagnosticCode,
    EncryptionError,
    ExtractionLimits,
    ExtractionStatus,
    FormatDetectionError,
    ListingLimits,
    MemberType,
    PasswordInput,
    SpoolLimits,
)

# --- Best-effort probes into internals (see the module docstring). ---------------


@dataclass
class _Probe:
    decoder_memory_peak: int = 0
    decoder_memory_what: str = ""
    # The ``what`` of the ``check_decoder_memory`` call in progress, so the peak and
    # its description are recorded together by whichever wrapper sees the new peak.
    pending_what: str = ""
    kdf_rounds: int = 0
    spool_bytes: int = 0
    # Archivey's own WARNING log records for the archive being scanned.
    warnings: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.decoder_memory_peak = 0
        self.decoder_memory_what = ""
        self.pending_what = ""
        self.kdf_rounds = 0
        self.spool_bytes = 0
        self.warnings = []


PROBE = _Probe()
# Names of probes that turned out not to work; reported once, with the install ones.
BROKEN_PROBES: set[str] = set()


def _install_probes() -> list[str]:
    """Wrap the internal check points; return the names of the ones that failed."""
    failed: list[str] = []
    # Backends import lazily, and a module imported after the patch keeps the
    # original binding, so everything is imported first.
    for mod in pkgutil.walk_packages(archivey.__path__, "archivey."):
        try:
            importlib.import_module(mod.name)
        except Exception:  # noqa: BLE001 - an optional dependency is missing
            continue
    try:
        from archivey.internal import config as internal_config

        original_exceeds = internal_config.exceeds_decoder_memory

        def exceeds(declared: int, limits: DecoderLimits) -> bool:
            if declared > PROBE.decoder_memory_peak:
                PROBE.decoder_memory_peak = declared
                # The LZMA Alone codec branches on this directly, without going
                # through ``check_decoder_memory``, so it has no ``what``.
                PROBE.decoder_memory_what = (
                    PROBE.pending_what or "LZMA Alone dictionary size"
                )
            return original_exceeds(declared, limits)

        original_check = internal_config.check_decoder_memory

        def check(declared: int, **kwargs: Any) -> None:
            PROBE.pending_what = str(kwargs.get("what", ""))
            try:
                original_check(declared, **kwargs)
            finally:
                PROBE.pending_what = ""

        # ``check_decoder_memory`` calls ``exceeds_decoder_memory`` through its
        # module's global; the codecs imported both names, so every binding is
        # replaced.
        for name, module in list(sys.modules.items()):
            if not name.startswith("archivey"):
                continue
            if getattr(module, "exceeds_decoder_memory", None) is original_exceeds:
                setattr(module, "exceeds_decoder_memory", exceeds)
            if getattr(module, "check_decoder_memory", None) is original_check:
                setattr(module, "check_decoder_memory", check)

        budget_cls = internal_config.KeyDerivationBudget
        original_spend = budget_cls.spend

        def spend(self: Any, rounds: int, *, what: str) -> None:
            original_spend(self, rounds, what=what)
            PROBE.kdf_rounds += rounds

        setattr(budget_cls, "spend", spend)
    except Exception:  # noqa: BLE001 - a probe that cannot install is only reported
        failed.append("decoder_memory/kdf_rounds")
    try:
        from archivey.internal.spool import SpoolBudget

        original_copy = SpoolBudget.copy

        def copy(self: Any, src: Any, out: Any) -> None:
            try:
                original_copy(self, src, out)
            finally:
                PROBE.spool_bytes = max(PROBE.spool_bytes, self._written)

        setattr(SpoolBudget, "copy", copy)
    except Exception:  # noqa: BLE001
        failed.append("spool_bytes")
    return failed


class _WarningCapture(logging.Handler):
    """Keep archivey's WARNING records for the archive being scanned.

    Most of them repeat a diagnostic or a failed result, which the CSV already counts,
    but two do not: a dry-run scratch directory that could not be removed, and the
    once-per-process notice that gzip falls back to the stdlib decoder because no
    rapidgzip child can start, which changes what ``seconds`` means for every gzip row.
    So each one goes to its archive's log rather than being dropped.
    """

    def emit(self, record: logging.LogRecord) -> None:
        PROBE.warnings.append(record.getMessage())


# --- Scanning ----------------------------------------------------------------------


def _scan_config(default_limits: bool) -> ArchiveyConfig:
    if default_limits:
        return ArchiveyConfig()
    return ArchiveyConfig(
        extraction_limits=ExtractionLimits.UNLIMITED,
        listing_limits=ListingLimits.UNLIMITED,
        spool_limits=SpoolLimits.UNLIMITED,
        decoder_limits=replace(DecoderLimits(), max_key_derivation_rounds=None),
    )


# Each resource column and the default limit it is measured against.
_LIMITS: tuple[tuple[str, str, float | None], ...] = (
    ("bytes_written", "max_extracted_bytes", ExtractionLimits().max_extracted_bytes),
    ("entries_written", "max_entries", ExtractionLimits().max_entries),
    ("archive_ratio", "max_ratio", ExtractionLimits().max_ratio),
    ("max_member_ratio", "max_ratio", ExtractionLimits().max_ratio),
    ("members", "max_members", ListingLimits().max_members),
    ("metadata_bytes", "max_metadata_bytes", ListingLimits().max_metadata_bytes),
    ("decoder_memory", "max_decoder_memory", DecoderLimits().max_decoder_memory),
    (
        "kdf_rounds",
        "max_key_derivation_rounds",
        DecoderLimits().max_key_derivation_rounds,
    ),
    ("spool_bytes", "spool_limits.max_bytes", SpoolLimits().max_bytes),
)
# A value past this share of its default, and not over it, gets a ``near:`` flag.
_NEAR = 0.5
# Diagnostics too common in ordinary archives to flag on their own.
_ROUTINE_DIAGNOSTICS = frozenset(
    {"member_name_normalized", "member_name_encoding_inferred"}
)
_SLOW_SECONDS = 60.0
_LOGGED_MEMBER_ERRORS = 10

COLUMNS = (
    "path file_size format detected version solid encrypted open extract flags seconds "
    "members entries_written bytes_written archive_ratio max_member_ratio "
    "metadata_bytes decoder_memory kdf_rounds spool_bytes codecs diagnostics error"
).split()
_NUMERIC_COLUMNS = frozenset({"seconds", *(column for column, _, _ in _LIMITS)})


@dataclass
class _Row:
    path: str
    file_size: int | None = None
    # The text columns of the CSV, and the numeric ones below, by column name.
    text: dict[str, str] = field(default_factory=dict)
    numbers: dict[str, float] = field(default_factory=dict)
    diagnostics: Counter[str] = field(default_factory=Counter)
    flags: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def flag(self, name: str) -> None:
        if name not in self.flags:
            self.flags.append(name)

    def failure(self, stage: str, exc: BaseException) -> None:
        """Record an exception that ended ``stage`` (detect, open, extract or scan)."""
        bug = not isinstance(exc, ArchiveyError)
        label = f"BUG:{type(exc).__name__}" if bug else type(exc).__name__
        self.text["extract" if stage == "extract" else "open"] = label
        self.text.setdefault("error", str(exc)[:200])
        self.log.append(f"{stage}: {type(exc).__name__}: {exc}")
        if bug:
            self.flag("bug")
            self.log.append(traceback.format_exc().rstrip())
        elif isinstance(exc, EncryptionError):
            self.flag("needs_password")
        else:
            self.flag(f"{stage}_error")

    @classmethod
    def from_csv(cls, record: Mapping[str, str]) -> _Row:
        """Rebuild a row written by an earlier run, for the summary of a resumed scan."""
        row = cls(path=record["path"])
        if record.get("file_size"):
            row.file_size = int(record["file_size"])
        for column in COLUMNS:
            value = record.get(column) or ""
            if not value or column in ("path", "file_size"):
                continue
            if column == "flags":
                row.flags = value.split()
            elif column == "diagnostics":
                for item in value.split():
                    code, _, count = item.rpartition("=")
                    row.diagnostics[code] = int(count)
            elif column in _NUMERIC_COLUMNS:
                row.numbers[column] = float(value)
            else:
                row.text[column] = value
        return row

    def csv_row(self) -> dict[str, object]:
        out: dict[str, object] = {"path": self.path, "file_size": self.file_size}
        out.update(self.text)
        out.update(self.numbers)
        out["flags"] = " ".join(self.flags)
        out["diagnostics"] = " ".join(
            f"{code}={n}" for code, n in sorted(self.diagnostics.items())
        )
        return out


def _format_name(fmt: archivey.ArchiveFormat) -> str:
    """``tar.gz``, ``zip``, ``raw_stream.xz``: the pair, without the class name."""
    stream = fmt.stream.value
    if stream == "uncompressed":
        return fmt.container.value
    return f"{fmt.container.value}.{stream}"


def _ratio(out: int, compressed: int | None) -> float | None:
    return round(out / compressed, 1) if compressed else None


def scan_one(
    path: Path, config: ArchiveyConfig, password: PasswordInput = None
) -> _Row | None:
    """Scan one file; ``None`` when it is not an archive.

    Every exit that returns a row goes through ``_finish``, so the flags the scan
    found reach the CSV whichever stage stopped it.
    """
    row = _Row(path=str(path), file_size=path.stat().st_size)
    PROBE.reset()
    try:
        info = archivey.detect_format(path, config=config)
    except FormatDetectionError:
        return None
    except Exception as exc:  # noqa: BLE001 - every failure becomes a row
        row.failure("detect", exc)
        return _finish(row)
    row.text["format"] = _format_name(info.format)
    row.text["detected"] = f"{info.detected_by}/{info.confidence.value}"
    if info.payload_offset:
        row.flag("sfx")
    if info.detected_by == "extension":
        row.flag("extension_only")

    started = time.perf_counter()
    try:
        _open_and_extract(path, config, row, password, info.diagnostics.counts)
    except Exception as exc:  # noqa: BLE001 - e.g. the reader's close() raising
        row.failure("scan", exc)
    seconds = time.perf_counter() - started
    row.numbers["seconds"] = round(seconds, 2)
    if seconds > _SLOW_SECONDS:
        row.flag("slow")
    return _finish(row)


def _finish(row: _Row) -> _Row:
    """Fold the probes into the row and derive the flags from everything recorded."""
    for column, value in (
        ("decoder_memory", PROBE.decoder_memory_peak),
        ("kdf_rounds", PROBE.kdf_rounds),
        ("spool_bytes", PROBE.spool_bytes),
    ):
        if value:
            row.numbers[column] = value
    if PROBE.decoder_memory_what:
        row.log.append(
            f"largest decoder allocation: {PROBE.decoder_memory_peak} bytes, "
            f"{PROBE.decoder_memory_what}"
        )
    for message in PROBE.warnings:
        row.log.append(f"warning: {message}")
        if message.startswith("Could not remove dry-run scratch directory"):
            row.flag("scratch_left_behind")
    _flag_limits(row)
    for code in sorted(row.diagnostics):
        if code not in _ROUTINE_DIAGNOSTICS:
            row.flag(f"diag:{code}")
    return row


def _open_and_extract(
    path: Path,
    config: ArchiveyConfig,
    row: _Row,
    password: PasswordInput,
    detection_diagnostics: Mapping[DiagnosticCode, int],
) -> None:
    try:
        reader = archivey.open_archive(path, config=config, password=password)
    except Exception as exc:  # noqa: BLE001
        row.failure("open", exc)
        # An open reader repeats detection's diagnostics in its own; without one,
        # they are only on the detection result, and a misdetection that made the
        # open fail is exactly where they matter.
        for code, count in detection_diagnostics.items():
            row.diagnostics[code.value] += count
        return
    with reader:
        row.text["open"] = "ok"
        ai = reader.info
        row.text["format"] = _format_name(ai.format)
        row.text["version"] = ai.format_version or ""
        row.text["solid"] = "yes" if ai.is_solid else ""
        row.text["encrypted"] = "yes" if ai.is_encrypted else ""

        member_bytes: dict[int, int] = {}  # id(member) -> bytes written
        total = [0]

        def on_progress(p: archivey.ExtractionProgress) -> None:
            member_bytes[id(p.member)] = p.member_bytes_written
            total[0] = p.bytes_written

        report = None
        try:
            with tempfile.TemporaryDirectory(prefix="archivey-scan-") as tmp:
                report = reader.extract_all(
                    Path(tmp) / "out",
                    dry_run=True,
                    on_error="continue",
                    on_progress=on_progress,
                )
        except Exception as exc:  # noqa: BLE001
            row.failure("extract", exc)
        row.numbers["bytes_written"] = total[0]

        listed = reader.members_report_if_available()
        members = listed.members if listed is not None else ()
        if listed is not None:
            row.numbers["members"] = len(members)
            row.text["codecs"] = " ".join(
                sorted(
                    {
                        "+".join(c.algo.value for c in m.compression)
                        for m in members
                        if m.compression
                    }
                )
            )
            if any(m.type is MemberType.OTHER for m in members):
                row.flag("device_or_fifo")
        tracker = getattr(reader, "_listing_tracker", None)
        if tracker is None:
            BROKEN_PROBES.add("metadata_bytes")
        else:
            row.numbers["metadata_bytes"] = tracker.metadata_bytes
        for code, count in reader.diagnostics.counts.items():
            row.diagnostics[code.value] += count

        if report is not None:
            _read_report(report, members, member_bytes, config, row)


def _read_report(
    report: archivey.ExtractionReport,
    members: tuple[archivey.ArchiveMember, ...],
    member_bytes: dict[int, int],
    config: ArchiveyConfig,
    row: _Row,
) -> None:
    statuses = Counter(r.status for r in report.results)
    failed = [r for r in report.results if r.status is ExtractionStatus.FAILED]
    blocked = [r for r in report.results if r.status is ExtractionStatus.BLOCKED]
    row.text["extract"] = "ok" if not failed and not blocked else "partial"
    if failed:
        row.flag("failed_members")
    if blocked:
        row.flag("blocked_members")
    for r in (failed + blocked)[:_LOGGED_MEMBER_ERRORS]:
        row.log.append(f"{r.status.value} {r.member.name!r}: {r.error}")
        row.text.setdefault("error", f"{type(r.error).__name__}: {r.error}"[:200])
    if any(isinstance(r.error, EncryptionError) for r in failed):
        row.flag("needs_password")
    # ``max_entries`` counts what an extraction leaves on disk, and refunds a
    # superseded entry, so EXTRACTED is the matching count here.
    row.numbers["entries_written"] = statuses[ExtractionStatus.EXTRACTED]
    written = int(row.numbers["bytes_written"])

    # A clean run writes exactly what the members declare; anything else is worth a
    # look. Only checked when every file member declares a size. This relies on the
    # default ``overwrite=ERROR``: a collision is a FAILED result, which makes the run
    # "partial" and skips the check. Under ``overwrite="skip"`` a NOT_OVERWRITTEN
    # member would declare a size and write nothing.
    files = [m for m in members if m.type is MemberType.FILE and m.is_current]
    if row.text["extract"] == "ok" and files and all(m.size is not None for m in files):
        declared = sum(m.size or 0 for m in files)
        if declared != written:
            row.flag("size_mismatch")
            row.log.append(f"members declare {declared} bytes, {written} were written")

    # ``max_ratio`` only looks at output past ``ratio_activation_threshold``, so the
    # ratio columns follow the same rule and compare with the limit.
    floor = config.extraction_limits.ratio_activation_threshold
    archive_ratio = _ratio(written, row.file_size)
    if written > floor and archive_ratio is not None:
        row.numbers["archive_ratio"] = archive_ratio
    best: tuple[float, str] | None = None
    for r in report.results:
        out = member_bytes.get(id(r.member), 0)
        ratio = _ratio(out, r.member.compressed_size)
        if ratio is not None and out > floor and (best is None or ratio > best[0]):
            best = (ratio, r.member.name)
    if best is not None:
        row.numbers["max_member_ratio"] = best[0]
        row.log.append(f"highest member ratio: {best[0]}:1, {best[1]!r}")


def _flag_limits(row: _Row) -> None:
    for column, limit, default in _LIMITS:
        value = row.numbers.get(column)
        if value is None or default is None:
            continue
        if value > default:
            row.flag(f"over:{limit}")
        elif value > default * _NEAR:
            row.flag(f"near:{limit}")


def _list_dir(directory: str) -> tuple[list[str], list[str]]:
    """The regular files and the subdirectories of ``directory``, each sorted.

    Symlinks are neither followed nor scanned.
    """
    files: list[str] = []
    dirs: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        dirs.append(entry.path)
                    elif entry.is_file():
                        files.append(entry.path)
                except OSError:
                    continue
    except OSError as exc:
        print(f"skipped {directory}: {exc}", file=sys.stderr)
    return sorted(files), sorted(dirs)


def _walk(
    root: Path, done_dirs: frozenset[str] = frozenset()
) -> Iterator[tuple[str, str]]:
    """Yield ``("file", path)`` for each file, depth first, as the tree is listed.

    After the last item under a directory, yield ``("dir", path)`` for it, so a
    directory is reported only once its whole subtree has been handed out. A directory
    in ``done_dirs`` is not listed at all.
    """
    if root.is_file():
        yield ("file", str(root))
        return
    top = str(root)
    if top in done_dirs:
        return
    # Each entry: a directory, and an iterator over the subdirectories still to visit.
    stack: list[tuple[str, Iterator[str]]] = []

    def enter(directory: str) -> Iterator[tuple[str, str]]:
        files, dirs = _list_dir(directory)
        stack.append((directory, iter([d for d in dirs if d not in done_dirs])))
        for path in files:
            yield ("file", path)

    yield from enter(top)
    while stack:
        directory, subdirs = stack[-1]
        nxt = next(subdirs, None)
        if nxt is None:
            stack.pop()
            yield ("dir", directory)
        else:
            yield from enter(nxt)


class _Progress:
    """The checkpoint a resumed scan starts from: one JSON object per line.

    The scan's root and ``--default-limits`` first, then ``{"start": path}`` before each file is scanned,
    ``{"file": path}`` after it, and ``{"dir": path}`` once a directory's whole subtree
    is done. JSON keeps any path intact, newlines and undecodable bytes included.
    """

    def __init__(
        self, path: Path, root: Path, default_limits: bool, resume: bool
    ) -> None:
        self.done_dirs: set[str] = set()
        self.done_files: set[str] = set()
        self.unfinished: str | None = None
        if resume and path.exists():
            self._load(path, {"root": str(root), "default_limits": default_limits})
        elif resume:
            raise SystemExit(f"--resume: no progress file {path}")
        self._file = path.open("a" if resume else "w", encoding="utf-8")
        if not resume:
            self._write({"root": str(root), "default_limits": default_limits})

    def _load(self, path: Path, header: dict[str, object]) -> None:
        with path.open(encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # The last line of a run that was killed mid-write.
                    continue
                if number == 1:
                    if entry != header:
                        raise SystemExit(
                            f"--resume: {path} is a scan with {entry}, "
                            f"not {header}; start a new scan instead"
                        )
                elif "start" in entry:
                    self.unfinished = entry["start"]
                elif "file" in entry:
                    self.done_files.add(entry["file"])
                    if entry["file"] == self.unfinished:
                        self.unfinished = None
                elif "dir" in entry:
                    self.done_dirs.add(entry["dir"])

    def _write(self, entry: Mapping[str, object]) -> None:
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def start(self, path: str) -> None:
        self._write({"start": path})

    def file_done(self, path: str) -> None:
        self._write({"file": path})

    def dir_done(self, path: str) -> None:
        self._write({"dir": path})

    def close(self) -> None:
        self._file.close()


def _load_rows(csv_path: Path) -> list[_Row]:
    with csv_path.open(newline="", encoding="utf-8", errors="surrogateescape") as fh:
        return [_Row.from_csv(record) for record in csv.DictReader(fh)]


def _passwords(args: argparse.Namespace) -> list[str]:
    passwords = list(args.password)
    if args.password_file is not None:
        lines = args.password_file.read_text(encoding="utf-8").splitlines()
        passwords += [line for line in lines if line]
    return passwords


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="file or directory to scan")
    parser.add_argument("-o", "--output", type=Path, default=Path("scan.csv"))
    parser.add_argument(
        "--password",
        action="append",
        default=[],
        help=(
            "a password to try on encrypted archives; repeat for several "
            "(visible in process lists: prefer --password-file)"
        ),
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        help="a file of passwords to try, one per line",
    )
    parser.add_argument(
        "--default-limits",
        action="store_true",
        help="scan under the default limits instead of turning the counting ones off",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "continue an interrupted scan of the same root into the same output, "
            "skipping what its .progress file records as done"
        ),
    )
    args = parser.parse_args(argv)
    passwords = _passwords(args) or None

    archivey_logger = logging.getLogger("archivey")
    archivey_logger.addHandler(_WarningCapture(logging.WARNING))
    # The capture keeps every record for the log; stderr is for the progress lines.
    archivey_logger.propagate = False
    failed_probes = _install_probes()
    config = _scan_config(args.default_limits)
    log_path = args.output.with_suffix(".log")

    progress_path = args.output.with_suffix(".progress")
    rows: list[_Row] = []
    if args.resume:
        if not args.output.exists():
            raise SystemExit(f"--resume: no CSV {args.output}")
        rows = _load_rows(args.output)
    progress = _Progress(progress_path, args.root, args.default_limits, args.resume)
    # A row reaches the CSV before its file is marked done, so a run stopped between
    # the two must not write the row twice.
    done_files = progress.done_files | {row.path for row in rows}
    if progress.unfinished is not None:
        print(
            f"the previous run stopped while scanning {progress.unfinished}; "
            "scanning it again",
            file=sys.stderr,
        )
    mode = "a" if args.resume else "w"
    # ``surrogateescape``: a file name that is not valid UTF-8 keeps its bytes.
    with (
        args.output.open(
            mode, newline="", encoding="utf-8", errors="surrogateescape"
        ) as csv_file,
        log_path.open(mode, encoding="utf-8", errors="surrogateescape") as log,
        contextlib.closing(progress),
    ):
        writer = csv.DictWriter(csv_file, fieldnames=COLUMNS)
        if args.resume:
            log.write(f"== resumed with {len(rows)} archives already scanned\n\n")
        else:
            writer.writeheader()
        try:
            for kind, name in _walk(args.root, frozenset(progress.done_dirs)):
                if kind == "dir":
                    progress.dir_done(name)
                    continue
                if name in done_files:
                    continue
                progress.start(name)
                path = Path(name)
                try:
                    row = scan_one(path, config, passwords)
                except OSError as exc:
                    print(f"skipped {path}: {exc}", file=sys.stderr)
                    progress.file_done(name)
                    continue
                if row is not None:
                    rows.append(row)
                    writer.writerow(row.csv_row())
                    csv_file.flush()
                    if row.flags:
                        log.write(f"== {path}\nflags: {' '.join(row.flags)}\n")
                        log.writelines(f"  {line}\n" for line in row.log)
                        log.write("\n")
                        log.flush()
                    status = " ".join(row.flags) or "ok"
                    print(f"{len(rows):>6} {status:<40.40} {path}", file=sys.stderr)
                progress.file_done(name)
        except KeyboardInterrupt:
            print(
                f"\ninterrupted; summarizing what was scanned "
                f"(--resume continues from {progress_path})",
                file=sys.stderr,
            )
        summary = _summary(rows)
        broken = sorted({*failed_probes, *BROKEN_PROBES})
        if broken:
            summary += f"warning: probes not installed: {', '.join(broken)}\n"
        log.write(summary)
    print(f"\n{summary}\nwrote {args.output} and {log_path}", file=sys.stderr)
    return 0


def _counts(title: str, counter: Counter[str]) -> list[str]:
    lines = [f"{title}:"]
    lines += [f"  {n:>7}  {key}" for key, n in counter.most_common()]
    return lines


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def _summary(rows: list[_Row]) -> str:
    lines = [f"== summary: {len(rows)} archives"]
    lines += _counts("formats", Counter(r.text.get("format", "?") for r in rows))
    lines += _counts("open", Counter(r.text.get("open", "?") for r in rows))
    lines += _counts("extract", Counter(r.text.get("extract", "-") for r in rows))
    lines += _counts("flags (archives)", Counter(f for r in rows for f in r.flags))
    lines += _counts(
        "diagnostics (archives)", Counter(c for r in rows for c in r.diagnostics)
    )
    # ``near`` here is the ``near:`` flag's definition: past half, not over.
    lines.append(
        f"{'limit':<26}{'default':>14}{'max':>14}{'p99':>14}{'p50':>12}"
        f"{'near':>7}{'over':>6}"
    )
    for column, _limit, default in _LIMITS:
        seen = [r.numbers[column] for r in rows if column in r.numbers]
        if not seen:
            lines.append(f"{column:<26}{default!s:>14}  no values")
            continue
        over = near = 0
        if default is not None:
            over = sum(1 for s in seen if s > default)
            near = sum(1 for s in seen if default * _NEAR < s <= default)
        lines.append(
            f"{column:<26}{default!s:>14}{max(seen):>14.0f}"
            f"{_percentile(seen, 0.99):>14.0f}{_percentile(seen, 0.5):>12.0f}"
            f"{near:>7}{over:>6}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

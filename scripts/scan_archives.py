"""Dry-run every archive under a directory: statistics, and a list of files to check.

    python scripts/scan_archives.py ~/backups -o scan.csv [--password PW ...]

Each archive goes through ``extract_all(dry_run=True)``, the real extraction pass with
file bodies discarded, so nothing is kept on disk. Files are found by content, not by
extension; a file ``detect_format`` cannot place is skipped.

Three outputs:

- ``scan.csv``: one short row per archive. The ``flags`` column says why an archive is
  worth a closer look (``bug``, ``open_error``, ``failed_members``, ``over:max_ratio``,
  ``diag:archive_trailing_data``, ...); filter on it. Empty flags means nothing stood
  out.
- ``scan.log``: the detail for every flagged archive (error messages, the members
  that failed, what declared the largest decoder allocation, tracebacks), then the
  summary.
- The summary, also printed at the end: counts by format and outcome, how many
  archives carry each flag and diagnostic, and for every configurable limit the
  largest value seen, the 99th percentile, and how many archives are over the default
  or past half of it.

**Limits.** By default the scan turns off the limits that only count something (bytes,
entries, ratio, members, metadata, key-derivation rounds, spool), so the columns show
the true value rather than stopping at the cap. The two that protect the scanning
machine, ``max_decoder_memory`` and ``max_ppmd_in_process_input``, stay at their
defaults. ``--default-limits`` scans under the full default config instead.

**Best-effort columns.** ``decoder_memory``, ``kdf_rounds`` and ``spool_bytes`` have no
public API yet. The script reads them by wrapping three internal functions for the
duration of the scan, so they can silently stop working when those internals change.
``decoder_memory`` is the largest allocation an archive *declared* that archivey checks
itself; xz and zstd hand the limit to liblzma / libzstd and are not counted.
``metadata_bytes`` reads the reader's private listing tracker.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import tempfile
import time
import traceback
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import archivey
from archivey import (
    ArchiveyConfig,
    ArchiveyError,
    DecoderLimits,
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
    kdf_rounds: int = 0
    spool_bytes: int = 0

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]


PROBE = _Probe()


def _install_probes() -> list[str]:
    """Wrap the internal check points; return the names of the ones that failed."""
    failed: list[str] = []
    # Backends import lazily, and a module imported after the patch keeps the
    # original binding, so everything is imported first.
    import importlib
    import pkgutil

    for mod in pkgutil.walk_packages(archivey.__path__, "archivey."):
        try:
            importlib.import_module(mod.name)
        except Exception:  # noqa: BLE001, S112 - optional dependency missing
            continue
    try:
        from archivey.internal import config as internal_config

        original_exceeds = internal_config.exceeds_decoder_memory

        def exceeds(declared: int, limits: DecoderLimits) -> bool:
            if declared > PROBE.decoder_memory_peak:
                PROBE.decoder_memory_peak = declared
            return original_exceeds(declared, limits)

        # ``check_decoder_memory`` calls it through its module's global; the codecs
        # that branch on it imported the name, so every binding is replaced.
        for name, module in list(sys.modules.items()):
            if name.startswith("archivey") and (
                getattr(module, "exceeds_decoder_memory", None) is original_exceeds
            ):
                module.exceeds_decoder_memory = exceeds  # type: ignore[attr-defined]

        original_check = internal_config.check_decoder_memory

        def check(declared: int, **kwargs: object) -> None:
            if declared >= PROBE.decoder_memory_peak:
                PROBE.decoder_memory_what = str(kwargs.get("what", ""))
            original_check(declared, **kwargs)  # type: ignore[arg-type]

        for name, module in list(sys.modules.items()):
            if name.startswith("archivey") and (
                getattr(module, "check_decoder_memory", None) is original_check
            ):
                module.check_decoder_memory = check  # type: ignore[attr-defined]

        budget_cls = internal_config.KeyDerivationBudget
        original_spend = budget_cls.spend

        def spend(self: object, rounds: int, *, what: str) -> None:
            original_spend(self, rounds, what=what)  # type: ignore[arg-type]
            PROBE.kdf_rounds += rounds

        budget_cls.spend = spend  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001 - a probe that cannot install is only reported
        failed.append("decoder_memory/kdf")
    try:
        from archivey.internal.spool import SpoolBudget

        original_copy = SpoolBudget.copy

        def copy(self: SpoolBudget, src: object, out: object) -> None:
            try:
                original_copy(self, src, out)  # type: ignore[arg-type]
            finally:
                PROBE.spool_bytes = max(PROBE.spool_bytes, self._written)

        SpoolBudget.copy = copy  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001
        failed.append("spool")
    return failed


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
    ("spool_bytes", "spool_max_bytes", SpoolLimits().max_bytes),
)
# A value past this share of its default gets a ``near:`` flag.
_NEAR = 0.5
# Diagnostics too common in ordinary archives to flag on their own.
_ROUTINE_DIAGNOSTICS = frozenset(
    {"member_name_normalized", "member_name_encoding_inferred"}
)
_SLOW_SECONDS = 60.0
_LOGGED_MEMBER_ERRORS = 10

COLUMNS = (
    "path file_size format version solid encrypted open extract flags seconds "
    "members entries_written bytes_written archive_ratio max_member_ratio "
    "metadata_bytes decoder_memory kdf_rounds spool_bytes codecs diagnostics error"
).split()


@dataclass
class _Row:
    values: dict[str, object] = field(default_factory=dict)
    diagnostics: Counter[str] = field(default_factory=Counter)
    flags: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def flag(self, name: str) -> None:
        if name not in self.flags:
            self.flags.append(name)

    def failure(self, stage: str, exc: BaseException) -> None:
        """Record an exception that ended ``stage`` (detect, open or extract)."""
        bug = not isinstance(exc, ArchiveyError)
        label = f"BUG:{type(exc).__name__}" if bug else type(exc).__name__
        self.values["open" if stage != "extract" else "extract"] = label
        self.values.setdefault("error", str(exc)[:200])
        self.log.append(f"{stage}: {type(exc).__name__}: {exc}")
        if bug:
            self.flag("bug")
            self.log.append(traceback.format_exc().rstrip())
        elif isinstance(exc, EncryptionError):
            self.flag("needs_password")
        else:
            self.flag(f"{stage}_error")


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
    """Scan one file; ``None`` when it is not an archive."""
    row = _Row()
    v = row.values
    v["path"] = str(path)
    v["file_size"] = path.stat().st_size
    try:
        info = archivey.detect_format(path, config=config)
    except FormatDetectionError:
        return None
    except Exception as exc:  # noqa: BLE001 - every failure becomes a row
        row.failure("detect", exc)
        return row
    v["format"] = _format_name(info.format)
    if info.payload_offset:
        row.flag("sfx")
    if info.detected_by == "extension":
        row.flag("extension_only")

    PROBE.reset()
    started = time.perf_counter()
    try:
        _open_and_extract(path, config, row, password)
    finally:
        seconds = time.perf_counter() - started
        v["seconds"] = round(seconds, 2)
        if seconds > _SLOW_SECONDS:
            row.flag("slow")
        v["decoder_memory"] = PROBE.decoder_memory_peak or None
        v["kdf_rounds"] = PROBE.kdf_rounds or None
        v["spool_bytes"] = PROBE.spool_bytes or None
        if PROBE.decoder_memory_what:
            row.log.append(
                f"largest decoder allocation: {PROBE.decoder_memory_peak} bytes, "
                f"{PROBE.decoder_memory_what}"
            )
    _flag_limits(row)
    for code in sorted(row.diagnostics):
        if code not in _ROUTINE_DIAGNOSTICS:
            row.flag(f"diag:{code}")
    v["flags"] = " ".join(row.flags)
    v["diagnostics"] = " ".join(f"{c}={n}" for c, n in sorted(row.diagnostics.items()))
    return row


def _open_and_extract(
    path: Path, config: ArchiveyConfig, row: _Row, password: PasswordInput
) -> None:
    v = row.values
    try:
        reader = archivey.open_archive(path, config=config, password=password)
    except Exception as exc:  # noqa: BLE001
        row.failure("open", exc)
        return
    with reader:
        v["open"] = "ok"
        ai = reader.info
        v["format"] = _format_name(ai.format)
        v["version"] = ai.format_version
        v["solid"] = ai.is_solid or None
        v["encrypted"] = ai.is_encrypted or None

        member_bytes: dict[int, int] = {}  # id(member) -> bytes written

        def on_progress(p: archivey.ExtractionProgress) -> None:
            member_bytes[id(p.member)] = p.member_bytes_written
            v["bytes_written"] = p.bytes_written

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

        listed = reader.members_report_if_available()
        members = listed.members if listed is not None else ()
        if listed is not None:
            v["members"] = len(members)
            v["codecs"] = " ".join(
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
        if tracker is not None:
            v["metadata_bytes"] = tracker.metadata_bytes
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
    v = row.values
    statuses = Counter(r.status for r in report.results)
    failed = [r for r in report.results if r.status is ExtractionStatus.FAILED]
    blocked = [r for r in report.results if r.status is ExtractionStatus.BLOCKED]
    v["extract"] = "ok" if not failed and not blocked else "partial"
    if failed:
        row.flag("failed_members")
    if blocked:
        row.flag("blocked_members")
    for r in (failed + blocked)[:_LOGGED_MEMBER_ERRORS]:
        row.log.append(f"{r.status.value} {r.member.name!r}: {r.error}")
        v.setdefault("error", f"{type(r.error).__name__}: {r.error}"[:200])
    if any(isinstance(r.error, EncryptionError) for r in failed):
        row.flag("needs_password")
    v["entries_written"] = statuses[ExtractionStatus.EXTRACTED]
    written = int(v.setdefault("bytes_written", 0))  # type: ignore[call-overload]

    # A clean run writes exactly what the members declare; anything else is worth a
    # look. Only checked when every file member declares a size.
    files = [m for m in members if m.type is MemberType.FILE and m.is_current]
    if v["extract"] == "ok" and files and all(m.size is not None for m in files):
        declared = sum(m.size or 0 for m in files)
        if declared != written:
            row.flag("size_mismatch")
            row.log.append(f"members declare {declared} bytes, {written} were written")

    # ``max_ratio`` only looks at output past ``ratio_activation_threshold``, so the
    # ratio columns follow the same rule and compare with the limit.
    floor = config.extraction_limits.ratio_activation_threshold
    if written > floor:
        v["archive_ratio"] = _ratio(written, v.get("file_size"))  # type: ignore[arg-type]
    best: tuple[float, str] | None = None
    for r in report.results:
        out = member_bytes.get(id(r.member), 0)
        ratio = _ratio(out, r.member.compressed_size)
        if ratio is not None and out > floor and (best is None or ratio > best[0]):
            best = (ratio, r.member.name)
    if best is not None:
        v["max_member_ratio"] = best[0]
        row.log.append(f"highest member ratio: {best[0]}:1, {best[1]!r}")


def _flag_limits(row: _Row) -> None:
    for column, limit, default in _LIMITS:
        value = row.values.get(column)
        if value is None or default is None:
            continue
        if float(value) > default:  # type: ignore[arg-type]
            row.flag(f"over:{limit}")
        elif float(value) > default * _NEAR:  # type: ignore[arg-type]
            row.flag(f"near:{limit}")


def _walk(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="file or directory to scan")
    parser.add_argument("-o", "--output", type=Path, default=Path("scan.csv"))
    parser.add_argument(
        "--password",
        action="append",
        default=[],
        help="a password to try on encrypted archives; repeat for several",
    )
    parser.add_argument(
        "--default-limits",
        action="store_true",
        help="scan under the default limits instead of turning the counting ones off",
    )
    args = parser.parse_args(argv)
    # Every warning archivey logs is also a diagnostic, which the CSV counts.
    logging.getLogger("archivey").setLevel(logging.ERROR)
    failed_probes = _install_probes()
    if failed_probes:
        print(f"warning: probes not installed: {failed_probes}", file=sys.stderr)
    config = _scan_config(args.default_limits)
    log_path = args.output.with_suffix(".log")

    rows: list[_Row] = []
    with (
        args.output.open("w", newline="", encoding="utf-8") as csv_file,
        log_path.open("w", encoding="utf-8") as log,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=COLUMNS)
        writer.writeheader()
        for path in _walk(args.root):
            try:
                row = scan_one(path, config, args.password or None)
            except OSError as exc:
                print(f"skipped {path}: {exc}", file=sys.stderr)
                continue
            if row is None:
                continue
            rows.append(row)
            writer.writerow(row.values)
            csv_file.flush()
            if row.flags:
                log.write(f"== {path}\nflags: {' '.join(row.flags)}\n")
                log.writelines(f"  {line}\n" for line in row.log)
                log.write("\n")
                log.flush()
            print(
                f"{len(rows):>6} {' '.join(row.flags) or 'ok':<40.40} {path}",
                file=sys.stderr,
            )
        summary = _summary(rows)
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
    values = [r.values for r in rows]
    lines = [f"== summary: {len(rows)} archives"]
    lines += _counts("formats", Counter(str(v.get("format")) for v in values))
    lines += _counts("open", Counter(str(v.get("open")) for v in values))
    lines += _counts("extract", Counter(str(v.get("extract", "-")) for v in values))
    lines += _counts("flags (archives)", Counter(f for r in rows for f in r.flags))
    lines += _counts(
        "diagnostics (archives)", Counter(c for r in rows for c in r.diagnostics)
    )
    lines.append(
        f"{'limit':<26}{'default':>14}{'max':>14}{'p99':>14}{'p50':>12}"
        f"{'>half':>7}{'over':>6}"
    )
    for column, limit, default in _LIMITS:
        seen = [float(v[column]) for v in values if v.get(column) is not None]  # type: ignore[arg-type]
        if not seen:
            lines.append(f"{column:<26}{default!s:>14}  no values")
            continue
        over = sum(1 for s in seen if default is not None and s > default)
        near = sum(1 for s in seen if default is not None and s > default * _NEAR)
        lines.append(
            f"{column:<26}{default!s:>14}{max(seen):>14.0f}"
            f"{_percentile(seen, 0.99):>14.0f}{_percentile(seen, 0.5):>12.0f}"
            f"{near:>7}{over:>6}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

"""Dry-run every archive under a directory and write one CSV row per archive.

The point is a corpus survey: which archives open and extract, what they are made of,
which diagnostics they raise, and how close each one comes to every configurable
resource limit, so the defaults can be tuned against real data. Nothing is written
outside a temporary directory: each archive goes through
``extract_all(dry_run=True)``, the real extraction pass with file bodies discarded.

    python scripts/scan_archives.py ~/backups -o scan.csv

Files are found by content, not by extension: a file ``detect_format`` cannot place is
skipped (``--all`` gives it a row anyway). Unexpected exceptions, the ones that are not
an ``ArchiveyError`` and so are archivey bugs, are counted in the ``open_result`` or
``extract_result`` column as ``BUG:<type>`` and their tracebacks go to
``<output>.bugs.txt``.

**Limits.** By default the scan turns off the limits that only count something (bytes,
entries, ratio, members, metadata, key-derivation rounds, spool), so the columns show
the true peak rather than stopping at the cap. Every ``*_peak`` column has a matching
default in the summary printed at the end, which counts the archives over it. The two
limits that protect the scanning machine, ``max_decoder_memory`` and
``max_ppmd_in_process_input``, stay at their defaults. ``--default-limits`` scans under
the full default config instead.

**Best-effort columns.** ``decoder_memory_peak``, ``kdf_rounds`` and ``spool_bytes``
have no public API yet. The script reads them by wrapping three internal functions for
the duration of the scan, so they can silently stop working when those internals
change. ``decoder_memory_peak`` is the largest allocation an archive *declared* that
archivey checks itself; xz and zstd hand the limit to liblzma / libzstd and are not
counted. ``listing_metadata_bytes`` reads the reader's private listing tracker.
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
    ExtractionLimits,
    ExtractionStatus,
    FormatDetectionError,
    ListingLimits,
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


# Defaults the summary compares the peaks against, as (column, default).
_DEFAULTS: tuple[tuple[str, float | None], ...] = (
    ("bytes_written", ExtractionLimits().max_extracted_bytes),
    ("entries_written", ExtractionLimits().max_entries),
    ("archive_ratio", ExtractionLimits().max_ratio),
    ("max_member_ratio", ExtractionLimits().max_ratio),
    ("member_count", ListingLimits().max_members),
    ("listing_metadata_bytes", ListingLimits().max_metadata_bytes),
    ("decoder_memory_peak", DecoderLimits().max_decoder_memory),
    ("kdf_rounds", DecoderLimits().max_key_derivation_rounds),
    ("spool_bytes", SpoolLimits().max_bytes),
)

_RESULT_STATUSES = tuple(s for s in ExtractionStatus)


@dataclass
class _Row:
    values: dict[str, object] = field(default_factory=dict)
    diagnostics: Counter[str] = field(default_factory=Counter)


def _format_name(fmt: archivey.ArchiveFormat) -> str:
    """``tar.gz``, ``zip``, ``raw_stream.xz``: the pair, without the class name."""
    stream = fmt.stream.value
    return (
        fmt.container.value
        if stream == "uncompressed"
        else f"{fmt.container.value}.{stream}"
    )


def _error_label(exc: BaseException) -> str:
    if isinstance(exc, ArchiveyError):
        return type(exc).__name__
    return f"BUG:{type(exc).__name__}"


def _ratio(out: int, compressed: int | None) -> float | None:
    if not compressed:
        return None
    return round(out / compressed, 2)


def scan_one(
    path: Path, config: ArchiveyConfig, bugs: list[str], password: PasswordInput = None
) -> _Row | None:
    """Scan one file; ``None`` when it is not an archive."""
    row = _Row()
    v = row.values
    v["path"] = str(path)
    try:
        v["file_size"] = path.stat().st_size
    except OSError as exc:
        v["open_result"] = f"OSError:{exc.errno}"
        return row

    try:
        info = archivey.detect_format(path, config=config)
    except FormatDetectionError:
        return None
    except Exception as exc:  # noqa: BLE001 - every failure becomes a row
        v["open_result"] = _error_label(exc)
        v["error"] = str(exc)[:300]
        if not isinstance(exc, ArchiveyError):
            bugs.append(f"{path} (detect)\n{traceback.format_exc()}")
        return row
    v["format"] = _format_name(info.format)
    v["detected_by"] = info.detected_by
    v["confidence"] = info.confidence.value
    v["payload_offset"] = info.payload_offset
    if info.cost_receipt is not None:
        v["detect_unique_bytes"] = info.cost_receipt.unique_bytes_read
        v["detect_decode_input"] = info.cost_receipt.decode_input
        v["detect_decode_output"] = info.cost_receipt.decode_output

    PROBE.reset()
    started = time.perf_counter()
    try:
        _open_and_extract(path, config, row, bugs, password)
    finally:
        v["seconds"] = round(time.perf_counter() - started, 3)
        v["decoder_memory_peak"] = PROBE.decoder_memory_peak or None
        v["decoder_memory_what"] = PROBE.decoder_memory_what or None
        v["kdf_rounds"] = PROBE.kdf_rounds or None
        v["spool_bytes"] = PROBE.spool_bytes or None
    return row


def _open_and_extract(
    path: Path,
    config: ArchiveyConfig,
    row: _Row,
    bugs: list[str],
    password: PasswordInput,
) -> None:
    v = row.values
    try:
        reader = archivey.open_archive(path, config=config, password=password)
    except Exception as exc:  # noqa: BLE001
        v["open_result"] = _error_label(exc)
        v["error"] = str(exc)[:300]
        if not isinstance(exc, ArchiveyError):
            bugs.append(f"{path} (open)\n{traceback.format_exc()}")
        return
    with reader:
        v["open_result"] = "ok"
        ai = reader.info
        v["format"] = _format_name(ai.format)
        v["format_version"] = ai.format_version
        v["is_solid"] = ai.is_solid
        v["is_encrypted"] = ai.is_encrypted
        v["is_multivolume"] = ai.is_multivolume
        v["solid_blocks"] = ai.cost.solid_block_count
        v["listing_cost"] = ai.cost.listing_cost.value

        member_sizes: dict[int, int] = {}  # id(member) -> bytes written

        def on_progress(p: archivey.ExtractionProgress) -> None:
            member_sizes[id(p.member)] = p.member_bytes_written
            v["bytes_written"] = p.bytes_written

        try:
            with tempfile.TemporaryDirectory(prefix="archivey-scan-") as tmp:
                report = reader.extract_all(
                    Path(tmp) / "out",
                    dry_run=True,
                    on_error="continue",
                    on_progress=on_progress,
                )
        except Exception as exc:  # noqa: BLE001
            v["extract_result"] = _error_label(exc)
            v["error"] = str(exc)[:300]
            if not isinstance(exc, ArchiveyError):
                bugs.append(f"{path} (extract)\n{traceback.format_exc()}")
            report = None

        members = reader.members_report_if_available()
        if members is not None:
            _describe_members(members.members, v)
        tracker = getattr(reader, "_listing_tracker", None)
        if tracker is not None:
            v["listing_metadata_bytes"] = tracker.metadata_bytes
        for code, count in reader.diagnostics.counts.items():
            row.diagnostics[code.value] += count

        if report is None:
            return
        statuses = Counter(r.status for r in report.results)
        for status in _RESULT_STATUSES:
            if statuses[status]:
                v[f"results_{status.value}"] = statuses[status]
        bad = statuses[ExtractionStatus.FAILED] + statuses[ExtractionStatus.BLOCKED]
        v["extract_result"] = "ok" if not bad else "partial"
        first_error = next((r.error for r in report.results if r.error), None)
        if first_error is not None and "error" not in v:
            v["error"] = f"{type(first_error).__name__}: {first_error}"[:300]
        v["entries_written"] = statuses[ExtractionStatus.EXTRACTED]
        v.setdefault("bytes_written", 0)
        written = int(v["bytes_written"])  # type: ignore[call-overload]
        # ``max_ratio`` only looks at output past ``ratio_activation_threshold``, so
        # the ratio columns follow the same rule and compare with the limit.
        # ``max_member_ratio_any`` includes the small members it never checks; the
        # archive's own figure is ``bytes_written / file_size`` when it is under.
        floor = config.extraction_limits.ratio_activation_threshold
        if written > floor:
            v["archive_ratio"] = _ratio(written, v.get("file_size"))  # type: ignore[arg-type]
        best: tuple[float, str] | None = None
        best_any: float | None = None
        for r in report.results:
            out = member_sizes.get(id(r.member), 0)
            ratio = _ratio(out, r.member.compressed_size)
            if ratio is None:
                continue
            best_any = ratio if best_any is None else max(best_any, ratio)
            if out > floor and (best is None or ratio > best[0]):
                best = (ratio, r.member.name)
        v["max_member_ratio_any"] = best_any
        if best is not None:
            v["max_member_ratio"], v["max_member_ratio_name"] = best


def _describe_members(members: tuple[archivey.ArchiveMember, ...], v: dict) -> None:
    v["member_count"] = len(members)
    types = Counter(m.type.value for m in members)
    for kind in ("file", "directory", "symlink", "hardlink", "other", "anti"):
        if types[kind]:
            v[f"members_{kind}"] = types[kind]
    v["encrypted_members"] = sum(m.is_encrypted for m in members) or None
    v["declared_size"] = sum(m.size or 0 for m in members)
    v["largest_member"] = max((m.size or 0 for m in members), default=0)
    codecs = sorted(
        {
            "+".join(c.algo.value for c in m.compression)
            for m in members
            if m.compression
        }
    )
    v["codecs"] = " ".join(codecs) or None


def _walk(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


_FIXED_COLUMNS = (
    "path file_size format format_version detected_by confidence payload_offset "
    "is_solid is_encrypted is_multivolume solid_blocks listing_cost "
    "open_result extract_result error seconds "
    "member_count members_file members_directory members_symlink members_hardlink "
    "members_other members_anti encrypted_members codecs declared_size largest_member "
    "bytes_written entries_written archive_ratio max_member_ratio max_member_ratio_name max_member_ratio_any "
    "listing_metadata_bytes decoder_memory_peak decoder_memory_what kdf_rounds "
    "spool_bytes detect_unique_bytes detect_decode_input detect_decode_output"
).split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="file or directory to scan")
    parser.add_argument("-o", "--output", type=Path, default=Path("scan.csv"))
    parser.add_argument(
        "--all", action="store_true", help="give non-archives a row too"
    )
    parser.add_argument(
        "--default-limits",
        action="store_true",
        help="scan under the default limits instead of turning the counting ones off",
    )
    parser.add_argument(
        "--password",
        action="append",
        default=[],
        help="a password to try on encrypted archives; repeat for several",
    )
    args = parser.parse_args(argv)
    # Every warning archivey logs is also a diagnostic, which the CSV counts.
    logging.getLogger("archivey").setLevel(logging.ERROR)

    failed_probes = _install_probes()
    if failed_probes:
        print(f"warning: probes not installed: {failed_probes}", file=sys.stderr)
    config = _scan_config(args.default_limits)

    rows: list[_Row] = []
    bugs: list[str] = []
    for path in _walk(args.root):
        row = scan_one(path, config, bugs, args.password or None)
        if row is None:
            if args.all:
                rows.append(_Row({"path": str(path), "open_result": "not_archive"}))
            continue
        rows.append(row)
        v = row.values
        print(
            f"{v.get('open_result', '?'):>10} {v.get('extract_result', '-'):>10} "
            f"{v.get('format', '?'):>8}  {path}",
            file=sys.stderr,
        )

    result_cols = [f"results_{s.value}" for s in _RESULT_STATUSES]
    diag_codes = sorted({c for r in rows for c in r.diagnostics})
    header = _FIXED_COLUMNS + result_cols + [f"diag_{c}" for c in diag_codes]
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            out = dict(row.values)
            out.update({f"diag_{c}": n for c, n in row.diagnostics.items()})
            writer.writerow(out)
    if bugs:
        args.output.with_suffix(".bugs.txt").write_text("\n\n".join(bugs))

    _print_summary(rows, len(bugs), args.output)
    return 0


def _print_summary(rows: list[_Row], bug_count: int, output: Path) -> None:
    archives = [r.values for r in rows if r.values.get("open_result") != "not_archive"]
    print(f"\n{len(archives)} archives -> {output}", file=sys.stderr)
    print(
        "open:    "
        + ", ".join(
            f"{k}={n}"
            for k, n in Counter(
                str(v.get("open_result")) for v in archives
            ).most_common()
        ),
        file=sys.stderr,
    )
    print(
        "extract: "
        + ", ".join(
            f"{k}={n}"
            for k, n in Counter(
                str(v.get("extract_result")) for v in archives
            ).most_common()
        ),
        file=sys.stderr,
    )
    if bug_count:
        print(
            f"bugs:    {bug_count} (tracebacks in {output.with_suffix('.bugs.txt')})",
            file=sys.stderr,
        )
    print("\nlimit                      max seen     default  over", file=sys.stderr)
    for column, default in _DEFAULTS:
        seen = [float(v[column]) for v in archives if v.get(column) is not None]
        peak = max(seen, default=0)
        over = sum(1 for s in seen if default is not None and s > default)
        print(
            f"{column:<24} {peak:>12.0f} {default if default is not None else '-':>11}"
            f"  {over}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())

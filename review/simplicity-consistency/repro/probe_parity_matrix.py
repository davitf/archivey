#!/usr/bin/env python3
"""Observable parity probe for the simplicity-consistency review.

Generates the observed column of the cross-format matrix by running code against
the declarative corpus (and a few synthetic archives for pipe / password cells).

Run from the repo root ([all] config):

    uv run --no-sync python review/simplicity-consistency/repro/probe_parity_matrix.py

Every cell is tagged observed / skipped-unavailable / n/a-with-reason.
This script does not change library behaviour — it records what ships today.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allow ``from tests...`` when run as a script from the repo root.
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from archivey import (
    ArchiveFormat,
    FormatSupport,
    extract,
    format_availability,
    open_archive,
    open_stream,
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    EncryptionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
    UnsupportedOperationError,
)
from archivey.types import MemberType

from tests.sample_archives import CORPUS, FORMAT_KEYS, corpus_archive_path

# Backend columns used in the review matrix (corpus format keys).
COLUMNS: list[str] = [
    "zip",
    "tar",
    "tar.gz",
    "7z",
    "rar",
    "iso",
    "dir",
    "gz",
    "bz2",
    "xz",
    "lz",
]

# Prefer the "basic" corpus shape when the format supports it; fall back to first match.
PREFERRED_ENTRY = "basic"
_TMP = Path(tempfile.mkdtemp(prefix="sc-probe-"))


@dataclass
class Cell:
    value: Any
    how: str  # observed | read | unmeasured | n/a
    reason: str = ""


@dataclass
class Row:
    name: str
    expected: str
    cells: dict[str, Cell] = field(default_factory=dict)


def _avail(fmt_key: str) -> tuple[ArchiveFormat, bool, str]:
    fmt = FORMAT_KEYS[fmt_key]
    a = format_availability(fmt)
    ok = a.support is FormatSupport.FULL
    reason = (
        "" if ok else f"support={a.support.value} missing={[m.name for m in a.missing]}"
    )
    return fmt, ok, reason


def _path_for(fmt_key: str) -> Path | None:
    fmt, ok, reason = _avail(fmt_key)
    if not ok:
        return None
    preferred = (
        "single-file"
        if fmt_key in {"gz", "bz2", "xz", "lz", "zst", "lz4", "zz", "br"}
        else PREFERRED_ENTRY
    )
    entry = next(
        (e for e in CORPUS if e.id == preferred and fmt_key in e.formats), None
    )
    if entry is None:
        entry = next((e for e in CORPUS if fmt_key in e.formats), None)
    if entry is None:
        return None
    try:
        return corpus_archive_path(entry, fmt_key, _TMP)
    except Exception as exc:  # builder skip (e.g. no ``rar`` CLI)
        print(f"  builder skip {fmt_key}: {exc}", file=sys.stderr)
        # Fallback: committed fixtures for formats whose builder needs a write tool.
        if fmt_key == "rar":
            fixture = _REPO / "tests/fixtures/rar/basic_nonsolid__.rar"
            if fixture.exists():
                print(f"  using fixture {fixture}", file=sys.stderr)
                return fixture
        return None


def _exc_name(exc: BaseException) -> str:
    return type(exc).__name__


def cell_observed(value: Any) -> Cell:
    return Cell(value=value, how="observed")


def cell_na(reason: str) -> Cell:
    return Cell(value=None, how="n/a", reason=reason)


def cell_unmeasured(reason: str) -> Cell:
    return Cell(value=None, how="unmeasured", reason=reason)


def probe_open_path(path: Path) -> dict[str, Any]:
    with open_archive(path) as r:
        members = list(r.members())
        info = r.info
        receipt = info.cost
        files = [m for m in members if m.type == MemberType.FILE]
        hashes = {
            m.name: sorted(m.hashes.keys()) if m.hashes else [] for m in files[:5]
        }
        return {
            "member_count_field": info.member_count,
            "n_members": len(members),
            "all_is_current": all(m.is_current for m in members),
            "listing_cost": receipt.listing_cost.name if receipt else None,
            "access_cost": receipt.access_cost.name if receipt else None,
            "stream_capability": receipt.stream_capability.name if receipt else None,
            "solid_block_count": receipt.solid_block_count if receipt else None,
            "hashes_sample": hashes,
            "format": str(info.format),
        }


def probe_open_bytesio(path: Path) -> dict[str, Any] | str:
    data = path.read_bytes()
    bio = io.BytesIO(data)
    try:
        with open_archive(bio) as r:
            members = list(r.members())
            return {
                "ok": True,
                "n_members": len(members),
                "stream_capability": r.info.cost.stream_capability.name,
            }
    except Exception as exc:
        return f"{_exc_name(exc)}: {exc}"


def probe_pipe_ra(path: Path) -> str:
    """Non-seekable source, streaming=False (default)."""
    data = path.read_bytes()

    class Pipe(io.RawIOBase):
        def __init__(self, blob: bytes) -> None:
            self._buf = io.BytesIO(blob)

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            return self._buf.read(size)

        def seekable(self) -> bool:
            return False

    try:
        with open_archive(Pipe(data)):
            return "UNEXPECTED_OK"
    except Exception as exc:
        return f"{_exc_name(exc)}"


def probe_pipe_streaming(path: Path) -> str:
    data = path.read_bytes()

    class Pipe(io.RawIOBase):
        def __init__(self, blob: bytes) -> None:
            self._buf = io.BytesIO(blob)

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            return self._buf.read(size)

        def seekable(self) -> bool:
            return False

    try:
        with open_archive(Pipe(data), streaming=True) as r:
            n = sum(1 for _ in r.stream_members())
            return f"OK n={n}"
    except Exception as exc:
        return f"{_exc_name(exc)}"


def probe_extract_pipe(path: Path, dest: Path) -> str:
    data = path.read_bytes()

    class Pipe(io.RawIOBase):
        def __init__(self, blob: bytes) -> None:
            self._buf = io.BytesIO(blob)

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            return self._buf.read(size)

        def seekable(self) -> bool:
            return False

    try:
        report = extract(Pipe(data), dest)
        return f"OK results={len(report.results)}"
    except Exception as exc:
        return f"{_exc_name(exc)}"


def probe_close_lifetime(path: Path) -> str:
    with open_archive(path) as r:
        files = [m for m in r.members() if m.type == MemberType.FILE]
        if not files:
            return "n/a: no file members"
        stream = r.open(files[0])
    try:
        stream.read(1)
        return "UNEXPECTED: read after reader.close() succeeded"
    except Exception as exc:
        return f"after_close={_exc_name(exc)}"


def build_matrix() -> list[Row]:
    rows: list[Row] = []

    # --- expected contracts (VISION + specs; written before probing) ---
    expected = {
        "open_path": "opens; returns members + CostReceipt",
        "open_seekable_bytesio": "same as Path when format allows BinaryIO",
        "pipe_streaming_false": "StreamNotSeekableError (loud; never spool)",
        "pipe_streaming_true": "OK for TAR/SFC; StreamNotSeekableError for ZIP/7z/RAR/ISO",
        "extract_pipe": "auto-streams; OK for TAR/SFC; StreamNotSeekableError for index-at-EOF formats",
        "hashes_emptiness": "per format-single-file / formats.md matrix; empty when not cheap",
        "all_is_current_unique": "True for unique-name corpora (last-wins residual only on dups)",
        "close_closes_member_streams": "read after reader.close fails",
        "listing_cost": "INDEXED | REQUIRES_SCANNING | REQUIRES_DECOMPRESSION per format law",
        "access_cost": "DIRECT | SOLID per format",
    }

    row_specs = [
        ("open_path", expected["open_path"], probe_open_path),
        (
            "open_seekable_bytesio",
            expected["open_seekable_bytesio"],
            probe_open_bytesio,
        ),
        ("pipe_streaming_false", expected["pipe_streaming_false"], probe_pipe_ra),
        ("pipe_streaming_true", expected["pipe_streaming_true"], probe_pipe_streaming),
        (
            "close_closes_member_streams",
            expected["close_closes_member_streams"],
            probe_close_lifetime,
        ),
    ]

    paths: dict[str, Path | None] = {}
    for col in COLUMNS:
        _, ok, reason = _avail(col)
        if not ok:
            paths[col] = None
            print(f"column {col}: unmeasured ({reason})")
        else:
            paths[col] = _path_for(col)
            print(f"column {col}: path={paths[col]}")

    for name, exp, fn in row_specs:
        row = Row(name=name, expected=exp)
        for col in COLUMNS:
            path = paths[col]
            if path is None:
                _, ok, reason = _avail(col)
                if not ok:
                    row.cells[col] = cell_unmeasured(reason)
                else:
                    row.cells[col] = cell_na(
                        "no corpus entry/builder for this format key"
                    )
                continue
            if col == "dir" and name.startswith("pipe"):
                row.cells[col] = cell_na("directory is Path-only; pipe N/A")
                continue
            if col == "dir" and name == "open_seekable_bytesio":
                row.cells[col] = cell_na("directory is Path-only")
                continue
            try:
                result = fn(path)
                row.cells[col] = cell_observed(result)
            except Exception as exc:
                row.cells[col] = cell_observed(f"raised {_exc_name(exc)}: {exc}")
        rows.append(row)

    # extract_pipe needs a temp dest
    row = Row(name="extract_pipe", expected=expected["extract_pipe"])
    for col in COLUMNS:
        path = paths[col]
        if path is None:
            _, ok, reason = _avail(col)
            row.cells[col] = (
                cell_unmeasured(reason) if not ok else cell_na("no corpus path")
            )
            continue
        if col == "dir":
            row.cells[col] = cell_na("directory is Path-only; pipe N/A")
            continue
        with tempfile.TemporaryDirectory() as td:
            try:
                row.cells[col] = cell_observed(probe_extract_pipe(path, Path(td)))
            except Exception as exc:
                row.cells[col] = cell_observed(f"raised {_exc_name(exc)}: {exc}")
    rows.append(row)

    # Derived summary rows from open_path
    open_row = next(r for r in rows if r.name == "open_path")
    for derived, key, exp_key in [
        ("hashes_emptiness", "hashes_sample", "hashes_emptiness"),
        ("all_is_current_unique", "all_is_current", "all_is_current_unique"),
        ("listing_cost", "listing_cost", "listing_cost"),
        ("access_cost", "access_cost", "access_cost"),
        ("stream_capability", "stream_capability", "open_path"),
    ]:
        row = Row(name=derived, expected=expected.get(exp_key, "see open_path"))
        for col, cell in open_row.cells.items():
            if cell.how != "observed" or not isinstance(cell.value, dict):
                row.cells[col] = Cell(
                    value=cell.value, how=cell.how, reason=cell.reason
                )
            else:
                row.cells[col] = cell_observed(cell.value.get(key))
        rows.append(row)

    return rows


def print_matrix(rows: list[Row]) -> None:
    print("\n=== EXPECTED (from VISION + specs; written before probe) ===\n")
    for r in rows:
        print(f"  {r.name}: {r.expected}")

    print("\n=== OBSERVED ===\n")
    cols = COLUMNS
    header = f"{'row':32} " + " ".join(f"{c:12}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        parts = []
        for c in cols:
            cell = r.cells.get(c)
            if cell is None:
                parts.append(f"{'?':12}")
                continue
            if cell.how == "observed":
                v = cell.value
                if isinstance(v, dict):
                    s = "OK"
                else:
                    s = str(v)
                if len(s) > 12:
                    s = s[:9] + "..."
                parts.append(f"{s:12}")
            elif cell.how == "n/a":
                parts.append(f"{'N/A':12}")
            else:
                parts.append(f"{'UNMEAS':12}")
        print(f"{r.name:32} " + " ".join(parts))

    print("\n=== DETAIL (JSON) ===\n")
    out = []
    for r in rows:
        out.append(
            {
                "row": r.name,
                "expected": r.expected,
                "cells": {
                    k: {"value": v.value, "how": v.how, "reason": v.reason}
                    for k, v in r.cells.items()
                },
            }
        )
    print(json.dumps(out, indent=2, default=str))


def main() -> int:
    print("format_availability snapshot:")
    for col in COLUMNS:
        fmt, ok, reason = _avail(col)
        print(f"  {col}: {'FULL' if ok else reason}")
    rows = build_matrix()
    print_matrix(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

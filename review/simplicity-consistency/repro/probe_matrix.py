#!/usr/bin/env python
"""Generate the simplicity & consistency review's observable matrix by *running* code.

The brief (`review/simplicity-consistency/brief.md`, §Suggested process step 2) asks
for a cross-format matrix built by execution rather than by reading source, because a
hand-read matrix over 25 corpus format keys is expensive and stale on arrival. This is
that generator.

Rows are caller-visible operations; columns are corpus format keys (the real column
count -- ``tar.gz`` and ``tar`` are different rows of behaviour) crossed with source
shapes where a shape can change the answer. Every cell is *observed*: the probe
actually performed the call and recorded what came back. Cells the probe could not
establish are recorded explicitly (``unmeasured``/``skipped``) rather than omitted, so
an unfinished matrix says which cells are unfinished.

Usage::

    uv run --no-sync python review/simplicity-consistency/repro/probe_matrix.py
    uv run --no-sync python review/simplicity-consistency/repro/probe_matrix.py --json out.json
    uv run --no-sync python review/simplicity-consistency/repro/probe_matrix.py --formats zip,tar,7z

Writes a Markdown matrix to stdout (or ``--markdown PATH``) and, optionally, the raw
observations as JSON for diffing between runs.

This script performs **no** library changes and asserts nothing: it observes. The
assertions that pin the observed behaviour live in
``tests/test_review_simplicity_consistency.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import archivey  # noqa: E402
from archivey import (  # noqa: E402
    ArchiveFormat,
    MemberType,
    format_availability,
    open_archive,
    open_stream,
)
from archivey.internal.registry import FormatSupport, get_registry  # noqa: E402
from tests.sample_archives import (  # noqa: E402
    CORPUS,
    FORMAT_KEYS,
    CorpusEntry,
    corpus_archive_path,
)

# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------

#: How a cell was established. The brief requires every cell to say this.
OBSERVED = "observed"
UNMEASURED = "unmeasured"


@dataclass
class Cell:
    """One probe result: what happened, and how we know."""

    value: str
    provenance: str = OBSERVED
    detail: str = ""

    def to_json(self) -> dict[str, str]:
        return {
            "value": self.value,
            "provenance": self.provenance,
            "detail": self.detail,
        }


def _exc_label(exc: BaseException) -> str:
    """A stable, comparable label for an exception: type name only.

    Messages carry paths and vary per run; the matrix compares *shapes*, so the type
    is the cell value and the message goes in ``detail``.
    """
    return type(exc).__name__


def probe(fn: Callable[[], Any]) -> Cell:
    """Run ``fn``; record its return value or the exception type it raised.

    ``BaseException`` is caught deliberately: a probe that trips ``KeyboardInterrupt``
    or ``SystemExit`` out of library code is itself a finding, and losing the rest of
    the matrix to it would hide the other 400 cells.
    """
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001 - see docstring
        return Cell(
            f"raise {_exc_label(exc)}",
            OBSERVED,
            str(exc).replace("\n", " ")[:300],
        )
    return Cell(f"ok {value}" if value is not None else "ok", OBSERVED)


def probe_value(fn: Callable[[], Any]) -> Cell:
    """Like :func:`probe` but the cell value *is* the returned value's repr."""
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001
        return Cell(
            f"raise {_exc_label(exc)}",
            OBSERVED,
            str(exc).replace("\n", " ")[:300],
        )
    return Cell(str(value), OBSERVED)


# ---------------------------------------------------------------------------
# Source shapes
# ---------------------------------------------------------------------------


class NonSeekable(io.RawIOBase):
    """Forward-only view over bytes -- the pipe/socket shape, without a real pipe."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False


def _seekable_stream(path: Path) -> BinaryIO:
    return io.BytesIO(path.read_bytes())


def _nonseekable_stream(path: Path) -> BinaryIO:
    return NonSeekable(path.read_bytes())  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------


@dataclass
class Target:
    """One (format key, built archive) pair the probes run against."""

    key: str
    fmt: ArchiveFormat
    entry: CorpusEntry
    path: Path
    is_directory_source: bool = False
    cells: dict[str, Cell] = field(default_factory=dict)

    @property
    def passwords(self) -> list[str]:
        """Passwords the corpus entry needs, so encrypted columns measure the row.

        Without this, every data-touching probe on an encrypted entry records
        ``EncryptionError`` -- a fact about the probe, not about the format.
        """
        return list(self.entry.passwords)

    def open_ra(self, **kwargs: Any):
        return self.open_ra_source(self.path, **kwargs)

    def open_ra_source(self, source: Any, **kwargs: Any):
        """Open an arbitrary source shape for this target, with its passwords applied."""
        pw = self.passwords
        if pw and "password" not in kwargs:
            kwargs["password"] = pw
        return open_archive(source, **kwargs)


def _entry_for(key: str, preferred: Iterable[str]) -> CorpusEntry | None:
    """The corpus entry to probe this format key with.

    Prefer a shape that exercises the interesting rows (a directory member, a symlink)
    so ``open(dir)`` and link rows are not silently ``N/A`` for want of a member.
    """
    by_id = {e.id: e for e in CORPUS}
    for entry_id in preferred:
        entry = by_id.get(entry_id)
        if entry is not None and key in entry.formats:
            return entry
    for entry in CORPUS:
        if key in entry.formats:
            return entry
    return None


def _format_is_available(fmt: ArchiveFormat) -> bool:
    return format_availability(fmt).support is FormatSupport.FULL


def build_targets(keys: Iterable[str], tmp: Path) -> tuple[list[Target], list[str]]:
    """Build one archive per requested format key. Returns (targets, unmeasured keys)."""
    targets: list[Target] = []
    unmeasured: list[str] = []
    for key in keys:
        fmt = FORMAT_KEYS[key]
        if not _format_is_available(fmt):
            unmeasured.append(f"{key}: reader not available in this environment")
            continue
        entry = _entry_for(key, ("basic", "symlinks", "single-file", "permissions"))
        if entry is None:
            unmeasured.append(f"{key}: no corpus entry builds this format")
            continue
        try:
            path = corpus_archive_path(entry, key, tmp)
        except BaseException as exc:  # noqa: BLE001 - a builder tool may be missing
            unmeasured.append(f"{key}: builder failed ({_exc_label(exc)}: {exc})")
            continue
        targets.append(
            Target(key, fmt, entry, path, is_directory_source=(key == "dir"))
        )
    return targets, unmeasured


# ---------------------------------------------------------------------------
# The probes
# ---------------------------------------------------------------------------


def _first_file_member(reader: archivey.ArchiveReader):
    for m in reader.members():
        if m.type is MemberType.FILE:
            return m
    return None


def _first_dir_member(reader: archivey.ArchiveReader):
    for m in reader.members():
        if m.type is MemberType.DIRECTORY:
            return m
    return None


def probe_entry_points(t: Target) -> None:
    """E rows: what each entry point admits, per source shape."""

    def _open_ra_path():
        with open_archive(t.path):
            return "opens"

    t.cells["E1 open RA / Path"] = probe(_open_ra_path)

    if t.is_directory_source:
        for row in (
            "E2 open RA / seekable stream",
            "E3 open RA / non-seekable",
            "E4 open streaming / non-seekable",
        ):
            t.cells[row] = Cell(
                "N/A", UNMEASURED, "directory pseudo-archive has no byte stream"
            )
    else:

        def _open_ra_seek():
            with open_archive(_seekable_stream(t.path)):
                return "opens"

        def _open_ra_nonseek():
            with open_archive(_nonseekable_stream(t.path)):
                return "opens"

        def _open_stream_nonseek():
            with open_archive(_nonseekable_stream(t.path), streaming=True) as r:
                # Force at least one member so "opens" is not a lazy lie.
                for _ in r:
                    break
                return "opens"

        t.cells["E2 open RA / seekable stream"] = probe(_open_ra_seek)
        t.cells["E3 open RA / non-seekable"] = probe(_open_ra_nonseek)
        t.cells["E4 open streaming / non-seekable"] = probe(_open_stream_nonseek)

    def _streaming_plus_concurrent():
        with open_archive(t.path, streaming=True, concurrent_members=True):
            return "opens"

    t.cells["E5 streaming+concurrent_members"] = probe(_streaming_plus_concurrent)

    def _password_on_plain():
        with open_archive(t.path, password="irrelevant"):
            return "opens (password accepted)"

    t.cells["E6 password= on this format"] = probe(_password_on_plain)

    def _wrong_explicit_format():
        wrong = (
            ArchiveFormat.TAR if t.fmt.container.name != "TAR" else ArchiveFormat.ZIP
        )
        with open_archive(t.path, format=wrong):
            return "opens with wrong format="
        return None

    t.cells["E7 explicit wrong format="] = probe(_wrong_explicit_format)

    def _open_stream_route():
        with open_stream(t.path) as s:
            s.read(1)
            return "opens"

    t.cells["E8 open_stream() on this file"] = probe(_open_stream_route)


def probe_reader_surface(t: Target) -> None:
    """R rows: the random-access reader surface."""
    try:
        reader = t.open_ra()
    except BaseException as exc:  # noqa: BLE001
        for row in _R_ROWS:
            t.cells[row] = Cell(
                "unmeasured", UNMEASURED, f"open failed: {_exc_label(exc)}"
            )
        return

    with reader:
        t.cells["R1 len(reader)"] = probe_value(lambda: len(reader))  # type: ignore[arg-type]
        t.cells["R2 'name' in reader"] = probe_value(
            lambda: "somename" in reader  # type: ignore[operator]
        )
        t.cells["R3 members_report_if_available() pre-pass"] = probe_value(
            lambda: (
                "None"
                if reader.members_report_if_available() is None
                else f"report(n={len(reader.members_report_if_available().members)}, "  # type: ignore[union-attr]
                f"error={reader.members_report_if_available().error!r})"
            )  # type: ignore[union-attr]
        )
        t.cells["R4 get('no-such-member')"] = probe_value(
            lambda: repr(reader.get("no-such-member"))
        )
        t.cells["R5 read('no-such-member')"] = probe(
            lambda: reader.read("no-such-member")
        )

        dir_member = _first_dir_member(reader)
        if dir_member is None:
            t.cells["R6 open(DIRECTORY member)"] = Cell(
                "N/A", UNMEASURED, "corpus entry has no directory member in this format"
            )
        else:
            t.cells["R6 open(DIRECTORY member)"] = probe(
                lambda: reader.open(dir_member).close()
            )

        file_member = _first_file_member(reader)
        if file_member is None:
            for row in (
                "R7 second overlapping open()",
                "R8 seek() without seekable_members",
                "R9 stream after reader.close()",
            ):
                t.cells[row] = Cell("N/A", UNMEASURED, "no FILE member")
        else:

            def _overlap():
                s1 = reader.open(file_member)
                try:
                    s2 = reader.open(file_member)
                    s2.close()
                    return "second open succeeded"
                finally:
                    s1.close()

            t.cells["R7 second overlapping open()"] = probe(_overlap)

            def _seek():
                with reader.open(file_member) as s:
                    s.seek(0)
                    return "seek succeeded"

            t.cells["R8 seek() without seekable_members"] = probe(_seek)

        t.cells["R10 op after reader.close()"] = Cell(
            "deferred", UNMEASURED, "measured in probe_close_lifecycle"
        )


_R_ROWS = [
    "R1 len(reader)",
    "R2 'name' in reader",
    "R3 members_report_if_available() pre-pass",
    "R4 get('no-such-member')",
    "R5 read('no-such-member')",
    "R6 open(DIRECTORY member)",
    "R7 second overlapping open()",
    "R8 seek() without seekable_members",
    "R9 stream after reader.close()",
    "R10 op after reader.close()",
]


def probe_close_lifecycle(t: Target) -> None:
    """R9/R10: reader close vs member-stream lifetime."""
    try:
        reader = t.open_ra()
        member = _first_file_member(reader)
    except BaseException as exc:  # noqa: BLE001
        t.cells["R9 stream after reader.close()"] = Cell(
            "unmeasured", UNMEASURED, _exc_label(exc)
        )
        t.cells["R10 op after reader.close()"] = Cell(
            "unmeasured", UNMEASURED, _exc_label(exc)
        )
        return

    if member is None:
        t.cells["R9 stream after reader.close()"] = Cell(
            "N/A", UNMEASURED, "no FILE member"
        )
    else:
        stream = reader.open(member)
        reader.close()
        t.cells["R9 stream after reader.close()"] = probe(lambda: stream.read(1))
    with contextlib.suppress(Exception):
        reader.close()
    t.cells["R10 op after reader.close()"] = probe_value(lambda: reader.format)


def probe_streaming(t: Target) -> None:
    """S rows: streaming-mode enforcement."""
    rows = [
        "S1 members() on streaming",
        "S2 get() on streaming",
        "S3 open() on streaming",
        "S4 read() on streaming",
        "S5 second forward pass",
        "S6 members_report_if_available() streaming pre-pass",
    ]
    try:
        reader = t.open_ra(streaming=True)
    except BaseException as exc:  # noqa: BLE001
        for row in rows:
            t.cells[row] = Cell(
                "unmeasured", UNMEASURED, f"streaming open failed: {_exc_label(exc)}"
            )
        return

    with reader:
        t.cells["S6 members_report_if_available() streaming pre-pass"] = probe_value(
            lambda: (
                "None"
                if reader.members_report_if_available() is None
                else f"report(n={len(reader.members_report_if_available().members)})"
            )  # type: ignore[union-attr]
        )
        t.cells["S1 members() on streaming"] = probe(lambda: len(reader.members()))
        t.cells["S2 get() on streaming"] = probe(lambda: reader.get("anything"))
        t.cells["S3 open() on streaming"] = probe(lambda: reader.open("anything"))
        t.cells["S4 read() on streaming"] = probe(lambda: reader.read("anything"))

        def _second_pass():
            for _ in reader:
                pass
            for _ in reader:
                pass
            return "second pass allowed"

        t.cells["S5 second forward pass"] = probe(_second_pass)


def probe_cost(t: Target) -> None:
    """C rows: the cost receipt."""
    rows = [
        "C1 listing_cost",
        "C2 access_cost",
        "C3 stream_capability (path)",
        "C4 solid_block_count",
        "C5 info.is_solid",
        "C6 cost.notes",
    ]
    try:
        reader = t.open_ra()
    except BaseException as exc:  # noqa: BLE001
        for row in rows:
            t.cells[row] = Cell("unmeasured", UNMEASURED, _exc_label(exc))
        return
    with reader:
        c = reader.cost
        t.cells["C1 listing_cost"] = Cell(c.listing_cost.value, OBSERVED)
        t.cells["C2 access_cost"] = Cell(c.access_cost.value, OBSERVED)
        t.cells["C3 stream_capability (path)"] = Cell(
            c.stream_capability.value, OBSERVED
        )
        t.cells["C4 solid_block_count"] = Cell(str(c.solid_block_count), OBSERVED)
        t.cells["C5 info.is_solid"] = Cell(str(reader.info.is_solid), OBSERVED)
        t.cells["C6 cost.notes"] = Cell(
            "()" if not c.notes else "; ".join(c.notes)[:120], OBSERVED
        )


def probe_metadata(t: Target) -> None:
    """H rows: hashes / size emptiness contracts on the first FILE member."""
    rows = [
        "H1 member.hashes keys (no read)",
        "H2 member.size before read",
        "H3 member.compressed_size",
        "H4 member.modified",
        "H5 hashes after full read",
    ]
    try:
        reader = t.open_ra()
    except BaseException as exc:  # noqa: BLE001
        for row in rows:
            t.cells[row] = Cell("unmeasured", UNMEASURED, _exc_label(exc))
        return
    with reader:
        member = _first_file_member(reader)
        if member is None:
            for row in rows:
                t.cells[row] = Cell("N/A", UNMEASURED, "no FILE member")
            return
        t.cells["H1 member.hashes keys (no read)"] = Cell(
            ",".join(sorted(h.value for h in member.hashes)) or "(empty)", OBSERVED
        )
        t.cells["H2 member.size before read"] = Cell(
            "None" if member.size is None else "int", OBSERVED, str(member.size)
        )
        t.cells["H3 member.compressed_size"] = Cell(
            "None" if member.compressed_size is None else "int",
            OBSERVED,
            str(member.compressed_size),
        )
        t.cells["H4 member.modified"] = Cell(
            "None" if member.modified is None else "datetime",
            OBSERVED,
            str(member.modified),
        )
        with contextlib.suppress(Exception):
            reader.read(member)
        t.cells["H5 hashes after full read"] = Cell(
            ",".join(sorted(h.value for h in member.hashes)) or "(empty)", OBSERVED
        )


def probe_metadata_by_source_shape(t: Target) -> None:
    """H6/H7: does the *source shape* change metadata, holding the flags fixed?

    Added after the merge with PR #231, which found a residual Path gate the first
    pass missed: a row that only ever opens a ``Path`` cannot see it. Everything here
    re-asks H1-H3 over a seekable ``BytesIO``, so a Path-only fill shows up as a
    difference rather than as a matching pair of cells.
    """
    rows = ("H6 compressed_size (BytesIO)", "H7 hashes (BytesIO)")
    if t.is_directory_source:
        for row in rows:
            t.cells[row] = Cell(
                "N/A", UNMEASURED, "directory source has no byte stream"
            )
        return
    data = t.path.read_bytes()
    try:
        with t.open_ra_source(io.BytesIO(data)) as reader:
            member = _first_file_member(reader)
            if member is None:
                for row in rows:
                    t.cells[row] = Cell("N/A", UNMEASURED, "no FILE member")
                return
            t.cells["H6 compressed_size (BytesIO)"] = Cell(
                "None" if member.compressed_size is None else "int",
                OBSERVED,
                str(member.compressed_size),
            )
            t.cells["H7 hashes (BytesIO)"] = Cell(
                ",".join(sorted(h.value for h in member.hashes)) or "(empty)", OBSERVED
            )
    except BaseException as exc:  # noqa: BLE001
        for row in rows:
            t.cells[row] = Cell("unmeasured", UNMEASURED, _exc_label(exc))


def probe_header_encryption(t: Target) -> None:
    """E9: does opening need the password, or only reading?

    `#225` made *data*-encryption password work lazy. Header encryption cannot be
    lazy -- the listing itself is ciphertext -- so this row separates the two and keeps
    "no password until you read" from being stated more broadly than it holds.
    """
    row = "E9 open without password (header-encrypted)"
    # The probe's main entry is a plain corpus shape, so find a header-encrypting one
    # for this format key rather than reusing ``t.entry``.
    entry = next(
        (e for e in CORPUS if e.encrypt_header and t.key in e.formats),
        None,
    )
    if entry is None:
        t.cells[row] = Cell(
            "N/A", UNMEASURED, "no header-encrypting corpus entry for this format"
        )
        return
    try:
        path = corpus_archive_path(entry, t.key, t.path.parent)
    except BaseException as exc:  # noqa: BLE001
        t.cells[row] = Cell("unmeasured", UNMEASURED, _exc_label(exc))
        return
    t.cells[row] = probe(lambda: open_archive(path).close())


def probe_stream_entry_parity(t: Target) -> None:
    """X rows: does the ``open_stream`` route agree with the ``open_archive`` route?

    Only meaningful for the raw-stream formats, where both entry points accept the same
    file. The brief's §C vocabulary seed asks whether the two spellings of the same
    concept tell one story; this measures whether they also *behave* alike.
    """
    from archivey.types import ContainerFormat

    if t.fmt.container is not ContainerFormat.RAW_STREAM:
        for row in ("X1 open_stream size", "X2 open_stream seek default"):
            t.cells[row] = Cell(
                "N/A", UNMEASURED, "not a single-file compressed stream"
            )
        return

    def _size():
        with open_stream(t.path) as s:
            return f"size={getattr(s, 'size', '<no attr>')}"

    def _seek_default():
        with open_stream(t.path) as s:
            s.read(1)
            s.seek(0)
            return "seek allowed by default"

    t.cells["X1 open_stream size"] = probe(_size)
    t.cells["X2 open_stream seek default"] = probe(_seek_default)


PROBES: tuple[Callable[[Target], None], ...] = (
    probe_entry_points,
    probe_reader_surface,
    probe_close_lifecycle,
    probe_streaming,
    probe_cost,
    probe_metadata,
    probe_metadata_by_source_shape,
    probe_header_encryption,
    probe_stream_entry_parity,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_markdown(targets: list[Target], unmeasured: list[str]) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for t in targets:
        for row in t.cells:
            if row not in seen:
                seen.add(row)
                rows.append(row)
    rows.sort()

    out: list[str] = []
    out.append("# Observed matrix (generated)\n")
    out.append(
        f"Generated by `review/simplicity-consistency/repro/probe_matrix.py` over "
        f"{len(targets)} format keys. Every cell below is **observed** unless the "
        f"value says otherwise.\n"
    )
    if unmeasured:
        out.append("## Unmeasured columns\n")
        for line in unmeasured:
            out.append(f"- {line}")
        out.append("")

    header = "| Row | " + " | ".join(t.key for t in targets) + " |"
    sep = "|---" * (len(targets) + 1) + "|"
    out.append(header)
    out.append(sep)
    for row in rows:
        cells = []
        for t in targets:
            cell = t.cells.get(row)
            cells.append(
                "unmeasured" if cell is None else cell.value.replace("|", "\\|")
            )
        out.append(f"| {row} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def to_json(targets: list[Target], unmeasured: list[str]) -> str:
    return json.dumps(
        {
            "unmeasured_columns": unmeasured,
            "formats": {
                t.key: {
                    "archive_format": str(t.fmt),
                    "corpus_entry": t.entry.id,
                    "cells": {k: c.to_json() for k, c in t.cells.items()},
                }
                for t in targets
            },
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formats",
        help="comma-separated corpus format keys (default: all of FORMAT_KEYS)",
    )
    parser.add_argument("--json", type=Path, help="write raw observations here")
    parser.add_argument("--markdown", type=Path, help="write the matrix here")
    args = parser.parse_args(argv)

    keys = (
        [k.strip() for k in args.formats.split(",")]
        if args.formats
        else list(FORMAT_KEYS)
    )
    unknown = [k for k in keys if k not in FORMAT_KEYS]
    if unknown:
        parser.error(f"unknown format keys: {unknown}")

    # Registering backends is what makes format_availability meaningful.
    get_registry()

    tmp = Path(tempfile.mkdtemp(prefix="archivey-probe-"))
    try:
        targets, unmeasured = build_targets(keys, tmp)
        for t in targets:
            for fn in PROBES:
                try:
                    fn(t)
                except BaseException:  # noqa: BLE001 - one bad probe must not eat the run
                    traceback.print_exc(file=sys.stderr)
        md = render_markdown(targets, unmeasured)
        if args.markdown:
            args.markdown.write_text(md)
        else:
            sys.stdout.write(md)
        if args.json:
            args.json.write_text(to_json(targets, unmeasured))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The corpus scan script, run against the test fixtures.

`scripts/scan_archives.py` dry-runs every archive under a directory and writes a CSV
whose `flags` column is the filter for "look at this one". It is not in CI otherwise,
and a flag that silently fails to reach the CSV looks exactly like an archive with
nothing wrong, so the cases where that could happen are pinned here.
"""

from __future__ import annotations

import csv
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

import archivey

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scan_archives.py"
FIXTURE = ROOT / "tests" / "fixtures" / "corpus" / "rar" / "basic.rar"

_spec = importlib.util.spec_from_file_location("scan_archives", SCRIPT)
assert _spec is not None and _spec.loader is not None
scan = importlib.util.module_from_spec(_spec)
# `scripts/` is not a package, so the module is loaded by path. It has to be in
# `sys.modules` before it executes: `@dataclass` looks its own module up by name.
sys.modules[_spec.name] = scan
_spec.loader.exec_module(scan)


def test_clean_archive_has_no_flags() -> None:
    row = scan.scan_one(FIXTURE, scan._scan_config(False))
    assert row is not None
    out = row.csv_row()
    assert out["open"] == "ok"
    assert out["extract"] == "ok"
    assert out["flags"] == ""
    assert out["members"] == out["entries_written"]


def test_non_archive_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not an archive\n", encoding="utf-8")
    assert scan.scan_one(path, scan._scan_config(False)) is None


def test_detect_crash_reaches_the_flags_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(*args: object, **kwargs: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(archivey, "detect_format", crash)
    row = scan.scan_one(FIXTURE, scan._scan_config(False))
    assert row is not None
    out = row.csv_row()
    assert out["open"] == "BUG:ValueError"
    assert "bug" in str(out["flags"]).split()


def test_near_excludes_over_in_flags_and_summary() -> None:
    limit = scan.ExtractionLimits().max_ratio
    over = scan._Row(path="over", numbers={"archive_ratio": limit * 3})
    near = scan._Row(path="near", numbers={"archive_ratio": limit * 0.7})
    for row in (over, near):
        scan._flag_limits(row)
    assert over.flags == ["over:max_ratio"]
    assert near.flags == ["near:max_ratio"]
    line = next(
        line
        for line in scan._summary([over, near]).splitlines()
        if line.startswith("archive_ratio")
    )
    near_count, over_count = line.split()[-2:]
    assert (near_count, over_count) == ("1", "1")


def test_main_writes_csv_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``main`` wraps library internals and reroutes the ``archivey`` logger for the
    # life of the process; neither may leak into the rest of the test session.
    monkeypatch.setattr(scan, "_install_probes", lambda: [])
    logger = logging.getLogger("archivey")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    out = tmp_path / "scan.csv"
    assert scan.main([str(FIXTURE.parent), "-o", str(out)]) == 0
    with out.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert reader.fieldnames == scan.COLUMNS
    assert rows
    assert all(row["open"] for row in rows)
    assert "== summary:" in out.with_suffix(".log").read_text(encoding="utf-8")


def test_detection_diagnostics_survive_a_failed_open(tmp_path: Path) -> None:
    # RAR content under a .zip name: detection reports the conflict, and a damaged
    # main header makes the open fail, so no reader carries the diagnostic. (A cut
    # inside a header no longer fails the open: it lists what precedes the cut.)
    data = bytearray(FIXTURE.read_bytes())
    data[8] ^= 0xFF  # the main header's CRC32, just after the signature
    path = tmp_path / "misnamed.zip"
    path.write_bytes(bytes(data))
    row = scan.scan_one(path, scan._scan_config(False))
    assert row is not None
    out = row.csv_row()
    assert out["open"] == "CorruptionError"
    assert out["detected"] == "magic/certain"
    assert "diag:format_extension_conflict" in str(out["flags"]).split()


def _quiet_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan, "_install_probes", lambda: [])
    logger = logging.getLogger("archivey")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "propagate", logger.propagate)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for sub in ("a", "a/deep", "b"):
        (root / sub).mkdir(parents=True)
        (root / sub / "x.rar").write_bytes(FIXTURE.read_bytes())
        (root / sub / "notes.txt").write_text("not an archive\n", encoding="utf-8")
    (root / "top.rar").write_bytes(FIXTURE.read_bytes())
    return root


def test_walk_reports_a_directory_after_its_subtree(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    events = [(kind, str(Path(p).relative_to(root))) for kind, p in scan._walk(root)]
    assert events == [
        ("file", "top.rar"),
        ("file", "a/notes.txt"),
        ("file", "a/x.rar"),
        ("file", "a/deep/notes.txt"),
        ("file", "a/deep/x.rar"),
        ("dir", "a/deep"),
        ("dir", "a"),
        ("file", "b/notes.txt"),
        ("file", "b/x.rar"),
        ("dir", "b"),
        ("dir", "."),
    ]
    skipped = [p for _, p in scan._walk(root, frozenset({str(root / "a")}))]
    assert not any(p.startswith(str(root / "a")) for p in skipped)


def test_resume_skips_what_was_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_main(monkeypatch)
    root = _tree(tmp_path)
    out = tmp_path / "scan.csv"
    scanned: list[Path] = []
    real_scan_one = scan.scan_one
    interrupt = [True]

    def counting(path: Path, *args: object, **kwargs: object) -> object:
        scanned.append(path)
        if path == root / "b" / "notes.txt" and interrupt.pop():
            raise KeyboardInterrupt
        return real_scan_one(path, *args, **kwargs)

    monkeypatch.setattr(scan, "scan_one", counting)
    assert scan.main([str(root), "-o", str(out)]) == 0
    assert scanned[-1] == root / "b" / "notes.txt"
    progress = out.with_suffix(".progress").read_text(encoding="utf-8")
    assert f'"dir": "{root / "a"}"' in progress

    scanned.clear()
    interrupt.append(False)
    monkeypatch.setattr(
        scan,
        "_list_dir",
        lambda d, real=scan._list_dir: (
            pytest.fail(f"listed a finished directory {d}")
            if Path(d).is_relative_to(root / "a")
            else real(d)
        ),
    )
    assert scan.main([str(root), "-o", str(out), "--resume"]) == 0
    # The interrupted file is scanned again; nothing already done is.
    assert scanned == [root / "b" / "notes.txt", root / "b" / "x.rar"]
    with out.open(encoding="utf-8", newline="") as fh:
        paths = [row["path"] for row in csv.DictReader(fh)]
    assert sorted(paths) == sorted(
        str(root / p) for p in ("top.rar", "a/x.rar", "a/deep/x.rar", "b/x.rar")
    )
    assert "== summary: 4 archives" in out.with_suffix(".log").read_text(
        encoding="utf-8"
    )


def test_resume_refuses_a_different_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_main(monkeypatch)
    root = _tree(tmp_path)
    out = tmp_path / "scan.csv"
    assert scan.main([str(root / "b"), "-o", str(out)]) == 0
    with pytest.raises(SystemExit, match="start a new scan"):
        scan.main([str(root), "-o", str(out), "--resume"])


def test_row_round_trips_through_the_csv() -> None:
    row = scan.scan_one(FIXTURE, scan._scan_config(False))
    assert row is not None
    row.flag("near:max_ratio")
    row.diagnostics["some_code"] = 2
    record = {k: "" if v is None else str(v) for k, v in row.csv_row().items()}
    back = scan._Row.from_csv(record)
    assert scan._summary([back]) == scan._summary([row])

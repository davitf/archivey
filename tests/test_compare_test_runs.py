"""`scripts/compare_test_runs.py`, the before/after check for pull requests that move tests.

The reports here are written by hand in the shapes pytest and coverage.py write: JUnit XML
with the xunit1 ``file`` attribute, and a coverage SQLite file with test contexts. The
cases are the ones the script exists for: a file moved, a test that stops running
anything while it still passes, and a test that starts skipping.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_test_runs.py"

_spec = importlib.util.spec_from_file_location("compare_test_runs", SCRIPT)
assert _spec is not None and _spec.loader is not None
compare = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compare
_spec.loader.exec_module(compare)


def _junit(path: Path, cases: dict[str, str]) -> Path:
    """A report holding ``{"file::name": outcome}``; outcome "skip:<reason>" skips."""
    rows = []
    for test_id, outcome in cases.items():
        file, name = test_id.split("::")
        classname = file.removesuffix(".py").replace("/", ".")
        body = ""
        if outcome.startswith("skip:"):
            reason = outcome.removeprefix("skip:")
            body = f'<skipped type="pytest.skip" message="{reason}"/>'
        elif outcome == "failure":
            body = '<failure message="boom"/>'
        rows.append(
            f'<testcase classname="{classname}" name="{name}" file="{file}">'
            f"{body}</testcase>"
        )
    path.write_text(
        f"<testsuites><testsuite>{''.join(rows)}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _coverage(path: Path, arcs: dict[str, list[tuple[int, int]]]) -> Path:
    """A branch-coverage data file: ``{test id: arcs it ran in src/archivey/a.py}``."""
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT);"
            "CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT);"
            "CREATE TABLE line_bits (file_id INT, context_id INT, numbits BLOB);"
            "CREATE TABLE arc (file_id INT, context_id INT, fromno INT, tono INT);"
        )
        db.execute("INSERT INTO file VALUES (1, '/repo/src/archivey/a.py')")
        db.execute("INSERT INTO context VALUES (1, '')")
        for index, (test_id, test_arcs) in enumerate(arcs.items(), start=2):
            db.execute("INSERT INTO context VALUES (?, ?)", (index, f"{test_id}|run"))
            db.executemany(
                "INSERT INTO arc VALUES (1, ?, ?, ?)",
                [(index, start, end) for start, end in test_arcs],
            )
    db.close()
    return path


def _run(*argv: str | Path) -> int:
    return compare.main([str(arg) for arg in argv])


def test_a_moved_file_matches_through_moved(tmp_path: Path) -> None:
    before = _junit(tmp_path / "b.xml", {"tests/test_a.py::test_x": "passed"})
    after = _junit(tmp_path / "a.xml", {"tests/zip/test_a.py::test_x": "passed"})
    assert _run("outcomes", before, after) == 1
    assert (
        _run(
            "outcomes", before, after, "--moved", "tests/test_a.py=tests/zip/test_a.py"
        )
        == 0
    )


def test_a_fold_moves_some_tests_and_leaves_the_rest(tmp_path: Path) -> None:
    before = _junit(
        tmp_path / "b.xml",
        {
            "tests/test_audit.py::test_x": "passed",
            "tests/test_audit.py::test_y": "passed",
        },
    )
    after = _junit(
        tmp_path / "a.xml",
        {
            "tests/test_zip.py::test_x": "passed",
            "tests/test_audit.py::test_y": "passed",
        },
    )
    moved = ("--moved", "tests/test_audit.py=tests/test_zip.py")
    assert _run("outcomes", before, after, *moved) == 0


def test_a_test_that_starts_skipping_is_a_change(tmp_path: Path) -> None:
    before = _junit(tmp_path / "b.xml", {"tests/test_a.py::test_x": "passed"})
    after = _junit(tmp_path / "a.xml", {"tests/test_a.py::test_x": "skip:no unrar"})
    assert _run("outcomes", before, after) == 1


def test_a_changed_skip_reason_is_a_change(tmp_path: Path) -> None:
    before = _junit(tmp_path / "b.xml", {"tests/test_a.py::test_x": "skip:no unrar"})
    after = _junit(tmp_path / "a.xml", {"tests/test_a.py::test_x": "skip:no 7z"})
    assert _run("outcomes", before, after) == 1


def test_a_new_test_fails_unless_allowed(tmp_path: Path) -> None:
    before = _junit(tmp_path / "b.xml", {"tests/test_a.py::test_x": "passed"})
    after = _junit(
        tmp_path / "a.xml",
        {"tests/test_a.py::test_x": "passed", "tests/test_a.py::test_new": "passed"},
    )
    assert _run("outcomes", before, after) == 1
    assert _run("outcomes", before, after, "--allow-new") == 0


def test_a_passing_test_that_runs_nothing_is_a_coverage_change(tmp_path: Path) -> None:
    before = _coverage(
        tmp_path / "b.coverage",
        {
            "tests/test_a.py::test_x": [(1, 2), (2, 3)],
            "tests/test_a.py::test_y": [(1, 2)],
        },
    )
    after = _coverage(
        tmp_path / "a.coverage",
        {"tests/test_a.py::test_x": [], "tests/test_a.py::test_y": [(1, 2)]},
    )
    assert _run("coverage", before, before) == 0
    assert _run("coverage", before, after) == 1


def test_coverage_of_a_moved_file_matches_through_moved(tmp_path: Path) -> None:
    before = _coverage(tmp_path / "b.coverage", {"tests/test_a.py::test_x": [(1, 2)]})
    after = _coverage(
        tmp_path / "a.coverage", {"tests/zip/test_a.py::test_x": [(1, 2)]}
    )
    moved = ("--moved", "tests/test_a.py=tests/zip/test_a.py")
    assert _run("coverage", before, after) == 1
    assert _run("coverage", before, after, *moved) == 0


def test_numbits_decode_as_coverage_writes_them() -> None:
    assert compare._numbits_to_lines(bytes([0b0000_0110, 0, 0b1000_0000])) == {1, 2, 23}

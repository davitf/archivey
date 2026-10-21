"""Compare two test runs: same tests, same outcomes, and the same lines covered by each.

A pull request that moves tests between files, folds one file into another, or swaps a
test's setup for a shared helper must leave every test doing what it did. Two checks show
it, each over a run before the change and a run after it, on the same environment:

    # 1. Every test ID and its outcome (passed, failed, error, skipped with its reason,
    #    xfailed). xunit1 is what writes the file attribute the node IDs come from.
    uv run pytest -n auto --no-cov -o junit_family=xunit1 --junitxml=before.xml
    uv run python scripts/compare_test_runs.py outcomes before.xml after.xml

    # 2. The src/ lines (or arcs, with branch coverage) each test runs. Per test, not
    #    the total: a test that stopped testing anything still passes, but it loses
    #    its lines.
    uv run pytest -n auto --cov=archivey --cov-context=test --cov-report= \
        && cp .coverage before.coverage
    uv run python scripts/compare_test_runs.py coverage before.coverage after.coverage

A test whose file moved is matched through ``--moved OLD=NEW`` (repeatable, paths as
pytest prints them, e.g. ``tests/test_zip_pipe.py=tests/zip/test_zip_pipe.py``). A test
that is in the before run's ``OLD`` file and not the after run's is looked for in ``NEW``
under the same name, so a fold that moves some tests of a file and leaves others works
too. The exit status is 1 when any test is missing, new, or changed, so a pull request
can quote the clean output. New tests are listed rather than failed on with
``--allow-new``.

Stdlib only: the coverage file is read as the SQLite database ``coverage.py`` writes.
"""

from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path

# How many differences of one kind to print before summarising the rest.
_SHOW = 40


def _parse_moves(pairs: Iterable[str]) -> dict[str, str]:
    moves: dict[str, str] = {}
    for pair in pairs:
        old, sep, new = pair.partition("=")
        if not sep or not old or not new:
            raise SystemExit(f"--moved wants OLD=NEW, got {pair!r}")
        moves[old] = new
    return moves


def _moved(test_id: str, moves: Mapping[str, str]) -> str | None:
    """``test_id`` with its file renamed by ``moves``, or ``None`` if none applies."""
    path, sep, rest = test_id.partition("::")
    if sep and path in moves:
        return f"{moves[path]}::{rest}"
    return None


def _match(
    before: Mapping[str, object], after: Mapping[str, object], moves: Mapping[str, str]
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Pair before-IDs with after-IDs: (pairs, missing from after, new in after)."""
    pairs: list[tuple[str, str]] = []
    missing: list[str] = []
    claimed: set[str] = set()
    for test_id in before:
        target = test_id if test_id in after else _moved(test_id, moves)
        if target is not None and target in after and target not in claimed:
            pairs.append((test_id, target))
            claimed.add(target)
        else:
            missing.append(test_id)
    new = [test_id for test_id in after if test_id not in claimed]
    return pairs, missing, new


def _print_list(title: str, items: list[str]) -> None:
    if not items:
        return
    print(f"{title}: {len(items)}")
    for item in items[:_SHOW]:
        print(f"  {item}")
    if len(items) > _SHOW:
        print(f"  ... and {len(items) - _SHOW} more")


# ---------------------------------------------------------------------------
# Outcomes, from JUnit XML
# ---------------------------------------------------------------------------


def _node_id(case: ET.Element) -> str:
    name = case.get("name", "")
    file = case.get("file")
    classname = case.get("classname", "")
    if file:
        # xunit1: classname is the dotted module, then any classes inside it.
        module = file.removesuffix(".py").replace("/", ".")
        inner = classname.removeprefix(module).lstrip(".")
        parts = [file, *inner.split(".")] if inner else [file]
        return "::".join([*parts, name])
    return f"{classname}::{name}"


def _outcome(case: ET.Element) -> str:
    for tag in ("failure", "error"):
        if case.find(tag) is not None:
            return tag
    skipped = case.find("skipped")
    if skipped is None:
        return "passed"
    message = (skipped.get("message") or "").strip()
    if skipped.get("type") == "pytest.xfail":
        return f"xfailed: {message}"
    return f"skipped: {message}"


def load_outcomes(report: Path) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for case in ET.parse(report).iter("testcase"):
        outcomes[_node_id(case)] = _outcome(case)
    return outcomes


def compare_outcomes(
    before: Mapping[str, str],
    after: Mapping[str, str],
    moves: Mapping[str, str],
    *,
    allow_new: bool,
) -> bool:
    pairs, missing, new = _match(before, after, moves)
    changed = [
        f"{old}: {before[old]} -> {after[new_id]}"
        + (f" (now {new_id})" if new_id != old else "")
        for old, new_id in pairs
        if before[old] != after[new_id]
    ]
    moved = sum(1 for old, new_id in pairs if old != new_id)
    print(f"before: {len(before)} tests, after: {len(after)} tests")
    print(f"matched: {len(pairs)} ({moved} under a moved file)")
    for label, outcomes in (("before", before), ("after", after)):
        counts = collections.Counter(o.split(":")[0] for o in outcomes.values())
        print(f"{label}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    _print_list("missing from after", missing)
    _print_list("new in after", new)
    _print_list("outcome changed", changed)
    return not missing and not changed and (allow_new or not new)


# ---------------------------------------------------------------------------
# Lines covered per test, from a coverage.py data file with test contexts
# ---------------------------------------------------------------------------


def _numbits_to_lines(numbits: bytes) -> set[int]:
    """coverage.py's ``numbits``: bit ``j`` of byte ``i`` is line ``8 * i + j``."""
    return {
        index * 8 + bit
        for index, byte in enumerate(numbits)
        if byte
        for bit in range(8)
        if byte & (1 << bit)
    }


def _test_of(context: str) -> str | None:
    """The test a pytest-cov context names, or ``None`` for the empty context.

    pytest-cov names a context ``<node id>|setup``, ``|run`` or ``|teardown``. All three
    phases count as the test's own lines: a fixture is part of what the test runs.
    """
    if not context:
        return None
    return context.rsplit("|", 1)[0]


def load_coverage(data_file: Path, root: str) -> dict[str, dict[str, set[object]]]:
    """``{test id: {source file from root on: what it ran}}`` for every test context.

    "What it ran" is the set of arcs (line pairs) when the run measured branches, as
    this repo's configuration does, and the set of lines otherwise.
    """
    with sqlite3.connect(f"file:{data_file}?mode=ro", uri=True) as db:
        if not db.execute("SELECT 1 FROM context WHERE context != ''").fetchone():
            raise SystemExit(
                f"{data_file} has no test contexts: run pytest with --cov-context=test"
            )
        files = dict(db.execute("SELECT id, path FROM file"))
        contexts = dict(db.execute("SELECT id, context FROM context"))
        per_test: dict[str, dict[str, set[object]]] = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )

        def add(file_id: int, context_id: int, items: Iterable[object]) -> None:
            test = _test_of(contexts[context_id])
            if test is None:
                return
            path = files[file_id]
            marker = path.find(root)
            if marker >= 0:
                path = path[marker:]
            per_test[test][path].update(items)

        for file_id, context_id, numbits in db.execute(
            "SELECT file_id, context_id, numbits FROM line_bits"
        ):
            add(file_id, context_id, _numbits_to_lines(numbits))
        for file_id, context_id, start, end in db.execute(
            "SELECT file_id, context_id, fromno, tono FROM arc"
        ):
            add(file_id, context_id, [(start, end)])
    return per_test


def compare_coverage(
    before: Mapping[str, Mapping[str, set[object]]],
    after: Mapping[str, Mapping[str, set[object]]],
    moves: Mapping[str, str],
    *,
    allow_new: bool,
) -> bool:
    pairs, missing, new = _match(before, after, moves)
    changed: list[str] = []
    for old, new_id in pairs:
        lines_before, lines_after = before[old], after[new_id]
        if lines_before == lines_after:
            continue
        lost = sum(
            len(lines - lines_after.get(path, set()))
            for path, lines in lines_before.items()
        )
        gained = sum(
            len(lines - lines_before.get(path, set()))
            for path, lines in lines_after.items()
        )
        changed.append(f"{old}: {lost} no longer run, {gained} newly run")

    def total(data: Mapping[str, Mapping[str, set[object]]]) -> int:
        union: dict[str, set[object]] = collections.defaultdict(set)
        for files in data.values():
            for path, lines in files.items():
                union[path] |= lines
        return sum(len(lines) for lines in union.values())

    print(f"before: {len(before)} tests with coverage, {total(before)} lines/arcs run")
    print(f"after: {len(after)} tests with coverage, {total(after)} lines/arcs run")
    print(f"matched: {len(pairs)}")
    _print_list("missing from after", missing)
    _print_list("new in after", new)
    _print_list("lines/arcs changed", changed)
    return not missing and not changed and (allow_new or not new)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("outcomes", "coverage"))
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument(
        "--moved",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="a test file that moved, as pytest prints its path",
    )
    parser.add_argument(
        "--allow-new", action="store_true", help="list new tests without failing"
    )
    parser.add_argument(
        "--root",
        default="src/archivey/",
        help="coverage paths are shown from this part on (default: src/archivey/)",
    )
    args = parser.parse_args(argv)
    moves = _parse_moves(args.moved)
    if args.kind == "outcomes":
        same = compare_outcomes(
            load_outcomes(args.before),
            load_outcomes(args.after),
            moves,
            allow_new=args.allow_new,
        )
    else:
        same = compare_coverage(
            load_coverage(args.before, args.root),
            load_coverage(args.after, args.root),
            moves,
            allow_new=args.allow_new,
        )
    print("same" if same else "DIFFERENT")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())

"""Compare two test runs: same tests, same outcomes, and the same lines covered by each.

A pull request that moves tests between files, folds one file into another, or swaps a
test's setup for a shared helper must leave every test doing what it did. Two checks show
it, each over a run before the change and a run after it, on the same environment:

    # 1. Every test ID and its outcome (passed, failed, error, skipped with its reason,
    #    xfailed). xunit1 is what writes the file attribute the node IDs come from.
    uv run pytest -n auto --no-cov -o junit_family=xunit1 --junitxml=before.xml
    uv run python scripts/compare_test_runs.py outcomes before.xml after.xml

    # 2. The src/ lines (or arcs, with branch coverage) each test runs. Per test, not
    #    the total: a test whose body no longer reaches the library still passes, but
    #    it loses its lines. The before tree runs twice, for --baseline below.
    uv run pytest -n auto --cov=archivey --cov-context=test --cov-report= \
        && cp .coverage before.coverage
    uv run pytest -n auto --cov=archivey --cov-context=test --cov-report= \
        && cp .coverage baseline.coverage
    uv run python scripts/compare_test_runs.py coverage before.coverage after.coverage \
        --baseline baseline.coverage

Neither check reads test code. A test that still makes the same library calls and has
lost an assertion, or asserts a weaker expected value, reports ``same`` on both, so a
reviewer still reads every moved test.

Per-test coverage is not exactly repeatable, and two things keep that out of the
result. Code that runs once per worker (an import, a ``functools.cache`` body, a
module-scoped fixture) is credited to whichever test reached that worker first, which
changes from run to run under ``-n auto``: a line one test no longer runs and another
test took up in the after run is counted as moved between tests, and does not fail the
check. Code that garbage collection or thread timing runs (a finalizer that closes a
stream) lands in a different test each time with nothing to pair it with: what differs
per test between the before run and ``--baseline``, a second run of the same tree, is
left out of the comparison. A test that became a no-op loses lines that other tests
ran in both runs, which neither rule covers. What is left after both still varies a
little, mostly in code a subprocess or a thread reaches. On seven test files run three
times unchanged, the most any test lost was 4 of its arcs with ``--baseline`` and 65
(2% of what it ran) without, while a test made to return at its first line lost 1,352.
So the check fails on a test that lost more than ``--max-lost`` (a tenth) of what it
ran, and lists smaller changes, largest first, for a person to read.

A test whose file moved is matched through ``--moved OLD=NEW`` (repeatable, paths as
pytest prints them, e.g. ``tests/test_zip_pipe.py=tests/zip/test_zip_pipe.py``). A test
that is in the before run's ``OLD`` file and not the after run's is looked for in ``NEW``
under the same name, so a fold that moves some tests of a file and leaves others works
too. A test whose name or parameter id changed is matched through ``--renamed OLD=NEW``
with whole node IDs. The exit status is 1 when any test is missing or new, when an
outcome changed, or when a test lost more than ``--max-lost`` of its coverage, so a
pull request can quote the clean output. New tests are listed rather than failed on
with ``--allow-new``.

Stdlib only: the coverage file is read as the SQLite database ``coverage.py`` writes.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import importlib.util
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path

# How many differences of one kind to print before summarising the rest.
_SHOW = 40
# How many files to name beside a test whose coverage changed.
_SHOW_FILES = 3

# One JUnit-to-node-id rule for every script that reads pytest's reports. ``scripts/``
# is not a package, so the module is loaded by path.
_junit_spec = importlib.util.spec_from_file_location(
    "junit_failure_lines", Path(__file__).with_name("junit_failure_lines.py")
)
assert _junit_spec is not None and _junit_spec.loader is not None
_junit = importlib.util.module_from_spec(_junit_spec)
_junit_spec.loader.exec_module(_junit)

Coverage = Mapping[str, Mapping[str, set[object]]]


def _parse_pairs(option: str, pairs: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in pairs:
        old, sep, new = pair.partition("=")
        if not sep or not old or not new:
            raise SystemExit(f"{option} wants OLD=NEW, got {pair!r}")
        parsed[old] = new
    return parsed


def _moved(test_id: str, moves: Mapping[str, str]) -> str | None:
    """``test_id`` with its file renamed by ``moves``, or ``None`` if none applies."""
    path, sep, rest = test_id.partition("::")
    if sep and path in moves:
        return f"{moves[path]}::{rest}"
    return None


def _match(
    before: Mapping[str, object],
    after: Mapping[str, object],
    moves: Mapping[str, str],
    renames: Mapping[str, str],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Pair before-IDs with after-IDs: (pairs, missing from after, new in after).

    IDs that are in both runs pair first, so a moved or renamed test cannot take the ID
    of a test that stayed where it was.
    """
    pairs = [(test_id, test_id) for test_id in before if test_id in after]
    claimed = {test_id for test_id, _ in pairs}
    missing: list[str] = []
    for test_id in before:
        if test_id in after:
            continue
        target = renames.get(test_id) or _moved(test_id, moves)
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


def _refuse_empty(kind: str, before: Path, count: int) -> None:
    if not count:
        raise SystemExit(
            f"{before} holds no tests: a collection error, a wrong path or a -k that "
            f"selected nothing would compare {kind} as the same"
        )


# ---------------------------------------------------------------------------
# Outcomes, from JUnit XML
# ---------------------------------------------------------------------------


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
        outcomes[_junit._node_id(case)] = _outcome(case)
    return outcomes


def compare_outcomes(
    before: Mapping[str, str],
    after: Mapping[str, str],
    moves: Mapping[str, str],
    renames: Mapping[str, str],
    *,
    allow_new: bool,
) -> bool:
    pairs, missing, new = _match(before, after, moves, renames)
    changed = [
        f"{old}: {before[old]} -> {after[new_id]}"
        + (f" (now {new_id})" if new_id != old else "")
        for old, new_id in pairs
        if before[old] != after[new_id]
    ]
    moved = sum(1 for old, new_id in pairs if old != new_id)
    print(f"before: {len(before)} tests, after: {len(after)} tests")
    print(f"matched: {len(pairs)} ({moved} moved or renamed)")
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
    uri = f"file:{data_file}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        if not db.execute("SELECT 1 FROM context WHERE context != ''").fetchone():
            raise SystemExit(
                f"{data_file} has no test contexts: run pytest with --cov-context=test"
            )
        files = dict(db.execute("SELECT id, path FROM file"))
        contexts = dict(db.execute("SELECT id, context FROM context"))
        # Every test context is a test that ran, including one that reached no src/
        # line at all: that is the no-op this check exists for, not a missing test.
        per_test: dict[str, dict[str, set[object]]] = {
            test: collections.defaultdict(set)
            for test in map(_test_of, contexts.values())
            if test is not None
        }

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


def _minus(
    lines: Mapping[str, set[object]], other: Mapping[str, set[object]]
) -> dict[str, set[object]]:
    """Per file, what ``lines`` has and ``other`` does not; empty files left out."""
    out = {path: items - other.get(path, set()) for path, items in lines.items()}
    return {path: items for path, items in out.items() if items}


def _describe(counts: Mapping[str, set[object]]) -> str:
    """``"N (a.py 5, b.py 2, ...)"``: the total, then the files with the most."""
    total = sum(len(items) for items in counts.values())
    top = sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))
    named = ", ".join(f"{path} {len(items)}" for path, items in top[:_SHOW_FILES])
    more = ", ..." if len(top) > _SHOW_FILES else ""
    return f"{total} ({named}{more})" if total else "0"


def compare_coverage(
    before: Coverage,
    after: Coverage,
    moves: Mapping[str, str],
    renames: Mapping[str, str],
    *,
    allow_new: bool,
    baseline: Coverage | None = None,
    max_lost: float = 0.1,
) -> bool:
    pairs, missing, new = _match(before, after, moves, renames)
    # What a second run of the unchanged tree shows varying, per test, is not compared.
    unstable: dict[str, dict[str, set[object]]] = {}
    if baseline is not None:
        for test_id, files in before.items():
            again = baseline.get(test_id, {})
            unstable[test_id] = {
                path: files.get(path, set()) ^ again.get(path, set())
                for path in {*files, *again}
            }
    lost: dict[str, dict[str, set[object]]] = {}
    gained: dict[str, dict[str, set[object]]] = {}
    for old, new_id in pairs:
        skip = unstable.get(old, {})
        lost[old] = _minus(_minus(before[old], after[new_id]), skip)
        gained[old] = _minus(_minus(after[new_id], before[old]), skip)

    # What some matched test gave up and what some test took up. A line in both moved
    # between tests: the once-per-worker code the module docstring describes.
    def union(per_test: Iterable[Mapping[str, set[object]]]) -> dict[str, set[object]]:
        merged: dict[str, set[object]] = collections.defaultdict(set)
        for files in per_test:
            for path, items in files.items():
                merged[path] |= items
        return merged

    all_lost = union(lost.values())
    all_gained = union([*gained.values(), *(after[test_id] for test_id in new)])

    # (share of its lines a test lost, its report line), largest loss first.
    changed: list[tuple[float, str]] = []
    reattributed = 0
    for old, new_id in pairs:
        real_lost = _minus(lost[old], all_gained)
        real_gained = _minus(gained[old], all_lost)
        lost_count = sum(len(items) for items in real_lost.values())
        reattributed += sum(len(items) for items in lost[old].values()) - lost_count
        if real_lost or real_gained:
            ran = sum(len(items) for items in before[old].values())
            share = lost_count / ran if ran else 0.0
            changed.append(
                (
                    share,
                    (
                        f"{old}: {_describe(real_lost)} no longer run ({share:.0%}), "
                        f"{_describe(real_gained)} newly run"
                    ),
                )
            )
    changed.sort(key=lambda item: -item[0])
    hollowed = [line for share, line in changed if share > max_lost]
    small = [line for share, line in changed if share <= max_lost]

    def total(data: Coverage) -> int:
        return sum(len(items) for items in union(data.values()).values())

    print(f"before: {len(before)} tests with coverage, {total(before)} lines/arcs run")
    print(f"after: {len(after)} tests with coverage, {total(after)} lines/arcs run")
    print(f"matched: {len(pairs)}")
    print(f"lines/arcs that moved between tests: {reattributed}")
    if baseline is not None:
        varying = sum(
            len(items) for files in unstable.values() for items in files.values()
        )
        print(
            f"lines/arcs that vary between the two before runs, not compared: {varying}"
        )
    _print_list("missing from after", missing)
    _print_list("new in after", new)
    _print_list(f"tests that lost over {max_lost:.0%} of their lines/arcs", hollowed)
    _print_list("smaller changes, listed for a person to read", small)
    return not missing and not hollowed and (allow_new or not new)


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
        "--renamed",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="a test whose node ID changed, both IDs whole",
    )
    parser.add_argument(
        "--allow-new", action="store_true", help="list new tests without failing"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="coverage: a second run of the before tree; what varies is not compared",
    )
    parser.add_argument(
        "--max-lost",
        type=float,
        default=0.1,
        help="coverage: the share of its lines a test may lose without failing "
        "(default: 0.1)",
    )
    parser.add_argument(
        "--root",
        default="src/archivey/",
        help="coverage paths are shown from this part on (default: src/archivey/)",
    )
    args = parser.parse_args(argv)
    if args.before.resolve() == args.after.resolve():
        raise SystemExit(f"before and after are the same file: {args.before}")
    moves = _parse_pairs("--moved", args.moved)
    renames = _parse_pairs("--renamed", args.renamed)
    if args.kind == "outcomes":
        outcomes_before = load_outcomes(args.before)
        _refuse_empty("outcomes", args.before, len(outcomes_before))
        same = compare_outcomes(
            outcomes_before,
            load_outcomes(args.after),
            moves,
            renames,
            allow_new=args.allow_new,
        )
    else:
        same = compare_coverage(
            load_coverage(args.before, args.root),
            load_coverage(args.after, args.root),
            moves,
            renames,
            allow_new=args.allow_new,
            baseline=(
                load_coverage(args.baseline, args.root) if args.baseline else None
            ),
            max_lost=args.max_lost,
        )
    print("same" if same else "DIFFERENT")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Checks an implementer runs before adding the `review` label, and again before each re-label.

The review loop's findings, measured over 38 pull requests (2026-09-23/24), are dominated
by three shapes that a script can find faster than a reviewer:

- a symbol or an exception type changed in `src/` and is still described the old way in a
  spec, a handbook page or the CHANGELOG (``sweep``);
- a paragraph edited in place and not rewrapped, leaving a line far wider than the rest of
  its file (``width``);
- a new test the PR body says fails on `main`, which passes there (``red-on-base``).

67 of the 70 findings raised after round 1 were caused by the previous round's fix, so the
same checks apply to the fix commits before the label goes on again.

    python3 scripts/review_prep.py                      # sweep + width against origin/main
    python3 scripts/review_prep.py --base HEAD~1        # just the last commit's fixes
    python3 scripts/review_prep.py red-on-base tests/test_x.py::test_y ...

``sweep`` only reports: whether a hit is stale needs reading. ``width`` exits 1 when an
added line is wider than its file allows. ``red-on-base`` prints a table to paste into the
PR body in place of a prose "fails on main" claim.

Stdlib only, so it runs outside the project environment; ``red-on-base`` needs the
project's environment because it runs pytest.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Where a claim about `src/` behaviour is written down, apart from `src/` itself.
DOC_ROOTS = (
    "openspec/specs",
    "openspec/changes",
    "docs",
    "dev-docs",
    "CHANGELOG.md",
    "README.md",
)
DOC_SUFFIXES = {".md", ".yml", ".yaml"}
# An archived change records what was true when it landed; it is not stale when code moves.
DOC_EXCLUDE = ("openspec/changes/archive/", "dev-docs/history/")

PY_WIDTH = 88
# How far past its file's width an added line may run before it is reported. Comments of
# 89-95 columns are common in the tree and nobody has flagged one; the findings were a
# paragraph edited in place and never rewrapped, which lands at 105-150.
SLACK = 10
# A markdown file counts as hard-wrapped when nearly all of its prose lines fit this width.
MD_WRAPPED_BELOW = 100
MD_WRAPPED_SHARE = 0.98

_DEF_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")
_CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=")
_RAISE_RE = re.compile(r"\braise\s+([A-Z]\w*(?:Error|Exception|Warning))\b")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@\s?(.*)$")
_HUNK_DEF_RE = re.compile(r"(?:def|class)\s+([A-Za-z_]\w*)")
_URL_RE = re.compile(r"https?://")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


# --- diff parsing ---------------------------------------------------------------------


@dataclass
class FileDiff:
    path: str
    added: list[tuple[int, str]] = field(
        default_factory=list
    )  # (new line number, text)
    removed: list[tuple[str, str]] = field(default_factory=list)  # (hunk context, text)


def parse_diff(text: str) -> list[FileDiff]:
    """Split a ``git diff -U0`` into per-file added and removed lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    context = ""
    old_path = ""
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = None
            continue
        if line.startswith("--- "):
            old_path = line[4:].removeprefix("a/")
            continue
        if line.startswith("+++ "):
            # A deleted file's removed lines count too: deleting a module is the
            # commonest way a name disappears.
            path = line[4:]
            current = FileDiff(
                old_path if path == "/dev/null" else path.removeprefix("b/")
            )
            files.append(current)
            continue
        if current is None:
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            new_line = int(hunk.group(1))
            context = hunk.group(2)
            continue
        if line.startswith("+"):
            current.added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            current.removed.append((context, line[1:]))
    return files


# --- sweep: names and exception types that moved --------------------------------------


@dataclass(frozen=True)
class Moved:
    kind: str  # "name" or "raise"
    old: str
    where: str  # file, and for a raise the enclosing function
    near: str = ""  # a raise: the function whose docs should mention the new type


def moved_claims(files: Iterable[FileDiff]) -> list[Moved]:
    """Names defined in removed `src/` lines that no added line defines again, and
    exception types a function stopped raising."""
    files = [f for f in files if f.path.startswith("src/") and f.path.endswith(".py")]
    defined_now = {
        m.group(1)
        for f in files
        for _, text in f.added
        for m in (_DEF_RE.match(text) or _CONST_RE.match(text),)
        if m
    }
    out: list[Moved] = []
    seen: set[tuple[str, str]] = set()
    for f in files:
        raised_now = {
            m.group(1) for _, text in f.added for m in _RAISE_RE.finditer(text)
        }
        for context, text in f.removed:
            m = _DEF_RE.match(text) or _CONST_RE.match(text)
            if m and m.group(1) not in defined_now and ("name", m.group(1)) not in seen:
                seen.add(("name", m.group(1)))
                out.append(Moved("name", m.group(1), f.path))
            for r in _RAISE_RE.finditer(text):
                exc = r.group(1)
                if exc in raised_now or ("raise", f"{f.path}:{exc}") in seen:
                    continue
                seen.add(("raise", f"{f.path}:{exc}"))
                func = _HUNK_DEF_RE.search(context)
                out.append(Moved("raise", exc, f.path, func.group(1) if func else ""))
    return out


def doc_files() -> list[Path]:
    paths: list[Path] = []
    for root in DOC_ROOTS:
        p = ROOT / root
        if p.is_file():
            paths.append(p)
        elif p.is_dir():
            paths.extend(
                q for q in p.rglob("*") if q.suffix in DOC_SUFFIXES and q.is_file()
            )
    # The contract first: a stale spec or published doc matters more than a stale plan.
    return [
        p
        for p in sorted(paths, key=lambda q: (not _authoritative(q), q))
        if not any(p.relative_to(ROOT).as_posix().startswith(x) for x in DOC_EXCLUDE)
    ]


def _authoritative(p: Path) -> bool:
    return p.relative_to(ROOT).as_posix().startswith(("openspec/specs/", "docs/"))


def sweep(moved: list[Moved], docs: list[Path]) -> list[str]:
    report: list[str] = []
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in docs}
    for m in moved:
        word = re.compile(rf"\b{re.escape(m.old)}\b")
        hits: list[str] = []
        for p, text in texts.items():
            # A raise is only worth a look where the doc also names the function.
            if m.kind == "raise" and (not m.near or m.near not in text):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if word.search(line):
                    hits.append(
                        f"    {p.relative_to(ROOT).as_posix()}:{n}: {line.strip()[:120]}"
                    )
        if not hits:
            continue
        if m.kind == "name":
            head = (
                f"`{m.old}` is no longer defined in src/ ({m.where}), still named in:"
            )
        else:
            head = (
                f"`{m.near or '?'}` in {m.where} no longer raises `{m.old}`; docs naming "
                f"both still say `{m.old}` at:"
            )
        report.append(head)
        report.extend(hits[:15])
        if len(hits) > 15:
            report.append(f"    … and {len(hits) - 15} more")
    return report


# --- width: added lines wider than their file ------------------------------------------


def _md_prose(lines: Iterable[str]) -> list[str]:
    """Lines a rewrap would touch: outside code fences, not tables, not bare URLs."""
    out: list[str] = []
    fenced = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|") or _URL_RE.search(line):
            continue
        out.append(line)
    return out


def md_limit(base_text: str | None) -> int | None:
    """The width a markdown file is wrapped at, or None when it is not hard-wrapped.

    Judged from the file as it was before this change, so an edit cannot widen its own
    limit. Some docs here are wrapped at 88, some at 91, and some not at all.
    """
    if base_text is None:
        return None
    prose = [line for line in _md_prose(base_text.splitlines()) if line.strip()]
    if len(prose) < 10:
        return None
    fitting = sorted(len(line) for line in prose if len(line) <= MD_WRAPPED_BELOW)
    if len(fitting) / len(prose) < MD_WRAPPED_SHARE:
        return None
    return max(PY_WIDTH, fitting[-1])


def base_text(base: str, path: str) -> str | None:
    try:
        return git("show", f"{base}:{path}")
    except subprocess.CalledProcessError:
        return None


def width(files: Iterable[FileDiff], base: str) -> list[str]:
    report: list[str] = []
    for f in files:
        if f.path.endswith(".py"):
            # Tests are left out: nobody has flagged a long line in one, and their
            # literal fixtures make the check noisy.
            limit: int | None = (
                PY_WIDTH if f.path.startswith(("src/", "scripts/")) else None
            )
            candidates = f.added
        elif f.path.endswith(".md"):
            limit = md_limit(base_text(base, f.path))
            prose = set(_md_prose(text for _, text in f.added))
            candidates = [(n, text) for n, text in f.added if text in prose]
        else:
            continue
        if limit is None:
            continue
        for n, text in candidates:
            if (
                len(text) > limit + SLACK
                and not _URL_RE.search(text)
                and "noqa: E501" not in text
            ):
                report.append(f"{f.path}:{n}: {len(text)} > {limit} columns")
    return report


# --- red-on-base: do the new tests fail without this PR's src/? -------------------------


def red_on_base(base: str, test_ids: list[str]) -> int:
    merge_base = git("merge-base", base, "HEAD").strip()
    tmp = Path(tempfile.mkdtemp(prefix="red-on-base-"))
    tree = tmp / "tree"
    try:
        git("worktree", "add", "--detach", str(tree), merge_base)
        # This branch's tests against the base's src/: the tests are the claim, the base
        # is what they must fail against.
        shutil.rmtree(tree / "tests")
        shutil.copytree(ROOT / "tests", tree / "tests")
        env = dict(os.environ, PYTHONPATH=str(tree / "src"))
        rows: list[tuple[str, str]] = []
        for test_id in test_ids:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--no-header",
                    "-rN",
                    test_id,
                ],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
            )
            rows.append((test_id, _outcome(proc.returncode, proc.stdout + proc.stderr)))
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(tree)],
            cwd=ROOT,
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"New tests against `src/` at the merge base ({merge_base[:10]}):\n")
    print("| Test | On the base |")
    print("|---|---|")
    for test_id, outcome in rows:
        print(f"| `{test_id}` | {outcome} |")
    passing = [t for t, o in rows if o.startswith("passes")]
    if passing:
        print(f"\n{len(passing)} test(s) pass without this change: they do not pin it.")
    return 1 if passing else 0


def _outcome(returncode: int, output: str) -> str:
    if returncode == 0:
        return "passes"
    if returncode == 1:
        return "fails"
    # A collection error is most often an import of a name this PR adds. That is red, but
    # it proves the import, not the behaviour the test names.
    why = next(
        (
            line.strip().removeprefix("E").strip()
            for line in output.splitlines()
            if "cannot import name" in line or "No module named" in line
        ),
        "",
    )
    if why:
        return f"errors at collection: {why[:90]}"
    if returncode in (4, 5) or "ERROR: not found" in output:
        return "not found (check the id)"
    return "errors at collection"


# --- entry point ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base", default="origin/main", help="compare against (origin/main)"
    )
    parser.add_argument("--head", default="HEAD", help=argparse.SUPPRESS)
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["all", "sweep", "width", "red-on-base"],
    )
    parser.add_argument("tests", nargs="*", help="red-on-base: pytest node ids")
    args = parser.parse_args(argv)

    if args.command == "red-on-base":
        if not args.tests:
            parser.error("red-on-base needs at least one pytest node id")
        return red_on_base(args.base, args.tests)

    files = parse_diff(git("diff", "-U0", f"{args.base}...{args.head}"))
    status = 0
    if args.command in ("all", "sweep"):
        found = sweep(moved_claims(files), doc_files())
        print(
            "=== sweep: docs still naming what this branch moved (read each; may be fine)"
        )
        print("\n".join(found) if found else "nothing found")
    if args.command in ("all", "width"):
        found = width(files, args.base)
        print("=== width: added lines wider than their file (rewrap the paragraph)")
        print("\n".join(found) if found else "nothing found")
        status = 1 if found else 0
    return status


if __name__ == "__main__":
    sys.exit(main())

"""Print one line per failed or errored test from a pytest JUnit XML report.

The PyPy trial job in ci.yml uses this. pytest's own ``-r`` summary prints the whole
assertion message, which can run to dozens of lines per failure, and the GitHub API
returns only the last few thousand lines of a job log. One line per failure keeps the
complete list readable from there. A count per message comes last.

Usage: python scripts/junit_failure_lines.py REPORT.xml [--width N] [--detail N]

Each line names the test by its pytest node id (``tests/test_x.py::TestThing::test_y``),
which pastes back into a pytest command. That needs the ``file`` attribute, which pytest
writes only with ``-o junit_family=xunit1``; without it the dotted class name is printed.
A line cut to ``--width`` ends in "…".

``--detail N`` also prints the last N lines of each failure's traceback, for a run made
with ``--tb=short`` or longer.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
import xml.etree.ElementTree as ET


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _node_id(case: ET.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "")
    path = case.get("file")
    if not path:
        return f"{classname}::{name}"
    # classname is the module's dotted path, then any class: keep only the class.
    module = path.removesuffix(".py").replace("/", ".")
    inner = classname.removeprefix(module).lstrip(".")
    return "::".join(part for part in (path, inner, name) if part)


def _cut(line: str, width: int) -> str:
    return line if len(line) <= width else line[: width - 1] + "…"


def _count_key(message: str) -> str:
    """``message`` with what varies per parameter replaced, to group failures on.

    Quoted values (often a test id or a path) and standalone numbers go; digits inside
    a word stay, so ``bzip2``, ``o.7z`` and ``\\x14`` still read as themselves. The cut
    to 120 characters keeps each counts row on one line of the log.
    """
    message = re.sub(r"'[^']*'", "'…'", message)
    message = re.sub(r'"[^"]*"', '"…"', message)
    return _cut(re.sub(r"\b\d+\b", "N", message), 120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report")
    parser.add_argument("--width", type=int, default=220)
    parser.add_argument("--detail", type=int, default=0)
    args = parser.parse_args(argv)

    root = ET.parse(args.report).getroot()
    counts: collections.Counter[str] = collections.Counter()
    totals: collections.Counter[str] = collections.Counter()
    for case in root.iter("testcase"):
        totals["tests"] += 1
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            totals[kind] += 1
            message = _first_line(node.get("message") or node.text or "")
            print(_cut(f"{kind.upper()} {_node_id(case)} - {message}", args.width))
            if args.detail and node.text:
                for line in node.text.splitlines()[-args.detail :]:
                    print(_cut(f"    {line}", args.width))
            counts[_count_key(message)] += 1
        if case.find("skipped") is not None:
            totals["skipped"] += 1

    print()
    print("Counts by message:")
    for message, count in counts.most_common():
        print(f"{count:5d}  {message}")
    print()
    print(", ".join(f"{key} {value}" for key, value in sorted(totals.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

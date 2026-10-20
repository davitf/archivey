"""Print one line per failed or errored test from a pytest JUnit XML report.

The PyPy trial job in ci.yml uses this. pytest's own ``-r`` summary prints the whole
assertion message, which can run to dozens of lines per failure, and the GitHub API
returns only the last few thousand lines of a job log. One line per failure keeps the
complete list readable from there. A count per message comes last.

Usage: python scripts/junit_failure_lines.py REPORT.xml [--width N]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report")
    parser.add_argument("--width", type=int, default=220)
    args = parser.parse_args(argv)

    root = ET.parse(args.report).getroot()
    counts: collections.Counter[str] = collections.Counter()
    totals = collections.Counter()
    for case in root.iter("testcase"):
        totals["tests"] += 1
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            totals[kind] += 1
            message = _first_line(node.get("message") or node.text or "")
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            print(f"{kind.upper()} {name} - {message}"[: args.width])
            # Numbers and quoted values vary per parameter; group on the rest.
            counts[re.sub(r"\d+", "N", message)[:120]] += 1
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

"""Assert that every package in ``[free-threaded]`` leaves the GIL disabled.

Run in the free-threaded CI job after ``uv sync --extra free-threaded``. The list of
packages comes from the installed archivey's own metadata, so it cannot drift from
``pyproject.toml``: a package added to the extra is imported here on the next run.

A requirement whose environment marker excludes this interpreter (``backports.zstd``
on 3.14+, ``cryptography`` below 3.14) is not installed, and is skipped. A requirement
with no ``python_version`` marker that is not installed is an error: the sync did not
install the extra this check is about.

Exits non-zero with a clear message on any violation. Stdlib only, because the job
runs it in an environment with no dev group.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys

EXTRA = "free-threaded"

# Distribution name -> the module to import, where importing the bare name would not
# load the C extension (``backports`` is a namespace package; ``lz4``'s frame codec is
# a submodule; cryptography's Rust bindings load with its cipher primitives).
IMPORT_NAME = {
    "backports-zstd": "backports.zstd",
    "lz4": "lz4.frame",
    "cryptography": "cryptography.hazmat.primitives.ciphers",
}

_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


def _dist_name(requirement: str) -> str:
    """The normalised distribution name at the start of a requirement string."""
    head = re.split(r"[<>=!~;\[ (]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def extra_requirements(requires: list[str], extra: str) -> list[tuple[str, str]]:
    """``(distribution name, marker)`` for each requirement that belongs to ``extra``."""
    found = []
    for requirement in requires:
        _, _, marker = requirement.partition(";")
        match = _EXTRA_MARKER.search(marker)
        if match and re.sub(r"[-_.]+", "-", match.group(1)).lower() == extra:
            found.append((_dist_name(requirement), marker))
    return found


def main() -> int:
    if not hasattr(sys, "_is_gil_enabled"):
        print("not a free-threaded-capable interpreter", file=sys.stderr)
        return 1
    if sys._is_gil_enabled():
        print("the GIL is enabled before any extra is imported", file=sys.stderr)
        return 1

    requirements = extra_requirements(
        importlib.metadata.requires("archivey") or [], EXTRA
    )
    if not requirements:
        print(f"archivey's metadata lists no [{EXTRA}] requirements", file=sys.stderr)
        return 1

    imported, skipped, missing = [], [], []
    for dist, marker in requirements:
        try:
            importlib.metadata.distribution(dist)
        except importlib.metadata.PackageNotFoundError:
            (skipped if "python_version" in marker else missing).append(dist)
            continue
        module = IMPORT_NAME.get(dist, dist.replace("-", "_"))
        importlib.import_module(module)
        imported.append(module)
        if sys._is_gil_enabled():
            print(f"importing {module} ({dist}) re-enabled the GIL", file=sys.stderr)
            return 1

    if missing:
        print(f"[{EXTRA}] packages not installed: {missing}", file=sys.stderr)
        return 1
    print(f"GIL still disabled with [{EXTRA}]: imported {', '.join(imported)}")
    if skipped:
        print(f"excluded by marker on this interpreter: {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

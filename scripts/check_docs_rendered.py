"""Assert no Sphinx cross-reference role survives into the built docs site.

The docstrings use Sphinx roles (``:class:`~archivey.ArchiveMember```), and
mkdocstrings does not understand them. ``scripts/griffe_extensions.py`` rewrites them
into links at build time; before it did, 129 of them reached the published API page as
literal text (``:class:~archivey.ArchiveMember``), and ``mkdocs build --strict`` was
green the whole time. It has nothing to warn about: to Markdown a role is just text.

This scans the built HTML (and the search index, which is what search results show)
for any role that got through: a new docstring spelling the extension does not match,
a role written into a page under ``docs/``, or the extension being dropped from
``mkdocs.yml``.

Run after a build:

    uv run --group docs mkdocs build --strict
    python3 scripts/check_docs_rendered.py [site-dir]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The role name, then what follows it once Markdown has turned the target's backticks
# into ``<code>`` (``\u003c`` or ``&lt;`` in escaped text), or the target itself. A role
# name standing alone, with a space or closing tag after it, is not a leak.
LEAKED_ROLE_RE = re.compile(
    r":(?:py:)?(?:class|func|meth|attr|data|const|exc|mod|obj|any):(?:[`~\w<&]|\\u003c)"
)


def main(argv: list[str]) -> int:
    site = Path(argv[1] if len(argv) > 1 else "site")
    if not (site / "index.html").is_file():
        print(f"error: no built site at {site}/ (run `mkdocs build` first)")
        return 2
    leaks: list[str] = []
    files = [*site.rglob("*.html"), *site.glob("search/*.json")]
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        for match in LEAKED_ROLE_RE.finditer(text):
            start = max(match.start() - 40, 0)
            context = " ".join(text[start : match.end() + 40].split())
            leaks.append(f"{path.relative_to(site)}: ...{context}...")
    if leaks:
        print(f"{len(leaks)} Sphinx role(s) leaked into the built site:")
        for leak in leaks:
            print(f"  {leak}")
        print(
            "\nWrite the reference as a mkdocstrings cross-reference "
            "([`Name`][archivey.Name]) or teach SphinxRolesToAutorefs in "
            "scripts/griffe_extensions.py the new spelling."
        )
        return 1
    print(f"ok: no Sphinx roles in {len(files)} built files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

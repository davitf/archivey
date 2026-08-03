"""Assert the published docs tree is complete and self-contained.

Two invariants, both of which `mkdocs build --strict` fails to enforce.

**Every file under `docs/` is in the nav.** MkDocs *reports* unlisted pages, but at
INFO level, and then exits 0 — so a page can be built, served, URL-reachable and
search-indexed while absent from every menu. Six pages had drifted out that way
(three PPMd investigations, two upstream reports, one 615-line ADR) with a green
strict build the whole time. Since `docs/` now holds user material only, "not in
the nav" means "published to nobody on purpose", which is never what anyone meant.

**Every absolute repo URL in the published pages resolves to a real file.** Published
pages must not link into unpublished maintainer docs; where the depth is worth
keeping, the link is an absolute `github.com/davitf/archivey/blob/main/…` URL
instead. Those URLs point at files this repository moves around, and nothing in a
docs build looks at them — so they rot silently, which is the exact failure this
whole reorganization exists to fix. Checking them offline costs nothing: the target
is a path in this very repository.

Run by hand with:

    uv run --group docs python scripts/check_docs_nav.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MKDOCS_YML = ROOT / "mkdocs.yml"

# Pages that link out to the repository do so with absolute URLs pinned to `main`,
# not to a tag: someone following one wants the current state of that document.
# `tree/` for directories, `blob/` for files — both name a repo-relative path.
REPO_URL = re.compile(
    r"https://github\.com/davitf/archivey/(?:blob|tree)/main/([^)\s\"'#]+)"
)


def load_nav_files() -> list[str]:
    """Return every `docs/`-relative path named in mkdocs.yml's nav.

    mkdocs.yml uses `!!python/name:` tags for Griffe extensions, which SafeLoader
    rejects — so ignore unknown tags rather than trusting the file with FullLoader.
    """

    class NavLoader(yaml.SafeLoader):
        pass

    NavLoader.add_multi_constructor("", lambda loader, suffix, node: None)

    with MKDOCS_YML.open(encoding="utf-8") as fh:
        config = yaml.load(fh, Loader=NavLoader)  # noqa: S506 — tags neutralised above

    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(config.get("nav") or [])
    return found


def main() -> int:
    problems: list[str] = []

    nav = load_nav_files()
    nav_pages = {entry for entry in nav if entry.endswith(".md")}
    on_disk = {
        str(path.relative_to(DOCS)) for path in DOCS.rglob("*.md") if path.is_file()
    }

    for page in sorted(on_disk - nav_pages):
        problems.append(
            f"docs/{page} is published but has no nav entry in mkdocs.yml. "
            f"Everything under docs/ is for users; maintainer material goes in dev-docs/."
        )

    for page in sorted(nav_pages - on_disk):
        problems.append(f"mkdocs.yml nav lists docs/{page}, which does not exist.")

    # Published pages plus the README, whose URLs freeze at the 0.2.0 tag because
    # PyPI release metadata is immutable.
    sources = sorted(DOCS.rglob("*.md")) + [ROOT / "README.md"]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for target in sorted(set(REPO_URL.findall(text))):
            if not (ROOT / target).exists():
                problems.append(
                    f"{source.relative_to(ROOT)} links "
                    f"github.com/davitf/archivey/…/{target}, which is not in this repo."
                )

    if problems:
        print("Published docs tree is inconsistent:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"docs/: {len(on_disk)} pages, all in nav; repo links all resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

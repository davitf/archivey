"""`scripts/review_prep.py`, the checks an implementer runs before adding the `review` label.

The diff parsing and the two reports are pure functions over text, so they are tested
here on hand-written diffs; the git and pytest calls around them are not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_prep.py"

_spec = importlib.util.spec_from_file_location("review_prep", SCRIPT)
assert _spec is not None and _spec.loader is not None
prep = importlib.util.module_from_spec(_spec)
# `scripts/` is not a package; `@dataclass` looks its module up by name while executing.
sys.modules["review_prep"] = prep
_spec.loader.exec_module(prep)

DIFF = """\
diff --git a/src/archivey/a.py b/src/archivey/a.py
--- a/src/archivey/a.py
+++ b/src/archivey/a.py
@@ -10,2 +10,2 @@ def open_member(self):
-        raise TruncatedError("short")
-class OldName:
+        raise CorruptionError("short")
+class NewName:
@@ -40,0 +41,1 @@ def other():
+    x = 1
diff --git a/src/archivey/gone.py b/src/archivey/gone.py
deleted file mode 100644
--- a/src/archivey/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-MAX_THING = 4
-def helper():
-    raise ValueError("x")
diff --git a/docs/x.md b/docs/x.md
--- a/docs/x.md
+++ b/docs/x.md
@@ -3,0 +4,1 @@
+new line
"""


def test_parse_diff_keeps_line_numbers_and_deleted_files() -> None:
    files = {f.path: f for f in prep.parse_diff(DIFF)}
    assert set(files) == {"src/archivey/a.py", "src/archivey/gone.py", "docs/x.md"}
    assert files["src/archivey/a.py"].added[-1] == (41, "    x = 1")
    assert files["docs/x.md"].added == [(4, "new line")]
    # Deleting a module is the commonest way a name disappears; its lines must count.
    assert [t for _, t in files["src/archivey/gone.py"].removed][0] == "MAX_THING = 4"


def test_moved_claims_finds_removed_names_and_dropped_raises() -> None:
    moved = prep.moved_claims(prep.parse_diff(DIFF))
    names = {m.old for m in moved if m.kind == "name"}
    assert names == {"OldName", "MAX_THING", "helper"}
    raises = {(m.old, m.near) for m in moved if m.kind == "raise"}
    # ValueError is not an archivey error type and matches the pattern only by suffix;
    # it is reported, and filtered later by needing a doc that names `helper`.
    assert ("TruncatedError", "open_member") in raises


def test_a_name_defined_again_is_not_moved() -> None:
    diff = DIFF.replace("+class NewName:", "+class OldName(Base):")
    names = {
        m.old for m in prep.moved_claims(prep.parse_diff(diff)) if m.kind == "name"
    }
    assert "OldName" not in names


def test_sweep_reports_a_doc_still_naming_a_removed_name(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(prep, "ROOT", tmp_path)
    spec = tmp_path / "openspec" / "specs" / "x" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("The reader SHALL use `OldName`.\nUnrelated OldNamed line.\n")
    report = prep.sweep([prep.Moved("name", "OldName", "src/a.py")], [spec])
    assert report[0].startswith("`OldName` is no longer defined")
    assert report[1:] == [
        "    openspec/specs/x/spec.md:1: The reader SHALL use `OldName`."
    ]


def test_sweep_only_reports_a_raise_where_the_doc_names_the_function(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(prep, "ROOT", tmp_path)
    near = tmp_path / "near.md"
    near.write_text("`open_member` raises `TruncatedError` on a short read.\n")
    far = tmp_path / "far.md"
    far.write_text("Everything else raises `TruncatedError`.\n")
    moved = [prep.Moved("raise", "TruncatedError", "src/a.py", "open_member")]
    report = prep.sweep(moved, [near, far])
    assert [line for line in report if line.startswith("    ")] == [
        "    near.md:1: `open_member` raises `TruncatedError` on a short read."
    ]


def _wrapped(width: int, lines: int = 20) -> str:
    return "\n".join("w" * width for _ in range(lines)) + "\n"


def test_md_limit_reads_the_files_own_width() -> None:
    assert prep.md_limit(_wrapped(91)) == 91
    assert prep.md_limit(_wrapped(70)) == 88  # never narrower than the code width
    # One long line per paragraph: the file is not hard-wrapped, so there is no limit.
    assert prep.md_limit(_wrapped(300)) is None
    # Too little prose to tell, and a file this change creates.
    assert prep.md_limit(_wrapped(80, lines=3)) is None
    assert prep.md_limit(None) is None


def test_md_limit_ignores_tables_code_and_urls() -> None:
    text = _wrapped(88) + "| " + "x" * 200 + " |\n```\n" + "y" * 200 + "\n```\n"
    assert prep.md_limit(text) == 88


def test_width_flags_only_lines_far_past_their_file(monkeypatch) -> None:
    monkeypatch.setattr(prep, "base_text", lambda base, path: _wrapped(88))
    limit = 88 + prep.SLACK
    diff = prep.FileDiff("docs/x.md", added=[(1, "a" * limit), (2, "b" * (limit + 1))])
    source = prep.FileDiff("src/archivey/x.py", added=[(7, "# " + "c" * 120)])
    test = prep.FileDiff("tests/test_x.py", added=[(3, "d" * 150)])
    url = prep.FileDiff("src/archivey/y.py", added=[(1, "# https://" + "e" * 150)])
    report = prep.width([diff, source, test, url], "base")
    assert report == [
        f"docs/x.md:2: {limit + 1} > 88 columns",
        "src/archivey/x.py:7: 122 > 88 columns",
    ]


def test_outcome_names_what_a_non_red_run_means() -> None:
    assert prep._outcome(0, "") == "passes"
    assert prep._outcome(1, "") == "fails"
    assert (
        prep._outcome(4, "ERROR: not found: tests/x.py::nope")
        == "not found (check the id)"
    )
    imported = "E   ImportError: cannot import name 'thing' from 'archivey.x'"
    assert prep._outcome(2, imported).startswith("errors at collection: ImportError")

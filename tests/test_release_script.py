"""The text edits `scripts/release.py` makes, exercised without git or GitHub.

A wrong edit here ships: a version that disagrees between the two packages fails the
publish job after the tag, and a CHANGELOG entry moved to the wrong place becomes the
GitHub release notes. So the edits are pinned here, including on the real files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.py"

_spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)

URL = release.REPO_URL


@pytest.mark.parametrize(
    "version", ["0.2.1", "1.0.0", "0.3.0rc1", "0.3.0b2", "0.2.1.dev0"]
)
def test_accepted_versions(version: str) -> None:
    assert release.check_version(version) == version


@pytest.mark.parametrize("version", ["v0.2.1", "0.2", "0.2.1-rc1", "0.2.1.post1", ""])
def test_refused_versions(version: str) -> None:
    with pytest.raises(release.ReleaseError):
        release.check_version(version)


@pytest.mark.parametrize(
    ("released", "dev"),
    [
        ("0.2.0", "0.2.1.dev0"),
        ("0.2.9", "0.2.10.dev0"),
        ("0.3.0rc1", "0.3.0rc2.dev0"),
        ("1.0.0a1", "1.0.0a2.dev0"),
    ],
)
def test_next_dev_version(released: str, dev: str) -> None:
    assert release.next_dev_version(released) == dev


def test_next_dev_version_refuses_a_dev_version() -> None:
    with pytest.raises(release.ReleaseError):
        release.next_dev_version("0.2.1.dev0")


def test_set_project_version_on_both_real_files() -> None:
    for path in (release.ROOT_PYPROJECT, release.CLI_PYPROJECT):
        text = path.read_text(encoding="utf-8")
        new = release.set_project_version(text, "9.8.7")
        assert release.project_version(new) == "9.8.7"
        # Only the version line and the pin change.
        changed = [
            (a, b)
            for a, b in zip(text.splitlines(), new.splitlines(), strict=True)
            if a != b
        ]
        assert 1 <= len(changed) <= 2
    cli = release.set_project_version(
        release.CLI_PYPROJECT.read_text(encoding="utf-8"), "9.8.7"
    )
    assert '"archivey[recommended]==9.8.7"' in cli


def test_set_project_version_leaves_dependency_floors_alone() -> None:
    text = (
        'version = "0.2.0"\ndependencies = ["pyppmd>=1.3.1"]\n[tool.x]\nversion = "1"\n'
    )
    assert release.set_project_version(text, "0.2.1") == (
        'version = "0.2.1"\ndependencies = ["pyppmd>=1.3.1"]\n[tool.x]\nversion = "1"\n'
    )


CHANGELOG = f"""# Changelog

Intro.

## [Unreleased]

### Fixed

- Fixed 3 bugs in ZIP reading.

## [0.2.0] - 2026-11-01

First release.

<!--
Example links:
[Unreleased]: https://example.invalid
-->

[Unreleased]: {URL}/compare/v0.2.0...HEAD
[0.2.0]: {URL}/releases/tag/v0.2.0
"""


def test_changelog_moves_unreleased_under_the_new_heading() -> None:
    out = release.changelog_for_release(CHANGELOG, "0.2.1", "v0.2.0", "2026-11-20")
    assert "## [Unreleased]\n\n## [0.2.1] - 2026-11-20\n\n### Fixed\n\n- Fixed 3" in out
    assert "\n## [0.2.0] - 2026-11-01\n" in out
    assert out.endswith(
        f"[Unreleased]: {URL}/compare/v0.2.1...HEAD\n"
        f"[0.2.1]: {URL}/compare/v0.2.0...v0.2.1\n"
        f"[0.2.0]: {URL}/releases/tag/v0.2.0\n"
    )
    # The example inside the comment is not a link definition and stays as it was.
    assert "[Unreleased]: https://example.invalid\n-->" in out


def test_changelog_section_is_the_release_notes() -> None:
    out = release.changelog_for_release(CHANGELOG, "0.2.1", "v0.2.0", "2026-11-20")
    assert (
        release.changelog_section(out, "0.2.1")
        == "### Fixed\n\n- Fixed 3 bugs in ZIP reading.\n"
    )
    assert release.changelog_section(out, "0.2.0") == "First release.\n"


def test_changelog_refuses_an_empty_entry() -> None:
    empty = CHANGELOG.replace("### Fixed\n\n- Fixed 3 bugs in ZIP reading.\n\n", "")
    with pytest.raises(release.ReleaseError, match="nothing under"):
        release.changelog_for_release(empty, "0.2.1", "v0.2.0", "2026-11-20")


def test_changelog_dates_a_prewritten_entry() -> None:
    text = CHANGELOG.replace(
        "### Fixed\n\n- Fixed 3 bugs in ZIP reading.\n\n", ""
    ).replace(
        "## [0.2.0] - 2026-11-01",
        "## [0.3.0] - Unreleased\n\nWritten ahead.\n\n## [0.2.0] - 2026-11-01",
    )
    out = release.changelog_for_release(text, "0.3.0", "v0.2.0", "2026-12-01")
    assert "## [0.3.0] - 2026-12-01\n\nWritten ahead." in out
    assert f"[0.3.0]: {URL}/compare/v0.2.0...v0.3.0\n" in out


def test_changelog_refuses_two_places_for_one_entry() -> None:
    text = CHANGELOG.replace(
        "## [0.2.0] - 2026-11-01", "## [0.3.0] - Unreleased\n\n## [0.2.0] - 2026-11-01"
    )
    with pytest.raises(release.ReleaseError, match="move the text"):
        release.changelog_for_release(text, "0.3.0", "v0.2.0", "2026-12-01")


def test_real_changelog_can_release_0_2_0() -> None:
    """The 0.2.0 entry was written ahead of time; the script only dates it."""
    text = release.CHANGELOG.read_text(encoding="utf-8")
    if "## [0.2.0] - Unreleased" not in text:
        pytest.skip("0.2.0 is released")
    out = release.changelog_for_release(text, "0.2.0", "v0.2.0rc1", "2026-11-01")
    assert "## [0.2.0] - 2026-11-01" in out
    assert out.count("## [Unreleased]") == 1
    assert f"[0.2.0]: {URL}/compare/v0.2.0rc1...v0.2.0\n" in out
    assert release.changelog_section(out, "0.2.0").startswith("First public release.")


def test_dry_run_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    before = {
        p: p.read_bytes() for p in (release.ROOT_PYPROJECT, release.CLI_PYPROJECT)
    }
    assert release.main(["--dry-run", "finish", "0.9.9.dev0"]) == 0
    assert {p: p.read_bytes() for p in before} == before
    out = capsys.readouterr().out
    assert "+ git switch -c release/v0.9.9.dev0" in out
    assert "+ gh pr create" in out


def test_start_refuses_before_changing_anything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty [Unreleased] is refused before the branch or any file is touched."""
    text = release.CHANGELOG.read_text(encoding="utf-8")
    body = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    if body.strip():
        pytest.skip("[Unreleased] has text")
    assert release.main(["--dry-run", "start", "0.9.9"]) == 1
    captured = capsys.readouterr()
    assert "nothing under" in captured.err
    assert "+ " not in captured.out


def test_changelog_without_a_comment_keeps_the_top_of_the_file() -> None:
    text = CHANGELOG.split("<!--", 1)[0] + f"[0.2.0]: {URL}/releases/tag/v0.2.0\n"
    out = release.changelog_for_release(text, "0.2.1", "v0.2.0", "2026-11-20")
    assert out.startswith(text.split("## [Unreleased]", 1)[0])
    assert out.endswith(
        f"[Unreleased]: {URL}/compare/v0.2.1...HEAD\n"
        f"[0.2.1]: {URL}/compare/v0.2.0...v0.2.1\n"
        f"[0.2.0]: {URL}/releases/tag/v0.2.0\n"
    )
    assert "\n\n\n" not in out


def test_changelog_without_a_comment_or_links_appends_them() -> None:
    text = CHANGELOG.split("<!--", 1)[0].rstrip("\n") + "\n"
    out = release.changelog_for_release(text, "0.2.1", "v0.2.0", "2026-11-20")
    assert out.startswith(text.split("## [Unreleased]", 1)[0])
    assert out.endswith(
        "First release.\n\n"
        f"[Unreleased]: {URL}/compare/v0.2.1...HEAD\n"
        f"[0.2.1]: {URL}/compare/v0.2.0...v0.2.1\n"
    )

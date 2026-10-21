"""The text edits `scripts/release.py` makes, exercised without git or GitHub.

A wrong edit here ships: a version that disagrees between the two packages fails the
publish job after the tag, and a CHANGELOG entry moved to the wrong place becomes the
GitHub release notes. So the edits are pinned here, including on the real files.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty [Unreleased] is refused before the branch or any file is touched."""
    release.CHANGELOG.write_text(
        CHANGELOG.replace("### Fixed\n\n- Fixed 3 bugs in ZIP reading.\n\n", "")
    )
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


def _real_changelog_with_entry(with_comment: bool) -> tuple[str, str]:
    text = release.CHANGELOG.read_text(encoding="utf-8")
    if not with_comment:
        start, end = text.rindex("<!--"), text.rindex("-->") + 3
        text = text[:start] + text[end:].lstrip("\n")
    head, rest = text.split("## [Unreleased]\n", 1)
    body = "### Fixed\n\n- Fixed 2 bugs in TAR reading.\n"
    return head + "## [Unreleased]\n\n" + body + "\n" + rest.lstrip("\n"), body


@pytest.mark.parametrize("comment", ["at the end", "none", "at the top"])
def test_real_changelog_shape_survives_any_release(comment: str) -> None:
    """Holds for every future release, unlike the 0.2.0 test above."""
    text, body = _real_changelog_with_entry(comment != "none")
    if comment == "at the top":
        start, end = text.rindex("<!--"), text.rindex("-->") + 3
        block = text[start:end]
        text = text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")
        text = text.replace("## [Unreleased]", block + "\n\n## [Unreleased]", 1)
    out = release.changelog_for_release(text, "9.9.9", "v9.9.8", "2030-01-01")
    head = text.split("## [Unreleased]", 1)[0]
    assert out.startswith(head)
    assert out.splitlines()[0] == "# Changelog"
    assert out.count("## [Unreleased]") == 1
    assert "\n\n\n" not in out
    assert release.changelog_section(out, "9.9.9") == body
    assert out.count("\n\n") >= text.count("\n\n")  # no blank line lost
    links = (
        f"[Unreleased]: {URL}/compare/v9.9.9...HEAD\n"
        f"[9.9.9]: {URL}/compare/v9.9.8...v9.9.9\n"
    )
    assert links in out
    assert out.index(links) > out.rindex("## [")


def test_finish_refuses_a_release_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main(["--dry-run", "finish", "0.3.0"]) == 1
    captured = capsys.readouterr()
    assert "not '0.3.0'" in captured.err
    assert "+ " not in captured.out


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the three files the script edits, outside git, so dry runs can succeed."""
    for name in ("pyproject.toml", "packages/archivey-cli/pyproject.toml"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"))
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "ROOT_PYPROJECT", tmp_path / "pyproject.toml")
    monkeypatch.setattr(
        release, "CLI_PYPROJECT", tmp_path / "packages/archivey-cli/pyproject.toml"
    )
    monkeypatch.setattr(release, "CHANGELOG", tmp_path / "CHANGELOG.md")
    return tmp_path


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_dry_run_start_prints_the_steps_in_order(
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _snapshot(fake_root)
    assert release.main(["--dry-run", "start", "0.2.1"]) == 0
    assert _snapshot(fake_root) == before
    steps = [
        line.split()[1:3] for line in capsys.readouterr().out.splitlines()
        if line.startswith("+ ")
    ]  # fmt: skip
    assert steps == [
        ["git", "switch"],
        ["write", "pyproject.toml"],
        ["write", "packages/archivey-cli/pyproject.toml"],
        ["uv", "lock"],
        ["write", "CHANGELOG.md"],
        ["git", "add"],
        ["git", "add"],
        ["git", "commit"],
        ["git", "push"],
        ["gh", "pr"],
        ["gh", "workflow"],
        *[["gh", "workflow"]] * len(release.RELEASE_GATE_WORKFLOWS),
    ]


def test_dry_run_tag_checks_both_packages(
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for path in (release.ROOT_PYPROJECT, release.CLI_PYPROJECT):
        path.write_text(release.set_project_version(path.read_text(), "0.2.1"))
    release.CHANGELOG.write_text(
        release.changelog_for_release(CHANGELOG, "0.2.1", "v0.2.0", "2026-11-20")
    )
    assert release.main(["--dry-run", "tag", "0.2.1"]) == 0
    assert "+ gh release create v0.2.1 --target main" in capsys.readouterr().out

    cli = release.CLI_PYPROJECT
    cli.write_text(release.set_project_version(cli.read_text(), "0.2.0"))
    assert release.main(["--dry-run", "tag", "0.2.1"]) == 1
    assert "archivey-cli/pyproject.toml is at version 0.2.0" in capsys.readouterr().err


def test_dry_run_start_dispatches_each_gate_once(
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release.main(["--dry-run", "start", "0.2.1", "--no-testpypi"]) == 0
    dispatches = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("+ gh workflow run")
    ]  # fmt: skip
    assert dispatches == [
        f"+ gh workflow run {workflow} --ref release/v0.2.1"
        for workflow in release.RELEASE_GATE_WORKFLOWS
    ]


HEAD = "a" * 40
OLD = "b" * 40


def _run(sha: str, status: str = "completed", conclusion: str = "success") -> dict:
    return {
        "headSha": sha,
        "status": status,
        "conclusion": conclusion,
        "url": f"https://example.invalid/{sha[:4]}",
    }


@pytest.fixture
def released_root(fake_root: Path) -> Path:
    """``fake_root`` after the release PR for 0.2.1 merged."""
    for path in (release.ROOT_PYPROJECT, release.CLI_PYPROJECT):
        path.write_text(release.set_project_version(path.read_text(), "0.2.1"))
    release.CHANGELOG.write_text(
        release.changelog_for_release(CHANGELOG, "0.2.1", "v0.2.0", "2026-11-20")
    )
    return fake_root


def _fake_gh(
    monkeypatch: pytest.MonkeyPatch, runs: dict[str, list[dict]]
) -> list[list[str]]:
    """Answer ``gh pr view`` with HEAD and ``gh run list`` from ``runs``; record the rest.

    The commands that would change something are recorded and not run.
    """
    changes: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            out = HEAD + "\n"
        elif cmd[:3] == ["gh", "run", "list"]:
            assert cmd[cmd.index("--branch") + 1] == "release/v0.2.1"
            out = json.dumps(runs.get(cmd[cmd.index("--workflow") + 1], []))
        else:
            changes.append(cmd)
            out = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(release, "_require_clean_main", lambda: None)
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    return changes


def _all_green() -> dict[str, list[dict]]:
    return {w: [_run(HEAD)] for w in release.RELEASE_GATE_WORKFLOWS}


def test_tag_passes_when_every_gate_is_green_on_the_head(
    released_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = _all_green()
    # A newer red run on an old commit does not matter.
    runs[release.RELEASE_GATE_WORKFLOWS[0]].insert(0, _run(OLD, conclusion="failure"))
    changes = _fake_gh(monkeypatch, runs)
    assert release.main(["tag", "0.2.1"]) == 0
    assert [cmd[:4] for cmd in changes] == [["gh", "release", "create", "v0.2.1"]]


@pytest.mark.parametrize(
    ("gate_runs", "reason"),
    [
        ([], "no run on aaaaaaaaaaaa"),
        ([_run(OLD)], "no run on aaaaaaaaaaaa"),
        ([_run(HEAD, conclusion="failure")], "failure"),
        ([_run(HEAD, status="in_progress", conclusion="")], "still running"),
    ],
    ids=["missing", "older-commit", "red", "running"],
)
def test_tag_refuses_a_gate_without_a_green_run_on_the_head(
    released_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gate_runs: list[dict],
    reason: str,
) -> None:
    first, *rest = release.RELEASE_GATE_WORKFLOWS
    runs = _all_green()
    runs[first] = gate_runs
    runs[rest[-1]] = []
    changes = _fake_gh(monkeypatch, runs)
    assert release.main(["tag", "0.2.1"]) == 1
    assert changes == []
    err = capsys.readouterr().err
    assert f"{first}: {reason}" in err
    assert f"{rest[-1]}: no run on" in err
    for workflow in (first, rest[-1]):
        assert f"gh workflow run {workflow} --ref release/v0.2.1" in err
    for workflow in rest[:-1]:
        assert f"{workflow}:" not in err


def test_dry_run_tag_prints_the_gate_checks(
    released_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release.main(["--dry-run", "tag", "0.2.1"]) == 0
    out = capsys.readouterr().out
    assert "+ gh pr view release/v0.2.1 --json headRefOid" in out
    for workflow in release.RELEASE_GATE_WORKFLOWS:
        assert f"+ gh run list --workflow {workflow} --branch release/v0.2.1" in out
    assert out.index("+ gh run list") < out.index("+ gh release create")


WORKFLOWS = ROOT / ".github" / "workflows"
# Scheduled workflows that are not release gates, and why.
NOT_RELEASE_GATES = {
    "benchmark-wall.yml": "the release checklist §2 runs the benchmark by hand",
    "conflict-notice.yml": "comments on conflicted pull requests; it tests nothing",
}


def _triggers(path: Path) -> dict:
    # pyyaml comes with the dev group, which the core-only leg does not install.
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the bare key `on` as True.
    triggers = data.get("on", data.get(True))
    return triggers if isinstance(triggers, dict) else dict.fromkeys(triggers)


def test_every_scheduled_workflow_is_classified() -> None:
    """A new scheduled workflow fails here until it is a gate or has a reason not to be."""
    scheduled = {
        path.name for path in WORKFLOWS.glob("*.yml") if "schedule" in _triggers(path)
    }
    gates = set(release.RELEASE_GATE_WORKFLOWS)
    assert not gates & set(NOT_RELEASE_GATES)
    assert scheduled - gates - set(NOT_RELEASE_GATES) == set()
    assert set(NOT_RELEASE_GATES) <= scheduled


def test_every_gate_can_be_dispatched() -> None:
    for workflow in release.RELEASE_GATE_WORKFLOWS:
        assert "workflow_dispatch" in _triggers(WORKFLOWS / workflow), workflow

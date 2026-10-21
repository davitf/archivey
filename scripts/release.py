#!/usr/bin/env python3
"""Do the mechanical parts of a release, so a patch release takes minutes.

The steps a person still decides are the CHANGELOG text and when to merge. Everything
else is here. A release is four commands, run from a clean, up-to-date ``main``:

1. ``notes`` prints the merged pull requests since the last tag, as raw material for
   the CHANGELOG entry. Write the entry under ``## [Unreleased]`` in a normal PR, or as
   the first commit of step 2.
2. ``start X.Y.Z`` sets the version in both pyproject files (and the ``archivey-cli``
   pin), refreshes ``uv.lock``, moves the ``[Unreleased]`` text under a dated
   ``[X.Y.Z]`` heading with its compare links, commits on ``release/vX.Y.Z``, pushes,
   opens the release pull request and starts the TestPyPI dry run on that branch.
3. After the release pull request merges: ``tag X.Y.Z`` creates the GitHub release
   ``vX.Y.Z`` on ``main`` with the CHANGELOG section as its body. The tag starts
   ``publish.yml``, which uploads to PyPI. Run it with your own ``gh`` login: a tag
   pushed from an agent session is refused.
4. ``finish`` sets ``main`` to the next ``.dev0`` version in a small pull request, so
   installs from ``main`` stay distinguishable from the release.

``--dry-run`` on any step prints the commands and changes nothing. The text edits are
pure functions, tested in ``tests/test_release_script.py``.

Stdlib only; GitHub is reached through the ``gh`` command.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_PYPROJECT = ROOT / "pyproject.toml"
CLI_PYPROJECT = ROOT / "packages" / "archivey-cli" / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://github.com/davitf/archivey"

#: The versions this script writes: a final release, a pre-release, or a ``.devN``.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?(\.dev\d+)?$")
_PROJECT_VERSION_RE = re.compile(r'^(version = ")([^"]+)(")$', re.MULTILINE)
_PIN_RE = re.compile(r'("archivey\[recommended\]==)([^"]+)(")')
_UNRELEASED_RE = re.compile(r"^## \[Unreleased\][^\n]*\n", re.MULTILINE)
_HEADING_RE = re.compile(r"^## \[", re.MULTILINE)
_TRAILER_RE = re.compile(r"^(<!--|\[[^\]]+\]: )", re.MULTILINE)
_LINK_RE = re.compile(r"^\[[^\]]+\]: ", re.MULTILINE)


class ReleaseError(Exception):
    """A precondition failed; the message says what to fix."""


def check_version(version: str) -> str:
    if not VERSION_RE.match(version):
        raise ReleaseError(
            f"{version!r} is not a version like 0.2.1, 0.3.0rc1 or 0.2.2.dev0"
        )
    return version


def project_version(pyproject_text: str) -> str:
    """The first ``version = "..."`` line, which is ``[project].version`` in both files."""
    match = _PROJECT_VERSION_RE.search(pyproject_text)
    if match is None:
        raise ReleaseError("no version line in pyproject.toml")
    return match.group(2)


def set_project_version(pyproject_text: str, version: str) -> str:
    """Replace ``[project].version``, and the ``archivey[recommended]==`` pin if present."""
    text, count = _PROJECT_VERSION_RE.subn(
        rf"\g<1>{version}\g<3>", pyproject_text, count=1
    )
    if count != 1:
        raise ReleaseError("no version line in pyproject.toml")
    return _PIN_RE.sub(rf"\g<1>{version}\g<3>", text)


def next_dev_version(version: str) -> str:
    """The ``.dev0`` that follows a release: 0.2.0 -> 0.2.1.dev0, 0.3.0rc1 -> 0.3.0rc2.dev0.

    After a pre-release, the next pre-release number keeps the order right: PEP 440 sorts
    ``0.3.0.dev0`` before ``0.3.0rc1``, but ``0.3.0rc2.dev0`` after it.
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$", check_version(version))
    if match is None:
        raise ReleaseError(f"{version!r} is a .dev version, not a release")
    major, minor, patch, pre, pre_number = match.groups()
    if pre:
        return f"{major}.{minor}.{patch}{pre}{int(pre_number) + 1}.dev0"
    return f"{major}.{minor}.{int(patch) + 1}.dev0"


def _link_section_start(text: str) -> int:
    """Where the trailing link definitions start.

    That is just after the last HTML comment, or with no comment the first link
    definition after the last ``## [`` heading, or the end of the file.
    """
    end = text.rfind("-->")
    if end >= 0:
        return end + 3
    last_heading = None
    for last_heading in _HEADING_RE.finditer(text):
        pass
    link = _LINK_RE.search(text, last_heading.end() if last_heading else 0)
    return link.start() if link else len(text)


def _section_end(text: str, start: int) -> int:
    """Where the section starting at ``start`` ends.

    That is the next ``## [`` heading, or for the last section the first trailing
    comment or link definition.
    """
    following = _HEADING_RE.search(text, start)
    if following:
        return following.start()
    trailer = _TRAILER_RE.search(text, start)
    return trailer.start() if trailer else len(text)


def changelog_for_release(
    text: str, version: str, previous_tag: str | None, date: str
) -> str:
    """Move the ``[Unreleased]`` text under ``## [version] - date`` and fix the links.

    If a ``## [version] - Unreleased`` heading already exists (the 0.2.0 entry was
    written ahead of time), it gets the date instead, and ``[Unreleased]`` must be
    empty. Otherwise ``[Unreleased]`` must hold the entry.
    """
    unreleased = _UNRELEASED_RE.search(text)
    if unreleased is None:
        raise ReleaseError("CHANGELOG.md has no '## [Unreleased]' heading")
    body_start = unreleased.end()
    body_end = _section_end(text, body_start)
    body = text[body_start:body_end].strip()

    prewritten = re.compile(
        rf"^## \[{re.escape(version)}\] - Unreleased$", re.MULTILINE
    )
    if prewritten.search(text):
        if body:
            raise ReleaseError(
                f"CHANGELOG.md has text under [Unreleased] and a prewritten [{version}] entry;"
                " move the text into one of them"
            )
        text = prewritten.sub(f"## [{version}] - {date}", text, count=1)
    else:
        if not body:
            raise ReleaseError(
                "CHANGELOG.md has nothing under [Unreleased]; write the entry first"
                " (`scripts/release.py notes` lists the merged pull requests)"
            )
        text = (
            text[:body_start]
            + f"\n## [{version}] - {date}\n\n{body}\n\n"
            + text[body_end:].lstrip("\n")
        )
    return _set_links(text, version, previous_tag)


def _set_links(text: str, version: str, previous_tag: str | None) -> str:
    tag = f"v{version}"
    unreleased_link = f"[Unreleased]: {REPO_URL}/compare/{tag}...HEAD"
    if previous_tag:
        version_link = f"[{version}]: {REPO_URL}/compare/{previous_tag}...{tag}"
    else:
        version_link = f"[{version}]: {REPO_URL}/releases/tag/{tag}"

    start = _link_section_start(text)
    head, links = text[:start], text[start:]
    lines = [line for line in links.strip("\n").splitlines() if line.strip()]
    lines = [
        line
        for line in lines
        if not line.startswith("[Unreleased]:") and not line.startswith(f"[{version}]:")
    ]
    lines = [unreleased_link, version_link, *lines]
    return head.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"


def changelog_section(text: str, version: str) -> str:
    """The body of ``## [version]``, for the GitHub release notes."""
    heading = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", text, re.MULTILINE)
    if heading is None:
        raise ReleaseError(f"CHANGELOG.md has no [{version}] entry")
    end = _section_end(text, heading.end())
    return text[heading.end() : end].strip() + "\n"


# ── Side effects ─────────────────────────────────────────────────────────────────────


class Runner:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run

    def run(self, *cmd: str, input_text: str | None = None) -> None:
        print("+ " + shlex.join(cmd), flush=True)
        if not self.dry_run:
            subprocess.run(cmd, cwd=ROOT, check=True, text=True, input=input_text)

    def write(self, path: Path, text: str) -> None:
        print(f"+ write {path.relative_to(ROOT)}", flush=True)
        if not self.dry_run:
            path.write_text(text, encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _previous_tag() -> str | None:
    try:
        return _git("describe", "--tags", "--abbrev=0", "--match", "v*")
    except subprocess.CalledProcessError:
        return None


def _require_clean_main() -> None:
    if _git("status", "--porcelain"):
        raise ReleaseError("the working tree has changes; commit or stash them first")
    if _git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise ReleaseError("check out main first")
    _git("fetch", "origin", "main")
    if _git("rev-parse", "HEAD") != _git("rev-parse", "origin/main"):
        raise ReleaseError("main is not at origin/main; run `git pull --ff-only` first")


def _versioned_pyprojects(version: str) -> dict[Path, str]:
    """Both pyproject.toml files with ``version`` set, computed before anything is written."""
    return {
        path: set_project_version(path.read_text(encoding="utf-8"), version)
        for path in (ROOT_PYPROJECT, CLI_PYPROJECT)
    }


def _write_versions(runner: Runner, pyprojects: dict[Path, str]) -> None:
    for path, text in pyprojects.items():
        runner.write(path, text)
    runner.run("uv", "lock")


def cmd_notes(_args: argparse.Namespace) -> None:
    previous = _previous_tag()
    since = f"{previous}..HEAD" if previous else "HEAD"
    print(f"Merged since {previous or 'the first commit'}:\n")
    print(_git("log", "--first-parent", "--format=- %s", since))


def cmd_start(args: argparse.Namespace) -> None:
    version = check_version(args.version)
    if ".dev" in version:
        raise ReleaseError("a release version has no .dev suffix")
    runner = Runner(args.dry_run)
    if not args.dry_run:
        _require_clean_main()
    # Everything that can refuse runs before the first change, so a refusal leaves
    # main as it was.
    pyprojects = _versioned_pyprojects(version)
    changelog = changelog_for_release(
        CHANGELOG.read_text(encoding="utf-8"),
        version,
        _previous_tag(),
        str(dt.date.today()),
    )
    branch = f"release/v{version}"
    runner.run("git", "switch", "-c", branch)
    _write_versions(runner, pyprojects)
    runner.write(CHANGELOG, changelog)
    runner.run("git", "add", "pyproject.toml", "packages/archivey-cli/pyproject.toml")
    runner.run("git", "add", "uv.lock", "CHANGELOG.md")
    runner.run("git", "commit", "-m", f"Release {version}")
    runner.run("git", "push", "-u", "origin", branch)
    body = (
        f"Sets the version to {version} in both packages and dates the CHANGELOG entry.\n\n"
        "Started by `scripts/release.py start`. The TestPyPI dry run runs on this branch"
        " (Actions, Publish). After merge: `scripts/release.py tag"
        f" {version}`, then `scripts/release.py finish`.\n"
    )
    runner.run(
        "gh", "pr", "create", "--base", "main", "--head", branch,
        "--title", f"Release {version}", "--body", body,
    )  # fmt: skip
    if not args.no_testpypi:
        runner.run(
            "gh",
            "workflow",
            "run",
            "publish.yml",
            "--ref",
            branch,
            "-f",
            "target=testpypi",
        )
    print(
        f"\nNext: wait for CI and the TestPyPI run, read the CHANGELOG entry, merge, then"
        f" run `scripts/release.py tag {version}`."
    )


def cmd_tag(args: argparse.Namespace) -> None:
    version = check_version(args.version)
    runner = Runner(args.dry_run)
    if not args.dry_run:
        _require_clean_main()
    current = project_version(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    if current != version:
        raise ReleaseError(
            f"main is at version {current}, not {version}; merge the release PR"
        )
    notes = changelog_section(CHANGELOG.read_text(encoding="utf-8"), version)
    pre = re.search(r"(a|b|rc)\d+$", version) is not None
    cmd = ["gh", "release", "create", f"v{version}", "--target", "main"]
    cmd += ["--title", f"archivey {version}", "--notes-file", "-"]
    if pre:
        cmd.append("--prerelease")
    runner.run(*cmd, input_text=notes)
    print(
        "\nThe tag starts publish.yml (Actions, Publish), which uploads to PyPI after the"
        " `pypi` environment approval. Then run `scripts/release.py finish`."
    )


def cmd_finish(args: argparse.Namespace) -> None:
    runner = Runner(args.dry_run)
    if not args.dry_run:
        _require_clean_main()
    current = project_version(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    dev = args.version or next_dev_version(current)
    check_version(dev)
    pyprojects = _versioned_pyprojects(dev)
    branch = f"release/v{dev}"
    runner.run("git", "switch", "-c", branch)
    _write_versions(runner, pyprojects)
    runner.run(
        "git",
        "add",
        "pyproject.toml",
        "packages/archivey-cli/pyproject.toml",
        "uv.lock",
    )
    runner.run("git", "commit", "-m", f"Start {dev} after the {current} release")
    runner.run("git", "push", "-u", "origin", branch)
    runner.run(
        "gh", "pr", "create", "--base", "main", "--head", branch,
        "--title", f"Start {dev}",
        "--body", f"Moves main past the {current} release, so installs from main show a"
        " .dev version.\n",
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print, change nothing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("notes", help="list merged pull requests since the last tag")
    start = sub.add_parser("start", help="open the release pull request")
    start.add_argument("version")
    start.add_argument(
        "--no-testpypi", action="store_true", help="skip the TestPyPI dry run"
    )
    tag = sub.add_parser(
        "tag", help="create the GitHub release and tag after the merge"
    )
    tag.add_argument("version")
    finish = sub.add_parser("finish", help="move main to the next .dev0 version")
    finish.add_argument("version", nargs="?", help="default: the next patch .dev0")
    args = parser.parse_args(argv)
    commands = {
        "notes": cmd_notes,
        "start": cmd_start,
        "tag": cmd_tag,
        "finish": cmd_finish,
    }
    try:
        commands[args.command](args)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"release: command failed: {shlex.join(exc.cmd)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

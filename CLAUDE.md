# Claude Code — Archivey

**Read [`AGENTS.md`](AGENTS.md) first.** It is the canonical agent guide: repo map,
session setup, the OpenSpec CLI, the `archivey-dev` reference repo, the 7z/RAR
native-first strategy, and the conventions. `CONTRIBUTING.md` holds the coding and
testing standards, including the "Before pushing…" three-config rule.

This file exists because Claude Code auto-loads it by name. It carries only what is
specific to Claude Code; everything else would drift if it were duplicated here.

## Claude Code specifics

- **Session setup runs automatically** via the `SessionStart` hook
  (`.claude/hooks/session-start.sh`, registered in `.claude/settings.json`). It calls
  the same `scripts/setup-dev-env.sh` every other environment uses, so they cannot
  drift, and it no-ops unless `CLAUDE_CODE_REMOTE=true` — a developer's own machine is
  left alone.
- **Read the hook's closing verification block.** `unrar` and `7z` missing makes ~109
  tests *skip quietly* while the suite still reports green. The script names anything
  missing on its last lines; if you did not see them, run it by hand.
- **The `openspec` CLI installs to `~/.local/bin`** here, because the global npm prefix
  is not user-writable. `AGENTS.md` §OpenSpec CLI has both recipes — use the
  `--prefix "$HOME/.local"` one on this image.
- **`archivey-dev` is not in the GitHub-tool scope** of a Claude Code session. Plain
  HTTPS `git clone` works; the GitHub API returns 403 for unauthenticated calls, which
  is rate limiting rather than a private repo. See `AGENTS.md` §Reference repository.

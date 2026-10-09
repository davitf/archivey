# Writing English: modes, limits and the linter

The standing rule is in [`AGENTS.md`](../AGENTS.md) §Writing English: `unslop` cuts AI
tells, `asd-ste100` cuts ambiguity, and both are advice rather than a gate. This page
holds the detail you need when you apply them.

## Advice, not a gate

**Maintainer decision (davitf, 2026-09-22): the scope is broad and the rules are advisory
only, and the tree gets rewritten as it is touched rather than in a sweep** — *"broad, and
advisory only. we'll rewrite as we go, let's see if it improves readability."* The point
is to find out whether this makes prose here easier to read, and a bulk rewrite would not
answer that.

What that means in practice:

- A semicolon or a long sentence is **not a defect**. A review that reports one as a
  finding is wrong, and so would a gate that failed on one be.
- Nothing in the tree is in violation, because there is nothing to violate. Improve the
  prose in a file you are already editing and leave the rest alone.
- `ste-lint.py` is a tool to point at your own draft. It is not a bar to clear, and
  `scripts/check.sh` does not call it.

## Which mode

The skill has two modes and tells you to pick one. Both are advice under the ruling above.
Pick by the text, and lean hardest where a second reading costs the most:

- **Strict** — short text someone meets once and out of context, where a second reading
  is expensive or impossible: exception and diagnostic messages, CLI `--help` text, code
  comments, and a short instruction written for another agent. A code comment belongs
  here for that reason and not because a machine parses it — the developer reading it has
  the code in front of them and not the change that produced it, which is the same
  argument `CONTRIBUTING.md` makes for writing comments at all.
- **STE-flavored** — everything else, which is most of it: chat, pull request
  descriptions and comments, commit messages, `docs/`, `dev-docs/`, `CONTRIBUTING.md`,
  and the pages under `.claude/skills/`. Structural rules as written. The lexical rules
  are a direction of travel everywhere, for the reason the skill gives: without ASD's
  dictionary they are a preference for plain words rather than a checkable standard.

## What it does not override

- **`CONTRIBUTING.md` wins on code comments.** Comments explain *why*, not *what*, and
  they match the density and style of the file around them. STE decides the shape of the
  sentence you write. It does not ask for more comments, shorter comments, or a comment
  where the rule says none.
- **Never trade a hedge for a shorter sentence.** "May have failed" is not "failed", and
  `PLAUSIBLE` is not `CONFIRMED`. The whole review vocabulary here is calibrated
  confidence, so a rewrite that firms one up has changed the finding.
- **It is style, not substance.** A rewrite that supplies a cause, a frequency or a
  mechanism the source did not state is no longer a rewrite.
- **Quoted text stays as it was quoted** — an error string under test, a commit message
  being cited, a maintainer's own words.

## The linter

`ste-lint.py` is stdlib-only and takes a file or stdin. `--baseline N` tolerates what is
already there, and `--disable` silences named rules.

**Read a clean run for what it is.** The linter splits on newlines, not on sentences, so
a sentence spanning a hard-wrapped line is measured as two short ones and the
sentence-length rule does not fire on it: the same 28-word sentence scores one hard
violation on a single line and zero when wrapped. Most prose here is hard-wrapped near 90
columns, so on those files that rule is close to unreachable and you judge sentence
length yourself. It does work, and is worth reading, wherever a line carries a whole
sentence — an exception message, CLI help text, an unwrapped chat draft, or a file like
the vendored `asd-ste100/SKILL.md`, whose paragraphs are one line each. What a clean run
always tells you is that the checks reading a whole line came back empty: semicolons,
phrasal verbs, nominalization, marketing adjectives. The script is vendored verbatim so
an upstream fix can be re-copied, which is why this caveat lives here and not in it.

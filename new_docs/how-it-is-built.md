# How it is built

Almost all of archivey's code, tests and documentation are written by AI coding agents, mostly
Claude Code and Cursor. The maintainer designs the library, makes the decisions, directs the work
and reviews it. If you're deciding whether to trust a library built this way, this page explains
who decides what, how a change gets made and tested, and what that does and doesn't promise.
Archivey is a work in progress, and so is the process described here.

## Who decides what

The maintainer designed the building blocks and the public API: one opener, one reader and one
member model for every format; reading as a stream, with a member's bytes decoded as you read them;
and the modes you choose at open, such as `streaming=True` for a pipe, `seekable_members=True` and
`concurrent_members=True`. [Philosophy](philosophy.md) explains the thinking behind them.

The maintainer also sets the scope, the architecture and the
[design rules](https://github.com/davitf/archivey/blob/main/dev-docs/design-rules.md) the agents
follow, and rules on the trade-offs they can't settle from the code. Two examples:

- **When formats disagree.** Archivey follows each format's official tool, such as `unrar` for RAR
  and GNU tar for TAR, unless being the same across formats matters more. Device files are one such
  case: every format lists them as "other".
- **What counts as damage.** Some problems refuse the archive, and others, which are only unusual,
  are reported as diagnostics.

Decisions are recorded in the repository as
[architecture decision records](https://github.com/davitf/archivey/tree/main/dev-docs/decisions),
[behaviour specs](https://github.com/davitf/archivey/tree/main/openspec/specs) and
[archived change proposals](https://github.com/davitf/archivey/tree/main/openspec/changes/archive).
The agents do the rest: they write the code and the tests, build the test archives, run
investigations and review each other's work. They also draft the docs you're reading, which the
maintainer reviews paragraph by paragraph.

## How a change gets made

AI makes mistakes, so every change is reviewed before it's merged.

1. An agent writes the change, with tests.
2. A separate AI session reviews it, starting from the diff and the repository alone, not from the
   session that wrote the code. Each finding gets a fix or a stated reason, and the fixes are
   reviewed too. The
   [review workflow](https://github.com/davitf/archivey/blob/main/dev-docs/review-loop.md) and the
   instructions the agents follow to
   [review](https://github.com/davitf/archivey/blob/main/.claude/skills/code-review-skill/SKILL.md)
   and to
   [address findings](https://github.com/davitf/archivey/blob/main/.claude/skills/address-review-findings/SKILL.md)
   are in the repository.
3. Anything the agents can't settle from the code or the design rules goes to the maintainer as a
   decision.
4. Sometimes the maintainer reviews the change directly.
5. The change is merged only when review and CI pass. The maintainer merges anything tricky, and an
   agent may merge a straightforward change once its review approves it and CI is green.

Even then, problems and cases nobody thought of creep in. So agents also review the whole codebase
rather than one change, each time with one goal, such as finding bugs, finding dead, redundant or
unclear code, or evaluating the public API. The first of them read every file and posted its
findings as comments on [PR 315](https://github.com/davitf/archivey/pull/315). Another, of
extraction and every backend, found about 40 bugs. Each got a test that reproduced it first. Most
were fixed in the same pull request ([PR 512](https://github.com/davitf/archivey/pull/512)), and the
rest stayed in the test suite as expected failures until later work fixed them.

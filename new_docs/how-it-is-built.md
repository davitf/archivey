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

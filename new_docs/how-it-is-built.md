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
unclear code, or evaluating the public API. One full review of the codebase ([PR
315](https://github.com/davitf/archivey/pull/315)) produced nearly 700 comments, and a bug hunt ([PR
512](https://github.com/davitf/archivey/pull/512)) found about 40 bugs, each reproduced with a test
and later fixed.

## How it is tested

Review catches mistakes in the reasoning. These checks catch the rest, and each one finds things the
others miss.

- **Thousands of tests on every pull request.** They run on Linux with Python 3.11 to 3.14, on macOS
  and Windows with 3.11 and 3.14, on a free-threaded build, with no optional packages installed, and
  with the oldest dependency versions archivey allows.
- **A test for every corner case.** Every corner case that has been fixed, or that someone worried
  about, has a test, so it can't quietly come back.
- **One set of test archives, built in every format.** Each test archive is described once and then
  built by the real tools (7-Zip, RAR, zip, tar and others) in every format that can hold it. Every
  copy must open, list, read back and extract safely. Hand-built hostile archives sit beside them:
  path traversal, links that point outside the destination, decompression bombs, and forged sizes
  and counts.
- **Other libraries as a reference.** Tests check what archivey reads against py7zr, rarfile and
  unrar. These libraries are used only in tests.
- **Fuzzing.** Every test archive is cut short, bit-flipped, zeroed and padded, and reading it must
  either succeed or raise an `ArchiveyError`. A coverage-guided fuzzer (Atheris) runs over every
  format's parser and every decoder, briefly on each pull request and for longer every night. It
  fixes up checksums so its changes reach the code behind them, and it checks that the optional fast
  decoders give the same result as the standard ones. Property-based tests cover name handling,
  extraction safety and link resolution.
- **Benchmarks.** Each pull request checks numbers that shouldn't change by accident, such as how
  many seeks a read takes and that a solid archive is decoded only once. A daily run measures time
  and fails if things get noticeably slower.
- **A threat model.** Known attacks and gaps are written down with their status, including the
  limits archivey accepts rather than fixes. [Security](security.md) covers what you need to know as
  a user, and the [full threat
  model](https://github.com/davitf/archivey/blob/main/dev-docs/threat-model.md) has the rest.
- **Bugs found in other libraries.** Comparing decoders turned up a use-after-free in pyppmd, three
  decoder bugs in pybcj and a hang in py7zr. Archivey avoids or works around each one.

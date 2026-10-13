# How it is built

Almost all of archivey's code, tests and documentation are written by AI coding agents,
mostly Claude Code and Cursor. The maintainer designs the library, makes the decisions,
directs the work and reviews it, but writes very little of the code by hand. This page
explains who decides what, how a change gets made and checked, and what that means for
you.

Archivey is a work in progress, and so is the process described here.

## Who does what

The maintainer decides. The agents write, test and review.

The maintainer designed the fundamental building blocks and the public API: one opener,
one reader and one member model for every format; streaming as the default way to read,
with a member's bytes decoded as you read them; and the modes you declare at open, such
as `streaming=True` for a pipe, `seekable_members=True` and `concurrent_members=True`.
[How it works](how-it-works.md) explains those choices.

The maintainer also sets the scope and the architecture, and rules on every
trade-off the agents cannot settle from the code: how strict a default is, how much
memory a decoder may use, what a damaged archive should do. Some examples of those
rulings:

- A bad optional record in a RAR header is dropped with a diagnostic.
  `DiagnosticPolicy.STRICT` refuses the archive instead, and a bad encryption record is
  always fatal.
- Decoder memory is capped at 2 GiB by default, and you can change the cap through
  `DecoderLimits`.
- The integrity guarantee covers a member read from start to end. A read that seeks is
  best effort, because a seek trusts the format's own index.

The maintainer reads code as well, especially to check the architecture and the tricky
parts. Decisions are recorded in the repository as
[architecture decision records](https://github.com/davitf/archivey/tree/main/dev-docs/decisions),
[behaviour specs](https://github.com/davitf/archivey/tree/main/openspec/specs) and
[archived change proposals](https://github.com/davitf/archivey/tree/main/openspec/changes/archive).

The agents do the rest. They write the code and the tests, build the test archives, run
investigations, review each other's work, and write the docs you are reading.

## How a change gets made

AI makes mistakes, so the review process is essential for catching them.

1. An agent writes the change, with tests.
2. A separate AI session reviews it, starting from the diff and the repository alone,
   not from the session that wrote the code. Each finding gets a fix or a stated reason,
   and the fixes are reviewed too. The
   [review workflow](https://github.com/davitf/archivey/blob/main/dev-docs/review-loop.md) and the instructions the agents follow
   to [review](https://github.com/davitf/archivey/blob/main/.claude/skills/code-review-skill/SKILL.md) and to
   [address findings](https://github.com/davitf/archivey/blob/main/.claude/skills/address-review-findings/SKILL.md) are in the
   repository.
3. Anything the agents cannot settle from the code goes to the maintainer as a decision.
4. Sometimes the maintainer reviews the change directly.
5. The change is merged only when review and CI pass and we are happy with it. The
   maintainer merges anything tricky. An agent may merge a straightforward change once
   its review approves it and CI is green.

Even then, problems and cases nobody thought of creep in. So we also run full-code
reviews: reviews over the whole codebase rather than one change, each with its own
goal, such as finding bugs, finding redundant, dead or unclear code, or evaluating the
public API. A recent bug hunt over extraction and every backend found about 40 bugs;
each got a reproducer test first. Most were fixed in the same pull request, and the
rest stay in the suite as expected failures until later work fixes them
([PR 512](https://github.com/davitf/archivey/pull/512)).

## How the code is checked

Besides review, these checks run on the code. Each one catches things the others miss.

- **Tests.** Thousands of tests run on every pull request: Python 3.11 to 3.14 on Linux,
  the oldest and newest of those on macOS and Windows, and a free-threaded build on the
  core and the extras that support it
  ([Platforms and threading](support-matrix.md) has the full table). They include a corpus of archives written by the real
  tools (7-Zip, RAR, zip, tar and others) and a set of hand-built hostile archives:
  path traversal, link escapes, decompression bombs, forged sizes and counts.
- **Fuzzing.** Every corpus archive is mutated (truncated, bit-flipped, padded with
  garbage) and must either succeed or fail with a typed error, never crash or hang.
  Property-based tests cover the path-safety logic. A coverage-guided fuzzer (Atheris)
  runs over the 7z, RAR, ZIP, TAR and ISO parsers and every codec on each pull request,
  and over the optional gzip and bzip2 accelerators, checking they decode exactly what
  the standard decoders do.
- **A threat model.** The known security gaps are written down with their status, from
  metadata bombs to a directory swapped for a symlink while it is being listed. The
  limits the maintainer accepts rather than fixes are recorded there too, in the
  [threat model](https://github.com/davitf/archivey/blob/main/dev-docs/threat-model.md), not hidden.
- **Upstream bugs written up.** Checking archivey's decoders against other libraries has
  turned up defects in them: a use-after-free in pyppmd, three decoder bugs in pybcj, and
  a hang in py7zr. Archivey works around or avoids each one.

## Why this raises quality

AI lets the maintainer build a better library, faster. It changes what is practical to
do.

- **Breadth.** Archivey reads ZIP, TAR, 7z, RAR, ISO and a dozen single-file codecs,
  including encrypted archives, multi-volume sets, self-extracting executables and
  archives nested inside archives. Each of those has corner cases that projects usually
  leave as unsupported. Here most of them have a test.
- **Hostile input.** Agents are good at the tedious question "what if this field is
  wrong?", asked of every field in every format. Many entries in the threat model are
  attacks the maintainer would not have thought of alone: a 7z archive that sizes an
  allocation from an untrusted count, or a password check where the archive chooses how
  much work each attempt costs.
- **Review is routine.** A from-zero review of every change and repeated reads of the
  whole codebase would take a team of people. With agents they are part of the normal
  workflow.
- **Measurement over memory.** Claims about speed, memory use and seeks are measured,
  and a benchmark gate in CI keeps the numbers, so a change that makes things worse
  shows up.

## What this does not promise

- The maintainer does not read every line. The line-by-line reading is done by agents,
  and the maintainer reads designs, decisions, the tricky parts and review findings.
- AI reviews find a lot, but their blind spots overlap: two sessions of similar models
  can miss the same thing.
- The fuzzing does not yet run at OSS-Fuzz scale. That is planned after the first
  release.
- Archivey has one maintainer, so we cannot promise that it has no security bugs. If
  you process untrusted archives, take the precautions you would with any other library,
  such as running it with limited privileges.

## Review it yourself

You don't have to take this page's word for it. The history is public: every
[pull request](https://github.com/davitf/archivey/pulls?q=is%3Apr), its review threads and the decisions behind it are
on GitHub, next to the decision records, specs and threat model linked above.

We welcome your own reviews, whether you read the code yourself or point an agent at it.
Bug reports, questions and pull requests are welcome; see
[CONTRIBUTING.md](https://github.com/davitf/archivey/blob/main/CONTRIBUTING.md). If you
find a security problem, report it privately as
[SECURITY.md](https://github.com/davitf/archivey/blob/main/SECURITY.md) describes,
rather than in a public issue.

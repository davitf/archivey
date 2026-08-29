## Why

A single-file compressed archive that is not what its name claims fails to fail. Two
independent defects, both reproduced on `main` (`e54eff7`):

**1. The eager open-time check never checks anything (P15).**
`single_file_reader.py:183-190` opens and closes a codec stream to make format errors
surface at open time — but every stdlib codec validates its header on *first read*, not at
construction, so the probe validates nothing. A 40 000-byte zero-filled file named
`backup.gz` opens cleanly, lists one fabricated member, and raises only on read. Measured
across all ten single-file codecs, ten of ten open successfully; a zero-byte file of each
does too.

**2. A corrupt bzip2 member reads as empty, silently.** Not previously recorded. With
`[seekable]` installed and `seekable_members=True`, the read goes through rapidgzip's
bundled bzip2 decoder, which yields zero bytes instead of raising:

| `backup.bz2` contents | `seekable_members=False` | `seekable_members=True` |
| --- | --- | --- |
| valid bzip2 | 11 bytes | 11 bytes |
| 40 000 zero bytes | `CorruptionError` | **0 bytes, no error** |
| empty file | `TruncatedError` | **0 bytes, no error** |

gzip's rapidgzip path raises correctly on the same inputs, so this is specific to the
bzip2 decoder. A capability flag turning a corrupt archive into an empty successful read is
data loss on exactly the workload `VISION.md` names as founding — verifying and indexing a
backup corpus, where "this archive is fine and contains nothing" is the worst possible
answer.

The two interact, which is why they ship together: defect 2 defeats the obvious fix for
defect 1. A probe that reads one byte gets `b''` back on the accelerated bzip2 path and
concludes the stream is a valid empty one.

Beyond the immediate surprise, defect 1 is the mechanism behind an honesty gap the
detection work depends on: because listing *succeeds*, the `EXTENSION_FORMAT_UNCONFIRMED`
diagnostic (which keys on an empty listing) cannot fire, and the failure lands on the read
path instead.

## What Changes

- **The eager probe reads.** The open-time validation SHALL pull at least one byte, so a
  source that is not decodable as the claimed codec raises at `open_archive` rather than on
  a later read. Verified: `read(1)` over wrong bytes raises a properly translated
  `ArchiveyError` for every codec whose decoder rejects the input.
- **Codecs whose decoder accepts an empty input get a structural floor.** `read(1)`
  returning `b""` is not proof of a valid empty stream. `unix-compress` reads a zero-byte
  source as an empty stream, so a source shorter than the codec's minimum header SHALL be
  rejected on its length rather than on a decode that cannot fail.
- **The accelerated bzip2 path SHALL NOT convert a decode failure into an empty stream.**
  A decoder that ends a stream without consuming its input, having produced no output and
  reached no valid end-of-stream, is a failure and SHALL raise the same translated error
  the non-accelerated path raises. **BREAKING** only in the sense that a call which
  currently returns `b""` now raises — which is the defect.
- **A red-green test pins the guarantee.** Nothing in `tests/test_single_file.py` asserts
  that a malformed single-file stream raises at `open_archive` time, which is how an eager
  check that never checked went unnoticed.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-single-file-compressors` — open-time validation becomes a stated obligation with
  a defined depth, rather than a comment describing an effect the code does not produce.
- `compressed-streams` — an accelerator SHALL NOT weaken the error contract of the codec it
  accelerates: same inputs, same raised errors, whatever the accelerator mode.

## Decisions

- **One byte, not a full validation pass.** The check answers "is this decodable as the
  claimed codec at all", which is what the comment always promised. Deeper validation is
  the reader's job on the read path.
- **The `.Z` case gets a length floor, not a special-cased read.** Its decoder treats an
  empty input as an empty stream rather than a truncated one, so no amount of reading
  distinguishes them. A minimum-header check is the honest test and generalises to any
  future codec with the same property.
- **The accelerator fix is a contract statement, not a bzip2 patch.** The rule is that an
  accelerator preserves the error behaviour of the path it replaces; bzip2 is today's
  violation. Stating it that way is what stops the next accelerator reintroducing it.
- **The bzip2 open-time cost is accepted and stated.** Measured on a ~1.8 MB payload,
  20 iterations: gzip construct-and-close 0.04 ms versus construct-and-read 0.05 ms;
  bzip2 0.06 ms versus **14.13 ms**, because bzip2 must decode a whole block (up to
  900 KB) to yield one byte. That is a real charge against the honest-cost contract and is
  a deliberate decision rather than a silent regression.

## Impact

- Modules: `src/archivey/internal/backends/single_file_reader.py` (the eager probe and its
  comment), `src/archivey/internal/streams/codecs.py` (the `unix-compress` minimum-header
  floor and the accelerated bzip2 end-of-stream check).
- Public API: unchanged in shape. Behaviour changes: malformed single-file sources raise at
  `open_archive` instead of on read; a corrupt bzip2 member under `seekable_members=True`
  raises instead of returning `b""`. Both are error-path corrections.
- Tests: malformed and zero-byte sources for all ten single-file codecs, asserting the
  raise happens at open; the bzip2 accelerator matrix above under both accelerator modes,
  which needs the `[seekable]` extra and skips cleanly without it; a valid empty stream of
  each codec still opening and reading as empty, so the floor does not over-reject.
- Docs: `docs/gotchas.md` and `docs/errors-and-diagnostics.md` describe read-time failure
  for wrongly-named single-file archives; the failure moves to open.
- Closes `dev-docs/open-issues.md` P15 and adds the bzip2 accelerator defect, which has no
  P-entry yet.

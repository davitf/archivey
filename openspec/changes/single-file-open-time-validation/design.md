## Context

`SingleFileReader.__init__` (`single_file_reader.py:183-190`) carries this comment:

> Eagerly open+close a codec stream so format/seekability errors surface at archive-open
> time rather than on a later read.

The probe opens and closes without reading. Every stdlib codec validates its header on
first read, so the probe validates nothing. `dev-docs/open-issues.md` P15 records the
defect and proposes reading one byte; this change implements that and adds a second defect
found while measuring the fix.

## Goals / Non-Goals

**Goals:**

- Make the open-time guarantee real, at a stated depth, for seekable sources.
- Close the accelerated-bzip2 silent-empty read, which defeats the first goal on that path.
- Pin both with tests, since neither had one.

**Non-Goals:**

- Deep validation at open. One decodable byte is the guarantee; a corrupt tail is still a
  read-time failure and should be.
- Non-seekable sources. The `else` branch hands the opened stream to the first
  `open_member`, so a probe read there consumes a caller byte. Left deferred, and the
  comment stops claiming otherwise.
- The honesty question of *which* error a wrongly-named archive should raise, and whether
  it carries `format_unconfirmed`. Moving the failure to open time does not by itself make
  it honest; that is `detection-evidence-ledger`.

## Investigations

Measured on `main` at `e54eff7`, with `rapidgzip` installed.

**P15 reproduces across all ten codecs.** A 40 000-byte zero-filled file and a zero-byte
file, named for each single-file codec, both `open_archive` cleanly, list one fabricated
member, and fail only on read:

| named | detection | open | read |
| --- | --- | --- | --- |
| `.gz` `.bz2` `.xz` `.zst` `.br` `.Z` `.lz4` `.lz` `.zz` (zeros) | extension / `GUESS` | succeeds | `CorruptionError`, `format_unconfirmed=False` |
| `.lzma` (zeros) | extension / `GUESS` | succeeds | `TruncatedError`, `format_unconfirmed=False` |
| all ten (zero-byte) | extension / `GUESS` | succeeds | `TruncatedError` / `CorruptionError`, except `.Z` |
| `.Z` (zero-byte) | extension / `GUESS` | succeeds | **read succeeds, returns `b""`** |

**`read(1)` fixes nine of ten.** Constructing each codec stream and pulling one byte
raises a properly translated `ArchiveyError` — no raw backend exception escapes:

| codec | zero-byte source | 40 000 zero bytes |
| --- | --- | --- |
| GZIP, ZSTD, LZ4, ZLIB, BROTLI, LZMA_ALONE | `TruncatedError` | `CorruptionError` (LZMA Alone: `TruncatedError`) |
| XZ, LZIP | `CorruptionError` | `CorruptionError` |
| UNIX_COMPRESS | **returns `b""`** | `CorruptionError` |

`unix-compress` is the exception P15 predicted: its decoder reads an empty input as an
empty stream, so no read distinguishes "valid and empty" from "not a `.Z` at all". Hence
the length floor rather than a deeper read.

**The second defect, not previously recorded.** Under `AcceleratorMode.AUTO` with a
seekable source, bzip2 opens through rapidgzip's bundled bzip2 decoder, which returns zero
bytes for input the stdlib decoder rejects. Isolated by holding everything else fixed:

| source | accel `OFF` | accel `AUTO` |
| --- | --- | --- |
| valid bzip2 | 11 bytes | 11 bytes |
| bzip2, 40 000 zero bytes | `CorruptionError` | **0 bytes, no error** |
| bzip2, zero-byte | `TruncatedError` | **0 bytes, no error** |
| gzip, 40 000 zero bytes | `CorruptionError` | `CorruptionError` |
| gzip, zero-byte | `TruncatedError` | `TruncatedError` |

End-to-end through the public API, with `[seekable]` installed:
`open_archive("garbage.bz2", seekable_members=True).read(member)` returns `b""`, while the
same file with `seekable_members=False` raises `CorruptionError`. `indexed_bzip2` is not
installed here, so the path is rapidgzip's bundled decoder specifically, and gzip's
rapidgzip path is unaffected — this is not a general accelerator-wrapper problem.

**Cost of the read, measured** on a ~1.8 MB payload over 20 iterations, codec construction
only (not a full `open_archive`):

| codec | construct + close | construct + `read(1)` |
| --- | --- | --- |
| gzip | 0.04 ms | 0.05 ms |
| bzip2 | 0.06 ms | **14.13 ms** |

bzip2 must decode a whole block — up to 900 KB at level 9 — to yield one byte. P15's own
estimate (3.47 ms → 6.67 ms) was taken over a full `open_archive` call and understates the
codec-level delta; both are the same phenomenon.

## Decisions

### 1. The probe reads one byte, and one byte is the whole guarantee

The comment always promised "format errors surface at open", not "the archive is sound".
One decoded byte establishes that the header parses and the first block decodes, which is
what a wrong-format source fails. Anything deeper turns `open_archive` into a verification
pass and breaks the honest-cost contract far worse than 14 ms.

**Rejected: validating the header without decoding.** It would be cheaper on bzip2, but it
needs a per-codec header parser for ten codecs — which is `detection-evidence-ledger`'s
validator table, arriving later and for a different purpose. Reusing the decoder is the
smaller correct change now.

### 2. `unix-compress` gets a minimum-header length floor

Its decoder cannot distinguish an empty input from an empty stream, so the test has to be
positional. A `.Z` source shorter than its minimum header is rejected on length. Stated as
a general rule rather than a `.Z` special case, because the property — "this decoder
accepts an empty input" — is what selects the floor, and a future codec may share it.

**Rejected: reading two bytes, or reading until non-empty.** Neither distinguishes the
cases: a valid empty `.Z` and a zero-byte non-`.Z` both yield `b""` forever.

### 3. The accelerator defect is fixed as a contract, not as a bzip2 patch

The rule stated in the spec delta is that an accelerator raises what the path it replaces
raises. bzip2 is today's violation; writing the requirement that way is what stops the next
accelerator reintroducing it, and `compressed-streams` already scopes rapidgzip *out* of
its "content faults raise from read" requirement, so there is currently no rule this
violates.

Implementation shape: the accelerated stream detects the end-of-stream-with-no-output,
no-input-consumed condition and raises the translated error the non-accelerated path would.
Where the accelerator cannot report enough to distinguish that from a genuine empty stream,
the fallback is to decline acceleration for sources below the codec's minimum framing size
— the same floor as decision 2, applied for a different reason.

**Rejected: forcing `AcceleratorMode.OFF` for bzip2.** It would fix the symptom by removing
the feature the `[seekable]` extra exists to provide, and would leave the contract unstated
for the next accelerator.

### 4. Both defects ship in one change

Defect 2 defeats the fix for defect 1: with `seekable_members=True` and the accelerator on,
the new probe read returns `b""` on a corrupt bzip2 and the eager check passes. Landing
them separately would ship a fix whose own test cannot pass on the configuration that
matters most.

## Risks / Trade-offs

- [bzip2 `open_archive` gains ~14 ms] → Accepted and stated in the proposal rather than
  discovered from a benchmark regression. It is proportional to the first block, not the
  archive, so it does not scale with archive size. If it proves unacceptable, the escape is
  decision 1's rejected alternative — a bzip2 header parser — not removing the guarantee.
- [Sources that used to open and fail later now fail at open] → That is the change. It is
  visible to any caller who opens speculatively and catches on read; release notes need it,
  and `docs/gotchas.md` currently documents the old behaviour.
- [The accelerator test needs the `[seekable]` extra] → It must skip cleanly without it,
  the same trap `AGENTS.md` records for `unrar` and `7z`: a container missing the extra
  would report green while never exercising the defect. The skip reason names the extra.
- [The open-time error carries `member='<name>'` for a failure that happened before any
  member was requested] → P15 notes this reads oddly. Worth correcting in the same change
  if the error construction allows it cheaply; not worth blocking on.

## Open Questions

- Whether the accelerated bzip2 stream can distinguish "no output, no input consumed" from
  a genuine empty stream through rapidgzip's API, or whether the minimum-framing floor is
  the only reliable discriminator. Settled during implementation by inspecting what the
  accelerator reports; both paths satisfy the requirement.

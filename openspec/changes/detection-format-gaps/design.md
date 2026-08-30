## Context

`_detect_format_body` (`src/archivey/internal/detection.py`) runs five steps: near magic →
SFX scan (on an executable cue) → content probes → far magic → extension. The shipped
`format-detection` requirement text describes a different and older order still (extension
before probes, no far magic at all); `prefixed-archive-detection`'s delta already flags that
text as stale.

Three magic-less or structurally-prefixed inputs fall out of that pipeline. The independent
design analysis (`dev-docs/investigations/archive-format-detection-algorithm.md` §5) names
all three, with the compatibility argument for each and a local decode check confirming that
the format's own tools accept the input archivey rejects.

This change takes only the part of §5 that fixes wrong answers. The rest of §5 — validators
whose job is to *grade* a match rather than fix one (gzip `FHCRC`, TAR checksum, XZ flags
CRC, LZ4 header checksum, 7z `StartHeaderCRC`, RAR main header, ISO descriptor tuple) —
needs the evidence classes to report into and belongs to `detection-evidence-ledger`.

## Goals / Non-Goals

**Goals:**

- Detect the three input classes reproduced below, with no change to any input that is
  detected correctly today.
- Move far magic ahead of the content probes, because one of the three fixes is unsafe
  without it and because the current order is itself a live defect.

**Non-Goals:**

- Evidence classes, confidence grading, the selection rule, `format_unconfirmed`
  provenance — `detection-evidence-ledger`.
- The ZIP tail tier, the widened prefix cue, the exhaustive scan — `prefixed-archive-detection`.
- Deciding how concatenated or skippable zstd frames compose into one *detected stream* for
  member purposes. This change reads past skippable frames to find the regular frame; what a
  reader does with several regular frames is unchanged.
- Any gzip `XFL` / `OS` identity gate. §5 measures those on one container's toolchain and
  explicitly declines to gate identity on them.

## Investigations

All measured on `main` at `e54eff7` in this container.

**The three false negatives, and that the decoders accept them:**

| input | `detect_format` today | decoder |
| --- | --- | --- |
| `skippable(0x184D2A50, 4 bytes) + zstd.compress(...)` | `FormatDetectionError` | `zstd.decompress` returns the payload |
| `zlib.compressobj(6, DEFLATED, 9)` output, header `18 95` | `FormatDetectionError` | `zlib.decompress` returns the payload |
| an Alone stream with bytes 1–4 zeroed | `FormatDetectionError` | `lzma.decompress(..., FORMAT_ALONE)` returns the payload |

**zlib's gate, sized.** Of the 65 536 `(CMF, FLG)` pairs, **66** satisfy `CM == 8`,
`CINFO <= 7` and `(CMF*256+FLG) % 31 == 0`; 34 of those set `FDICT`. The shipped allow-list
holds **4**. Generating a stream for each legal window size shows the allow-list matching
only `wbits=15`:

| window bits | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| header | `18 95` | `28 91` | `38 8d` | `48 89` | `58 85` | `68 81` | `78 9c` |
| matched today | no | no | no | no | no | no | yes |

**The Alone guard is load-bearing in the current order.** Lifting `dict_size == 0` and
re-running detection on a 40 000-byte source whose only structure is `01 CD001 01` at
offset 32 768 returns `LZMA_ALONE` / `PROBABLE` / `content_probe`. The same source with the
guard in place, or with far magic consulted first, returns ISO. Two further observations
from the same run:

- ~~A 40 000-byte all-zero source is **not** claimed even with the guard lifted, because the
  completeness gate (`probe-completeness-gate`) sees the whole source and rejects a decode
  that does not finish.~~ **Wrong, and corrected at implementation — see §5 below.** The
  completeness gate only fires when the source fits *inside* the peeked prefix; 40 000
  bytes does not, so the gate never runs and the source is claimed as `LZMA_ALONE`. The
  ISO case is a bounded probe for the same reason, which is the half of this sentence that
  was right.
- The widened zlib grammar does **not** accept either source: an all-zero header fails
  `CM == 8`. So the zlib half of this change carries no ISO coupling and the two fixes are
  independent in that direction.

**The skippable-frame walk.** A skippable frame is a 4-byte magic in
`0x184D2A50 .. 0x184D2A5F` followed by a little-endian `uint32` frame size and that many
bytes, so the next frame's offset is exact arithmetic with no decoding:

| source | walk result |
| --- | --- |
| regular frame only | offset 0 |
| one skippable frame, then regular | offset 12 |
| three skippable frames (payloads 4, 17, 4), then regular | offset 49 |
| skippable frame only | no regular frame — declined |
| 64 zero bytes | not a frame magic — declined |

`zstd.decompress` returns the payload for the three-skippable case, so the walk agrees with
the decoder.

## Decisions

### 1. Far magic moves ahead of the content probes, in this change

The reorder is a prerequisite for the Alone fix (Investigations above), and it independently
closes a silent wrong answer: a bootable or hybrid ISO reserves its first 32 KiB for
bootloader code, which is exactly the data class the Brotli probe accepts, so such an image
detects as `BROTLI` and opens as one fabricated `*.uncompressed` member.

The size gate keeps it cheap. `source_byte_size()` is already computed at the probe step for
the framing gate, so hoisting it costs nothing, and no ISO is smaller than the extended
window — a small source never pays the 32 775-byte peek.

**Rejected: leaving the reorder in `prefixed-archive-detection`.** #257 claims the same move
in its Impact, but it is now sequenced behind `detection-evidence-ledger` and the Alone fix
would have to wait for both. Two in-flight changes MODIFYing the same requirement is the
archive-order conflict the investigation §14 warns about, so this change takes the reorder
and #257's revision drops that paragraph — recorded in §Sequencing rather than discovered at
archive time.

**Rejected: keeping the `dict_size != 0` guard and calling the reorder optional.** The guard
is a false negative on legal input, and the comment above it already documents that its real
job is ordering. Fixing the ordering and keeping the workaround would leave the workaround
with no stated reason to exist.

### 2. zlib gates on the RFC 1950 grammar, not on a widened allow-list

Enumerating the 66 legal pairs would work and would be a smaller diff, but it encodes a
derivation as data: the next reader cannot tell whether a missing pair is a deliberate
exclusion or an oversight, which is exactly how the current four-entry list came to exclude
six of seven window sizes. The three-line grammar check says what the format says.

`FDICT` is accepted rather than rejected: a stream with a preset dictionary is valid zlib,
and the probe's decode — not the header — is what determines whether archivey can read it.
A dictionary the decoder does not hold fails the decode and the candidate falls through.

**Correction found at implementation.** The proposal's "detects as `ZLIB` when the
dictionary is available" is not reachable: nothing in archivey supplies a preset
dictionary to the codec layer, so *every* `FDICT` stream fails the decode today. Accepting
the bit is still the right gate — it says what RFC 1950 says, and it is the header check
that would otherwise have to be revisited the day a dictionary can be supplied — but the
observable behaviour is one row, not two, and the spec scenario and task 1.4 were
corrected to say so rather than pinning a case no test can reach.

### 3. A zstd skippable-frame prefix is walked, and a regular frame is still required

Registering the 16 skippable magics as ordinary magic-table entries would be less code and
is wrong: a file of nothing but skippable frames would then report `ZST` and open as an
empty fabricated member. The walk keeps identification tied to a frame that carries data.

The walk is bounded by the peeked prefix: a skippable frame whose declared size runs past
the prefix ends the walk with no claim, rather than triggering a larger read. A 4 GiB
skippable frame is legal and is not worth a peek extension to see past.

**Rejected: deciding multi-frame composition here.** §13 lists "how concatenated and
skippable frames compose into one detected stream" as an open experiment. Detection only
needs to find the first regular frame; the reader's behaviour across several regular frames
is unchanged and untouched.

### 4. The zlib widening is taken knowing it costs false-positive surface

From about 2 to the minus 14 to about 2 to the minus 10 at the header, before the decode.
The compensating rule the investigation pairs with it — a decode that produced only stored
(`BTYPE=00`) output is graded on its header alone — needs evidence classes and lands with
`detection-evidence-ledger`. Taken now anyway: a real stream archivey cannot open is a hard
failure, a probe false positive is already graded `PROBABLE`, already stamps
`format_unconfirmed` on a decode failure since #267, and is about to be regraded to `GUESS`.

### 5. The Alone guard is replaced, not simply removed — a zero *size* is refused

Found at implementation, against `tests/test_review_simplicity_consistency.py::test_content_detection_refuses_a_zero_filled_file`, which exists as a guardrail against exactly this. Lifting `dict_size == 0` on its own makes **any all-zero source larger than the peeked prefix** — 32 KiB of zeros, sparse-file padding, a zero-truncated backup — detect as `LZMA_ALONE` / `PROBABLE`. The Investigations note above claimed otherwise on faulty reasoning; the reorder is not what fails, since plain zeros hold no `CD001` for far magic to find.

Measured, so the fix is aimed at the mechanism rather than the symptom:

| what | finding |
| --- | --- |
| `00 × 13` as a header | props `0x00` = a legal `(lc 0, lp 0, pb 0)`; dictionary 0; declared size **0** |
| `00 × 18` fed to liblzma | `eof=True`, **0 bytes out** — a *valid, complete, empty* stream (13-byte header + 5-byte range-coder init, whose first byte must be zero), not a corrupt one |
| what the bounded probe sees | the empty stream ends, the reader continues into the trailing zeros as concatenated members, runs off the end → `TruncatedError` → the bounded path scores truncation as a match, **before** `require_output` is consulted |
| dictionary 0 with a real payload | independent fields: props `0x5D`, dictionary 0, size unknown decodes 700 bytes — so the false negative this change fixes is untouched |

So the probe **SHALL refuse a header declaring an uncompressed size of exactly zero**. Any value but the all-ones sentinel is the stream's exact output length, so zero declares a stream with no payload — nothing archivey could open. That is not a new policy: it is the probe's existing `require_output` ("an empty successful read is not a claim") and this change's own "skippable frames alone are not zstd", stated at the header because the bounded decode cannot reach it.

**What real encoders write, surveyed.** Every liblzma-based producer writes the all-ones *unknown* sentinel, and it does so **unconditionally** — the size of the input and the way the tool is invoked make no difference. Checked across `xz --format=lzma` and the `lzma` CLI at `-0`…`-9` with and without `-e`, and Python's `lzma.compress` and `LZMACompressor`, over inputs of 0, 1, 2, 13, 100, 4 096, 100 000 and 5 000 000 bytes, **and pointed at a file on disk** (where the tool knows the length up front and could seek back to patch the header) as well as through a pipe. Zero headers out of the whole matrix carried a real size. It is structural, not incidental: liblzma's alone encoder has nowhere to receive an uncompressed size. A zero **dictionary** is not writable at all either — liblzma refuses any value below `LZMA_DIC_MIN` (4096) — so the all-zero header is one no encoder can produce.

Size 0 *is* producible, though not by liblzma. The LZMA SDK's own `lzma_alone` writes the actual length: `LzmaUtil.c` calls `File_GetLength` and copies `fileSize` into the 8-byte field verbatim, with no sentinel path. Verified rather than asserted — the SDK's `LzmaUtil.c` was built from the vendored sources in `pylzma-0.6.1` and run: a 0-byte input gives `5D 00000002 0000000000000000`, 18 bytes, decoding to nothing, while 1/13/100/100 000-byte inputs give headers carrying 1/13/100/100 000.

**That costs nothing, measured end-to-end against those real files.** The size-known non-empty ones all detect as `LZMA_ALONE` / `PROBABLE` / `content_probe` and read back correctly — the gate is `size != 0`, so a known size is exactly as welcome as the sentinel. Only the genuinely empty one is refused, and it was never claimed by the probe anyway (`require_output` declines the empty decode first); a `.lzma` name still opens it through the extension, one empty member, exactly as before.

**Rejected: refusing only an all-zero 13-byte header.** Narrower, and it is the one rule that would admit that SDK-style empty stream — but what it admits is not worth having, and it is measurably weaker. Census over 68 156 files under `/usr`, counting fabricated Alone claims: plain removal **5**, all-zero-header rule **3**, declared-size-zero rule **2**. The file separating them is real rather than synthetic — `/usr/lib/libreoffice/presets/database/biblio/biblio.dbt`, header `5C` followed by zeros: a plausible properties byte over a zero region, which the all-zero rule lets through. It is also a shape guard rather than a statement about the format, the same species of workaround as the guard being removed.

**Rejected: rejecting a decode that ends cleanly before the source does (no trailing data).** The most appealing alternative, because it is the mechanism-level rule and it is one the codebase already applies elsewhere — Brotli's chain walk rejects "a declared end with trailing bytes". Applied to Alone it would work: the extent is observable (a `LZMADecompressor` reports `eof` with `unused_data`), 32 KiB of zeros ends its stream at byte 18 and would be rejected, and a bounded prefix of a large real stream never terminates, so it is never wrongly rejected. It was measured, not argued away, and it loses on three counts:

1. **It does not buy back the empty stream, which was its whole appeal.** An empty Alone stream is *still* not claimed under it, because `require_output` refuses a zero-byte decode further down. Confirmed for both the 23-byte liblzma stream and the 18-byte SDK one. No header or trailing rule can make archivey claim an empty stream, so there is nothing to recover.
2. **It ties rather than wins.** Same `/usr` census, 68 242 files: props only **5**, trailing-data rule **2**, declared-size-zero rule **2**, both together **2** — the same two survivors (`huffman-null-max.golden`, in two Go trees). Combining them buys nothing measurable over either alone.
3. **It has a hole on exactly the weakest source shape.** It needs a known length to compare against, and `source_byte_size()` is `None` for a non-seekable stream. Piping 32 KiB of zeros with a props-only header gate returns `LZMA_ALONE` / `PROBABLE` again; the declared-size rule refuses it with no length knowledge at all.

Worth revisiting as a **general** probe rule rather than an Alone one — the decode probes today have a completeness gate (incomplete → reject) with no trailing-data counterpart outside Brotli's chain walk, and a codec-independent version would strengthen gzip, xz and the rest at the same time. That belongs with the evidence classes in `detection-evidence-ledger`, not here.

**Rejected: honouring `require_output` on the truncated path instead.** It targets the real mechanism — claiming a format having decoded nothing — but it is unsafe, and measurably so: a genuine Alone stream of 100 KiB of incompressible data raises `TruncatedError` with no output from its bounded 256-byte prefix, exactly like the zero padding. That rule would stop detecting it. The two are separable only at the header, where one declares zero output and the other declares unknown.

## Risks / Trade-offs

- [The zlib gate accepts 16.5 times more headers before decoding] → The decode still has to
  succeed, and `detection-evidence-ledger` regrades stored-only decodes and drops all bounded
  probes to `GUESS`. Sequence the ledger change soon after rather than long after.
- [Removing the Alone guard depends on the reorder being correct, not merely present] → The
  regression pin is a *zero-system-area* ISO fixture as well as the boot-code one: the first
  proves the guard's removal did not reintroduce the claim, the second proves the reorder
  fixed the case the guard never covered.
- [The reorder makes every unidentified source larger than 32 775 bytes pay one extended
  peek before the probes] → It already pays it after the probes today; this moves the cost,
  it does not add it. Sources below the window are size-gated out, and
  `detection-prefix-workspace` later makes the peek a delta read rather than a re-read
  from zero.

## Open Questions

None. The three fixes and the reorder are each measured above.

## Sequencing

Lands first among the detection changes; blocks nothing and is blocked by nothing.

`prefixed-archive-detection`'s revision must drop its far-magic Impact bullet and the
far-magic step from its `Magic-first detection…` delta, because this change ships that move.
Its bootable-ISO evidence stays useful as the justification recorded here.

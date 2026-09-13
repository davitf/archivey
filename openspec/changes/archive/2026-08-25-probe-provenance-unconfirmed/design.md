# Design — provenance-based unconfirmed-format signal

## The switch, and why it is wired wrong

`src/archivey/core.py:112`:

```python
probe_guess = (
    detected.detected_by == "content_probe"
    and detected.confidence is DetectionConfidence.GUESS
)
```

`base_reader.py:1967` then stamps `format_unconfirmed` and emits
`PROBE_FORMAT_UNCONFIRMED` when that is true and the error is a `TruncatedError` or
`CorruptionError`.

The first clause is the provenance question — *did anything other than a probe agree?* The
second is a strength-of-evidence question that the Brotli work happened to answer at the
same time. They coincide only for Brotli's uncompressed/metadata-first class. Everywhere
else the AND silently narrows the channel:

- **LZMA Alone** reports `PROBABLE` unconditionally, by an explicit decision in
  `brotli-probe-framing-gate` (0 false positives in 20 000 random blobs). Sound as a
  confidence claim; it still means an Alone probe-only hit can never be stamped.
- **Brotli compressed-first** was deliberately moved to `PROBABLE` by that change's task
  3.1a, *because* the flag was GUESS-keyed — the reasoning was "compressed-first evidence
  is strong enough to take the corroborated failure path". That inverts the dependency:
  a confidence value was chosen to steer error behaviour.

## What the measurement says

`scripts/exploration/probe_residual_census.py`, 66 361 files under `/usr`:

| claimed as | confidence | genuine | fabricated | stamped |
| --- | --- | --- | --- | --- |
| Brotli | `GUESS` | 0 | 60 | yes |
| Brotli | `PROBABLE`, compressed-first | 4 | 64 | no |
| LZMA Alone | `PROBABLE` | 0 | 4 | no |

68 of 128 fabrications (53%) are invisible to the channel. The four genuine streams are
`.br`/`.brotli` files that the probe identified correctly — they are also the reason this
change must not become "distrust every probe hit".

### The 3.1a question, and why provenance dissolves it

Task 3.1a's evidence was **0.014% acceptance on random data** versus ~100% for
uncompressed-first. That number is sound and this change does not dispute it. It simply
does not transfer: real files are not uniform random, and the compressed-first class picks
up 64 real fabrications — 35 of them under 16 bytes, where a handful of bytes parses as a
compressed block by accident.

Two ways to resolve it:

- **A. Move compressed-first back to `GUESS`.** Direct, but it re-couples confidence to
  error behaviour, discards a genuinely good random-data measurement, and would report
  `GUESS` for the four real `.brotli` files found here.
- **B. Leave confidence alone; key the stamp on provenance.** Compressed-first keeps
  `PROBABLE` — the evidence really is stronger — and *still* gets stamped when nothing
  corroborated it, because that is a different question.

**B**, which is this change. It is why the proposal calls 3.1a "largely moot" rather than
"wrong": the decision was reasonable given a flag it should never have been steering.

**Sequencing note.** `probe-completeness-gate` removes 91 of the 128 fabrications,
including 57 of the 64 compressed-first ones. Land it first and re-run the census: this
change's argument survives (the blind spot is structural, not a matter of count), but the
numbers quoted in any PR description should be the post-completeness ones.

## What counts as corroboration

The rule is *"a probe said so and nothing else did"*, deliberately not a list of probes:

| signal | corroborating? | why |
| --- | --- | --- |
| exact magic | n/a | not a probe hit at all |
| matching file extension | **yes** | the existing `.br` rule, unchanged |
| inner-TAR upgrade (`ustar` found in the decompressed prefix) | **yes** | a second independent signal, and it required an actual successful decompression to see |
| SFX scan found archive magic | n/a | not a probe hit |
| probe hit alone | **no** | the whole subject |

The inner-TAR case is `brotli-probe-framing-gate` task 5.9, folded in here because it is
the same predicate. It is a small population, but leaving it out would mean a
`TAR_BROTLI` result — which decompressed successfully enough to find a TAR header — is
treated as no better corroborated than a four-byte coincidence.

## What this change does not do

- **No new failures.** A probe-only result that reads cleanly is a success, exactly as
  #261 decided. This adds information to failures that already happen.
- **No confidence changes.** `DetectionConfidence` values stay as #261 set them. The point
  is to stop them steering error behaviour.
- **No reduction in false positives.** That is `probe-completeness-gate`. These two are
  complementary: one shrinks the population, the other makes the survivors honest.
- **No new error type.** `TruncatedError` / `CorruptionError` stay, per #261's decision
  that `except TruncatedError` must keep working.

## Naming

`FormatProvenance.probe_guess` → `probe_only`, and its docstring stops referring to
`GUESS`. The rename is the point rather than cosmetic: the current name is why the field
reads as correct at the call site.

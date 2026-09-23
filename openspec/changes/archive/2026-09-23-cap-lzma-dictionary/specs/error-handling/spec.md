# error-handling — cap the LZMA dictionary delta

## MODIFIED Requirements

### Requirement: A decode failure on probe-only evidence names its provenance

When a single-file member's format was chosen by a content probe and **nothing else
agreed**, a decoding failure while reading that member SHALL:

1. Keep the same exception **type** (`TruncatedError` / `CorruptionError` as today, or
   `ResourceLimitError` when the read tripped a limit) — no new subclass; callers catching
   those types must keep working.
2. Set `format_unconfirmed=True` on the exception (see the standard-attributes
   requirement).
3. Rewrite the **message** so it reports that the format identification was unconfirmed,
   rather than presenting as a plain truncation or corruption of a file whose format is
   settled. The message MUST NOT imply that nothing was produced: a read may already have
   delivered a full buffer (65 536 bytes measured) of bytes copied verbatim from the
   source before the decoder errors. The message MUST NOT name a confidence level, which
   is no longer what the stamp keys on.
4. Emit diagnostic `PROBE_FORMAT_UNCONFIRMED` (see `diagnostics`) — a new code, not a
   stretch of `EXTENSION_FORMAT_UNCONFIRMED`.

**A limit trip counts as a decoding failure here.** A `ResourceLimitError` raised while
reading such a member SHALL be stamped the same way. The case that forces it is the LZMA
dictionary cap (`DecoderLimits`): the Alone probe claims bytes that are not LZMA at all —
an OLE/CFB header over zero padding is the measured example — and reads four arbitrary
header bytes as a dictionary size, 2.7 GiB for that header. Left unstamped, the refusal
tells the caller the archive asked for too much and to raise the cap if it is trusted,
about a file that was never an archive of that format. Whether the probe's claim was the
only evidence is the same question for every error the read raises.
The rewritten message for a limit trip SHALL say the read was stopped by a limit
rather than that decoding failed, since a decoder-memory refusal stops the read
before any decoder is built; it still MUST NOT imply that nothing was produced.

**The trigger is provenance, not confidence.** The question this signal answers is "was
there any evidence besides one probe?", which `DetectionConfidence` does not track:
confidence grades *how strong* the evidence is. Keying on `GUESS` left 68 of 128 measured
real-world fabrications unstamped — LZMA Alone, which reports `PROBABLE` unconditionally,
and Brotli's compressed-first class, which was moved to `PROBABLE` precisely *because* the
flag was confidence-keyed. Separating them also lets confidence be retuned later without
silently changing which errors are stamped.

A claim SHALL count as **corroborated**, and therefore not stamped, when either of these
holds: the filename agrees with the detected format; or the probe hit was upgraded to a
`TAR_*` format because a TAR header was found in the decompressed prefix, which is a
second independent signal obtained by actually decompressing. Exact magic and SFX scans do
not reach this requirement at all, being different detection paths.

**"The filename agrees" SHALL be the exact negation of what raises a
`FORMAT_EXTENSION_CONFLICT`** — the extension's format equals the detected format, or is
the documented *deferred inner-TAR* case where a `TAR_*` extension stands over a bare
compressor result because the inner-TAR probe could not run. So `foo.tar.br` reported as
bare `BROTLI` corroborates, and the rule generalizes past `.br` to every magic-less codec
(`.lzma`, `.zz`). One predicate SHALL serve both, so the system cannot both warn that a
name conflicts and count it as corroboration.

Agreement SHALL NOT be reduced to the `stream` component alone. Every container format
shares `StreamFormat.UNCOMPRESSED`, so a `stream`-only test makes a `.zip` name corroborate
a `TAR` result. No content probe can produce a container format today — every one is a
`RAW_STREAM` codec — but `ReadBackend.CONTENT_PROBES` exists so a container backend can
register one, and that seam MUST NOT silently arm this.

> **Contested, and scheduled for replacement.** PR #263's design analysis holds that the
> filename must not decide whether a failure is stamped, keying the signal on the winning
> candidate's **content-evidence class** instead: `NAME` ranks below `BOUNDED_PROBE`, so a
> matching extension is retained as evidence but cannot promote the class, and a failure
> whose winning class is still `BOUNDED_PROBE` is stamped whether or not the name agrees
> (§6). Its §9 goes further and drops the `.br`-raises-confidence rule too, so a bounded
> Brotli probe is `GUESS` with or without the extension. It accepts the consequence
> explicitly — a genuinely truncated `x.br` carries the flag — on the grounds that
> `format_unconfirmed` must mean "the bytes did not confirm this identity", not "the
> identity is probably wrong", and requires the winning evidence ledger to be a public
> outcome so a caller can see the `NAME` item and present the error accordingly.
>
> **Scope of that follow-up: two sites, not one.** The filename decides the stamp here via
> `_extension_corroborates`, and in `_brotli_probe_confidence` via the `.br`-to-`PROBABLE`
> rule shipped in #261. They are the same rule expressed twice; removing only the first
> leaves the second contradicting §9.

Today an uncorroborated source raises `TruncatedError (member=…, format=BROTLI)` — or
`CorruptionError (format=LZMA_ALONE)` — asserting two things that are not known: that the
bytes are that format, and that the file is truncated or corrupt. For a magic-less format
identified only by decoding a bounded prefix, the likelier explanation is that the file was
never that format.

This SHALL NOT refuse the open: a probe-only identification that reads cleanly is a
success, and an extensionless stream the probe identified correctly must stay readable.
A corroborated result keeps today's type, message, and `format_unconfirmed=False`.

#### Scenario: unconfirmed-format decode failure

| Case | Expected |
| --- | --- |
| Probe-only Brotli result (`GUESS`), decode fails | Same `TruncatedError`/`CorruptionError` type; `format_unconfirmed is True`; message names unconfirmed identification; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe-only Brotli result, **compressed-first** (`PROBABLE`), decode fails | Same treatment — stamped. Confidence does not gate the signal |
| Probe-only **LZMA Alone** result (`PROBABLE`), decode fails | Same treatment — stamped |
| Probe-only **LZMA Alone** result whose header declares a dictionary over `max_decoder_memory` | `ResourceLimitError`, stamped: `format_unconfirmed is True`; message names unconfirmed identification and a limit stop, not a decode failure; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe match corroborated by extension, decode fails | Ordinary truncation/corruption message; `format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| Probe hit upgraded to `TAR_BROTLI` via an inner-TAR header, decode fails | Corroborated: `format_unconfirmed is False` |
| Probe-only result, decode succeeds | Success; no error and no diagnostic |
| Decode fails after bytes were already delivered | Error still raised; message does not claim zero output |
| `DiagnosticPolicy.pedantic()`, probe-only decode fails | Same typed error with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Format came from exact magic, decode fails | Untouched — this requirement does not apply |

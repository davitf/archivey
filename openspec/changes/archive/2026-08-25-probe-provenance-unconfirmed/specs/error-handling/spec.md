## MODIFIED Requirements

### Requirement: Every ArchiveyError carries standard attributes

The system SHALL ensure every `ArchiveyError` instance carries:

| Attribute | Type | Contract |
| --- | --- | --- |
| `message` | `str` | Human-readable explanation, stored **escaped** for terminal safety |
| `raw_message` | `str` | The same text as the call site wrote it, unescaped; for embedding in a message that will escape it |
| `source_format` | `ArchiveFormat \| None` | Format being processed, if known |
| `archive_name` | `str \| None` | Path or source stream `name`; `None` for anonymous streams; never fabricated; **raw** |
| `member_name` | `str \| None` | Member in context, if any; **raw** |
| `link_target` | `str \| None` | Symlink/hardlink target in context, if any; **raw** |
| `format_unconfirmed` | `bool` | `True` when the format claim rested on a content probe alone, with nothing corroborating it (`detected_by="content_probe"`, no matching extension and no inner-TAR upgrade) — **whatever confidence detection reported**; default `False` for every other raise |
| `__cause__` | `BaseException \| None` | Original exception via `raise ... from exc` when wrapping |

`__str__` SHALL append `format_unconfirmed=True` when the flag is set (alongside
`format=…`), so a printed exception carries the same signal as the attribute.

#### Scenario: context attribute matrix

| Case | Expected |
| --- | --- |
| `CorruptionError` while reading ZIP member `"data/file.txt"` | `source_format == ArchiveFormat.ZIP`; `member_name == "data/file.txt"`; `format_unconfirmed is False` |
| `FormatDetectionError` before any member | `member_name is None`; `format_unconfirmed is False` |
| Probe-only Brotli read fails as `TruncatedError` | `format_unconfirmed is True`; `str(exc)` contains `format_unconfirmed=True` |
| Probe + `.br` read fails | `format_unconfirmed is False` — extension corroborated |
| Probe-only **LZMA Alone** read fails | `format_unconfirmed is True` — `PROBABLE` confidence, but nothing corroborated it |
| Probe-only Brotli, **compressed-first** (`PROBABLE`), read fails | `format_unconfirmed is True` — confidence is not the test |

### Requirement: A decode failure on probe-only evidence names its provenance

When a single-file member's format was chosen by a content probe and **nothing else
agreed**, a decoding failure while reading that member SHALL:

1. Keep the same exception **type** (`TruncatedError` / `CorruptionError` as today) — no
   new subclass; callers catching those types must keep working.
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

**The trigger is provenance, not confidence.** The question this signal answers is "was
there any evidence besides one probe?", which `DetectionConfidence` does not track:
confidence grades *how strong* the evidence is. Keying on `GUESS` left 68 of 128 measured
real-world fabrications unstamped — LZMA Alone, which reports `PROBABLE` unconditionally,
and Brotli's compressed-first class, which was moved to `PROBABLE` precisely *because* the
flag was confidence-keyed. Separating them also lets confidence be retuned later without
silently changing which errors are stamped.

A claim SHALL count as **corroborated**, and therefore not stamped, when any of these
holds: the file extension matches the detected format; or the probe hit was upgraded to a
`TAR_*` format because a TAR header was found in the decompressed prefix, which is a
second independent signal obtained by actually decompressing. Exact magic and SFX scans do
not reach this requirement at all, being different detection paths.

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
| Probe match corroborated by extension, decode fails | Ordinary truncation/corruption message; `format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| Probe hit upgraded to `TAR_BROTLI` via an inner-TAR header, decode fails | Corroborated: `format_unconfirmed is False` |
| Probe-only result, decode succeeds | Success; no error and no diagnostic |
| Decode fails after bytes were already delivered | Error still raised; message does not claim zero output |
| `DiagnosticPolicy.pedantic()`, probe-only decode fails | Same typed error with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Format came from exact magic, decode fails | Untouched — this requirement does not apply |

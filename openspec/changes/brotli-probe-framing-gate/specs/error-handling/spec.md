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
| `format_unconfirmed` | `bool` | `True` when the format claim rested only on a content probe with no corroborating extension (`detected_by="content_probe"` at `GUESS`); default `False` for every other raise |
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

## ADDED Requirements

### Requirement: A decode failure on probe-only evidence names its provenance

When a single-file member's format was chosen by a content probe with no corroborating
extension (`detected_by="content_probe"` at `GUESS` confidence), a decoding failure while
reading that member SHALL:

1. Keep the same exception **type** (`TruncatedError` / `CorruptionError` as today) — no
   new subclass; callers catching those types must keep working.
2. Set `format_unconfirmed=True` on the exception (see the standard-attributes
   requirement).
3. Rewrite the **message** so it reports that the format identification was unconfirmed,
   rather than presenting as a plain truncation or corruption of a file whose format is
   settled. The message MUST NOT imply that nothing was produced: a read may already have
   delivered a full buffer (65 536 bytes measured) of bytes copied verbatim from the
   source before the decoder errors.
4. Emit diagnostic `PROBE_FORMAT_UNCONFIRMED` (see `diagnostics`) — a new code, not a
   stretch of `EXTENSION_FORMAT_UNCONFIRMED`.

Today such a source raises `TruncatedError (member=…, format=BROTLI)`, which asserts two
things that are not known: that the bytes are Brotli, and that the file is truncated. For
a magic-less format identified only by decoding a bounded prefix, the likelier explanation
is that the file was never that format — measured at 3.5% of an ordinary `/usr` tree
before the framing gate.

This SHALL NOT refuse the open: a probe-only identification that reads cleanly is a
success, and an extensionless stream the probe identified correctly must stay readable.
A corroborated result (`PROBABLE`, extension agrees) keeps today's type, message, and
`format_unconfirmed=False`.

#### Scenario: unconfirmed-format decode failure

| Case | Expected |
| --- | --- |
| Probe-only `GUESS` result, decode fails | Same `TruncatedError`/`CorruptionError` type; `format_unconfirmed is True`; message names unconfirmed identification; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe match corroborated by extension (`PROBABLE`), decode fails | Ordinary truncation/corruption message; `format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| Probe-only `GUESS` result, decode succeeds | Success; no error and no diagnostic |
| Decode fails after bytes were already delivered | Error still raised; message does not claim zero output |

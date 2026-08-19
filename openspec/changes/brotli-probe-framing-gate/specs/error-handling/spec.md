## ADDED Requirements

### Requirement: A decode failure on probe-only evidence names its provenance

When a single-file member's format was chosen by a content probe with no corroborating
extension (`detected_by="content_probe"` at `GUESS` confidence), a decoding failure while
reading that member SHALL report that the **format identification was unconfirmed**,
rather than presenting as a plain truncation or corruption of a file whose format is
settled.

Today such a source raises `TruncatedError (member=…, format=BROTLI)`, which asserts two
things that are not known: that the bytes are Brotli, and that the file is truncated. For
a magic-less format identified only by decoding a bounded prefix, the likelier explanation
is that the file was never that format — measured at 3.5% of an ordinary `/usr` tree
before the framing gate.

This SHALL NOT change *which* exception type is raised for a corroborated source, and
SHALL NOT refuse the open: a probe-only identification that reads cleanly is a success,
and an extensionless stream the probe identified correctly must stay readable.

Callers can already have received output before the failure — a read may deliver a full
buffer (65 536 bytes measured) of bytes copied verbatim out of the source before the
decoder errors. The error text SHALL therefore not imply that nothing was produced.

#### Scenario: unconfirmed-format decode failure

| Case | Expected |
| --- | --- |
| Probe-only `GUESS` result, decode fails | Error names the unconfirmed identification; not a bare truncation claim |
| Probe match corroborated by extension (`PROBABLE`), decode fails | Ordinary `TruncatedError` / `CorruptionError` — the format is corroborated |
| Probe-only `GUESS` result, decode succeeds | Success; no error and no diagnostic |
| Decode fails after bytes were already delivered | Error still raised; message does not claim zero output |

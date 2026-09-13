## ADDED Requirements

### Requirement: Diagnostic messages are inert for terminal display

`Diagnostic.message` interpolates archive-derived values and is logged verbatim to the
CLI's stderr (`log.warning("%s", message)`), which makes it a terminal-display channel as
much as an exception message is. The system SHALL escape `Diagnostic.message` at
construction, losslessly, on the same terms as an exception message.

`Diagnostic.context` SHALL remain **raw**. It is the structured channel — `member_name`
and its siblings, surfaced through `to_dict()` — and a caller routing diagnostics to a
JSON sink needs the real values, not a display rendering.

Escaping SHALL NOT be left to the sites that build diagnostic messages. Every one of
them interpolates through `quoted()` today, so the text would be inert either way — but
that is a property of the current call sites, not of the type: a future message written
as `f"...{name}"` would look exactly like its neighbours and emit raw control bytes, and
only for a hostile archive.

`Diagnostic.message` is subject to the escape-exactly-once rule in `error-handling`:
values interpolated into it SHALL be raw, via `quoted()` rather than `!r`.

#### Scenario: a diagnostic message cannot spoof a terminal line

| Case | Expected |
| --- | --- |
| A diagnostic whose message embeds `ev\x1b[2Kil\rSPOOF.txt` | `message` carries `\x1b` as four literal characters; the logged line cannot be erased and rewritten |
| `context.member_name` for the same diagnostic | The raw name, unescaped |
| `to_dict()["context"]` | Raw values, suitable for a structured sink |
| A message with no control bytes | Unchanged |

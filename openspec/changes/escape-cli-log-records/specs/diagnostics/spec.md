## ADDED Requirements

### Requirement: Diagnostic messages are inert for terminal display

`Diagnostic.message` interpolates archive-derived values and is logged verbatim to the
CLI's stderr (`log.warning("%s", message)`), which makes it a terminal-display channel as
much as an exception message is. The system SHALL escape `Diagnostic.message` at
construction, losslessly, on the same terms as an exception message.

`Diagnostic.context` SHALL remain **raw**. It is the structured channel — `member_name`
and its siblings, surfaced through `to_dict()` — and a caller routing diagnostics to a
JSON sink needs the real values, not a display rendering.

Escaping SHALL NOT be left to the fact that message call sites happen to interpolate
through `!r`, which escapes as a side effect. That property holds today, but one new
`{name}` would undo it silently, and the resulting gap would appear only for a hostile
archive.

#### Scenario: a diagnostic message cannot spoof a terminal line

| Case | Expected |
| --- | --- |
| A diagnostic whose message embeds `ev\x1b[2Kil\rSPOOF.txt` | `message` carries `\x1b` as four literal characters; the logged line cannot be erased and rewritten |
| `context.member_name` for the same diagnostic | The raw name, unescaped |
| `to_dict()["context"]` | Raw values, suitable for a structured sink |
| A message with no control bytes | Unchanged |

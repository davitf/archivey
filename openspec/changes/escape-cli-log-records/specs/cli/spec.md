## ADDED Requirements

### Requirement: Archive-derived text is escaped before terminal display

Archive member names are attacker-controlled. The CLI SHALL escape archive-derived
text before writing it to a terminal stream, so a member name cannot emit control
sequences that rewrite, erase, or spoof the output line reporting it. Escaping SHALL be
lossless (a backslash form that names the escaped byte), not elision.

This SHALL hold for **both** paths by which archive-derived text reaches the CLI's
streams:

- **Print sites** — member names and any path derived from a member name, in listings,
  report lines, summaries, and the error detail appended to a failure line.
- **Log records** — records emitted by the library's `archivey` logger tree and rendered
  by the handler the CLI installs. The message SHALL be escaped as displayed; the
  records the library emits SHALL NOT be altered, so a handler installed by an embedding
  application or a test still receives them verbatim.

Escaping SHALL apply to the rendered message. A record's `exc_info` traceback SHALL
remain readable across lines rather than being escaped into one line. This leaves the
traceback's final line — the exception's own message, which MAY be archive-derived —
unescaped; that is a **recorded residual** (threat-model O9), not a claim that a traceback
is archivey's own text. No archivey logging call site passes `exc_info`, and a call site
that starts to SHALL escape the exception lines first.

A member-derived **path** SHALL be rendered relative to the operation's root, with `/`
separators, before escaping. A native path would otherwise have every separator doubled
by the backslash escape on Windows; after the relative rendering, a backslash surviving
into the output is a character in a member name, which is what SHALL be escaped.

Escaping is a **display** concern and SHALL NOT change the bytes written to disk, the
member names the library reports, or any value on `ExtractionResult`.

#### Scenario: control sequences cannot spoof CLI output

| Case | Expected |
| --- | --- |
| Member named `ev\x1b[2Kil\rSUCCESS.txt` in a report line | Printed as `ev\x1b[2Kil\rSUCCESS.txt` — no raw ESC or CR reaches the stream |
| The same member named in a library WARNING record | Escaped identically; the log line cannot be erased and rewritten |
| The same member in the error detail appended to `failed:` | Escaped |
| A record carrying `exc_info` | Traceback stays multi-line and readable; its exception-message line is a recorded residual |
| `archivey test` failure detail, and the abort / top-level error notices | Escaped — these print the exception, not a report line |
| A handler installed by an embedding app or `caplog` | Receives the record unescaped and unmodified |
| An ordinary member name with no control bytes | Printed unchanged |
| A Windows destination path in a report line | Separators not doubled (rendered relative, `/`-separated, before escaping) |

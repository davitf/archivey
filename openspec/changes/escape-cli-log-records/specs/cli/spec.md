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

Escaping SHALL apply to the rendered message only. A record's `exc_info` traceback SHALL
remain readable across lines — escaping its newlines would collapse a traceback into a
single unreadable line, and a traceback is archivey's own text rather than the archive's.

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
| A record carrying `exc_info` | Traceback stays multi-line and readable |
| A handler installed by an embedding app or `caplog` | Receives the record unescaped and unmodified |
| An ordinary member name with no control bytes | Printed unchanged |
| A Windows destination path in a report line | Separators not doubled (rendered relative, `/`-separated, before escaping) |

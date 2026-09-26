## ADDED Requirements

### Requirement: Archive-derived text is escaped before terminal display

Archive member names are attacker-controlled. Archive-derived text SHALL NOT reach a
terminal stream carrying control sequences that rewrite, erase, or spoof the output line
reporting it. Escaping SHALL be lossless (a backslash form that names the escaped byte),
not elision.

Escaping SHALL happen where archive-derived text **becomes a message**, not where a
message is displayed:

- **Exception and diagnostic messages** escape themselves at construction
  (`error-handling`, `diagnostics`). This is the only placement that also covers a
  message the CLI never prints itself — an uncaught exception whose traceback the
  interpreter writes to stderr, whose final line is the exception's message.
- **Print sites** escape the member names and member-derived paths they format
  themselves — listings, report lines, summaries.

The CLI's log handler SHALL NOT escape the records it renders. Escaping a message that
already escaped itself would double every backslash in it, and the library's own log
call sites interpolate member names through `%r`, which escapes. Records the library
emits SHALL NOT be altered, so a handler installed by an embedding application or a test
receives them verbatim.

When rendering an exception it did not construct — an `OSError` carrying a destination
filename, a third-party error — the CLI SHALL escape it, since only archivey's own
exceptions escape themselves.

A member-derived **path** formatted by a print site SHALL be rendered relative to the
operation's root, with `/` separators, before escaping. A native path would otherwise
have every separator doubled by the backslash escape on Windows.

Escaping is a **display** concern and SHALL NOT change the bytes written to disk, the
member names the library reports, or any value on `ExtractionResult`.

#### Scenario: control sequences cannot spoof CLI output

| Case | Expected |
| --- | --- |
| Member named `ev\x1b[2Kil\rSUCCESS.txt` in a report line | Printed as `ev\x1b[2Kil\rSUCCESS.txt` — no raw ESC or CR reaches the stream |
| The same member named in a library WARNING record | Escaped identically; the log line cannot be erased and rewritten |
| The same member in the error detail appended to `failed:` / `blocked:` | Escaped |
| `archivey test` failure detail, and the abort / top-level error notices | Escaped — these print the exception, not a report line |
| An `ArchiveyError` propagating uncaught out of the CLI | Traceback's final line is the escaped message; no raw control byte reaches stderr |
| A record carrying `exc_info` | Traceback stays multi-line and readable, and its exception-message line is escaped |
| An `OSError` naming a member-derived path | Escaped by the CLI, which does not assume the exception escaped itself |
| A handler installed by an embedding app or `caplog` | Receives the record unescaped and unmodified |
| An ordinary member name with no control bytes | Printed unchanged |
| A Windows destination path in a report line | Separators not doubled (rendered relative, `/`-separated, before escaping) |

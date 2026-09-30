## MODIFIED Requirements

### Requirement: Treat scan races as diagnostics and genuine errors as errors

The directory backend SHALL propagate genuine directory-walk `OSError`s
unchanged. If a listed entry or subdirectory vanishes before inspection, the
reader SHALL skip it, continue scanning, and emit `SCAN_ENTRY_VANISHED` or
`SCAN_DIRECTORY_VANISHED` with a JSON-safe relative path and entry kind. These
events are reader-operation aggregate data and SHALL NOT attach to a member that
does not exist.

On a platform that can open a directory without following a symlink (POSIX), a
listed subdirectory that was replaced before its scan, by a symlink or by a different
directory, SHALL stop the walk with an `OSError` whose `errno` is `errno.ESTALE`. The
walk SHALL NOT list any entry through the replacement.

Under `RAISE`, `DiagnosticRaisedError` SHALL halt the scan. Diagnostic context
MUST NOT retain `DirEntry`, `Path`, exception, or filesystem handle objects.

#### Scenario: directory scan matrix

| Case | Expected |
| --- | --- |
| Entry disappears between listing and `stat` under default policy | Entry skipped; `SCAN_ENTRY_VANISHED` counted/retained/logged; walk continues |
| Subdirectory vanishes and code resolves to `RAISE` | `DiagnosticRaisedError` halts scan |
| Walking subdirectory raises `PermissionError` | Original error propagates unchanged; no vanished-path diagnostic substitutes |
| Listed subdirectory, or a parent of it, replaced by a symlink before its scan (POSIX) | `OSError` with `errno.ESTALE`; nothing from the link target is listed |

# Errors and diagnostics

Archivey tells you about problems in two ways. An exception means it couldn't give you a correct
answer, so it stopped. A diagnostic means the answer is correct, but something along the way is
worth knowing, such as a password that wasn't needed or a timestamp that was invalid and is
`None`.

## Exceptions

```python
try:
    with archivey.open_archive("maybe.7z") as archive:
        archive.extract_all("out/")
except archivey.ArchiveyError as e:
    print("Could not extract:", e)
```

Every problem with the archive, or with something archivey needs in order to read it, raises a
subclass of `ArchiveyError`. These are the ones you're most likely to handle:

| Exception | Raised when |
|---|---|
| `CorruptionError` | The archive's data is damaged. Its subclass `TruncatedError` means the data seems to end early, but that's a guess, so catch `CorruptionError` for both |
| `EncryptionError` | A password is needed and none was given, or none of the ones given works |
| `FormatDetectionError` | The file isn't in a format archivey recognizes |
| `UnsupportedFeatureError` | The format is recognized, but this archive uses something archivey can't handle. An unknown compression method or version number can also mean a damaged header: nothing checksums the header field it's read from, such as a ZIP entry's compression method, so archivey can't tell the two apart, and the message says so. If all you need to know is whether you can use the file, the answer is no either way |
| `PackageNotInstalledError` | The archive needs an optional package or program that isn't installed. The message names it |
| `ResourceLimitError` | The archive went over one of the [limits](security.md#hardening) |

The [reference](api.md#archivey.ArchiveyError) lists the rest, such as `ReadError`, the parent of
the first two.

If reading fails and the format was only a guess, the exception has `format_unconfirmed=True`. That
happens when nothing in the file confirmed its format, so archivey went by the file name, or when
the format has no reliable signature and only a check of the contents suggested it, as for zlib or
Brotli data. The file may not be in that format at all, such as a file of zeros named `backup.gz`,
rather than a damaged one.

Mistakes in the calling code raise `ArchiveyUsageError`, which isn't an `ArchiveyError`, so a
catch-all for archive problems never hides a bug. The ones you're most likely to meet are calling
`members()`, `open()` or `read()` on a reader opened with `streaming=True`, and opening a second
member stream while another is still open, without `concurrent_members=True`. [Choosing how to
read](reading.md) explains both. A source that isn't a path or a file object raises `TypeError`, and
asking for a member name the archive doesn't have raises `KeyError`, as a dictionary would.

Errors that aren't about the archive also pass through as themselves. A missing file raises
`FileNotFoundError`, and a disk or permission error while reading or writing raises `OSError`, so
catch `OSError` next to `ArchiveyError` if your program cares about both. Any other exception type
from inside archivey is a bug, and we'd like to hear about it.

Exception messages are safe to print. Control characters from the archive, such as a member name
built to move the terminal cursor, are escaped in the message. Attributes like `e.member_name` keep
the raw value.

## Damaged archives

`members()` raises if the archive is damaged partway through its list of members, and gives you
nothing. Iterating over the reader, or over `stream_members()`, gives you the members before the
damage, then raises. `members_report()` gives you both at once:

```python
with archivey.open_archive("damaged.tar") as archive:
    report = archive.members_report()
    for member in report:
        print(member.name)
    if report.error is not None:
        print("The list stops early:", report.error)
```

A TAR archive cut between two members, or inside a member's header, has nothing left that looks
wrong, so it lists as a complete, shorter archive with no error. Only the diagnostic
`ARCHIVE_EOF_MARKER_MISSING` shows it, and
[`DiagnosticPolicy.strict()`](#raising-or-quieting-a-diagnostic) turns it into an exception.

Where the format stores a checksum for each member, reading a member from start to end checks it,
and the read that reaches the end raises if the data is damaged. If you stop early, nothing is
checked. A loop that reads in chunks gets every byte that could be read before the error, but those
bytes may be wrong too, since archivey can't tell where the damage starts. Some formats store
nothing to check a single member against, so damage there can go unnoticed: a TAR member has no
checksum of its own, the checksum of a `.tar.gz` covers the whole file rather than one member, and
Brotli, `.Z` and `.lzma` store none at all.

If you opened the archive with `seekable_members=True` (see [Choosing how to read](reading.md)), a
seek back to the start begins the check again, but after a seek anywhere else, damage may go
unnoticed. Once a read has raised, a later read that reaches the end raises the same error, even
after a seek, so seeking back can't hand you the damaged member as if it were complete.

[Damaged members](extracting.md#damaged-members) covers what extraction does.

## Diagnostics

Each diagnostic, such as one for a name with characters that make it display as a different one,
has a `code` to check for, a `message` meant for people, and a `context` with details such as the
member's name, kept raw as in `e.member_name`. Archivey keeps them in a few places, depending on
what you called:

| After | Read them from |
|---|---|
| Opening, and anything on the reader | `archive.diagnostics`: everything since the archive was opened |
| `archive.open(member)` | `stream.diagnostics`: that one read |
| `extract_all` | `report.diagnostics`: that one call |
| `members_report()` | `report.diagnostics`: that one listing |
| Any listing | `member.diagnostics`: the ones about that member |

All but the last are summaries: `counts` has an exact count for each code, and `retained` keeps the
records themselves. An archive keeps at most 256 records by default, counting those attached to
members, and `max_retained_diagnostic_references` in `ArchiveyConfig` changes that.

Each diagnostic is also logged as a warning on a logger under `archivey`, such as
`archivey.normalization`, so a script that
calls `logging.basicConfig()` prints a line for each. To handle them as they happen instead, pass a
function as `on_diagnostic=` in `archivey.ArchiveyConfig`. Archivey calls it with each
[`Diagnostic`](api.md#archivey.Diagnostic) as it's recorded, the same record the summaries keep. An
exception it raises stops the operation and reaches your code.

## Raising or quieting a diagnostic

```python
config = archivey.ArchiveyConfig(diagnostic_policy=archivey.DiagnosticPolicy.strict())
```

With `DiagnosticPolicy.strict()`, the diagnostics that say the archive itself is unusual, such as a
name that displays as a different one, raise `DiagnosticRaisedError` from the call that found them.
The ones about your own arguments, such as an unused password, and a few others, such as an empty
archive, are only recorded as before. The [reference](api.md#archivey.DiagnosticPolicy) lists which
codes raise.
`DiagnosticPolicy.pedantic()` raises on every code, including codes a later release adds.

```python
config = archivey.ArchiveyConfig(
    diagnostic_policy=archivey.DiagnosticPolicy(
        overrides={archivey.DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: "ignore"},
    ),
)
```

To change one code, pass it in `overrides`. With `"ignore"`, it's no longer logged, passed to the
callback or kept, but `counts` still counts it. `"raise"` makes that one code raise. The
[reference](api.md#archivey.DiagnosticCode) lists every code.

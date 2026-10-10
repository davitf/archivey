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

Mistakes in the calling code raise `ArchiveyUsageError` instead, which isn't an `ArchiveyError`, so
a catch-all for archive problems never hides a bug. The ones you're most likely to meet are calling
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

Reading a member from start to end lets archivey notice if its data is damaged, and the read that
reaches the end raises. If you stop early, nothing is checked. A loop that reads in chunks gets
every byte that could be read before the error, but those bytes may be wrong too, since archivey
can't tell where the damage starts. A few formats, such as Brotli, `.Z` and `.lzma`, store nothing
to check against, so damage there can go unnoticed.

A seek back to the start begins the check again, but after a seek anywhere else, damage may go
unnoticed. Once a read has raised, a later read that reaches the end raises the same error, even
after a seek, so seeking back can't hand you the damaged member as if it were complete.

[Damaged members](extracting.md#damaged-members) covers what extraction does.

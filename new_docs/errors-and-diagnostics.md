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
| `UnsupportedFeatureError` | The format is recognized, but this archive uses something archivey can't handle, such as an unknown compression method |
| `PackageNotInstalledError` | The archive needs an optional package or program that isn't installed. The message names it |
| `ResourceLimitError` | The archive went over one of the [limits](security.md#hardening) |

The [reference](api.md#archivey.ArchiveyError) lists the rest, such as `ReadError`, the parent of
the first two.

Damage can look like something else, and then the exception names what it looks like. In an
encrypted member, a format with no reliable password check can't tell damaged data from a wrong
password, so it raises `EncryptionError`. That's the case for 7z, and for the old ZIP encryption,
whose check is a single byte. WinZip AES and RAR 5 check the password on its own, so damage there
raises `CorruptionError`. A damaged header field that nothing else checks, such as a ZIP entry's
compression method, can look like a feature archivey doesn't support and raise
`UnsupportedFeatureError`. Either way, you can't use the file.

If reading fails and the format was only a guess, the exception has `format_unconfirmed=True`. That
happens when nothing in the file confirmed its format, so archivey went by the file name, or when
the format has no reliable signature and only a check of the contents suggested it, as for zlib or
Brotli data. The file may not be in that format at all, such as a file of zeros named `backup.gz`,
rather than a damaged one.

Errors that aren't about the archive also pass through as themselves. A missing file raises
`FileNotFoundError`, and a disk or permission error while reading or writing raises `OSError`, so
catch `OSError` next to `ArchiveyError` if your program cares about both. Any other exception type
from inside archivey is a bug, and we'd like to hear about it.

Exception messages are safe to print. Control characters from the archive, such as a member name
built to move the terminal cursor, are escaped in the message. Attributes like `e.member_name` keep
the raw value.

### Mistakes in the calling code

Mistakes in the calling code raise `ArchiveyUsageError`, which isn't an `ArchiveyError`, so a
catch-all for archive problems never hides a bug. The ones you're most likely to meet are:

- calling `members()`, `open()` or `read()` on a reader opened with `streaming=True`;
- opening a second member stream while another is still open, without `concurrent_members=True`;
- calling `open()` or `read()` on a directory;
- giving an option a value it doesn't know, such as `policy="strictt"`. The error names the
  accepted values.

[Choosing how to read](reading.md) explains the first two. A source that isn't a path or a file
object raises `TypeError`, and asking for a member name the archive doesn't have raises `KeyError`,
as a dictionary would.

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

??? note "Why does `members()` raise if the archive is damaged?"
    It returns a list, and a shorter list looks just like a complete one, so code that only looks at
    the list would carry on as if the archive ended there. Iterating hands you each member as it's
    found, so the exception at the end still tells you the rest is missing.

A TAR archive cut between two members, or inside a member's header, has nothing left that looks
wrong, so it lists as a complete, shorter archive with no error. Only the diagnostic
`ARCHIVE_EOF_MARKER_MISSING` shows it, and
[`DiagnosticPolicy.STRICT`](#raising-or-quieting-a-diagnostic) turns it into an exception.

A ZIP read from a pipe gives you its members before its index, which comes last, so some damage
shows only once the index has been read, and iterating raises it after the last member. A member the
index doesn't list, such as an update appended to the archive, is dropped from the list then and
logged as `ARCHIVE_UNLISTED_DATA`, since reading the same archive from a file never sees it.

Where the format stores a checksum for each member, reading a member from start to end checks it,
and the read that reaches the end raises if the data is damaged. If you stop early, nothing is
checked. A loop that reads in chunks gets every byte that could be read before the error, but those
bytes may be wrong too, since archivey can't tell where the damage starts. Some archives store
nothing to check a single member against, so damage there can go unnoticed:

- A TAR member has no checksum of its own, and the checksum of a `.tar.gz` covers the whole file
  rather than one member.
- ISO images and Brotli, `.Z` and `.lzma` files have no checksums at all.
- `.xz`, `.zst` and `.lz4` files have one only if the program that wrote them added it.

When archivey can't check a member that you'd expect to be checked, such as a 7z member encrypted
without a CRC, it records the diagnostic `DIGEST_UNVERIFIABLE`, which `STRICT` also turns into an
exception.

A damaged member doesn't always raise before its end. Depending on the compression method, damaged
data can decode to wrong bytes with no error, and only the checksum at the end catches it. A member
stored without compression never notices damage on its own.

A cut-off member can also end early without an error. `stream.read(member.size)` then returns fewer
bytes than the member should hold, and the next read raises `TruncatedError`. So code that reads a
member in one call and then stops should check the length it got, or call `read()` once more.

No option makes archivey check a member before handing you its bytes. If you must never use
unchecked data, read the whole member into memory or a temporary file first, and use it only once
the read has finished without an error.

An encrypted member whose password was accepted only on a weak check, such as the one-byte check of
old ZIP encryption, is in the same position. If you close its stream before the end, or seek past
bytes without reading them, archivey logs `ENCRYPTED_MEMBER_UNVERIFIED`, since the bytes you read
may come from a wrong password. WinZip AES members are the exception: their check holds across
seeks.

If you opened the archive with `seekable_members=True` (see [Choosing how to read](reading.md)),
most seeks keep the checksum check: a seek back, a seek to or past the end, and a seek forward that
the next read has to decode its way through anyway. Bytes you read again after a seek back aren't
checked a second time. A seek forward that jumps over bytes without decoding them, and lands before
the end, stops the checksum check once you read on from there. That happens in a member stored
without compression, and through the `seekable` accelerator when it has a restart point between
what you've read and where you seek to. Seeking back to a point you had already read up to brings
it back. The length is still checked after a jump, and damage the decompressor itself notices still
raises. Once a read has raised, a later read that reaches the end raises the same error, even after
a seek, so seeking back can't hand you the damaged member as if it were complete.

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

??? note "Why a cap?"
    A crafted archive can have a problem in each of a million members, and a record for each would
    take memory that the listing limits don't count. `counts` stays exact either way.

Each diagnostic is also logged as a warning on a logger under `archivey`, such as
`archivey.normalization`, so a script that calls `logging.basicConfig()` prints a line for each. To
handle them as they happen instead, pass a function as `on_diagnostic=` in
`archivey.ArchiveyConfig`. Archivey calls it with each [`Diagnostic`](api.md#archivey.Diagnostic) as
it's recorded, the same record the summaries keep. An exception it raises stops the operation and
reaches your code.

## Raising or quieting a diagnostic

```python
config = archivey.ArchiveyConfig(diagnostic_policy=archivey.DiagnosticPolicy.STRICT)
```

With `DiagnosticPolicy.STRICT`, the diagnostics that say the archive itself is unusual, such as a
name that displays as a different one, raise `DiagnosticRaisedError` from the call that found them.
The ones about your own arguments, such as an unused password, and a few others, such as an empty
archive, are only recorded as before. The [reference](api.md#archivey.DiagnosticPolicy) lists which
codes raise. `DiagnosticPolicy.PEDANTIC` raises on every code, including codes a later release
adds.

```python
config = archivey.ArchiveyConfig(
    diagnostic_policy=archivey.DiagnosticPolicy(
        ignore={archivey.DiagnosticCode.PASSWORD_ARGUMENT_UNUSED},
    ),
)
```

A policy is two sets of codes. A code in `ignore` is no longer logged, passed to the callback or
kept, but `counts` still counts it. A code in `raise_on` raises. Every other code is recorded as
before. To change a preset, build a policy from the same set: `STRICT` is
`DiagnosticPolicy(raise_on=archivey.ARCHIVE_INTEGRITY_CODES)`, so
`raise_on=archivey.ARCHIVE_INTEGRITY_CODES - {archivey.DiagnosticCode.ARCHIVE_TRAILING_DATA}` is
`STRICT` without that one code. The [reference](api.md#archivey.DiagnosticCode) lists every code.

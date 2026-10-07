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
| `UnsupportedFeatureError` | The format is recognized, but this archive uses something archivey can't handle |
| `PackageNotInstalledError` | The archive needs an optional package or program that isn't installed. The message names it |
| `ResourceLimitError` | The archive went over one of the [limits](security.md#hardening) |

The [reference](api.md#archivey.ArchiveyError) lists the rest, such as `ReadError`, the parent of
the first two.

If reading fails and the format was only a guess, the exception has `format_unconfirmed=True`. That
happens when nothing in the file confirmed its format, so archivey went by the file name, or when
the format has no reliable signature and only a check of the contents suggested it, as for zlib or
Brotli data. The file may not be in that format at all, such as a file of zeros named `backup.gz`,
rather than a damaged one.

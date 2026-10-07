# Opening an archive

## What you can open

`open_archive` takes a path, a binary file object or a folder. A folder opens like an archive of
the files inside it. For a 7z or RAR archive split into several volumes, pass the path of any one
of them and archivey finds the others. Pass a list of volumes, in order, only when it can't find
them by name, such as volumes you hold as open file objects.

A file object is read from its current position to its end, so an archive that starts partway
through a larger file opens as is, as long as nothing follows it. If something does, pass a file
object that ends where the archive ends. A file object that can't seek, such as a pipe or an HTTP
response, needs `streaming=True` and works only for some formats, which
[Reading once](reading.md#reading-once) lists.

## Format

Archivey identifies the format from the file's contents, so a ZIP named `backup.bin` still
opens as a ZIP. It falls back to the file name only when the contents don't settle it. To skip
detection, pass `format=`, such as `format="zip"` or `format=archivey.ArchiveFormat.TAR_GZ`. If
the file isn't in the specified format, it fails like a damaged one.

## Passwords

Pass the password as `password=`: one string, a list of candidates when you're not sure which one
the archive uses, or a function that supplies passwords as they're needed.

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

Archivey tries a list in order, so put the most likely password first: each wrong one costs some
hashing work before it's rejected. An archive can use different passwords for different members,
and archivey finds the right one from your list for each.

You can also pass a function, such as one that asks the user. Archivey calls it only when a
password is needed and none tried so far works. It passes a `PasswordRequest` with the member being
opened and the attempt number. When the password is needed to read an encrypted members list,
which 7z and RAR support, `PasswordRequest.member` is `None`. Return a password to try, or `None` to
give up, which raises `EncryptionError`.

A password or list of passwords given for a format with no encryption, such as TAR, is ignored and
noted in the [diagnostics](errors-and-diagnostics.md), which log a warning for each such archive by
default. One list can then serve a whole batch of archives, and the diagnostics page shows how to
quiet the warning.

## Names in other encodings

Most archives record which encoding their names use, and archivey decodes them with it. When an
archive doesn't say, which is mostly older ones, archivey uses UTF-8 if the bytes are valid UTF-8.
Otherwise it uses the format's traditional encoding, such as cp437 for ZIP, or, where there isn't
one, keeps each byte it can't decode as an escape: a placeholder character between U+DC80 and
U+DCFF, as Python's `surrogateescape` does. If you know the encoding, pass it as `encoding=`,
and archivey uses it in place of that default for names that aren't valid UTF-8: a Latin-1
`café.txt` in a TAR lists as `'caf\udce9.txt'` without it and `'café.txt'` with
`encoding="latin-1"`. Only ZIP, TAR, ISO and RAR read `encoding=`. The other formats decode names
their own way, and a value passed to them is ignored and noted in the diagnostics.

`member.name` is the decoded name, and `member.raw_name` is the bytes as stored. A name with an
escaped byte, like `'caf\udce9.txt'`, can't be encoded as UTF-8, so printing or logging it can raise
`UnicodeEncodeError`. `archivey.terminal.escape_control_chars(member.name)` gives a version that's
safe to show.

## Configuration

Settings you'd keep the same across many archives live in `archivey.ArchiveyConfig`, passed as
`config=`. These include the [limits](security.md#hardening), which RAR program to use, the
accelerators, and how [diagnostics](errors-and-diagnostics.md) are reported. Without it, the
defaults apply. The [reference](api.md#archivey.ArchiveyConfig) lists every setting.

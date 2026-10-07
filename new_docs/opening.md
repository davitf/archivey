# Opening an archive

## What you can open

`open_archive` takes a path, a binary file object or a folder. A file object is read from its
current position, so an archive that starts partway through a larger file opens as is. A folder
opens like an archive of the files inside it. For an archive split into several files, pass them
as a list, in order.

## Format

Archivey identifies the format from the file's first bytes, so a ZIP named `backup.bin` still
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

You can also pass a function, such as one that asks the user. Archivey calls it only when a member
needs a password that none tried so far has opened, and passes a `PasswordRequest` naming the
member and counting the attempts. Return a password to try, or `None` to give up, which raises
`EncryptionError`.

A password given for a format with no encryption, such as TAR, is ignored and noted in the
[diagnostics](errors-and-diagnostics.md). One list of passwords can then serve a whole batch of
archives.

## Names in other encodings

Most archives record which encoding their names use, and archivey decodes them with it. When an
archive doesn't say, which is mostly older ones, archivey uses UTF-8 if the bytes are valid UTF-8.
Otherwise it uses the format's traditional encoding, such as cp437 for ZIP, or, where there isn't
one, keeps the bytes it can't decode as escapes. If you know the encoding, pass it as `encoding=`
to use it instead: a Latin-1 `café.txt` in a TAR lists as `'caf\udce9.txt'` without it and
`'café.txt'` with `encoding="latin-1"`. `member.name` is the decoded name, and `member.raw_name`
is the bytes as stored.

## Configuration

Settings you'd keep the same across many archives live in `archivey.ArchiveyConfig`, passed as
`config=`. These include the [limits](security.md#hardening), which RAR program to use, the
accelerators, and how [diagnostics](errors-and-diagnostics.md) are reported. Without it, the
defaults apply. The [reference](api.md#archivey.ArchiveyConfig) lists every setting.

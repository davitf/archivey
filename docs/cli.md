# Command line

The `archivey` command ships with the base package (`pip install archivey`). Progress bars
need `tqdm`, which comes with `[recommended]`; without it the command still runs.

```bash
archivey photos.zip                 # same as: archivey list photos.zip
archivey l photos.zip               # list (alias)
archivey t photos.zip               # full-read integrity check
archivey x photos.zip               # safe extract (alias for extract)
archivey info photos.zip            # format / identity + access cost (alias: detect)
archivey --version -v               # version + format availability for this install
```

### Safer extract demo

```bash
# Default policy=strict, overwrite=rename, on_error=continue. With no -d, a
# multi-entry archive lands in ./photos/ instead of splattering the current
# directory (tarbomb-safe). Hostile/corrupt members are reported and skipped;
# remaining members are still extracted. Exit 3 if only policy blocks; 1 if
# any member failed.
archivey extract photos.zip

# Classic unzip-into-cwd (opt-in):
archivey extract photos.zip -d .

# All-or-nothing on member *failures* (library STOP). Policy blocks are still
# reported and skipped; remaining members extract. Exit 3 if only blocks.
archivey extract photos.zip --stop-on-error

# Filters: positionals are includes; --exclude subtracts. Unmatched includes
# warn on stderr; extract/test exit 1 when the patterns select nothing, also when
# --exclude removed every match (list warns but stays 0). A sole unmatched
# pattern that looks like a destination gets a -d hint. On an archive with no
# index (a compressed TAR) these warnings come after the run: the run's own pass
# is what checks the patterns, so the archive is decompressed once. A listing
# that ends in damage settles no pattern: no verb warns that a pattern matched
# nothing, because the members past the damage are unknown, and an extract that
# aborts there may leave the destination directory it created.
archivey extract photos.zip -d out/ '*.py' --exclude '*_test.py'
archivey extract photos.zip --policy trusted -d /tmp/out

# Dry run: every check and every read, nothing written. Same report lines
# and exit code as a real extraction into an empty directory. Without -d, a
# single top-level folder is named where it would be moved to; what is
# already there is not checked for collisions, nor whether it can be written to.
archivey extract photos.zip --dry-run
```

### Defaults that differ from the library

`archivey extract` uses the library's `policy=strict`, but two of its defaults differ
from `reader.extract_all()`. They are what breaks a script ported from one to the other:

| Setting | CLI default | Library default |
| --- | --- | --- |
| Name collision | `--overwrite rename` (`photo (1).jpg`) | `OverwritePolicy.ERROR` |
| Member failure | continue and report; exit `1` at the end | `OnError.STOP`: raise at the first one |

Where the library raises, the CLI finishes the run and reports. To get the library's
behaviour, pass `--overwrite error --stop-on-error`. For the CLI's in Python, pass
`overwrite="rename", on_error="continue"`.

### Passwords

`--password` puts the password on the command line, where other users of the machine can
see it in the process list (`ps`) and your shell may keep it in its history. Leave it out
if you can: when an archive needs a password and stdin is a terminal, `archivey` asks for
it without echoing. With no terminal, as in a pipe or a cron job, it does not ask, and the
encrypted members fail as if no password had been given.

### Notes

- Verbs are bare words (`x`, `list`); dash-prefixed forms like `-x` are not mode selectors.
- Shared flags (`--password`, `-v`, `--hide-progress`, `--track-io`) go before or after the
  verb; a verb's own flags (`-d`, `--policy`, `--exclude`, ...) go after a verb that
  owns them (`--policy` after `extract`, `--exclude` after `list`, `test` or `extract`).
- A pattern naming a directory selects the directory and everything under it, as `tar`
  does: `archivey extract a.zip docs` and `archivey extract a.zip docs/` both extract
  `docs/` and its contents, but not a file named `docs.txt`. On Windows, `docs\sub`
  works like `docs/sub`. `--exclude` matches the same way. This is a CLI rule: in the
  Python API, `members=` needs the exact stored name (`docs/` for the directory entry).
- A pattern that matches is a pattern, even if a local folder has the same name:
  `archivey x a.tar out` extracts the archive's `out/` subtree and gives no `-d` hint.
  The hint appears only when such a pattern matches nothing.
- A bare verb word is always a verb: `archivey x a.zip` extracts. Any path-qualified
  token is a path and gets listed, so a file named `x` is reached as `archivey ./x`
  (or `archivey dir/x`, `archivey /abs/x`, `archivey list x`).
- A file whose name starts with `-` is reached after `--`: `archivey -- -weird.zip`
  (or `archivey list -- -weird.zip`). Every word after `--` is a file or pattern, so
  `archivey -- list` opens a file named `list`.
- Without `-d`, `extract` may wrap the members in a folder named after the archive.
  That folder is always a new one: if anything already has that name (a folder, a file
  or a symlink), `extract` uses the next free `name (N)` instead, whatever `--overwrite`
  says. It never writes into an existing folder or through a link it did not choose. To
  re-extract into an existing folder, name it: `archivey x backup.zip -d backup
  --overwrite replace`.
- When the wrapper ends up holding a single entry, `extract` moves that entry up into the
  working directory, where `--overwrite` decides what happens to entries already there.
  It skips the move when the entry is a symlink, or when a symlink in it leaves it on
  the way to its target (or is absolute), because such a link would point somewhere
  else after the move. It also skips the move when part of the entry could not be
  listed, since it may hold such a link. It prints a line saying why the files stayed
  in the wrapper.
- `test` exits `1` when its summary reports members as not tested, or digests as not
  verified, even if none failed. A digest is not verified when the library could not
  check it (`DIGEST_UNVERIFIABLE` or `ENCRYPTED_MEMBER_UNVERIFIED`), for example a gzip
  trailer past the trailing-data scan bound; the warning logged for it says why.
- Member names, paths and messages are printed with control characters escaped, so a
  hostile name cannot rewrite the terminal line that reports it
  (see [Errors and diagnostics](errors-and-diagnostics.md#the-exception-tree)).
- Exit codes: `0` success, `1` operation failed or extract aborted on a member
  failure (`--stop-on-error`), `2` usage error, `3` extract
  **completed** with ≥1 safety-policy block and no member failure (safe members
  on disk; under CONTINUE or STOP), `130` interrupted by Ctrl-C (it prints
  `interrupted`; `130` is 128 + 2, the shell's code for SIGINT), `141` output
  pipe closed (see below). Codes `4` to `127` are reserved.
- When the pipe that stdout or stderr writes to closes (`archivey t big.zip 2>&1 |
  head -1`), every verb stops quietly with exit `141` (128 + 13, the shell's code
  for SIGPIPE; the same code on Windows). So do `--help` and a usage error whose
  message is lost. `141` says output was lost: for `test` the archive may not have
  been fully verified, so treat it as a result you do not have.
- When the archive path is a pipe (FIFO) or a character device, which can be read
  only once, every verb reads it in one forward pass. That works for TAR (also
  compressed) and single-file formats such as `.gz`. ZIP, 7z, RAR and ISO need to
  seek, so for those the verb exits `1` and says to copy the input to a regular file
  first. If the format's optional package is not installed, the verb reports that
  first.
- Such a path can be `/dev/stdin`, so on Linux and macOS an archive piped on stdin
  can be read: `cat a.tar | archivey list /dev/stdin`. On Linux, `/proc/self/fd/N`
  works the same way. The same format limits apply.
- `--salvage`, the `-` token for stdin, and `hash` / `create` / `convert` are reserved
  for later.
- An empty archive path or `--dest ""` is a usage error, not the current
  directory. Write `.` for the current directory.

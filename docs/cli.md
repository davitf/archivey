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
# warn on stderr; extract/test exit 1 when nothing matched (list warns but
# stays 0). A sole unmatched pattern that looks like a destination gets a -d hint.
archivey extract photos.zip -d out/ '*.py' --exclude '*_test.py'
archivey extract photos.zip --policy trusted -d /tmp/out
```

### Defaults that differ from the library

`archivey extract` uses the library's `policy=strict`, but two of its defaults differ
from `archivey.extract()`. They are what breaks a script ported from one to the other:

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
  If a symlink already has that name, `extract` never writes through it, whatever
  `--overwrite` says: it uses the next free `name (N)` instead. Pass `-d name` to
  extract through the link on purpose.
- `test` exits `1` when its summary reports members as not tested, even if none failed.
- Member names, paths and messages are printed with control characters escaped, so a
  hostile name cannot rewrite the terminal line that reports it
  (see [Errors and diagnostics](errors-and-diagnostics.md#the-exception-tree)).
- Exit codes: `0` success, `1` operation failed or extract aborted on a member
  failure (`--stop-on-error`), `2` usage error (argparse), `3` extract
  **completed** with ≥1 safety-policy block and no member failure (safe members
  on disk; under CONTINUE or STOP). Codes `≥4` are reserved.
- `--salvage`, stdin (`-`), and `hash` / `create` / `convert` are reserved for later.

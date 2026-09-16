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

### Notes

- Verbs are bare words (`x`, `list`); dash-prefixed forms like `-x` are not mode selectors.
- A file whose name is a verb word (e.g. `./x`) is reached with an explicit verb:
  `archivey list ./x`.
- Exit codes: `0` success, `1` operation failed or extract aborted on a member
  failure (`--stop-on-error`), `2` usage error (argparse), `3` extract
  **completed** with ≥1 safety-policy block and no member failure (safe members
  on disk; under CONTINUE or STOP). Codes `≥4` are reserved.
- `--salvage`, stdin (`-`), and `hash` / `create` / `convert` are reserved for later.

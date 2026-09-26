# TAR names decode as UTF-8 by default

## Why

Without `encoding=`, the TAR reader passed `encoding=None` to `tarfile.open`, so ustar
and GNU names used `tarfile.ENCODING`: UTF-8 on Windows, the process filesystem encoding
on POSIX. The same archive listed differently under a non-UTF-8 locale. A Latin-1 locale
turned the UTF-8 name `café.txt` into `cafÃ©.txt`; an ASCII one surrogate-escaped it.
The maintainer chose a fixed default: UTF-8, the encoding PAX already mandates and that
current tar writers use.

## What changes

- `format-tar`: without `encoding=`, ustar and GNU names (and `uname`, `gname`,
  `linkname`) decode as UTF-8 with `surrogateescape`, whatever the locale. A caller's
  `encoding=` still wins. PAX records are unchanged.

## Impact

Code: `internal/backends/tar_reader.py`. Docs: `docs/opening-and-listing.md`,
`dev-docs/formats/tar.md`. Tests: `tests/test_tar.py`.

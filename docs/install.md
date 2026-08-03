# Install and extras

Archivey's core installs with no dependencies at all and reads ZIP, TAR, directories
and the stdlib codecs. Everything else — RAR, ISO, the 7z extended codecs, the seek
accelerator — is an opt-in extra, and one format also needs a binary that no extra
can supply. This page is the whole answer to "what do I have to install?"

```bash
pip install archivey                 # zero-dep core: ZIP, TAR, gz/bz2/xz, directory, …
pip install archivey[recommended]    # every format and codec that installs everywhere
pip install archivey[seekable]       # + rapidgzip: gz/bz2 random access and speed
pip install archivey[all]            # both of the above
```

There are four extras and no per-format ones — member codecs are shared across
containers, so a format name would be the wrong thing to install. On a free-threaded
build use `archivey[free-threaded]`; see
[Platforms and threading](support-matrix.md#free-threaded-python-313t-and-later).

RAR **member data** also needs the system `unrar` binary on `PATH` (listing works without
it). See [Formats and extras](formats.md).

## What each format needs

The per-format detail lives on [Formats and extras](formats.md); the short version
is that every format except RAR is a pip install away, and RAR **member data** needs
the RARLAB `unrar` binary on `PATH` — not `unrar-free`, `unar`, or `7z`. Listing and
metadata work without it.

## Free-threaded builds

Use `archivey[free-threaded]` on 3.13t and later: it is the measured subset of extras
that leaves the GIL disabled. See
[Platforms and threading](support-matrix.md#free-threaded-python-313t-and-later).

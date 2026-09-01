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

RAR **member data** also needs the RARLAB `unrar` binary on `PATH` (listing works without
it). How to get that binary is below; format quirks live on
[Formats and extras](formats.md).

## What each format needs

The per-format detail lives on [Formats and extras](formats.md); the short version
is that every format except RAR is a pip install away, and RAR **member data** needs
the RARLAB `unrar` binary on `PATH` — not `unrar-free`. Listing and metadata work
without it.

## Getting RARLAB `unrar`

Listing a RAR works without it. Reading member bytes does not. Archivey looks for a
binary named `unrar` whose banner contains `UNRAR` plus `Alexander Roshal` or
`RARLAB` — run `unrar` with no arguments to check.

### Linux

```bash
sudo apt install unrar    # Debian/Ubuntu: non-free / multiverse, not the `unrar-free` package
```

Other distros ship an equivalently named package of RARLAB UnRAR.

### Windows

Download the official command-line UnRAR from
[RARLAB](https://www.rarlab.com/rar_add.htm) (the Windows UnRAR row) and put
`UnRAR.exe` on `PATH`.

### macOS

Homebrew core does not ship `unrar` (the UnRAR license is not open source). The
`rar` cask that used to install RARLAB's macOS binaries is disabled: those builds
are not Apple-notarized.

**If you use Homebrew**, an unofficial formula compiles UnRAR from RARLAB's
published source and puts `unrar` on `PATH`:

```bash
brew install gromgit/new-life/unrar
unrar   # confirm the banner names RARLAB / Alexander Roshal
```

The tap is not Homebrew core. If you would rather not use it, compile the source
yourself (needs a C++ compiler — Xcode command-line tools):

```bash
# Download "UnRAR source" (unrarsrc-*.tar.gz) from https://www.rarlab.com/rar_add.htm
tar xf unrarsrc-*.tar.gz
cd unrar
make
cp unrar "$(brew --prefix)/bin/"   # or another directory on PATH
```

RARLAB's current [RAR for macOS](https://www.rarlab.com/download.htm) packages
(ARM and Intel) also include an `unrar` binary. They are not Apple-notarized, so a
browser download may be blocked until you allow it in System Settings. The Homebrew
formula and the source compile above do not hit that prompt. Do not use the
"UnRAR for Mac OS X 64 bit" link on the add-ons page — that is a 2018 Intel
user contribution, not the current official binary.

## Free-threaded builds

Use `archivey[free-threaded]` on 3.13t and later: it is the measured subset of extras
that leaves the GIL disabled. See
[Platforms and threading](support-matrix.md#free-threaded-python-313t-and-later).

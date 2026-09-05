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

RAR **member data** also needs RARLAB `unrar` **7.0 or later** on `PATH` (listing works
without it). How to get that binary is below; format quirks live on
[Formats and extras](formats.md).

## What each format needs

The per-format detail lives on [Formats and extras](formats.md); the short version
is that every format except RAR is a pip install away, and RAR **member data** needs
RARLAB `unrar` **7.0 or later** on `PATH` — not `unrar-free`, `unar`, or `7z`. `rarfile`
accepts those last two as data backends; archivey does not: they either cannot read
solid RAR or fail silently on it. Listing and metadata work without it.

## Getting RARLAB `unrar`

Listing a RAR works without it. Reading member bytes does not. Archivey looks for a
binary named `unrar` whose banner contains `UNRAR` plus `Alexander Roshal` or
`RARLAB`, and whose version in that banner is **7.0 or later** (`UNRAR 7.00`,
`UNRAR 7.11`). Run `unrar` with no arguments to check. An older RARLAB build is
refused at identification, not per member.

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

**If you use Homebrew**, an unofficial formula installs RARLAB UnRAR:

```bash
brew install gromgit/new-life/unrar
unrar   # confirm UNRAR 7.0+ and RARLAB / Alexander Roshal
```

The tap is not Homebrew core. `brew install` trusts whatever formula the tap serves
at install time and again on every `brew upgrade` — there is no lockfile pin. Where
a bottle matches your macOS version, Homebrew pours a prebuilt binary from the
tap's GHCR; otherwise it compiles the formula's RARLAB source tarball. Both paths
skip Gatekeeper (they are a formula, not a cask).

If you do not want to trust the tap, compile UnRAR from RARLAB's source yourself
(needs a C++ compiler — Xcode command-line tools):

```bash
# Download "UnRAR source" (unrarsrc-*.tar.gz) from https://www.rarlab.com/rar_add.htm
tar xf unrarsrc-*.tar.gz
cd unrar
make
mkdir -p "$HOME/.local/bin"
cp unrar "$HOME/.local/bin/"
# add ~/.local/bin to PATH if it is not already there
```

RARLAB's current [RAR for macOS](https://www.rarlab.com/download.htm) packages
(ARM and Intel) also include an `unrar` binary. They are not Apple-notarized, so a
browser download may be blocked until you allow it in System Settings. Compiling
from source yourself does not hit that prompt. Do not use the
"UnRAR for Mac OS X 64 bit" link on the add-ons page — that is a 2018 Intel
user contribution, not the current official binary.

## Free-threaded builds

Use `archivey[free-threaded]` on 3.13t and later: it is the measured subset of extras
that leaves the GIL disabled. See
[Platforms and threading](support-matrix.md#free-threaded-python-313t-and-later).

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
the RARLAB `unrar` binary on `PATH` — not `unrar-free`, The Unarchiver (`unar`), or
7-Zip (`7z` / Homebrew `7zz`). Listing and metadata work without it.

## Getting RARLAB `unrar`

Archivey looks for a binary named `unrar` whose banner contains `UNRAR` plus
`Alexander Roshal` or `RARLAB`. Run `unrar` with no arguments to check. Common
lookalikes are not that binary:

| What you installed | What you got | Archivey RAR data |
| --- | --- | --- |
| Distro `unrar` / compiled RARLAB UnRAR | RARLAB `unrar` | yes |
| `brew install 7-zip` (aliases `sevenzip`, `7zip`) | `7zz`, built with `DISABLE_RAR_COMPRESS=1` | **no** — listing a RAR may work; solid / compressed members print `ERROR: Unsupported Method` |
| `brew install unar` | The Unarchiver | **no** — archivey will not call it (RAR5 solid + empty FILE crashes or returns empty, including extract-to-disk) |
| `unrar-free` | DFSG unrar | **no** |

### Linux

```bash
sudo apt install unrar    # Debian/Ubuntu: non-free / multiverse, not unrar-free
```

Other distros ship an equivalently named package of RARLAB UnRAR.

### Windows

Download UnRAR from [RARLAB](https://www.rarlab.com/rar_add.htm) and put `UnRAR.exe`
on `PATH`.

### macOS

Homebrew core dropped `unrar` in 2020 (the UnRAR license is not open source) and
disabled the `rar` cask on 2026-09-01 (official binaries fail Gatekeeper
notarization). There is no first-party brew formula that gives archivey a RAR
decompressor.

**Compile RARLAB UnRAR** from the source tarball on
[rarlab.com](https://www.rarlab.com/rar_add.htm):

```bash
# after unpacking unrarsrc-*.tar.gz
make
# copy the resulting `unrar` onto PATH, e.g. /usr/local/bin or $(brew --prefix)/bin
```

A checkout of this repo can do the same pin CI uses:

```bash
./scripts/install-rarlab-unrar.sh --dest "$(brew --prefix)/bin"
```

**Unofficial Homebrew taps** rebuild that same RARLAB source. As of 2026-09,
`brew install gromgit/new-life/unrar` fetches `unrarsrc-7.1.10.tar.gz` from
rarlab.com (checksummed in the formula) and installs a banner-compatible `unrar`.
The tap is a one-person resurrection of formulae Homebrew will not ship; archivey's
CI does not use it; bottles lag current macOS, so a new OS may compile from that
tarball instead of downloading a bottle. If you use it, run `unrar` with no
arguments and confirm the banner names RARLAB / Alexander Roshal.

## Free-threaded builds

Use `archivey[free-threaded]` on 3.13t and later: it is the measured subset of extras
that leaves the GIL disabled. See
[Platforms and threading](support-matrix.md#free-threaded-python-313t-and-later).

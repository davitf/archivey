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

RAR **member data** also needs RARLAB `unrar` or `rar` **6.0 or later** on `PATH`
(listing works without it). How to get that binary is below; format quirks live on
[Formats and extras](formats.md).

## What each format needs

The per-format detail lives on [Formats and extras](formats.md); the short version
is that every format except RAR is a pip install away, and RAR **member data** needs
RARLAB `unrar` or `rar` **6.0 or later** on `PATH` — not `unrar-free` or `7z`.
Listing and metadata work without it. `unar` can stand in for `unrar` when you ask for it
with `ArchiveyConfig(rar_decompressor="unar")`, but it reads fewer RAR archives; see
[Formats and extras](formats.md#rar).

What each install line adds, by what you type. [Formats and extras](formats.md) stays
the authority on what each format can do:

| You install | It adds |
| --- | --- |
| `archivey` (core) | ZIP, TAR, directories, 7z with its common codecs, and RAR listing and metadata. The stdlib codecs — gzip, bzip2, xz, LZMA, zlib — plus lzip and `.Z`, as single files and inside TAR. zstd too on Python 3.14 and later |
| `[recommended]` | ISO, `.lz4`, Brotli, zstd before Python 3.14; PPMd, Deflate64 and zstd members in ZIP and 7z, and Brotli members in 7z; AES encryption in 7z, WinZip AES ZIP and RAR headers; progress bars in the CLI |
| `[seekable]` | No new format. `seekable_members=True` streams over gzip, zlib, raw deflate and bzip2 use `rapidgzip` for random access |
| `[free-threaded]` | The part of `[recommended]` that keeps the GIL disabled: ISO, `.lz4`, zstd, CLI progress bars, and AES on 3.14t. Not PPMd, Deflate64, Brotli or `[seekable]` |
| `[all]` | `[recommended]` and `[seekable]` |

### Check what this install can read

`format_availability()` answers at runtime, so a program can check a format before it
promises a user that it works:

```python
from archivey import FormatSupport, format_availability

availability = format_availability("7z")
if availability.support is not FormatSupport.FULL:
    for component in availability.missing:
        print(component.name, component.install_hint)
```

- **`FULL`** — the format opens, and every optional codec it can use is installed.
- **`PARTIAL`** — the format opens and lists, and members in its common codecs read. A
  member that needs a missing codec raises `PackageNotInstalledError` when you read it.
  Only ZIP and 7z can be `PARTIAL`.
- **`NONE`** — the format cannot be opened. `open_archive()` raises
  `UnsupportedFormatError` naming the package: ISO without `pycdlib`, `.lz4` without
  `lz4`, `.tar.zst` without a zstd backend.

`missing` names each absent package with the `pip install` line that adds it, and is
empty when support is `FULL`. Two requirements are not counted: `cryptography`, which
only encrypted members need, and `unrar`, so RAR reports `FULL` whatever is on `PATH`.
`list_supported_formats()` returns every format that is `FULL` or `PARTIAL`. Whether a
format can be read from a pipe is a separate question, answered by `required_source` on
[Opening and listing](opening-and-listing.md).

## Getting RARLAB `unrar` or `rar`

Listing a RAR works without either. Reading member bytes does not. Archivey looks for
`unrar` first, then `rar`, and accepts the first whose banner is RARLAB **6.0 or later**:
`UNRAR 6.02` / `UNRAR 7.00` plus `Alexander Roshal` or `RARLAB`, or the writer's
`RAR 7.00 … Alexander Roshal` (often with `Trial version`). A `RAR` token is not taken
from inside `UNRAR`. Run the binary with no arguments to check. An older RARLAB build
is refused at identification, not per member. `unar`, `7z`, and `unrar-free` stay
refused even if they sit on `PATH` under another name. To use `unar`, select it with
`ArchiveyConfig(rar_decompressor="unar")` and install `unar` 1.10 or later
(`brew install unar` on macOS, `sudo apt install unar` on Debian and Ubuntu).

### Linux

```bash
sudo apt install unrar    # Debian/Ubuntu: non-free / multiverse, not the `unrar-free` package
unrar                     # confirm UNRAR 6.00+ and RARLAB / Alexander Roshal
```

Debian 12 and Ubuntu 22.04 ship 6.x, which is enough. Ubuntu 24.04 ships 7.00.
A 5.x package (some older releases) is refused — compile from RARLAB source
(same steps as the macOS recipe below) rather than trusting `apt` on those distros.

`apt install rar` puts RARLAB's trialware writer on `PATH` and only Suggests `unrar`.
That `rar` binary is accepted when `unrar` is missing (same 6.0 floor; confirm
`RAR 6.00+` and Alexander Roshal / RARLAB). Prefer `unrar` when both are installed.

Other distros ship an equivalently named package of RARLAB UnRAR.

### Windows

Download the official command-line UnRAR from
[RARLAB](https://www.rarlab.com/rar_add.htm) (the Windows UnRAR row) and put
`UnRAR.exe` on `PATH`. The WinRAR `Rar.exe` writer is looked up as `rar` the same
way as on Unix; its identification banner has not been measured here, so prefer
`UnRAR.exe` until you have confirmed a `RAR x.yy` / Alexander Roshal banner.

### macOS

Homebrew core does not ship `unrar` (the UnRAR license is not open source). The
`rar` cask that used to install RARLAB's macOS binaries is disabled: those builds
are not Apple-notarized.

**If you use Homebrew**, an unofficial formula installs RARLAB UnRAR:

```bash
brew install gromgit/new-life/unrar
unrar   # confirm UNRAR 6.0+ and RARLAB / Alexander Roshal
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

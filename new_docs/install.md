# Install

Archivey is on PyPI as `archivey`. It needs Python 3.11 or later and has no dependencies of its own.
Optional packages, grouped as extras, add the rest:

```bash
pip install archivey                 # no dependencies
pip install "archivey[recommended]"  # every format and compression method that installs everywhere
pip install "archivey[seekable]"     # faster seeking in large gzip and bzip2 files
pip install "archivey[all]"          # recommended and seekable together
```

If you use a free-threaded Python build, [Free-threaded Python](#free-threaded-python) has the extra
to use instead. There's no extra per format, because ZIP, 7z and TAR share their compression
methods.

| You install | It reads |
| --- | --- |
| `archivey` | ZIP, TAR and 7z with their common compression methods, and directories. gzip, bzip2, xz, LZMA, zlib, lzip and `.Z` files, alone or inside a TAR, and Zstandard too on Python 3.14 and later. RAR archives can be listed, and their uncompressed, unencrypted files read. |
| `[recommended]` adds | ISO images, and LZ4, Brotli and Zstandard files (Zstandard only before Python 3.14). PPMd, Deflate64 and Zstandard inside 7z and ZIP, and Brotli and LZ4 inside 7z. AES decryption for 7z, RAR and WinZip AES ZIP files. Progress bars in the command-line tool. |
| `[seekable]` adds | No new format. Faster seeking inside gzip, zlib and bzip2 data when you open with `seekable_members=True`. |

Compressed or encrypted RAR files also need an outside program, which no extra can install. The next
section covers it.

## RAR needs an outside program

RAR is a proprietary format, and there's no self-contained Python decoder for it, so archivey reads
compressed or encrypted RAR members with an outside program. It lists RAR archives, and reads stored
(uncompressed) members, by itself.

The preferred program is RARLAB's `unrar` (or `rar`), version 6.0 or later. Archivey also supports
`unar` 1.10 or later, from The Unarchiver, but it has some limitations. If you use `unar` with an
encrypted archive, the password goes on its command line, where other users on the same machine can
see it while it runs. The `unar` packages in Debian and Ubuntu have a bug that drops some files, so
archivey refuses those versions; install `unrar` there instead. If you want to choose the program
yourself, set `rar_decompressor` in `ArchiveyConfig` to `"unrar"`, `"unar"` or `"none"`.

### Getting `unrar`

On Debian and Ubuntu, `sudo apt install unrar` installs RARLAB's program. It's in the non-free
(Debian) or multiverse (Ubuntu) section, and the `unrar-free` package is a different program that
archivey doesn't use. Other Linux distributions have a package of RARLAB's UnRAR under a similar
name.

On Windows, download the command-line UnRAR from [RARLAB](https://www.rarlab.com/rar_add.htm) and
put `UnRAR.exe` on your `PATH`.

On macOS, Homebrew no longer ships `unrar`. You can install it from an unofficial Homebrew tap,
which builds or downloads RARLAB's program:

```bash
brew install gromgit/new-life/unrar
```

If you'd rather not trust that tap, download the UnRAR source from
[RARLAB](https://www.rarlab.com/rar_add.htm) and build it with Xcode's command-line tools:

```bash
tar xf unrarsrc-*.tar.gz
cd unrar
make
cp unrar ~/.local/bin/   # or any folder on your PATH
```

Running `unrar` with no arguments prints its version, so you can check it's 6.0 or later.

### Getting `unar`

On macOS, `brew install unar` installs a version archivey accepts. On Debian and Ubuntu, the
packaged `unar` has the bug described above, so either install `unrar` or build `unar` 1.10.8 from
[The Unarchiver's source](https://github.com/MacPaw/XADMaster).

## Checking what an install can read

`format_availability()` tells a program at run time whether a format works with what's installed:

```python
from archivey import FormatSupport, format_availability

availability = format_availability("7z")
if availability.support is not FormatSupport.FULL:
    for package in availability.missing:
        print(package.name, package.install_hint)
```

`FULL` means the format opens and every compression method it can use is installed. `PARTIAL` means
it opens and lists, but a member that needs something missing raises `PackageNotInstalledError` when
you read it; ZIP, 7z and RAR can be partial, RAR when neither `unrar` nor `unar` is usable. `NONE`
means it can't be opened at all, as with ISO without `pycdlib`. `list_supported_formats()` returns
every format that is `FULL` or `PARTIAL`.

`cryptography` isn't counted, because only encrypted members need it.

## Python versions and platforms

Archivey needs CPython 3.11 or later. Every change is tested on Linux with 3.11 to 3.14, and on
macOS and Windows with 3.11 and 3.14. Other platforms and interpreters such as PyPy aren't tested.
The core is pure Python, so it should run there, but some optional packages may not install. [How it
is built](how-it-is-built.md) has the full test setup.

## Free-threaded Python

On a free-threaded build (3.13t and later), importing a compiled package that doesn't declare
free-threading support turns the GIL back on for the rest of the process. If you use `pip install
"archivey[free-threaded]"`, you get only the optional packages that keep it off: ISO, LZ4 and
Zstandard support, and CLI progress bars, plus AES decryption from 3.14t. On 3.13t, `[recommended]`
doesn't install at all, because `cryptography` can't be installed there yet.

Other optional packages can still be installed. Archivey imports each one only when a file needs it,
so the GIL comes back the first time you read PPMd, Deflate64 or Brotli data, or seek in bzip2 data
with `seekable_members=True`. Free-threaded builds are tested on Linux only.

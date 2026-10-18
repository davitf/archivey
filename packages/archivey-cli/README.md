# archivey-cli

The [`archivey`](https://pypi.org/project/archivey/) command-line tool, installed together
with archivey's recommended optional packages: ISO images, LZ4, Brotli and Zstandard,
PPMd and Deflate64 inside 7z and ZIP, AES decryption, and progress bars.

```bash
uvx archivey-cli photos.zip          # run it once, without installing
pipx install archivey-cli            # or install the `archivey` command
archivey extract photos.zip
```

This package holds no code of its own. It depends on `archivey[recommended]` at the same
version and installs the `archivey` command (and `archivey-cli`, the same command under the
package's name, which is what `uvx archivey-cli` runs). If you use archivey as a library,
install `archivey` itself.

The [command-line guide](https://davitf.github.io/archivey/cli/) lists every command and
option. Compressed RAR archives also need an outside program, which no Python package can
install; the [install page](https://davitf.github.io/archivey/install/) explains how to get
one.

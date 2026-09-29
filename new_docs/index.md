# Archivey

Archivey reads ZIP, TAR, 7z, RAR, ISO and single compressed files from Python, with one
simple API. Untrusted archives extract safely by default. Archive formats hide some slow and
error-prone ways of reading, and archivey's defaults steer around them whenever possible.

## Install

Install the `archivey` package from PyPI with your usual tool (e.g.
`uv add "archivey[recommended]"` or `pip install "archivey[recommended]"`). Without
`recommended`, archivey has no dependencies and reads ZIP, TAR, 7z with its common compression
methods, and the single compressed files the standard library handles, such as gzip, bzip2 and
xz. The extra adds ISO, zstd, lz4 and Brotli, the rarer compression methods in 7z and ZIP, and
AES decryption, which most encrypted 7z and ZIP archives need.

RAR archives can be listed with nothing else installed. To read the files inside, you also
need RARLAB's `unrar` 6.0 or later, or `unar` 1.10 or later, which handles fewer RAR archives.
[Install](install.md) has the details.

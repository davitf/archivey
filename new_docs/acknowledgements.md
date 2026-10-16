# Acknowledgements

Archivey builds on the work of the people who designed these formats and wrote the libraries and
tools around them. This page credits the code archivey adapted, the projects it learned from and
checks itself against, and the packages it uses. If we missed a project that should be here, please
open an issue or a pull request.

## Code adapted from other projects

Two parts of archivey are adapted from other projects' code, and their licence notices are kept in
the files that hold it:

| Project | What archivey adapted | Licence |
| --- | --- | --- |
| [uncompresspy](https://github.com/kYwzor/uncompresspy) (Tiago Gomes) | The LZW decoder for `.Z` files ([notice](https://github.com/davitf/archivey/blob/main/src/archivey/internal/streams/codecs/unix_compress_decoder.py)) | BSD-3-Clause |
| [rarfile](https://github.com/markokr/rarfile) (Marko Kreen) | RAR3 password key derivation and the RAR3 Unicode filename decoder ([notice](https://github.com/davitf/archivey/blob/main/src/archivey/internal/backends/rar_parser.py)) | ISC |

The ARM64 branch filter used in 7z archives follows the algorithm in liblzma, from the
[xz](https://github.com/tukaani-project/xz) project, and is checked against 7-Zip's.

## Projects archivey learned from

[py7zr](https://github.com/miurahr/py7zr) (Hiroshi Miura and contributors) and rarfile were the main
references for how 7z and RAR archives behave in practice. Archivey's 7z and RAR readers were
checked against them at every step and take ideas from them, though not code (apart from the rarfile
pieces above). [7-Zip](https://www.7-zip.org/)'s and [RARLAB](https://www.rarlab.com/)'s UnRAR
sources were the references for the formats themselves, and WinZip's AES specification for encrypted
ZIP files.

The indexed and seekable decompressors ([python-xz](https://github.com/Rogdham/python-xz),
[indexed_bzip2](https://github.com/mxmlnkn/indexed_bzip2),
[indexed_gzip](https://github.com/pauldmccarthy/indexed_gzip),
[ratarmount](https://github.com/mxmlnkn/ratarmount) and
[indexed_zstd](https://github.com/martinellimarco/indexed_zstd)) shaped how archivey seeks inside
compressed streams, even though archivey doesn't depend on them.

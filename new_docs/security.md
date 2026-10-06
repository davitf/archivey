# Security

Archivey treats everything in an archive as untrusted: names, link targets, sizes and the
compressed data itself. Whatever an archive contains, extraction writes only inside the destination
folder, the limits cap how much it writes and how much memory decoding uses, and damaged data raises
an `ArchiveyError`. This page covers what archivey assumes about everything else, and where those
guarantees stop.

## What archivey relies on

Archivey checks each path in the destination right before writing to it, so a folder that another
program swaps for a link between two files is caught. A program that swaps a folder at exactly the
right moment could still redirect a write outside the destination, so extract into a folder that
only trusted programs can write to.

If the archive changes while it's open, reads may fail or return a mix of old and new data, but the
guarantees at the top of this page still hold.

A folder opened as a source gets extra checks, because its entries can be replaced by other kinds of
file. A read never follows a link out of the folder or hangs on a pipe put in a file's place. You
may still get content written after the folder was listed.

Archivey also trusts the optional packages and programs it uses, such as `unrar` for RAR data, not
to be malicious. It doesn't count on them to handle every input: when one fails, you get an
`ArchiveyError` like any other damaged data, except in the cases below.

## Where the guarantees stop

- **A crash in native code can take your process with it.** Decoders written in C, such as the
  standard library's zlib, bz2 and lzma, run inside your process. Archivey runs the decoders known to
  crash in a child process, or feeds them in a way that avoids the crash, but one nobody has found
  yet would still abort yours. Under a tight memory limit, such as a container's, the PPMd decoder
  some 7z archives use can also crash instead of raising an error.
- **Running out of memory raises `MemoryError`, not an `ArchiveyError`,** so it's never mistaken
  for a damaged archive. `DecoderLimits` caps the memory an archive can ask a decoder for, 2 GiB by
  default. Lower it if your process has less.
- **Nothing limits how long an operation takes.** The limits cap bytes, entries and
  password-hashing work, not time. Raising an exception from `on_progress` stops an extraction, but
  the callback only runs between chunks of written data, so a decompressor that's slow to produce
  the next chunk can't be interrupted. If you need a hard time limit, run archivey in a process you
  can stop.
- **Limits apply to each archive separately.** A small archive can hold archives that each expand
  up to the limit, and those can hold more, so the total grows with every level you extract. Be
  careful if you extract archives found inside other archives.
- **Accelerators are on by default when installed.** With the `seekable` extra, archivey uses
  rapidgzip for large gzip and DEFLATE data, and its bzip2 decoder, when you ask for seekable
  streams. Archivey's fuzz testing doesn't cover them yet, and the bzip2 one runs in your process.
  To avoid them, pass
  `config=ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF)`.
- **After a seek, a crafted `.xz` or `.lz` file can return wrong data without an error.** A seek
  trusts the file's own index, and the integrity check covers reading from start to end without
  seeking.
- **On Windows, a folder source is less protected.** Windows can't open a path without following
  links, so a subfolder swapped for a link during the listing can list files from outside the
  folder. Reading a file still checks it's the one listed.

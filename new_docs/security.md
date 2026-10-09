# Security

Archivey treats everything in an archive as untrusted: names, link targets, sizes and the
compressed data itself. Whatever an archive contains, extraction writes only inside the
destination folder, the limits cap how much an extraction writes and how much memory a decoder
may ask for, and damaged data raises an `ArchiveyError`. This page covers what archivey assumes
about everything else, and where those guarantees stop.

## What archivey relies on

Archivey checks each path in the destination right before writing to it, so a folder that another
program swaps for a link between two files is caught. A program that swaps a folder at exactly the
right moment could still redirect a write outside the destination, so the guarantee holds fully only
for a folder that untrusted programs can't write to.

If the archive changes while it's open, reads may fail or return a mix of old and new data, but the
guarantees at the top of this page still hold.

A folder opened as a source gets extra checks, because its entries can be replaced by other kinds of
file. A read never follows a link out of the folder or hangs on a pipe put in a file's place, and a
file that was replaced or changed size since the listing is refused. On a filesystem that doesn't
report file identities, such as some network and FUSE mounts, a file replaced by another of the same
size isn't caught, and the replacement can be a hard link to a file elsewhere on that filesystem.
Everywhere else, the most you get is content written after the folder was listed.

Archivey also trusts the optional packages and programs it uses, such as `unrar` for RAR data, not
to be malicious. It doesn't count on them to handle every input: when one fails, you get an
`ArchiveyError` like any other damaged data, except in the cases below.

## Where the guarantees stop

- **A crash in native code can take your process with it.** Decoders written in C, such as the
  standard library's zlib, bz2 and lzma, run inside your process. Archivey runs the decoders known
  to crash in a child process, or feeds them in a way that avoids the crash, but one nobody has
  found yet would still abort yours. Members compressed with PPMd, a method some 7z and ZIP archives
  use, are decoded in your process up to `decoder_limits.max_ppmd_in_process_input` (16 MiB by
  default) and in a child process above it. Under a tight memory limit, such as a container's, the
  in-process decoder can crash instead of raising an error.
- **Running out of memory raises `MemoryError`, not an `ArchiveyError`,** so it's never mistaken for
  a damaged archive. `DecoderLimits` caps the memory an archive can ask one decoder for, 2 GiB by
  default. Everything else needs memory on top of that: the rest of your program, archivey itself,
  other archives open at the same time, and other members read at once, each with its own decoder.
  If memory is tight, such as in a container or on a server that opens many archives, a lower limit
  makes an archive that asks for too much raise `ResourceLimitError` instead.
- **Nothing limits how long an operation takes.** The limits cap bytes, entries and
  password-hashing work, not time. Raising an exception from `on_progress` stops an extraction, but
  the callback only runs between chunks of written data, so a decompressor that's slow to produce
  the next chunk can't be interrupted. If you need a hard time limit, run archivey in a process you
  can stop.
- **Reading a member has no size limit.** `read()` returns the whole decompressed member as bytes in
  memory. A stream from `open()` decodes only as much as you ask for, so `stream.read(n)` holds
  about `n` bytes however large the member is, but a `stream.read()` with no size returns the rest
  of the member. `DecoderLimits` is the main limit that applies to a read. It caps the decoder's own
  memory, so the data you read takes additional memory on top. `ExtractionLimits` doesn't apply,
  because it covers only what `extract_all` writes to disk. A RAR archive opened from a file object
  rather than a path is copied to a temporary file the first time a member needs `unrar` or `unar`,
  and `spool_limits.max_bytes` caps that copy. When the archive records a member's size in
  `member.size`, archivey never decodes more than that, and a member holding more raises
  `CorruptionError`.
- **A streaming pass doesn't enforce the listing limits.** With `streaming=True` or
  `stream_members()`, `max_members` and `max_metadata_bytes` aren't checked, except that 7z, RAR and
  ISO archives still check the member count when they open.
- **Limits apply to each archive separately.** A small archive can hold archives that each expand
  up to the limit, and those can hold more, so the total grows with every level you extract. Be
  careful if you extract archives found inside other archives.
- **Accelerators are on by default for seekable streams.** If you open an archive with
  `seekable_members=True` and the `seekable` extra is installed, archivey reads bzip2 data, and
  DEFLATE data over 16 MiB compressed, through rapidgzip. DEFLATE is the compression in gzip files
  and in most ZIP members. rapidgzip marks places in the stream it can restart from as it reads, so
  a seek jumps to the nearest one instead of decompressing from the start. Archivey's fuzz testing
  doesn't cover them yet, and the bzip2 one runs in your process.
  To avoid them, pass
  `config=archivey.ArchiveyConfig(use_rapidgzip=archivey.AcceleratorMode.OFF,
  use_indexed_bzip2=archivey.AcceleratorMode.OFF)`.
- **After a seek, a crafted `.xz` or `.lz` file can return wrong data without an error.** A seek
  trusts the file's own index, and the integrity check covers reading from start to end without
  seeking.
- **On Windows, a folder source is less protected.** Windows can't open a path without following
  links, so a subfolder swapped for a link during the listing can list files from outside the
  folder. Reading a file still checks it's the one listed where the filesystem reports file
  identities. Where it doesn't, only the last part of the path is checked, after the file is opened,
  so a folder above it swapped for a link can serve a same-size file from outside.

## Hardening

RAR data is decompressed by an external program found on your `PATH`: `unrar` or `rar`, or `unar`
when neither is installed. Like the optional packages, it's [trusted not to be
malicious](#what-archivey-relies-on), and archivey checks only that it runs and that archivey works
with its version, which can be several years old. Older versions can have security bugs that later
ones fixed, so an up-to-date version is safer. `unar` receives the password on its command line,
where other users on the same machine can see it while it runs. If you set
`rar_decompressor=RarDecompressor.UNRAR` in `ArchiveyConfig`, archivey never runs `unar`. With
`RarDecompressor.NONE` it runs no external program at all, so stored, unencrypted RAR members still
read, and the rest raise `UnsupportedFeatureError`.

Every limit has a default that lets large legitimate archives through. A higher limit lets a
malicious archive use more memory, disk space or processing time before it's stopped, so if you know
what your archives look like, lower the limits to fit them. They're set in `ArchiveyConfig`, and
`extract_all` also takes `limits=` for one call.
[`scripts/measure_limit_costs.py`](https://github.com/davitf/archivey/blob/main/scripts/measure_limit_costs.py)
in the repository measures what each limit costs in time and memory on your machine.

| Setting | Default | What it caps | Rough cost |
|---|---|---|---|
| `extraction_limits.max_extracted_bytes` | 2 GiB | Bytes one extraction writes | Disk space, plus 5-10 ms per MiB written, 10-20 s at the default |
| `extraction_limits.max_entries` | 1,048,576 | Members one extraction writes | About 0.6 ms per file written, 10 minutes at the default |
| `extraction_limits.max_ratio` | 1000 | How much a member, or the whole archive, expands, checked once it has written 5 MiB | None of its own |
| `listing_limits.max_members` | 1,048,576 | Members an archive can list | About 25-50 µs and 1.5 KiB of memory per member, under a minute and 1.5 GiB at the default |
| `listing_limits.max_metadata_bytes` | 64 MiB | Text kept for names, comments and link targets | About 1.7 MiB of memory per MiB counted, 110 MiB at the default |
| `decoder_limits.max_decoder_memory` | 2 GiB | Memory an archive can ask a decoder for | The memory itself |
| `decoder_limits.max_key_derivation_rounds` | 2^27 | Password-hashing work per open archive | 0.2-0.7 µs per round, 30-100 s at the default |
| `decoder_limits.max_ppmd_in_process_input` | 16 MiB | Largest PPMd member decoded in your process rather than a child process | Up to twice the limit in memory while a PPMd member decodes, 32 MiB at the default |
| `spool_limits.max_bytes` | 1 GiB | Temporary disk space, used when archivey needs a copy of the source, such as a pipe | The disk space itself |

The costs were measured on one 2.8 GHz core.

A 7z or RAR archive made by the usual tools costs 2^19 rounds per password for 7z and about 2^16
for RAR. A crafted archive can ask for up to 2^24 rounds and repeat that work for each member, so
eight such members reach the default. Each password you try costs the same work again.

Going over a limit raises `ResourceLimitError`.

For archives from strangers, the default `policy="strict"` refuses or rewrites every kind of unsafe
member archivey knows of. The [policy
table](extracting.md#what-each-policy-does-with-unusual-members) on the Extracting page shows what
each policy does. Untrusted archives should be extracted into an empty folder that nothing else
uses, so you can check what came out before moving it anywhere else.

## Reporting a vulnerability

Please report security problems privately through
[GitHub's private vulnerability reporting](https://github.com/davitf/archivey/security/advisories/new),
not in a public issue. [SECURITY.md](https://github.com/davitf/archivey/blob/main/SECURITY.md) says
what's in scope and what happens after you report. The limits on this page are known and accepted.
If you find one that does more harm than this page describes, please report it.

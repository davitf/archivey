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

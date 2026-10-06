# Security

Archivey treats everything in an archive as untrusted: names, link targets, sizes and the
compressed data itself. Whatever an archive contains, extraction writes only inside the destination
folder, the limits cap how much it writes and how much memory decoding uses, and damaged data raises
an `ArchiveyError`. This page covers what archivey assumes about everything else, and where those
guarantees stop.

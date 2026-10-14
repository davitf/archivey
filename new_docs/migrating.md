# Migrating from other libraries

Archivey reads ZIP, TAR, 7z, RAR and ISO archives, and single compressed files, through one
interface, so code written for zipfile, tarfile or a `7z` subprocess becomes the same few calls.
This page gives the archivey version of each, then what behaves differently. The difference you'll
meet first is in extraction: archivey refuses some members that zipfile or tarfile would write, and
it stops an archive that expands past its limits.

# Where listing time goes (2026-10-16)

A profile of `open_archive(p).members()` on large archives of small members, taken while
speeding up the native ZIP reader's listing (PR 845). It records where the time goes after
that PR, so the next listing-performance change starts from it rather than from a fresh
profile. Wall times are from one Linux x86_64 4-core VM, CPython 3.12; the shares are what
to compare, not the absolute numbers.

The totals re-measure with
[`benchmarks/zip_listing_baseline.py`](../../benchmarks/zip_listing_baseline.py) and
[`benchmarks/tar_listing_baseline.py`](../../benchmarks/tar_listing_baseline.py). The
splits below came from one-off measurements that no script in the repo repeats, each the
fastest of 11-21 runs on the same 20 000-member archives (the TAR and 7z ones built from
the `zip -r` tree with `tar cf` and `7z a`):

- the ZIP parts by subtraction: the directory walk alone (`ZipReader._directory_walk`),
  `_to_member` over the walked entries, and a full listing whose member iterator
  returns members built beforehand, which leaves the shared per-member work;
- each helper's share inside `_to_member` by replacing it with a constant-returning stub
  and timing again, with `cProfile` to find the candidates;
- garbage collection as the listing time with `gc.enable()` minus with `gc.disable()`.

The stream open and close cost in the last section comes from an earlier investigation
of reading stored members (2026-10-12), not from these runs.

## ZIP: four parts

On a 20 000-member ZIP written by `zipfile.writestr` (no extra fields), listing takes
about 330 ms against `zipfile.infolist()`'s 55 ms. It splits into four parts:

- **about 40 ms walking the central directory**: reading and unpacking each record.
  `zipfile`'s whole 55 ms is roughly this part alone.
- **about 130-170 ms building `ArchiveMember`s** (`ZipReader._to_member`): name
  decoding and normalization, timestamps, owner, leftover extra fields, the hash dict and
  the dataclass itself. The extra field is split once per member and its fields shared by
  every reader of it.
- **about 60 ms of shared per-member work** that every format runs
  (`BaseArchiveReader._register_member`, the `ListingLimits` accounting, the diagnostic
  replay log of the member walk, the walk lock).
- **about 40 ms of garbage collection** over the objects each member keeps: the
  member, its `extra` bag, its hash dict, its datetimes and the locator.

The Info-ZIP shape (`zip -r` of a tree, each entry carrying the extended-timestamp and
new-Unix owner fields) costs about 200 ms more, almost all in parsing those two fields and
building the extra datetimes.

What is left is structural: the member objects themselves and the collection they cost.
Shaving more from the parse helpers gains little.

## Garbage collection by format

Collection time over the same tree listed in each format: ZIP about 40 ms, Info-ZIP ZIP
about 37, TAR about 10, 7z about 53. It scales with the objects kept per member, not with
the format's parse work. Fewer objects per member (a shared empty `extra`, no per-member
hash dict when there is no digest, fields built on first access) would cut it in every
format at once; that changes `ArchiveMember`'s mutation, `replace()` and equality, so it
needs its own design.

## 7z and TAR

The same tree as a 7z lists in about 290 ms, faster than ZIP: the 7z header stores each
property as one array for all members, so the parse is a few bulk reads rather than one
record per member, and there is no per-member extra field to split.

TAR lists in about 820 ms, and more than half of it is TAR's own header reading
(`TarWalker`): a seek and a 512-byte read per header through the shared stream view,
`parse_header_block` (about a quarter, with `parse_number` about a tenth), and resolving
the extended headers. Building the member is about 18%. `tarfile` takes about 18 µs per
member. Reading headers in larger chunks and a cheaper field split are the obvious levers.

## Per-member stream open and close

Separate from listing: opening and closing one member's stream costs about 25 µs
(the source lease, the finalizer, the registration). It dominates reading many tiny
members and is the same in every format.

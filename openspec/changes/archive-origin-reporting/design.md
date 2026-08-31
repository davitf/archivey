# Design — archive-origin-reporting

## The shape of the problem

Three formats can carry a prefix. Each resolves its origin differently, and none reports
the result. Measured on `main` (`056c429`), 4 224-byte `MZ` stub, `scan_for_magic`
instrumented:

| case | detection `payload_offset` | scans during `open_archive` | members |
| --- | --- | --- | --- |
| 7z SFX, auto-detect | 4 224 | 0 | 1 |
| 7z SFX, `format=SEVEN_Z` | — | 1 | 1 |
| RAR SFX, auto-detect | 4 224 | 0 | 1 |
| RAR SFX, `format=RAR` | — | 1 | 1 |
| ZIP SFX, auto-detect | 4 224 | 0 | 1 |
| ZIP SFX, `format=ZIP` | — | 0 | 1 |
| 7z / RAR plain | 0 | 0 | 1 |

Two things worth keeping straight, because both look like defects and are not:

**The detected path does not re-scan.** Both parsers open with a fast-path read —
`find_signature_offset` reads 6 bytes, `_find_sfx_header` reads 8 — and return offset 0
when the magic is already at the open position. When detection supplied the offset, the
view starts on the magic, so the fast path hits and the scan never runs. The scan is the
*forced-path fallback*, not redundant work layered on detection.

**The scan is not duplicated logic.** `scan_for_magic` and `SFX_MAX` are already shared
from `internal/sfx.py`, and `detection-prefix-workspace` (#273) just moved both call sites
onto one `MagicHit` return type. What is duplicated is the ~10-line wrapper around it, and
what is missing is a single place that owns the answer so it can be reported.

## Why `ArchiveInfo` and not the reader

`archive-data-model` already fixes the boundary: *"`ArchiveInfo` SHALL NOT carry runtime
diagnostics"*, with the scenario *"ArchiveInfo remains an open-time value"*. The payload
origin is exactly an open-time structural fact about the archive — established before the
first member is listed, immutable thereafter, and derived from the bytes rather than from
how the open went. It sits with `is_solid` and `is_multivolume`, not with diagnostics.

This is deliberately *not* the same surface as `detection-result-surface`. That change puts
the **detection result** on the reader — evidence, confidence, provenance, and for a
`format=` open it truthfully records `DECLARED_BY_CALLER` because detection did not run.
It therefore cannot answer "where did this archive start" on the forced path, because on
that path nothing detected anything: the *parser* found the origin. The two changes are
complementary and neither subsumes the other:

| question | answered by |
| --- | --- |
| why did archivey think this was a 7z? | `detection-result-surface` (reader's ledger) |
| what preceded the payload, and where did it start? | this change (`ArchiveInfo`) |

## The three-state field

`FormatInfo` needs two states (`prefix_kind is NONE` ⟺ `payload_offset == 0`). An opened
archive needs three, because of one real case:

- **forced `format=ZIP` on a prefixed ZIP.** `zip_reader` slices at `start_offset` when it
  has one; with `format=` there is no offset, and stdlib `zipfile` locates the central
  directory from the tail and self-adjusts past the stub on its own. The archive opens and
  lists correctly, and archivey never learns where the payload began.

`is_sfx: bool` cannot hold that, and `payload_offset: int` would have to encode it as `0` —
asserting "starts at byte zero" about a file that does not. Hence `int | None`, paired with
`PrefixKind.UNKNOWN`:

```python
prefix_kind is PrefixKind.NONE     <=>  payload_offset == 0
prefix_kind is PrefixKind.UNKNOWN  <=>  payload_offset is None
otherwise                                payload_offset > 0
```

The invariant is stated in the spec so it can be asserted in the conformance sweep rather
than left as a convention.

### Why not a second, simpler enum

A tempting alternative is `ArchiveInfo.is_prefixed: bool | None` and leaving `PrefixKind`
to detection. It fails the same way the bool does, one level up: `prefixed-archive-detection`
introduced the enum precisely because "offset > 0" bundles four different things — an
executable SFX stub, a shebang script wrapper, another file format the archive was appended
to, and an unclassifiable prefix. A caller sweeping a directory wants to open the installer
and skip the polyglot JPEG; that decision needs the kind, not the bit. Describing the same
property with a rich enum on one object and a coarse bool on another is the cross-surface
inconsistency §2 exists to prevent.

## One resolver

```python
# internal/sfx.py
def resolve_payload_origin(
    fp: BinaryIO,
    needles: Sequence[bytes | ScanNeedle],
    *,
    limit: int = SFX_MAX,
) -> MagicHit:
    """Origin of the payload relative to ``fp``'s current position.

    Fast path: the magic is already at the current position -> candidate_origin 0 and one
    short read. Otherwise a bounded forward scan. Raises CorruptionError past ``limit`` so
    a non-matching source fails loudly instead of opening as an empty archive. ``fp`` is
    restored to its starting position.
    """
```

Returning `MagicHit` rather than `int` is what lets RAR share it: `hit.needle` is
`RAR5_ID` or `RAR_ID`, which is the version, and the fast-path read already has those
bytes. Today `_find_sfx_header` returns `tuple[int, int]` purely to carry the version
alongside the offset; `MagicHit` carries it as data, so the wider signature disappears.

`find_signature_offset` becomes a one-line wrapper (it is exported and used by tests and
the fuzz harness) or is deleted with its callers updated — task 3.4 decides on the evidence
of what still imports it.

ZIP does not join the resolver: it has no scan and needs none. Its contribution is
reporting what it did — slice origin when given one, `UNKNOWN` otherwise.

## Reporting the origin back

`open_read` already takes `start_offset` *in*. The return direction needs a channel, and
the options differ mostly in blast radius:

| option | cost |
| --- | --- |
| A. reader attribute the base class reads when building `ArchiveInfo` | one attribute, set by the three prefix-capable backends, defaulted in the base |
| B. widen `open_read`'s return type | touches all seven backends for a fact four of them cannot produce |
| C. `ArchiveInfo.extra["sfx.offset"]` | no schema change, but stringly-typed and outside the field the user asked for |

**A**, with the default in `BaseArchiveReader` so the four `reject_start_offset` backends
are unaffected. C is rejected on §2 grounds: `extra` is for format-specific metadata
(`iso.namespace`), and a property three formats share and every format can be asked about
is not format-specific.

## Sequencing

Depends on `prefixed-archive-detection` for `PrefixKind`, and on `detection-prefix-workspace`
(landed, #273) for `MagicHit`.

If the order inverts and this lands first, the fallback is to ship the resolver
unification and `payload_offset: int | None` alone, and add `prefix_kind` with the other
change — `payload_offset` is well-defined without the enum, whereas the enum without
`payload_offset` is not worth having. This is a fallback, not the plan: shipping the field
in two halves means two public-shape changes where one would do.

Not sequenced against `detection-result-surface`: they touch different objects
(`ArchiveInfo` vs the reader's detection field) and neither reads the other's field.

## Open question

**Should forced-format ZIP learn its origin?** Once `prefixed-archive-detection` lands the
format-bounded tail probe (EOCD comment length is a `uint16`, so 65 535 + 22 bytes is a
hard ceiling), running it at open time for a forced `format=ZIP` would turn `UNKNOWN` into
a real offset. Against: it is new I/O to populate a metadata field, on the path a caller
chose to skip work; and `UNKNOWN` is an honest answer, not a wrong one. For: it removes the
last asymmetry between the two doors, and the bound is small and format-derived rather than
a constant we picked. Left to the maintainer; `UNKNOWN` is the v1 behaviour either way, so
deciding later costs nothing.

# Design — archive-origin-reporting

## The shape of the problem

Three formats can carry a prefix **on today's `main`**, each resolves its origin
differently, and none reports the result. Measured on `main` (`056c429`), 4 224-byte `MZ`
stub, `scan_for_magic` instrumented:

| case | detection `payload_offset` | scans during `open_archive` | members |
| --- | --- | --- | --- |
| 7z SFX, auto-detect | 4 224 | 0 | 1 |
| 7z SFX, `format=SEVEN_Z` | — | 1 | 1 |
| RAR SFX, auto-detect | 4 224 | 0 | 1 |
| RAR SFX, `format=RAR` | — | 1 | 1 |
| ZIP SFX, auto-detect | 4 224 | 0 | 1 |
| ZIP SFX, `format=ZIP` | — | 0 | 1 |
| 7z / RAR plain | 0 | 0 | 1 |

That census expires with `prefixed-archive-detection`, which makes a makeself `.run`
(`#!` + tar.gz) detect as `TAR_GZ` at the gzip offset. Everything in this section describes
the state the change starts from; the implementation must cover whatever that change can
hand a `start_offset` (task 3.6), not this list of three.

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

- **an empty ZIP behind a prefix.** `payload_offset` is defined as the position of the
  earliest local file header (`format-detection`); an archive with no members has none, so
  there is nothing to measure from and no supplied offset to fall back on. The archive
  opens and lists (as empty) correctly, and archivey cannot say where the payload began.

Forced `format=ZIP` on a *non-empty* prefixed ZIP is **not** such a case, though it looks
like one. Measured (stdlib `zipfile`, no archivey involvement):

| fixture | true origin | `min(header_offset)` |
| --- | --- | --- |
| `zipapp` `.pyz` | 23 | 23 |
| shebang + concatenated ZIP | 28 | 28 |
| `MZ` stub + ZIP | 128 | 128 |
| JPEG + appended ZIP | 2 004 | 2 004 |
| plain ZIP | 0 | 0 |
| `MZ` + **empty** ZIP | 502 | `None` |

`zipfile` applies its prefix adjustment to every entry's `header_offset` while reading the
central directory, so the smallest one *is* the origin in source coordinates — already
parsed, no extra read. The tempting wrong answer is `concat`, the adjustment itself: it is
`0` for a `zipapp` (member offsets written from byte 0), which is exactly the lie
`format-detection`'s earliest-local-header definition exists to prevent.

`is_sfx: bool` cannot hold that, and `payload_offset: int` would have to encode it as `0` —
asserting "starts at byte zero" about a file that does not. Hence `int | None`.

**The kind is spelled as absence, not as `UNKNOWN`.** `prefixed-archive-detection` already
defines `UNKNOWN` as *a prefix that matched no cue, reachable only via the opt-in exhaustive
scan* — which always has a positive offset. Reusing it for "we never established the origin"
would give one member two meanings and make `ArchiveInfo.UNKNOWN` disagree with
`FormatInfo.UNKNOWN`. So the field is `PrefixKind | None`:

```python
prefix_kind is PrefixKind.NONE     <=>  payload_offset == 0
prefix_kind is None                <=>  payload_offset is None
PrefixKind.UNKNOWN                      payload_offset > 0   (unclassified prefix)
otherwise                               payload_offset > 0
```

The invariant is stated in the spec so it can be asserted in the conformance sweep rather
than left as a convention. Note what it must *not* do: reject `UNKNOWN` with a positive
offset. That is a legitimate PAD value, and an invariant written as `UNKNOWN <=> None`
would fail on PAD's own exhaustive-scan fixtures.

### The kind is not derivable from the offset

`payload_offset` and `prefix_kind` answer different questions and are established by
different work. The offset falls out of opening the archive: 7z and RAR resolve it with the
bounded scan they already run, ZIP reads it off the central directory it already parsed. The
kind requires *looking at the prefix*, which only detection does.

So on a forced-`format=` open the offset is known and the kind is not. Three ways to fill
that gap were considered:

| option | cost | consequence |
| --- | --- | --- |
| sniff the first ~8 bytes for `MZ`/ELF/Mach-O/`#!` | one tiny read | covers SFX installers; a JPEG+ZIP still reports the wrong thing (`UNKNOWN`, where detection says `OTHER_FORMAT`) |
| run the full classifier, including detecting the prefix's own format | a detection pass over the prefix | doors always agree; the forced path pays for work the caller passed `format=` to skip |
| **report `prefix_kind is None`** | none | doors agree on the offset, and the kind is honestly absent rather than partly right |

The third is chosen. The cost is that the two absences become independent — `prefix_kind is
None` no longer implies `payload_offset is None` — which the spec states explicitly rather
than leaving as a trap. The alternative was a field that is *sometimes* a cheap guess, and a
partly-classified kind is worse than an absent one: a caller filtering "open installers,
skip polyglots" would get the polyglot wrong on one door and right on the other, silently.

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

`MagicHit.candidate_origin` is relative to `fp`'s position, which is **not** what
`ArchiveInfo.payload_offset` reports. The reader owns the conversion: the reported value is
`start_offset + hit.candidate_origin`, measured from the start of `source`. 7z already
computes exactly that sum (`self._origin = start_offset + find_signature_offset(probe)`);
RAR today does not (`self._origin = start_offset`, with the parser keeping its own
`sfx_offset`), so the two RAR doors currently hold the value in different variables — `N`
and `0` on auto-detect, `0` and `N` on forced. Reporting either component alone would make
the doors disagree; the sum is the same on both.

Returning `MagicHit` rather than `int` is what lets RAR share it: `hit.needle` is
`RAR5_ID` or `RAR_ID`, which is the version, and the fast-path read already has those
bytes. Today `_find_sfx_header` returns `tuple[int, int]` purely to carry the version
alongside the offset; `MagicHit` carries it as data, so the wider signature disappears.

`find_signature_offset` becomes a one-line wrapper (it is exported and used by tests and
the fuzz harness) or is deleted with its callers updated — task 2.5 decides on the evidence
of what still imports it.

ZIP does not join the resolver: it has no scan and needs none. It reports the supplied
slice origin when it has one, and otherwise the smallest `header_offset` in the central
directory it already parsed — no extra read, and correct for `zipapp` where `concat` is
not.

## Reporting the origin back

`open_read` already takes `start_offset` *in*. The return direction needs a channel, and
the options differ mostly in blast radius:

| option | cost |
| --- | --- |
| A. reader attribute the base class reads when building `ArchiveInfo` | one attribute, set by each prefix-capable backend, defaulted in the base |
| B. widen `open_read`'s return type | touches all seven backends for a fact four of them cannot produce |
| C. `ArchiveInfo.extra["sfx.offset"]` | no schema change, but stringly-typed and outside the field the user asked for |

**A**, with the default in `BaseArchiveReader` so backends that never receive a start
offset are unaffected. Derive that set from `reject_start_offset` at implementation time
rather than from today's four: PAD moves TAR and the single-file codecs out of it. C is rejected on §2 grounds: `extra` is for format-specific metadata
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

## Resolved: forced-format ZIP does learn its origin

An earlier draft left this open, on the assumption that a forced `format=ZIP` open would
need a tail probe to find the payload start. It does not — `min(header_offset)` over the
already-parsed central directory is the answer, measured above. No tail probe, no extra
read, and no asymmetry between the two doors except for an empty archive.

The remaining `None` case (empty ZIP) is genuinely unestablished rather than a cost
trade-off, so there is nothing left to decide here.

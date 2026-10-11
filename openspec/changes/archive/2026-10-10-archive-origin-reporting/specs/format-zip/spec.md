## ADDED Requirements

### Requirement: Report the ZIP payload origin

The ZIP reader SHALL report where the ZIP proper began inside the source, as
`ArchiveInfo.prefix_kind` / `ArchiveInfo.payload_offset` (`archive-data-model`), on both
the auto-detect and the forced-`format=` path.

ZIP resolves its origin differently from 7z and RAR and SHALL NOT be forced onto the
shared forward-scan resolver — it needs no scan at all. `payload_offset` SHALL be the
**position of the earliest local file header**, the same definition `format-detection`
uses, and it SHALL be taken from the central directory the reader already parsed:
`zipfile` applies its prefix adjustment to each entry's `header_offset` while reading the
central directory, so the smallest such offset is the payload origin in source
coordinates. Deriving it costs no additional read.

The reader SHALL NOT use `zipfile`'s internal offset adjustment (`concat`) for this: it is
`0` for a `zipapp`, a Spring Boot executable JAR, and any other prefixed ZIP whose member
offsets were written relative to the file rather than to the payload, so it would report
those as unprefixed. The earliest-local-header definition exists precisely to avoid that.

Where an origin is **supplied** (detection's `payload_offset`, or any other start offset),
the reader SHALL slice the source at that offset so the payload is the whole world, and
report that offset. Slicing rather than relying on the stdlib's own adjustment is required
for correctness independently of reporting: "adjust past whatever precedes the EOCD" is not
the same promise as "the archive starts here", and a stub carrying EOCD-shaped bytes would
otherwise move the answer.

An archive with **no members** has no local file header to measure from. The reader SHALL
report the origin as not established (`prefix_kind is None`, `payload_offset is None`)
rather than `0`, unless an origin was supplied. It SHALL NOT scan or tail-probe to resolve
that case: an unestablished origin is reported as unestablished, not searched for.

Where the origin **is** established and non-zero, the reader SHALL classify the prefix from
the source's leading bytes as `archive-reading` requires, so both open paths agree on
`prefix_kind`. That bounded read is not a search for the origin and is not covered by the
prohibition above.

#### Scenario: ZIP payload origin matrix

| Case | Expected |
| --- | --- |
| Plain ZIP, either path | `prefix_kind is NONE` and `payload_offset == 0` |
| Prefixed ZIP at offset N, origin supplied | Source sliced at N; `payload_offset == N` |
| Prefixed ZIP at offset N, forced `format=ZIP` | `payload_offset == N`, from the earliest local header; no extra read |
| `zipapp` `.pyz` (member offsets written from byte 0), forced `format=ZIP` | `payload_offset` is the shebang length, **not** `0` |
| JPEG + appended ZIP, forced `format=ZIP` | `payload_offset` is the JPEG length |
| Empty ZIP behind a prefix, forced `format=ZIP` | `prefix_kind is None` and `payload_offset is None` — no member to measure from |
| Forced `format=ZIP` on a `zipapp` `.pyz` | `prefix_kind is SCRIPT`, classified from the leading `#!` |
| Forced `format=ZIP` on a JPEG + appended ZIP | `prefix_kind is UNKNOWN` — no cue matched |
| Forced `format=ZIP` on a plain ZIP | `prefix_kind is NONE` — offset `0` means there is no prefix to classify |
| Forced `format=ZIP` on any ZIP | No scan or tail probe is performed to fill the fields |
| Stub carrying EOCD-shaped bytes, origin supplied | Slicing pins the answer to the supplied origin |

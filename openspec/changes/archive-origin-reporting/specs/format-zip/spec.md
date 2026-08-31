## ADDED Requirements

### Requirement: Report the ZIP payload origin

The ZIP reader SHALL report where the ZIP proper began inside the source, as
`ArchiveInfo.prefix_kind` / `ArchiveInfo.payload_offset` (`archive-data-model`).

ZIP resolves its origin differently from 7z and RAR and SHALL NOT be forced onto the
shared forward-scan resolver. A ZIP locates its central directory from the end of the
file, so two distinct situations arise:

- **The origin is supplied** (detection's `payload_offset`, or any other start offset).
  The reader slices the source at that offset so the payload is the whole world, and SHALL
  report that offset. Slicing rather than relying on the stdlib's own adjustment is
  required for correctness independently of reporting: "adjust past whatever precedes the
  EOCD" is not the same promise as "the archive starts here", and a stub carrying
  EOCD-shaped bytes would otherwise move the answer.
- **No origin is supplied** (forced `format=ZIP`). Stdlib `zipfile` locates the central
  directory past any prefix on its own and the archive opens correctly, but archivey does
  not learn where the payload began. The reader SHALL report `prefix_kind is UNKNOWN` and
  `payload_offset is None`.

The reader SHALL NOT perform a scan or a tail probe solely to populate these fields: an
unestablished origin is reported as unestablished, not searched for at open time.

#### Scenario: ZIP payload origin matrix

| Case | Expected |
| --- | --- |
| Plain ZIP, either open path | `prefix_kind is NONE` and `payload_offset == 0` |
| Prefixed ZIP at offset N, origin supplied | Source sliced at N; `payload_offset == N` |
| Prefixed ZIP, forced `format=ZIP` | Members listed correctly; `prefix_kind is UNKNOWN` and `payload_offset is None` |
| Forced `format=ZIP` on any ZIP | No additional scan or tail probe is performed to fill in the field |
| Stub carrying EOCD-shaped bytes, origin supplied | Slicing pins the answer to the supplied origin |

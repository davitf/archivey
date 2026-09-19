## MODIFIED Requirements

### Requirement: Reject multi-volume ZIP cleanly

The ZIP backend SHALL detect split/spanned ZIP archives and raise
`UnsupportedFeatureError` with a clear rejoin-first message instead of
mis-reading data or surfacing stdlib `BadZipFile`. Detection covers Info-ZIP
`.zNN` segment names, 7-Zip `.zip.NNN` segment names, non-zero classic EOCD disk
fields (treating `0xFFFF` as the ZIP64 sentinel, not a disk number), and ZIP64
locator `disks > 1`. Archivey joins multi-volume 7z/RAR elsewhere; stdlib
`zipfile` cannot resolve ZIP `(disk-number, offset-within-disk)` addressing, and
naive segment concatenation is unreliable. Proper support is deferred to a
future native ZIP reader.

#### Scenario: multi-volume ZIP matrix

`open_archive` on a split/spanned ZIP signal — Info-ZIP `.zNN` / final `.zip`
with non-zero EOCD disk fields, 7-Zip `.zip.NNN`, or ZIP64 locator `disks > 1` —
raises `UnsupportedFeatureError` (not `CorruptionError`, `FormatDetectionError`,
or a raw stdlib `BadZipFile`).

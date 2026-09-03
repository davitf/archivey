## RENAMED Requirements

- FROM: `### Requirement: Reject multi-volume ZIP cleanly`
- TO: `### Requirement: Join 7-Zip .zip.NNN sets; reject spanned ZIP cleanly`

## MODIFIED Requirements

### Requirement: Join 7-Zip .zip.NNN sets; reject spanned ZIP cleanly

7-Zip's `-v` byte-splits one finished single-disk ZIP into
`name.zip.001 … name.zip.00N`, exactly as it splits `name.7z.NNN`. With every
part `1..N` present beside the one named, `open_archive` SHALL concatenate them
and read the result as the ordinary ZIP it is, from any part, reporting
`ArchiveInfo.is_multivolume = True` and `ArchiveInfo.extra["zip.volume_count"] = N`
(not `ArchiveMember.extra`, which stays empty).
A gap in the numbering SHALL raise `TruncatedError`.

Every other split/spanned signal SHALL raise `UnsupportedFeatureError` with a
rejoin-first message rather than mis-read data or surface stdlib `BadZipFile`:
Info-ZIP `.zNN` segment names, non-zero classic EOCD disk fields (`0xFFFF` is the
ZIP64 sentinel, not a disk number), ZIP64 locator `disks > 1`, and a `.zip.NNN`
part whose siblings are not on disk. Info-ZIP `zip -s` writes a genuinely spanned
set addressed by `(disk, offset-within-disk)`, which stdlib `zipfile` cannot
resolve and which concatenation reconstructs only by coincidence; it stays
deferred to a native ZIP reader.

#### Scenario: multi-volume ZIP join and refusal

`open_archive` on a complete 7-Zip `.zip.NNN` set — named at any part — lists and
reads its members, including data spanning a part boundary. An incomplete such set
raises `TruncatedError`; every other split/spanned signal raises
`UnsupportedFeatureError`. Neither surfaces `CorruptionError`,
`FormatDetectionError`, or a raw stdlib `BadZipFile`.

## MODIFIED Requirements

### Requirement: Report ZIP format properties

The ZIP backend SHALL expose these properties for every opened ZIP archive:

| Property | Value |
| --- | --- |
| Backend dependency | `zipfile` (stdlib) for central-directory parsing; shared codec layer for member data |
| Listing cost | `ListingCost.INDEXED` — central directory read at open |
| Access cost | `AccessCost.DIRECT` — independent local file offsets |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only; no implicit buffering/spooling |
| Write support | No — writing is not shipped for any format (`PLAN.md` phase 9) |

`reader.get()` and other name lookups SHALL use the central-directory-derived
member map without extra archive I/O. Unencrypted ZIP member data SHALL decode
through the shared `compressed-streams` codec layer (bounded local-header parse
+ slice + method-id dispatch), including extended codecs when their backends are
installed: Deflate64 (method 9, via `[recommended]`/`inflate64`), ZSTD (method 93), PPMD
(method 98, ZIP PPMd8 framing via `[recommended]`/`pyppmd`). A missing optional backend
SHALL raise `PackageNotInstalledError`. An unknown/unsupported method id SHALL
raise `UnsupportedFeatureError`. `format_availability(ZIP)` SHALL report FULL
when every optional ZIP member codec is installed, else PARTIAL with the missing
components listed. Encrypted members (ZipCrypto / WinZip AE) MAY retain a
separate decryption path.

#### Scenario: ZIP property matrix

| Case | Expected |
| --- | --- |
| Open valid ZIP | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| `reader.get("some/member.txt")` | Satisfied from the in-memory central-directory name map; no additional archive I/O |
| Member uses unknown method id | Listing succeeds; reading raises `UnsupportedFeatureError` |
| Deflate64/Zstd/PPMd member, backend present | Decodes via the shared codec layer |
| Deflate64/Zstd/PPMd member, backend absent | `PackageNotInstalledError` |
| Optional ZIP codecs all installed | `format_availability(ZIP)` is FULL |

## REMOVED Requirements

### Requirement: Support streaming ZIP write via data descriptors

**Reason**: Described a writer that does not exist. There is no `archivey.create`,
no `ArchiveWriter`, and no writer module in `src/` — writing is `PLAN.md` phase 9 and
out of scope for now. The requirement and its scenario matrix (`add_stream` without
`size`, data-descriptor placeholders, readable by standard ZIP tools) were the only
place the data-descriptor write path was specified; the format-level fact that a ZIP
records late sizes in a data descriptor survives as read-path behaviour in
`dev-docs/formats/zip.md` §1. Restore the requirement with the writing phase rather
than editing around it.

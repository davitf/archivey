## MODIFIED Requirements

### Requirement: Report TAR format properties

The TAR backend SHALL expose these properties for every opened TAR archive:

| Format | `tarfile` mode | Listing cost | Access cost |
| --- | --- | --- | --- |
| Plain `.tar` | `r:` | `REQUIRES_SCANNING` | `DIRECT` |
| `.tar.gz` | `r:gz` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.bz2` | `r:bz2` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.xz` | `r:xz` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.zst` | zstd-backed equivalent | `REQUIRES_DECOMPRESSION` | `SOLID` |
| Auto-detected TAR | `r:*` where needed | Based on detected compression | Based on detected compression |

TAR is read-only here: writing is not shipped for any format (`PLAN.md` phase 9).
Compressed variants remain solid even when the source is seekable: random member
opens may re-decompress earlier bytes, while `stream_members()` is the preferred
progressive path.

#### Scenario: TAR property matrix

| Case | Expected |
| --- | --- |
| Open `TAR` | `cost.listing_cost=REQUIRES_SCANNING`; `cost.access_cost=DIRECT`; mode `r:` |
| Open `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` | `cost.listing_cost=REQUIRES_DECOMPRESSION`; `cost.access_cost=SOLID`; matching decompressor mode |
| Open `.tar.gz` | `tarfile` invoked with gzip mode |
| Open plain `.tar` | No decompression wrapper |

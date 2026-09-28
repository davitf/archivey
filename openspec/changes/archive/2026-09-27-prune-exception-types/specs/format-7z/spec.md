# format-7z — fewer exception types

## MODIFIED Requirements

### Requirement: Declare 7-Zip format properties

The 7-Zip backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency | None; native parser + shared stdlib-backed decoders |
| Write dependency | Not shipped in the current release (writing deferred; no 7z-writing extra) |
| Listing cost | O(1); native header parse, no file-data decompression |
| Access cost | `SOLID` when any folder packs multiple files; `DIRECT` for single-file folders |
| Supports write | No (read-only until the writing phase) |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open a seekable 7z for listing | Header is parsed natively; full member list is available; no third-party reader imports |
| Open from a non-seekable source | Open fails because 7z requires seek |
| Attempt 7z write | `UnsupportedFeatureError` (writing not implemented) |

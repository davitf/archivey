## ADDED Requirements

### Requirement: A single-file source is validated at open, not at first read

`open_archive` on a single-file compressor SHALL establish that the source is decodable as
the detected codec before returning a reader, and SHALL raise the translated error
(`CorruptionError` / `TruncatedError` / the codec's `PackageNotInstalledError`) from
`open_archive` rather than from a later read.

Validation depth is **one decoded byte, or a proof that one cannot exist**:

- the reader SHALL pull at least one byte from a codec stream over the source, because
  every stdlib codec validates its header on first read and not at construction;
- when `read` returns empty, that is a valid empty stream **only** if the source is at
  least the codec's minimum framing size. A codec whose decoder reads a zero-byte input as
  an empty stream — `unix-compress` does — SHALL reject a source shorter than its minimum
  header on length.

A genuinely valid empty stream SHALL still open and read as empty.

Non-seekable sources are out of scope: the opened stream is handed to the first
`open_member`, so a probe read there would consume a byte the caller expects. The
obligation applies to seekable sources, and the reader SHALL say so where it is stated.

#### Scenario: open-time validation matrix

| Source, named for each of the ten codecs | Expected |
| --- | --- |
| Valid stream | Opens; member reads its content |
| Valid **empty** stream | Opens; member reads `b""` |
| 40 000 zero bytes | `open_archive` raises `CorruptionError` (`TruncatedError` for LZMA Alone) |
| Zero-byte source, codec whose decoder rejects it | `open_archive` raises the translated error |
| Zero-byte source, `unix-compress` | `open_archive` raises on the minimum-header floor, not on a decode that cannot fail |
| Non-seekable source | Unchanged — validation deferred to the first read |

#### Scenario: the failure carries honest provenance

Moving the failure to open time changes **where** it is raised, not what it says about
provenance. This change does not make the error honest by itself; `detection-evidence-ledger`
does, by keying `format_unconfirmed` on the winning content-evidence class. The two compose:
the raise moves here, the flag becomes correct there, and the flag follows the decode failure
whichever call surfaces it.

| Case | Expected |
| --- | --- |
| `backup.gz` of zeros, format chosen by extension alone | Raises at `open_archive`, not on a later read |
| Same, `format_unconfirmed` on that exception | `False` until `detection-evidence-ledger` lands, `True` after — this change moves the raise, it does not rekey the flag |
| Listing is never reached for an undecodable source | The empty-listing diagnostic channel is not the reporting path for this class |
| A source that opens and fails later | Unchanged — still a read-time failure |

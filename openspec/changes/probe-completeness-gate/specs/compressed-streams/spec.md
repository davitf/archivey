## MODIFIED Requirements

### Requirement: One StreamCodec descriptor describes each codec

The system SHALL register each single-stream codec through one descriptor
containing its open function, exception translator, exact magic signatures,
optional content probe, file extensions, metadata extractor, and optional
dependency requirement (package/extra/tool, install hint, unlocked capability).
A codec SHALL be recognized by exact magic or content probe; there is no separate
weak-magic flag. Descriptor construction MUST NOT eagerly import optional codec
libraries.

A content probe SHALL receive the peeked prefix and MAY additionally receive the
**length of the source** when the caller knows it. The length is an optional input,
not a required one: a probe that does not need it SHALL be unaffected, and a probe that
uses it SHALL behave as before when it is absent — an unknown length means "cannot apply
the check", never "reject".

Two uses follow from those two inputs together, and neither requires a new one:

- **Framing.** A probe MAY test a declared framing length against the bytes the source can
  actually hold (see `format-detection`). Brotli tests a declared meta-block length against
  the source. **LZMA Alone** tests the weaker version of the same invariant: its 13-byte
  header is followed by range-coder payload, so a source no longer than the header cannot
  be an Alone stream — which was the whole of its measured real-world false-positive set,
  4 files in 40 000, each exactly 13 bytes.
- **Completeness.** When `source_length` does not exceed the prefix the probe was handed,
  the probe holds the whole source, and a decode ending in "needs more input" SHALL be a
  rejection (see `format-detection`). This is available to every probe without any
  interface change, and it is why the sentence above no longer names zlib as a probe that
  has no use for the length: completeness applies to every probe that decodes.

A probe MUST NOT use the source length to read beyond the prefix it was given, **except**
through a bounded read facility the caller supplies explicitly for that purpose. Where such
a facility exists, it SHALL be optional, absent by default, and bounded in both offset
range and number of reads; a probe that does not take it SHALL behave exactly as it does
today. This exception exists for the self-describing block chain in `format-detection`,
whose successor offsets frequently sit past a 4 KiB prefix, and it does not license
open-ended reading.

Registering a standalone codec descriptor SHALL make detection, the single-file
reader, and availability reporting work without edits elsewhere.

#### Scenario: descriptor matrix

| Case | Expected |
| --- | --- |
| New standalone codec descriptor is registered | `detect_format()`, `SingleFileBackend`, and availability reporting pick it up |
| Import `archivey` with no optional codec packages | No third-party codec import and no `ImportError` |
| Probe that ignores the source length | Same verdict with the length supplied or omitted |
| Probe that uses it, length known | May reject a prefix whose declared framing exceeds the source |
| Probe that uses it, length unknown (non-seekable source longer than the peek) | Falls back to the prefix-only verdict; MUST NOT reject on that basis |
| LZMA Alone probe, known length ≤ 13 | Reject — a source that is only the header carries no range-coder payload |
| LZMA Alone probe, length unknown | Today's prefix-only verdict |
| Any probe, `source_length <= len(prefix)`, decode wants more input | Reject — the whole source is visible and the stream does not terminate |
| Any probe, `source_length <= len(prefix)`, decode completes | Accept |
| Probe offered no bounded read facility | Behaves exactly as today; prefix is its whole world |
| Probe given one, reads past the prefix within its bound | Permitted, for the block-chain walk only |
| Probe given one, attempts an unbounded or unlimited-count read | Not permitted |

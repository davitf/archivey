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
not a required one: a probe that does not need it (zlib) SHALL be unaffected, and a
probe that uses it SHALL behave as before when it is absent — an unknown length means
"cannot apply the check", never "reject". This exists so a probe can test a declared
framing length against the bytes the source can actually hold (see `format-detection`);
a probe MUST NOT use it to read beyond the prefix it was given.

**Two probes use it**, for the same reason in two forms. Brotli tests a declared
meta-block length against the source. **LZMA Alone** tests the weaker version of the same
invariant: its 13-byte header is followed by range-coder payload, so a source no longer
than the header cannot be an Alone stream. That is the whole of its measured real-world
false-positive set — 4 files in 40 000, each exactly 13 bytes.

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

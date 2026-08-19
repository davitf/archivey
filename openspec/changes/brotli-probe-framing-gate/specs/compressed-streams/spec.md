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
not a required one: a probe that does not need it (zlib, LZMA Alone) SHALL be
unaffected, and a probe that uses it SHALL behave as before when it is absent — an
unknown length means "cannot apply the check", never "reject". This exists so a probe
can test a declared framing length against the bytes the source can actually hold
(see `format-detection`); a probe MUST NOT use it to read beyond the prefix it was
given.

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

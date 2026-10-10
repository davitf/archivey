# error-handling — native TAR reader delta

> Each MODIFIED block is the full requirement as it will read after the change.

## MODIFIED Requirements

### Requirement: Original cause and traceback are preserved centrally

The system SHALL preserve original decoding-library exceptions as `__cause__`
using `raise ... from exc`; libraries MUST NOT swallow the original traceback.
Type translation is per underlying library (for example `zipfile`, `lzma`,
`pycdlib`, `unrar`, crypto backend), not per format. The `ArchiveReader` base class
SHALL centrally stamp `source_format`, `archive_name`, and `member_name` on
propagating `ArchiveyError`s; backends do not hand-fill those fields.

No internal library exception SHALL escape unwrapped when it originates from a
decoding library taxonomy. A backend that serves raw bytes through no decoding
library (directory backend, stored member stream) SHALL NOT wrap plain `OSError`;
there is no codec taxonomy to translate.

#### Scenario: translation matrix

| Case | Expected |
| --- | --- |
| `zipfile.BadZipFile` is wrapped as `CorruptionError` | `__cause__` is the original `BadZipFile` |
| `traceback.print_exc()` after catching `ArchiveyError` | Chained output includes original exception and traceback |
| Decoder raises an unexpected taxonomy exception | Re-raised as an `ArchiveyError` subclass with `raise ... from exc` |
| Context-free translator raises while reading 7z member `"data/file.txt"` from `"/tmp/a.7z"` | Base reader stamps `SEVEN_Z`, archive name, and member name |

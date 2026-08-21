## ADDED Requirements

### Requirement: A ZIP may be preceded by arbitrary bytes

ZIP locates its central directory from the **end** of the file, and every offset it stores
is relative to the start of the ZIP data rather than to the start of the file. A leading
prefix is therefore not a special case to be tolerated — it is a property the format was
designed with, and it is why concatenating a stub with a ZIP has been a working idiom for
decades (`zipapp`, pex, shiv, Spring Boot executable JARs, self-extracting `.exe`
installers, and appended-ZIP polyglots).

The system SHALL locate the End of Central Directory by searching backwards from the end of
a seekable source, and SHALL read such an archive without requiring a forward scan, a
stub cue, or a caller-supplied offset. `payload_offset` SHALL report where the ZIP data
actually begins.

The search bound is **derived from the format, not chosen**: the EOCD comment length field
is a `uint16`, so the record cannot begin more than 65535 + 22 bytes before the end. The
system SHALL NOT search further back than that, and SHALL NOT make the bound configurable —
a larger value cannot find a valid EOCD, and a smaller one would reject legal archives.

A non-seekable source cannot be searched from the end. There the system SHALL fall back to
the ordinary tiers (forward scan when a prefix cue fires, then extension), and a prefixed
ZIP arriving as a pipe MAY therefore go undetected. That is a documented limitation of the
source, not a defect in the ZIP reader.

#### Scenario: prefixed ZIP matrix

| Case | Expected |
| --- | --- |
| `#!/usr/bin/env python3` + ZIP (a `zipapp` `.pyz`) | `ZIP`; all members listed and readable |
| `#!/bin/sh` launcher + ZIP (Spring Boot executable JAR) | `ZIP`; members readable |
| PE stub + ZIP (a self-extracting installer) | `ZIP`, `payload_offset` at the ZIP data |
| JPEG header + appended ZIP | `ZIP`; the polyglot opens as the archive it also is |
| Plain ZIP | Unchanged; `payload_offset == 0` |
| Prefixed ZIP on a non-seekable stream | Not found by the tail probe; falls through to the other tiers |
| A file whose last 64 KiB contain `PK\x05\x06` but no valid central directory | No ZIP claim |

## ADDED Requirements

### Requirement: Serve a solid pass's file copies from the source it decoded

A solid `stream_members()` pass, and the extraction built on it, SHALL keep the bytes of
each member that a later RAR5 file copy (`rar -oi`) in the pass reads, as its pipe passes
them, whether the caller reads that member or skips it, under `unrar` and `unar`. It
SHALL serve the copies from the kept bytes, so a kept source is decoded once for all its
copies. Kept bytes SHALL pass the source's digest check and declared size as the pipe's
own bytes do, and SHALL count toward extraction limits once per copy.

The pass SHALL keep up to 8 MiB of sources in memory. This constant is a tuning value: it
SHALL NOT refuse a read or change the bytes a read returns, and it is not a field of
`ArchiveyConfig`. A source past it SHALL go to a temporary file charged to
`SpoolLimits.max_bytes` (`archive-reading`). The file SHALL be created, and the source
charged, only when the source's first byte is decoded, so a pass that reads nothing writes
nothing and any copy of the archive source the pass needs is charged before the kept
source. The charge SHALL be given back when the pass ends. A source the limit has no room
for SHALL NOT be kept, and its copies SHALL read the source with a named open, as
`open()` does; that SHALL NOT raise `ResourceLimitError`. A source that the pass does not
emit (a `unar` refusal) SHALL fall back the same way.

#### Scenario: kept file-copy source matrix

| Case | Expected |
| --- | --- |
| Solid pass, source read, copies read | One decompressor run; every copy reads the source's bytes |
| Solid pass, source skipped, copies read | One decompressor run; the pass moves through the source and keeps it |
| `extract_all()` with a filter that drops the source | One decompressor run; the copies are written |
| Source over 8 MiB, no member read, stream source | Nothing is written: no temporary file, no copy of the archive |
| Source over the memory constant, within `SpoolLimits.max_bytes` | Kept in the temporary file; one decompressor run |
| Two passes on one reader, room for the source once | Each pass keeps it; one decompressor run per pass |
| Stream source, first member a source; the archive copy and the source each fit the limit, not both | The archive copy is made; the source is not kept; each copy decodes it again; no `ResourceLimitError` |
| `SpoolLimits.max_bytes=0`, path source, source over the memory constant | Not kept; each copy decodes it again; nothing is refused |
| Kept bytes that do not match the source's digest | `CorruptionError` on the copy's read |
| `max_extracted_bytes` below the total with copies | `ResourceLimitError`; each copy counts its bytes |

## ADDED Requirements

### Requirement: An accelerator preserves the error contract of the path it replaces

When an optional accelerator backend (today rapidgzip, including its bundled bzip2
decoder) replaces a codec's default decoder, the accelerated stream SHALL raise the same
class of translated error, on the same inputs, as the non-accelerated path. An accelerator
SHALL NOT convert a decode failure into a successful empty read.

Specifically, a decoder that ends a stream having produced no output, without consuming its
input and without reaching a valid end-of-stream marker, SHALL raise rather than report
end-of-file. Accelerator mode is a performance choice and SHALL NOT be observable as a
difference in whether a corrupt source raises.

#### Scenario: accelerator error parity

| Source | Accelerator `OFF` | Accelerator `AUTO` |
| --- | --- | --- |
| Valid bzip2 stream | Content | Content |
| Valid **empty** bzip2 stream | `b""` | `b""` |
| bzip2 source of 40 000 zero bytes | `CorruptionError` | `CorruptionError` — not `b""` |
| Zero-byte bzip2 source | `TruncatedError` | `TruncatedError` — not `b""` |
| Corrupt gzip source | `CorruptionError` | `CorruptionError` |

#### Scenario: the parity holds through the public reader

| Case | Expected |
| --- | --- |
| `open_archive(corrupt.bz2, seekable_members=True).read(member)` | Raises, matching `seekable_members=False` |
| A capability flag (`seekable_members`) | Never changes whether a corrupt source raises |

## MODIFIED Requirements

### Requirement: An accelerator preserves the error contract of the path it replaces

When an optional accelerator backend (today rapidgzip, including its bundled bzip2
decoder) replaces a codec's default decoder, the accelerated stream SHALL raise the same
class of translated error, on the same inputs, as the non-accelerated path. An accelerator
SHALL NOT convert a decode failure into a successful empty read.

Specifically, a decoder that ends a stream having produced no output, without consuming its
input and without reaching a valid end-of-stream marker, SHALL raise rather than report
end-of-file. Where the accelerator cannot report enough to tell that apart from a genuine
empty stream (rapidgzip's bundled bzip2 decoder cannot), the first empty read before any
output SHALL be re-decoded by the non-accelerated decoder over a fresh view of the
source, which raises or confirms the empty stream. A seek before that first read does not
bypass the check: on such a stream the accelerator clamps the seek to 0. Accelerator mode
is a performance choice and SHALL NOT be observable as a difference in whether a corrupt
source raises, with one exception. Where every byte of output is covered by checks the
data itself declares, those checks give the verdict, and an accelerator MAY differ from
the standard-library decoder on stream-boundary malformations they cannot see:

- for a container member that declares its size and CRC (a ZIP member, a 7z coder
  under a CRC-checked file), a second stream or
  trailing bytes inside the member's compressed data, which the accelerator MAY read as
  content where the standard-library decoder stops at the first stream's end; the
  declared size and CRC then decide, so output that matches both reads and output that
  breaks either raises;
- for a standalone multi-member gzip, a wrong ISIZE on a member other than the last,
  when every member's CRC-32 is still checked;
- for a standalone gzip followed by zero padding, a wrong ISIZE on the last member when
  the last four bytes of the file equal the length decoded and every member's CRC-32 is
  still checked.

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
| A capability flag (`seekable_members`) | Never changes whether a corrupt source raises, except through the accelerator on the stream-boundary malformations listed above |

#### Scenario: a ZIP member with a second stream inside its compressed data

| Member | Accelerator `OFF` | Accelerator `ON` |
| --- | --- | --- |
| Two DEFLATE or bzip2 streams; declared size and CRC cover both | `TruncatedError` (decoder stops after the first) | Both streams' content |
| Two streams; declared size and CRC cover both sizes but the CRC is the first stream's | `TruncatedError` | `CorruptionError` (CRC) |
| Two bzip2 streams; declared size and CRC cover the first | First stream's content | `CorruptionError` (output past the declared size) |

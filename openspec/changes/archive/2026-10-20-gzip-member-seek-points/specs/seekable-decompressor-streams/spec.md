## ADDED Requirements

### Requirement: gzip member starts are seek points

Each gzip member starts a fresh DEFLATE stream. On declared seek demand the standard
library engine SHALL record every member after the first that has output as a seek
point when a forward read reaches it. A stream opened without seek demand SHALL keep no
points.

#### Scenario: member point matrix

| Case | Expected |
| --- | --- |
| Multi-member gzip, read forward, then seek back | Resumes at the member start before the target |
| Plain one-member gzip | No points, no diagnostic; seeks decode from the start |
| Stream opened without seek demand | No walk, no points |

### Requirement: BGZF members are walked without decoding

A forward seek past the known points, a `SEEK_END`, or a size query SHALL walk a BGZF
file's members from the start: each member's `BC` extra subfield gives its size and its
ISIZE its output. The walk SHALL stop quietly at the first member without one, keeping
the points before it; the size is known only when it reaches the end of the file. The
sizes are trusted like the xz and lzip indexes (threat-model O17). A `.gzi` file is not
read.

#### Scenario: BGZF walk matrix

| Case | Expected |
| --- | --- |
| `seek(0, SEEK_END)` | Total from the walk; headers and trailers read, nothing decoded |
| Seek into block N | Decoding resumes at block N's start |
| BGZF blocks, then a plain member | Points for the blocks; size from decoding to the end |
| Walk over the seek-table cap | `SEEK_INDEX_DEGRADED` (scan `bgzf_blocks`, `SeekTableThinned`); exact total kept |

## MODIFIED Requirements

### Requirement: Gzip and bzip2 random access use rapidgzip only

The system SHALL use `rapidgzip` as the only optional accelerator library for
both gzip and bzip2: gzip through `rapidgzip.RapidgzipFile`, bzip2 through
`rapidgzip.IndexedBzip2File`. It MUST NOT import the standalone `indexed_bzip2`
package because loading both C++ cores in one process can corrupt the heap on
macOS. `use_indexed_bzip2` remains the bzip2 configuration flag but selects the
rapidgzip-bundled decoder.

When rapidgzip is unavailable or disabled, gzip and bzip2 SHALL use stdlib
decoders; backward seek is serviced by re-decompressing from the start, or for gzip
from the nearest member start it knows (next requirement), and MUST not degrade
silently.

The system MUST NOT run any rapidgzip decoder in the caller's process. The bzip2 decoder
SHALL run in a child process with the same rules as the DEFLATE family's (next
requirement): the child opens a path source itself, a stream source stays in the caller's
process, a child that dies on the data is a verdict the standard library takes over from,
and where no child can be started `AUTO` decodes with the stdlib backend, logging one
warning per process that names `use_indexed_bzip2=OFF`, and `ON` raises
`ResourceLimitError`. No crash of that decoder has been seen; it is isolated as a precaution,
because it comes from the same library as the DEFLATE decoder, whose crashes cannot always be
avoided. A maintainer script SHALL run each rapidgzip decoder
in-process on damaged input in a watched process and report every crash by its
signature, so that whether the isolation is still needed stays measurable.

#### Scenario: accelerator matrix

| Case | Expected |
| --- | --- |
| `use_rapidgzip` enabled and package installed | gzip seeks without full re-decompression |
| `use_indexed_bzip2` enabled and rapidgzip installed | bzip2 uses `rapidgzip.IndexedBzip2File` in a child process; the caller's process imports neither rapidgzip nor standalone `indexed_bzip2` |
| `use_indexed_bzip2=AUTO`, no child process can start | stdlib decoder; one warning per process naming `use_indexed_bzip2=OFF` |
| `use_indexed_bzip2=ON`, no child process can start | `ResourceLimitError`; never decoded in-process |
| rapidgzip absent or flag `OFF` | stdlib decoder; backward seek re-decompresses from the start, or from a known gzip member start, and warns when it re-decodes past the threshold |


## ADDED Requirements

### Requirement: Native decoders run where process_isolation puts them

The system SHALL decide where each native decoder runs from
`ArchiveyConfig.process_isolation`:

| Value | Runs in a child process | Runs in the caller's process |
| --- | --- | --- |
| `KNOWN_CRASHES` (default) | rapidgzip (every codec it decodes); a PPMd member over `max_ppmd_in_process_input` | every other decoder |
| `NATIVE_DECODERS` | every native decoder: `zlib`, `bz2`, `lzma` (all formats and filter chains), zstd, `lz4` (frame and block), `brotli`, `inflate64`, `pyppmd`, rapidgzip | nothing that decodes compressed data |
| `NONE` | nothing | every decoder |

The setting SHALL cover every decode of archive data the library runs, not only member
reads: format detection's content probes, password confirmation, the decodes a seek
index is built from and the checks that end a stream. The native code that does not
decode compressed data SHALL stay in the caller's process under every value: checksums
(`zlib.crc32`, `hashlib`) and AES decryption through `cryptography`. The external
programs (`unrar`, `unar`, `7z`) are separate processes under every value.

#### Scenario: where decoding runs

| Case | Expected |
| --- | --- |
| `.tar.xz` read under `NATIVE_DECODERS` | Same bytes as `KNOWN_CRASHES`; `lzma` decodes in a child process |
| `.br` stream under `NATIVE_DECODERS` on a free-threaded build | Same bytes; `brotli` never imported by the caller's process, the GIL stays disabled |
| `detect_format` on a `.zz` stream under `NATIVE_DECODERS` | The zlib content probe decodes in a child process |
| A WinZip AES member under `NATIVE_DECODERS` | AES decrypts in the caller's process; the codec after it decodes in a child |

### Requirement: Each open decoder has a child process of its own

A child process SHALL run one decoder at a time, and an open decoder SHALL have a child
of its own, so a crash is charged to the one decoder whose request was in flight and
threads decode in parallel. A child SHALL import only the standard library and the
library of the codec it is asked for, and MUST NOT import `archivey`; importing
`archivey` SHALL NOT import any codec library the caller's process would not import
without the setting. Under `NATIVE_DECODERS` the caller's process MUST NOT import
`pyppmd`, `inflate64`, `brotli` or `rapidgzip`.

A child whose decoder ended without an error from the child MAY be kept and reused for a
later decoder (any codec but PPMd), so a run of small members does not start a process
per member. A child MUST NOT be reused after it reported an error, died, or was
interrupted part-way through a reply, and a PPMd child MUST NOT be reused at all. Idle
children SHALL be ended when the interpreter exits, MUST NOT outlive it, and a process
created by `fork` MUST NOT use its parent's idle children.

#### Scenario: child per decoder and reuse

| Case | Expected |
| --- | --- |
| Two members of one archive read on two threads under `NATIVE_DECODERS` | Two children, decoding at the same time |
| A ZIP of 1,000 small deflate members read one after another under `NATIVE_DECODERS` | Children reused; far fewer than 1,000 started |
| A PPMd member under `NATIVE_DECODERS` | Child process at any size; the child is never reused |

### Requirement: A decoder child's end and errors keep the in-process verdicts

A decoder child that ends during a request SHALL be reported by how it ended, and every
later call on that decoder SHALL raise the same error: a fault signal (or its Windows
status) SHALL raise `CorruptionError`; SIGKILL SHALL raise `ResourceLimitError`; any other
end SHALL raise `ReadError`, which is not a verdict on the data. An exception the
decoder raises in the child SHALL reach the codec's translator as the same type it
would raise in-process, so a corrupt or truncated stream raises the same archivey
error under every value.

#### Scenario: child ends and errors

| Case | Expected |
| --- | --- |
| The decoder child crashes / is killed by SIGKILL / by SIGTERM during a read | `CorruptionError` / `ResourceLimitError` / `ReadError`; every later read raises it again |
| A corrupt deflate stream under `NATIVE_DECODERS` | `CorruptionError`, the same message as `KNOWN_CRASHES`; that child is not reused |

### Requirement: Start failures, NONE and time limits

Under `NATIVE_DECODERS`, where no child process can be started (a frozen application,
an interpreter without `sys.executable`, an archivey imported from a zip with no worker
script on disk, a spawn the operating system refuses), opening a decoder SHALL raise
`ResourceLimitError` naming the reason and `process_isolation`; it MUST NOT decode
in-process.

Under `NONE` no child process SHALL be started for decoding. PPMd SHALL decode
in-process at any size, a ZIP member with bit 3 set read from a non-seekable source
included. rapidgzip SHALL NOT be used: `use_rapidgzip=AUTO` and `use_indexed_bzip2=AUTO`
SHALL decode with the standard library without the warning that a child that cannot
start logs, and `ON` is refused at configuration (`archive-reading`, "Explicit
configuration object").

The system SHALL NOT bound the time a decoder takes under any value.

#### Scenario: start failure and NONE

| Case | Expected |
| --- | --- |
| `NATIVE_DECODERS`, no child process can be started | `ResourceLimitError` naming `process_isolation`; nothing decoded in-process |
| 7z PPMd member of 200 KB compressed, `max_ppmd_in_process_input=1024`, `NONE` | Decoded in-process |
| gzip stream opened seekable, `use_rapidgzip=AUTO`, `NONE` | Standard library decoder; no warning |

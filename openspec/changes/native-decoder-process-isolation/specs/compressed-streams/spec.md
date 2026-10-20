## ADDED Requirements

### Requirement: Native decoders run where process_isolation puts them

The system SHALL decide where each native decoder runs from
`ArchiveyConfig.process_isolation`:

| Value | Runs in a child process | Runs in the caller's process |
| --- | --- | --- |
| `KNOWN_CRASHES` (default) | rapidgzip (every codec it decodes); `pyppmd` (every PPMd member) | every other decoder |
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

A child MAY be kept and reused for a later decoder only after a clean finish: the
decoder ended with no error from the child and was closed normally, so a run of small
members does not start a process per member. A child MUST NOT be reused after it
reported an error, died, or was interrupted part-way through a reply. A PPMd child MUST
NOT be reused after a member that did not decode to its declared end, whose check value
did not match, or that was refused, nor after the decoder sent the one-symbol decode that
stops a worker waiting on input, since pyppmd can corrupt its memory there without
crashing at once. A child SHALL serve at most a fixed number of decoders (64) before it
is ended.

A crash or an error in a child that served earlier decoders MUST NOT be charged to the
decoder at once, since an earlier member may have left damage that surfaces only now:
the decoder SHALL re-run what it sent in a fresh child, once, and only a crash or error
there SHALL be its verdict. A decoder SHALL keep what it sent while in a reused child up
to 1 MiB of input, and past that SHALL move to a fresh child at once, so a re-run is
always possible. Output already returned to the caller SHALL be checked during the
re-run: when the fresh child's output for those requests differs, the call SHALL raise
`ReadError`, which is not a verdict on the data. A PPMd read abandoned before the
member's end SHALL retire its child, since closing it sends the one-symbol decode. Idle
children SHALL be ended when the interpreter exits, MUST NOT outlive it, and a process
created by `fork` MUST NOT use its parent's children: idle ones are dropped, closing an
inherited decoder leaves the parent's child alone, and a call on one SHALL raise
`ArchiveyUsageError`.

#### Scenario: child per decoder and reuse

| Case | Expected |
| --- | --- |
| Two members of one archive read on two threads under `NATIVE_DECODERS` | Two children, decoding at the same time |
| A ZIP of 1,000 small deflate members read one after another under `NATIVE_DECODERS` | Children reused; far fewer than 1,000 started |
| 1,000 small valid PPMd members read one after another under `KNOWN_CRASHES` | Each in a child process; children reused |
| A PPMd member that ends early (corrupt or cut) | Child process; that child is not reused |
| Member A leaves its reused child damaged without crashing; member B's decode then crashes in that child | B re-runs in a fresh child and reads correctly; nothing is charged to B |
| A corrupt member decoded in a reused child | Its error comes from the re-run in a fresh child, the same error as in-process |
| A reused child returned wrong bytes for part of a member, then crashed | The re-run's output differs from what was returned: `ReadError`, nothing charged to the data |
| A PPMd member whose read is stopped part-way, then closed | That child is not reused |

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

Under `NATIVE_DECODERS`, and for PPMd and rapidgzip `ON` under `KNOWN_CRASHES`, where no
child process can be started (a frozen application,
an interpreter without `sys.executable`, an archivey imported from a zip with no worker
script on disk, a spawn the operating system refuses), opening a decoder SHALL raise
`ResourceLimitError` naming the reason and `process_isolation`; it MUST NOT decode
in-process. rapidgzip `AUTO` under `KNOWN_CRASHES` keeps falling back to the standard
library with one warning.

Under `NONE` no child process SHALL be started for decoding. PPMd SHALL decode
in-process at any size, a ZIP member with bit 3 set read from a non-seekable source
included: as in a child, a member up to `max_ppmd_in_process_input` is held and handed
to pyppmd whole, and a larger one, or one whose size is not known, is fed to it in pieces,
which can crash the process on corrupt input. That risk is the one `NONE` accepts. rapidgzip SHALL NOT be used: `use_rapidgzip=AUTO` and `use_indexed_bzip2=AUTO`
SHALL decode with the standard library without the warning that a child that cannot
start logs, and `ON` is refused at configuration (`archive-reading`, "Explicit
configuration object").

The system SHALL NOT bound the time a decoder takes under any value.

#### Scenario: start failure and NONE

| Case | Expected |
| --- | --- |
| `NATIVE_DECODERS`, no child process can be started | `ResourceLimitError` naming `process_isolation`; nothing decoded in-process |
| 7z PPMd member of 1 KB, default config, no child process can be started | `ResourceLimitError` naming `process_isolation` on the first read |
| 7z PPMd member of 200 KB compressed, `max_ppmd_in_process_input=1024`, `NONE` | Decoded in-process |
| gzip stream opened seekable, `use_rapidgzip=AUTO`, `NONE` | Standard library decoder; no warning |

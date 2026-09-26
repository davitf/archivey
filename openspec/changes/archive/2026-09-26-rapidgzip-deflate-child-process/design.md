# Design — rapidgzip DEFLATE family in a child process

## Measurements (before the design)

`scripts/bench_rapidgzip_child.py`, Linux, 4 CPUs, Python 3.11, rapidgzip 0.16.0. A 100 MB
text-like payload, 44.4 MB as gzip. Best of 5, three runs; the range is across runs.

| What | Time |
| --- | --- |
| Spawn `python -P`, no imports, to first reply | 11 ms |
| Spawn `python -P` + `import rapidgzip`, to first reply | 25 ms |
| Full read, stdlib `zlib` | 473–492 ms (~205 MB/s) |
| Full read, archivey stdlib engine (`GzipDecompressorStream`) | 506–540 ms |
| Full read, rapidgzip in-process, `parallelization=0` | 234–261 ms (~400 MB/s) |
| Full read, child over a pipe, 64 KiB / 1 MiB / 4 MiB round trips | 302–325 / 302–343 / 353–369 ms |
| Full read, child over shared memory, 4 MiB | 321–331 ms |
| seek + `read(4096)`, in-process / child, index built | 2.5 µs / 66–79 µs |

A minimal worker that only answers `read(n)` measured 282 ms against 262 ms in-process in a
separate run; neither a 1 MiB pipe buffer (`F_SETPIPE_SZ`) nor decoding the next chunk ahead
in the child changed that. Shared memory was no faster than the pipe, so the pipe it is.

The child costs a fixed start (~25 ms spawn and import, plus the open) and ~70 µs per round
trip. A full read stays about 1.5× faster than the stdlib engine.

Through archivey (`open_codec_stream(GZIP)`, `use_rapidgzip=ON`), in-process against child:

| What | In-process | Child |
| --- | --- | --- |
| Full read of the 100 MB payload | 246 ms | 332 ms |
| Open + `read(1)` | 66 ms | 117 ms |
| seek + `read(4096)` (a backward seek also asks the rewind probe) | 14 µs | 164–183 µs |

## Decisions

- **One child per stream, started at open.** It builds rapidgzip's index while the caller
  reads, so the first backward seek is as cheap as in-process. Starting it lazily at the first
  backward seek would save the start on streams read once, but that seek would then decode from
  the start; that is the rejected stdlib-first design's cost without its proof pass.
- **The parent serves a stream source.** The worker gives rapidgzip a file object whose
  `read`/`seek`/`tell` send a request to the parent; the parent answers from the caller's
  stream. An exception there is parked and raised to the caller as itself when the current
  call ends, so the `_TrappingSource` contract (a caller-source exception reaches the caller
  unchanged) holds, and the child only sees an end of input. An interrupt (`KeyboardInterrupt`)
  propagates at once; the half-finished exchange cannot be resumed, so the child is killed and
  the stream raises `ArchiveyUsageError` from then on.
- **rapidgzip's threads read ahead.** Source requests can arrive before the reply to any
  request, including while the parent is idle. A pump thread in the child sorts parent frames
  into requests and source answers; the parent serves source requests whenever it waits for a
  reply, so a background read blocks only until the next call.
- **Death classification** follows the PPMd child (`is_crash`): SIGSEGV/SIGABRT/SIGBUS/SIGILL/
  SIGFPE or the Windows NTSTATUS for them (and the MSVC `abort()` status 3, which the worker
  never uses itself) is a crash, SIGKILL is the OOM killer, anything else came from outside.
  stderr goes to a temporary file, not a pipe (no drain thread, no deadlock), and after a
  death it is scanned from the start, a line at a time (capped at 64 MiB): rapidgzip's abort
  message on an early end makes it `TruncatedError`. Not a window at either end: with
  faulthandler on (`PYTHONFAULTHANDLER`, which the child inherits), Python 3.14 writes
  ~6.5 KiB of thread and C stacks after the message, and a 4 KiB tail missed it on CI;
  output written before the abort would push it out of a window at the start the same way.
  A line over 64 KiB is read in pieces, each carrying the end of the one before, so the
  message is found across a split. The helpers are in `child_exit.py`, shared with the PPMd
  child.
- **Read-ahead in the parent.** Measured through a `.tar.gz`, whose reader reads in small
  pieces, a round trip per piece was the cost. After the first read that follows a seek, a
  read asks the child for at least 64 KiB, doubling to 1 MiB while reads stay sequential; a
  seek inside what is buffered costs no round trip. The position is kept in the parent, so
  `tell` needs none either.
- **Child-reported errors are marked.** They come back as the same built-in type, so the
  existing translator applies. Because they are marked, any `RuntimeError` rapidgzip raised
  translates to `CorruptionError`, while a `RuntimeError` from the caller's own source stays
  itself.
- **No escape hatch in new config.** rapidgzip has no safe in-process mode for these codecs,
  so there is nothing like `max_ppmd_in_process_input` to size. A caller that wants no child
  process sets `use_rapidgzip=OFF`.
- **Where no child can run:** `AUTO` decodes with the stdlib backend, whether that is known
  before the open (a frozen or embedded interpreter, a zip import with no worker script on
  disk) or only at it (a spawn or temporary file the operating system refuses, a child that
  cannot import rapidgzip). The stdlib backend is correct and raises `TruncatedError` on a
  cut stream the same way, only slower, and `AUTO` already uses it for every stream under
  the threshold. The PPMd child raises instead because its alternative is decoding
  in-process, which is the hazard; here it is not. Each of those failures comes before the
  child reads the source, so the stdlib decoder starts where the source was. `ON` raises
  `ResourceLimitError`. Both name the reason, which `rapidgzip_child_unavailable_reason`
  gives for the cases known before the open; the `AUTO` warning is logged by the codec's
  `open`, not by the backend resolution, which opens nothing.
- **bzip2 stays in-process.** It never aborted in 110 random cuts, and its seek index is the
  reason to use it at all.

## The `AUTO` threshold moves to 16 MiB

Each accelerated stream now costs about 45 ms more to start and open. A full read through
the child saves about 3.4 ms per MB of compressed input over the stdlib (about 10.9 ms/MB
stdlib against 7.5 ms/MB child, on the 44 MB benchmark file). So the child breaks even near
13 MB compressed, and at the old 1 MiB threshold (~5 ms of decode) `AUTO` made a stream
slower. `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` is now 16 MiB, past the break-even point.
A caller that seeks backward often gains from rapidgzip's index sooner and can set `ON`.

`ON` is explicit, so it pays the start whatever the size. The realistic harness shows it:
`targz_read_all_accel_on` 19 → 96 ms (one stream, 16 MiB unpacked), and
`zip_read_all_accel_on`, which forces `ON` over 64 members of 256 KiB, 87 ms → 2.3 s. Those
cases stay as they are. Not done: keeping one idle child for reuse (a process that outlives
the stream, which the leak oracle and users would see).

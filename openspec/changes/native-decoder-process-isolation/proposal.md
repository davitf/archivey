# Native decoders in child processes, behind one setting

## Why

archivey runs two native decoders in child processes today, because each has crashed on
hostile input in a way no `try` can catch: rapidgzip (it aborts on a DEFLATE stream that
ends early) and pyppmd above `max_ppmd_in_process_input`. Every other native decoder
(`zlib`, `bz2`, `lzma`, zstd, `lz4`, `brotli`, `inflate64`, small PPMd members) runs in
the caller's process. A caller who reads untrusted archives and does not want any native
fault to take their process down has no way to ask for more, and a caller who trusts their
files, or runs where no process can be started, has no way to ask for less.

The two existing children also duplicate their protocol plumbing (exact reads, the error
payload and its rebuild table, core-dump suppression, the died and interrupted states),
and a third, fourth or eighth isolated codec would copy it again.

## What Changes

- New public enum `ProcessIsolation` and field `ArchiveyConfig.process_isolation`:
  `KNOWN_CRASHES` (default: today's behaviour), `NATIVE_DECODERS` (every native decoder
  in a child process, detection probes included) and `NONE` (everything in-process; for
  trusted files, tests, and environments that cannot spawn processes; discouraged).
- One generic decoder worker, keyed by codec name, imports only the standard library and
  the one codec library it is asked for. A small pool of idle children is reused, one
  child per open decoder. The PPMd child moves onto it.
- rapidgzip keeps its own message set (it serves source reads and index queries), but
  moves onto the shared framing, error payload and helpers.
- Under `NONE`, PPMd decodes in-process at any size, and rapidgzip is not used: `AUTO`
  falls back to the standard library and `ON` is refused at configuration.
- AES and checksums stay in the caller's process. No time limit per call; the docs show
  how to run the CLI in a subprocess to get a hard limit on a whole operation.

## Capabilities

### Modified Capabilities

- `compressed-streams`: new requirement on where native decoders run.
- `archive-reading`: `ArchiveyConfig` gains the field; `max_ppmd_in_process_input`
  applies under `KNOWN_CRASHES` only.
- `format-zip`: a bit-3 PPMd member from a pipe decodes in-process under `NONE`.

## Impact

- Public: one enum, one config field. Default behaviour unchanged.
- Code: new `codecs/decoder_worker.py`, `streams/decoder_child.py`, `codecs/isolated.py`;
  `ppmd_worker.py` removed; `ppmd_child.py`, `rapidgzip_child.py`, `rapidgzip_worker.py`
  slimmed; every native decoder construction site takes its library from one selector.
- Docs: security page section on isolation and the CLI time-limit recipe.

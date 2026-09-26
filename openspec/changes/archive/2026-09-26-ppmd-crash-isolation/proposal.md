# PPMd: never let pyppmd crash the process

## Why

pyppmd 1.3.1 (and 1.2.0) segfaults when asked to decode again after a corrupt stream has
ended early. Random bytes reach that state: a wrong password on a 7z PPMd folder, or a
crafted 7z or ZIP method-98 member. The caller cannot tell that end apart from a valid
stream waiting for input, because pyppmd's `eof` also rises (and never clears) on valid
streams at a feed boundary inside a run of zero bytes. Details and measurements are in
`dev-docs/known-issues.md` §"Random input: decode after an early end segfaults".

## What Changes

- `PpmdDecoder` holds a member's compressed input and hands it to pyppmd in one piece,
  once it has the whole member or compressed EOF. With nothing more to wait for, any
  short return is the end and pyppmd is never asked to decode past it. Output still
  streams in bounded calls.
- A member larger than the new `DecoderLimits.max_ppmd_in_process_input` (default
  16 MiB of compressed input) decodes in a child Python process running a standalone
  worker script, where a native crash becomes `CorruptionError`.
- Where no child process can be started (a frozen application), such a member raises
  `ResourceLimitError`. `None` (as in `DecoderLimits.UNLIMITED`) decodes every member
  in-process.
- Password confirmation reads at most 1 MiB, so it always stays in-process.

## Impact

- Public: one new `DecoderLimits` field.
- Code: `internal/streams/decompress.py`, `decompressor_stream.py` (a decoder may keep
  draining after `flush`), `codecs.py`, new `ppmd_child.py` and `ppmd_worker.py`.
- Tests: `tests/test_ppmd_crash_isolation.py`.

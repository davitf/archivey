## Why

The `cli` spec already requires that archive-derived text not reach a terminal carrying
control sequences, and names summaries as a print site. Two print paths did not follow it
(review hub findings S24-K1 and S24-K7):

- `archivey extract`'s closing summary and the hoist's `moved to` line print the sole
  top-level entry's name, which is the member's own name, raw. A root named
  `ev\x1b[2Kil\rSUCCESS` leaves the operator reading `SUCCESS/` as the last line.
- `archivey info` printed every field through a bare f-string. A ZIP comment carrying
  `\x1b[2K\r` chose what the operator read, and on **stdout**, so `2>/dev/null` hid
  nothing.

The wrapper directory is named after the archive file and was printed raw too. That
name is not archive content, but a nested archive gets its filename from the outer one,
and a shell loop over `*.tar` passes it on without anyone typing it.

## What Changes

- `info` escapes every value it prints, not only the comment. The archive path is
  rendered `/`-separated first. The `open:` line prints the exception through
  `format_error_detail`, which does not escape archivey's own exceptions a second time.
- `extract` escapes the summary destination, the hoist's `moved to` / `removed wrapper`
  lines, the `extracting into` wrapper, and the `hoist stopped` / `files left in` lines.
- A new `escape_path` helper in `cli/format.py` renders a path `/`-separated and then
  escapes it. The `renamed:`, `skipped:` and `Destination already exists:` lines use it
  in place of escaping the native path, which doubled every separator on Windows.
- The spec gains scenario rows for the summary, the wrapper and `info`, and says that
  non-member paths are `/`-separated before escaping too.

## Impact

Display only. No bytes on disk, member name, or `ExtractionResult` value changes. On
POSIX an ordinary name prints exactly as before; on Windows the wrapper, summary and
`info` path now print with `/` separators.

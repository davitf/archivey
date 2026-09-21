# Drop the `unrar x` tempdir strategy from the RAR spec

## Why

`format-rar`'s "Serve random access and extraction with bounded explicit temp use"
lets a solid random read "extract once with `unrar x` into an explicitly managed
temporary directory and serve later reads from disk", and lets `extract_all()` use
"one `unrar x` to a temporary directory". Two scenario rows say the same.

Nothing has ever done either, and the same spec already forbade it twice over. The
requirement "Use RARLAB unrar only for member data that needs it" ends "The spawn
SHALL be the `p` (print to stdout) command only", and `Constrain unrar argv by call
site` enumerates every call site without an `x` among them. So the clause was not
merely unbuilt, it contradicted its own neighbours — and the argv requirement, not
this one, is where per-call-site mechanics belong.

The tree agrees. `unrar` is spawned from one place, `rar_unrar.py`, and always as `p`;
there is no `x` subcommand in `src/` at all. A solid random `open()` builds a fresh
`unrar p -n./<member>` each time and reports the re-decode through
`RewindWarning.min_redecode_bytes` (`_unrar_solid_prefix`) rather than avoiding it.
`extract_all()` is served by the same `stream_members()` pass as any other caller,
plus a second pass for hardlink sources the selector excluded; on a solid archive each
pass is one unnamed `unrar p` pipe over the whole archive.

The handbook already records the decision, in three places: the RAR page's At a glance
note ("there is no `unrar x` anywhere in `src/`, so every out-of-order solid `open()` is
its own whole-archive decode"), §2.4 ("amortizing via `unrar x` into a temp directory was
considered and rejected because it hides decode work behind later reads
(`VISION.md`; §6)"), and the §6 deliberate-omissions row, which is where the **#8**
label lives. The reason is a VISION one: `AccessCost.SOLID`
and a per-open decode are honest signals, and a tempdir cache amortizes work the caller
can neither see nor bound.

So this is the spec catching up with a settled decision, not a new one. Leaving it as it
stands invites a future implementation to build the rejected strategy and call it
conformant.

## What Changes

- **`format-rar`, "Serve random access and extraction with bounded explicit temp use"** —
  the solid random-read sentence keeps only the decode-from-start alternative and says
  outright that the reader does not amortize by extracting to a temporary directory. The
  `extract_all()` sentence names the `stream_members()` pass it actually uses. The
  "declared RAR strategy" clause stays and now separates what the reader *implements*
  from what this capability *declares*: the source copy — copying a non-path archive
  *source* to disk so `unrar` can seek it — is the one it implements, and the deferred
  small-member optimization is the other strategy declared.
- **Two scenario rows** in the random/extract matrix state what happens instead of what a
  backend may do.
- **`archive-reading`, "Bounded implicit temporary storage"** cites the `unrar x`
  tempdir as its example of a per-format strategy that may declare proportional temp
  storage. The rule is unchanged; the example becomes the one RAR actually declares,
  the copy of a non-path archive source to disk.
- **Two unarchived changes restate the whole requirement** in a `MODIFIED` delta, so
  they are re-derived from the live text with only their own edits re-applied:
  `bounded-source-spooling` and `rar5-stored-encrypted-native-read`. Correcting the one
  sentence in place would have left the rest of each block as it was written before the
  lazy stream-volume copy landed — two paragraphs and four scenario rows short of the
  live requirement, which archiving either change would then have deleted.

No code changes, no behaviour change, and no test changes: this removes permission for
something that was never built.

## Impact

- Affected specs: `format-rar`, `archive-reading` (one parenthetical example).
- Affected changes: `bounded-source-spooling`, `rar5-stored-encrypted-native-read`
  (delta text only; neither one's own subject moves).
- Affected code: none.

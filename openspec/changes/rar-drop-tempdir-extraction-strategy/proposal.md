# Drop the `unrar x` tempdir strategy from the RAR spec

## Why

`format-rar`'s "Serve random access and extraction with bounded explicit temp use"
lets a solid random read "extract once with `unrar x` into an explicitly managed
temporary directory and serve later reads from disk", and lets `extract_all()` use
"one `unrar x` to a temporary directory". Two scenario rows say the same.

Nothing has ever done either, and the project decided not to. `unrar` is spawned from
one place in the tree, `rar_unrar.py`, and always as `p`; there is no `x` subcommand in
`src/` at all. A solid random `open()` builds a fresh `unrar p -n./<member>` each time
and reports the re-decode through `RewindWarning.min_redecode_bytes`
(`_unrar_solid_prefix`) rather than avoiding it. `extract_all()` is served by the same
`stream_members()` pass as any other caller — one unnamed `unrar p` pipe on a solid
archive, per-member named opens on a non-solid one.

The handbook already records the decision, in three places: the RAR page's At a glance
note ("there is no `unrar x` anywhere in `src/`, so every out-of-order solid `open()` is
its own whole-archive decode"), §2.4 ("amortizing via `unrar x` into a temp directory was
considered (**#8**) and rejected because it hides decode work behind later reads"), and
the §6 deliberate-omissions row for #8. The reason is a VISION one: `AccessCost.SOLID`
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
  "declared RAR strategy" clause stays and now names the one materialization the reader
  does declare: copying a non-path archive *source* to disk so `unrar` can seek it.
- **Two scenario rows** in the random/extract matrix state what happens instead of what a
  backend may do.
- **`archive-reading`, "Bounded implicit temporary storage"** cites the `unrar x`
  tempdir as its example of a per-format strategy that may declare proportional temp
  storage. The rule is unchanged; the example becomes the one RAR actually declares,
  the copy of a non-path archive source to disk.
- **Two unarchived changes carry the same sentence** and are corrected in step:
  `bounded-source-spooling` and `rar5-stored-encrypted-native-read` both restate this
  requirement in a `MODIFIED` delta. Archiving either one after this lands would
  otherwise re-widen the requirement it just narrowed.

No code changes, no behaviour change, and no test changes: this removes permission for
something that was never built.

## Impact

- Affected specs: `format-rar`, `archive-reading` (one parenthetical example).
- Affected changes: `bounded-source-spooling`, `rar5-stored-encrypted-native-read`
  (delta text only; neither one's own subject moves).
- Affected code: none.

# Tasks — drop the `unrar x` tempdir strategy from the RAR spec

## 1. Correct the requirement

- [x] 1.1 Narrow the solid random-read sentence to the decode-from-start alternative and
      state that the reader does not amortize with a temporary directory.
- [x] 1.2 Replace the `extract_all()` MAY clause with the `stream_members()` pass it uses.
- [x] 1.3 Keep the "declared RAR strategy" clause and separate implements from declares:
      the source copy is the materialization the reader implements, the deferred
      small-member optimization the other one this capability declares.
- [x] 1.4 Rewrite the two random/extract matrix rows to state behaviour rather than
      permission.

## 2. Repoint the cross-capability example

- [x] 2.1 `archive-reading`'s "Bounded implicit temporary storage" cites the source copy
      instead of `unrar x`; the requirement itself is unchanged.

## 3. Keep the unarchived deltas in step

- [x] 3.1 Re-derive `bounded-source-spooling`'s `format-rar` `MODIFIED` block from the
      live requirement and re-apply only that change's own spool-limit edits. Copying the
      corrected sentence into the block it already had was not enough: the rest of that
      block was written before the lazy stream-volume copy landed, so it was missing two
      live paragraphs and four scenario rows and would have deleted them on archive. Its
      "volumes were copied at open" wording went with them -- that change's own proposal
      says a spool happens at the first operation needing it, not at open.
- [x] 3.2 Re-derive `rar5-stored-encrypted-native-read`'s `format-rar` block the same way,
      re-applying only its own edits: the caveat tracks the spawn set rather than
      compression, and the two RAR5 scenario rows.
- [x] 3.3 Put the re-derive rule where the person who needs it will read it: a gate task
      in each sibling's own `tasks.md`, since whoever archives one of them works from that
      change, not from this archived one. A `MODIFIED` delta replaces the whole
      requirement block, so a block written against an older live text deletes whatever
      the requirement has gained since -- archive order is only one way that happens, and
      `openspec validate --strict` sees none of it. The rule is therefore re-derive from
      live and read the dry-run **diff**, not watch the order.

## 4. Gates

- [x] 4.1 `openspec validate rar-drop-tempdir-extraction-strategy --strict`.
- [x] 4.2 `./scripts/check.sh --fix`.
- [x] 4.3 Archive this change, so `openspec/specs/` carries the corrected requirement.

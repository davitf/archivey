# Tasks — drop the `unrar x` tempdir strategy from the RAR spec

## 1. Correct the requirement

- [x] 1.1 Narrow the solid random-read sentence to the decode-from-start alternative and
      state that the reader does not amortize with a temporary directory.
- [x] 1.2 Replace the `extract_all()` MAY clause with the `stream_members()` pass it uses.
- [x] 1.3 Keep the "declared RAR strategy" clause and name the source copy as the one
      materialization the reader declares.
- [x] 1.4 Rewrite the two random/extract matrix rows to state behaviour rather than
      permission.

## 2. Repoint the cross-capability example

- [x] 2.1 `archive-reading`'s "Bounded implicit temporary storage" cites the source copy
      instead of `unrar x`; the requirement itself is unchanged.

## 3. Keep the unarchived deltas in step

- [x] 3.1 Apply the same correction to `bounded-source-spooling`'s `format-rar` delta.
- [x] 3.2 Apply the same correction to `rar5-stored-encrypted-native-read`'s `format-rar`
      delta.
- [x] 3.3 Record the archive order this relies on. Both deltas here are written against
      the live spec, and a `MODIFIED` delta replaces the whole requirement block, so this
      change archives **before** `bounded-source-spooling` and
      `rar5-stored-encrypted-native-read`. If either of those archives first, re-derive
      this change's deltas from the live spec rather than applying them as written --
      `openspec validate --strict` cannot see that class of mis-targeting.

## 4. Gates

- [x] 4.1 `openspec validate rar-drop-tempdir-extraction-strategy --strict`.
- [x] 4.2 `./scripts/check.sh --fix`.
- [x] 4.3 Archive this change, so `openspec/specs/` carries the corrected requirement.

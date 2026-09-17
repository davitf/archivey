# Fold `_HeaderDecryptStream` into `AesDecryptStream`

## Why

The tree has **two** AES-CBC pull streams over a ciphertext source:
`AesDecryptStream` (`internal/streams/crypto.py`, the 7z member wrapper, extended with
seek in #342) and `_HeaderDecryptStream` (`internal/backends/rar_parser.py:697-782`, the
RAR3/RAR5 encrypted-header wrapper). They implement the same loop — gather whole
ciphertext blocks, `stage.update`, hold the unread tail of the last block — and they
already disagree about the cases that are easy to get wrong:

| | `AesDecryptStream` | `_HeaderDecryptStream` |
| --- | --- | --- |
| short source read | buffered in the stage; identity breaks | gathered via `read_exact` |
| source EOF mid-message | `finalize()`; `_AesCbcTruncatedError` | stops; never calls `finalize` |
| `read(-1)` | reads to EOF | `CorruptionError` |
| `tell()` | plaintext offset | ciphertext (archive) offset |

Each row is a decision someone made once, in one class, for a reason recorded in that
class. A fix applied to one does not reach the other, and neither docstring is wrong —
they are both right about their own caller. That is the cost this change is trying to
remove, and also the reason it is not a mechanical merge.

Both class docstrings already name the blockers, which is why this is a written-up change
rather than a stale TODO: `crypto.py` §"Folding `_HeaderDecryptStream` in is blocked by
the header walk", and the `_HeaderDecryptStream` docstring §"Not `AesDecryptStream`".

## What Changes

- The RAR header walk SHALL get its archive offset through an explicit accessor rather
  than `tell()`, so the two arms of `header_fd: _Readable = source` (raw handle or
  decrypt stream) stop overloading one method name with two meanings.
- `AesDecryptStream` SHALL grow whatever the header caller genuinely needs — a
  ciphertext-cursor accessor, a way to refuse `seek` on a mid-file unbounded stream, and
  full-count source reads — and `_HeaderDecryptStream` SHALL be deleted.
- Behaviour SHALL NOT move. Every error type the walk raises today (`CorruptionError` vs
  `TruncatedError` vs `EncryptionError`) stays what it is; the wrong-password paths in
  particular depend on it.

**This change is allowed to conclude that the fold is not worth it.** The bar is stated
in `design.md`: if the flags `AesDecryptStream` must grow to serve the header walk are
more than the ~86 lines deleted, the outcome is to replace both docstring sections with
one pointer to a recorded decision and close the change. That is a real result, not a
failure — it is what stops the question being re-derived every few months.

## Impact

- Capabilities: none. No requirement changes wording (`skip_specs: true` in
  `.openspec.yaml`) — the header walk's contract is unchanged, and this is exactly the
  "pure refactor" case that flag exists for.
- Code: `internal/streams/crypto.py`, `internal/backends/rar_parser.py`. Seven references
  to `_HeaderDecryptStream`, two of them in docstrings that must be rewritten rather than
  deleted.
- Independent of `rar5-stored-encrypted-native-read`; either can land first. They touch
  the same two files, so landing them concurrently means one rebase.

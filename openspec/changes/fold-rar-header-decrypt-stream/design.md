# Design — one AES-CBC pull stream

Line references are against the tree at the time of writing (post-#342/#343).

## What the header walk actually asks for

`_HeaderDecryptStream` (`rar_parser.py:697`) is not a stripped-down
`AesDecryptStream`. It is a stream with four deliberately different answers, each
recorded in its docstring with the measurement or incident behind it:

1. **`tell()` is the ciphertext cursor.** After a full header read, `data_offset` must be
   where the *next* salt/IV or packed data starts — a 16-byte boundary. Subtracting the
   unread tail (`len(_buf)`) reports the plaintext offset and lands inside the AES
   padding, and the next header decrypts as garbage. Measured: every FILE header on
   `encrypted_header__.rar` and `encrypted_header__rar4.rar` has
   `header_size % 16 != 0`, so this is not a corner case — it is every header.
2. **Source reads are `read_exact`, not `read`.** A short non-empty source read would
   leave a partial block in the stage; the remaining ciphertext then decrypts against the
   wrong IV and every later header looks corrupt. `AesDecryptStream.read` calls
   `self._source.read(ask)` and its own docstring notes that the ciphertext-cursor
   identity does not hold if that comes back short.
3. **`read(-1)` is `CorruptionError`.** The wrapper sits mid-file on the shared archive
   handle with no length bound, so "read to EOF" means "decrypt the rest of the archive".
   `AesDecryptStream` treats `n < 0` as read-to-EOF, which is right for a bounded 7z pack
   view and wrong here.
4. **`finalize()` is never called.** The header message ends when the walk has taken
   `header_size` bytes, not at source EOF. `AesDecryptStream` finalizes on a source that
   returns empty and raises `_AesCbcTruncatedError` for a short last block.

Plus a structural one: **`header_fd: _Readable = source`** (`rar_parser.py:1102`, `:1802`)
is either the raw archive handle or the decrypt stream, and `.tell()` means *archive
offset* on both arms (`:1125`, `:1152`, `:1996`, `:2027`). A second method on
`AesDecryptStream` does not fix that by itself — the raw handle is the other arm and does
not have it.

## Decisions

### Resolve the union before touching the crypto class

The overloaded `tell()` is the blocker that has to go first, and it is worth doing whether
or not the fold follows. Introduce a module-level accessor in `rar_parser.py`:

```python
def _archive_offset(fd: _Readable) -> int: ...
```

returning `fd.tell()` for the raw handle and the ciphertext cursor for a decrypt stream,
and use it at all four sites. `_Readable` then documents *archive offset*, not "a
ciphertext `tell`", and the two arms stop pretending to be the same protocol. Do this as
its own commit: it is reviewable on its own, and if the fold is abandoned the codebase is
still better for it.

### What `AesDecryptStream` would have to grow

| Need | Shape | Cost |
| --- | --- | --- |
| Ciphertext cursor | `cipher_tell()`; the identity `_cipher_start + _pos + len(_buf)` is already documented in `read` | small, and arguably clarifies the existing docstring |
| Full-count source reads | `read_exact(self._source, ask)` instead of `self._source.read(ask)` | small; strictly more correct for the 7z caller too |
| Refuse `seek` on a mid-file unbounded stream | a constructor flag, or a bounded source | **this is the one to watch** |
| Refuse `read(-1)` | a constructor flag | ditto |
| Do not `finalize` at source EOF | a constructor flag, or never reach EOF | ditto |

The first two are improvements to `AesDecryptStream` on their own terms. The last three
are *the header caller's policy*, and a flag per policy is how a shared class turns into a
union of two classes with a discriminator.

### The bar

Land the fold only if the last three rows collapse to **at most one** constructor
argument. The plausible way there is to bound the source instead of flagging the stream: a
`SlicingStream` / `SharedView` over `[data_start, data_start + header_size)` makes the
message finite, at which point read-to-EOF, `finalize` and `seek` are all *correct*
rather than forbidden. The obstacle is that `header_size` is not known until part of the
header has been decrypted — which is exactly why `_read_rar5_block` reads the size vint
byte-at-a-time. A two-phase bound (decrypt the fixed prefix, then re-bound) would need the
stage to survive re-bounding, so measure it before committing to it.

If the answer is three flags, **stop and record it.** Replace the two docstring sections
(`crypto.py` §"Folding `_HeaderDecryptStream` in is blocked by…" and
`_HeaderDecryptStream` §"Not `AesDecryptStream`") with one ADR or one
`dev-docs/discussions/` note the next reader can find, and keep the `_archive_offset`
cleanup. The question has now been asked twice; the point of the change is that it should
not be asked a third time from scratch.

### Error types must not move

The RAR3 walk catches `(CorruptionError, TruncatedError)` and re-raises as
`EncryptionError` when the block is encrypted — that is how wrong-password candidates keep
iterating. A fold that lets `_AesCbcTruncatedError` escape from a path that raises
`CorruptionError` today, or that changes *which* of the two a truncated archive produces,
changes observable behaviour even though no requirement mentions the class. The RAR5 side
has the same shape around `_check_rar5_password`. Every error-type assertion in the RAR
parser tests is load-bearing here.

### One stale claim to fix either way

`_Readable`'s docstring says streamtools bases "either close the inner stream or have no
ciphertext `tell`". Since #340 that is half wrong: `AesDecryptStream` takes
`owns_inner=False` and borrows. The surviving half is the ciphertext `tell`. Correct the
sentence in whichever direction this change ends up going.

### Rejected: share only the `DecryptStage`

That is the status quo — both classes already call `open_aes_decrypt_stage`. The
duplication being paid for is the *gather loop and its edge cases*, not the cipher
construction, so this is not a resolution.

### Rejected: fold `WinZipAesDecryptStream` in as well

It is CTR, not CBC: it builds its own cipher and shares only the availability check. It
has no block-restart or IV-chaining semantics to unify, so including it would widen the
change with no shared logic to gain.

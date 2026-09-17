# Tasks — fold `_HeaderDecryptStream` into `AesDecryptStream`

## 1. Disentangle the header walk (worth landing alone)

- [ ] 1.1 Add `_archive_offset(fd: _Readable) -> int` in `rar_parser.py` and use it at
      the four sites that mean "archive offset": `:1125`, `:1152` (RAR3 `header_offset`
      and `data_offset`) and `:1996`, `:2027` (RAR5). `_Readable` documents archive
      offset from then on, not "a ciphertext `tell`".
- [ ] 1.2 Fix `_Readable`'s stale claim that streamtools bases "close the inner stream":
      `AesDecryptStream` borrows since #340. The surviving reason is the ciphertext
      cursor.
- [ ] 1.3 Commit this on its own. It stands whether or not §2 lands.

## 2. Improvements to `AesDecryptStream` that stand on their own

- [ ] 2.1 `read` gathers a short source read (`read_exact(self._source, ask)`) instead of
      handing a partial block to the stage. This makes the
      `_cipher_start + _pos + len(_buf)` identity in the `read` docstring unconditional;
      today the docstring has to disclaim it.
- [ ] 2.2 Expose the ciphertext cursor (`cipher_tell()`), derived from that identity.
- [ ] 2.3 Tests for both, on the 7z path, before any RAR caller exists: a source that
      returns short reads must produce identical plaintext and an unchanged cursor.

## 3. The fold itself — measure before committing

- [ ] 3.1 Write down what the header caller needs beyond §2: refuse `seek`, refuse
      `read(-1)`, do not `finalize` at source EOF. Count the constructor arguments.
- [ ] 3.2 Try the bounded-source route first (`design.md` §"The bar"): a view over
      `[data_start, data_start + header_size)` makes all three correct rather than
      forbidden. Establish whether the stage can survive the two-phase bound that
      `_read_rar5_block`'s byte-at-a-time vint read forces.
- [ ] 3.3 **Decision gate.** One constructor argument or fewer → fold, delete
      `_HeaderDecryptStream`, rewrite the seven references (two are docstrings and must be
      rewritten, not deleted). More than one → stop, do task 5.2, and close the change
      with §1 and §2 landed.

## 4. Tests (only if §3 folds)

- [ ] 4.1 Error types are unchanged: for each existing RAR parser test that asserts
      `CorruptionError` / `TruncatedError` / `EncryptionError` on an encrypted-header
      archive, the same type still comes out. Truncated-archive cases specifically —
      `_AesCbcTruncatedError` must not escape where `CorruptionError` is raised today.
- [ ] 4.2 `data_offset` lands on the ciphertext boundary for every header of
      `encrypted_header__.rar` and `encrypted_header__rar4.rar`, where
      `header_size % 16 != 0` for every FILE header. This is the regression test for
      divergence 1 in `design.md`.
- [ ] 4.3 A wrong password on an encrypted-header RAR3 archive still raises
      `EncryptionError` (not `CorruptionError`), so password candidates keep iterating.
- [ ] 4.4 No unbounded read reaches the source: assert the shared archive handle's
      position after a header walk, and that `read(-1)` is still refused on the header
      path.
- [ ] 4.5 `./scripts/check.sh && ./scripts/test.sh --all-configs`.

## 5. Record

- [ ] 5.1 If folded: remove `crypto.py` §"Folding `_HeaderDecryptStream` in is blocked by
      the header walk" and the `_HeaderDecryptStream` docstring, and keep the four
      divergence answers (ciphertext `tell`, gathered reads, no unbounded read, no
      `finalize`) as comments where they now live.
- [ ] 5.2 If not folded: replace both docstring sections with one pointer to a recorded
      decision (an ADR or a `dev-docs/discussions/` note) stating the measured reason, so
      the question is not re-derived a third time.
- [ ] 5.3 `openspec validate --strict fold-rar-header-decrypt-stream`.

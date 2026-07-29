## Why 4, and why these 4

The question the maintainer posed was "do we need one extra per format?" The measured
answer is that we never had one: `[7z]` is a codec bundle, `[rar]` is an alias of
`[crypto]`, `[zstd]`/`[lz4]` are subsets of `[7z]`. So this is not a redesign — it is
making the names match what the groups already are.

The selection rule: **an extra should answer a question a user asks about themselves**,
not name an internal dependency group. Users ask "will it just work?" (`recommended`),
"do I need fast seeking?" (`seekable`), "am I on free-threaded Python?"
(`free-threaded`), "give me everything" (`all`). Nobody asks "do I want pybcj?".

## Why `cryptography` stays in `recommended` rather than becoming the split point

The maintainer asked whether `cryptography` is simple enough to fold into the default
bundle. It is **already there** transitively (`recommended` → `7z` → `cryptography`), so
folding it in changes nothing; the question is whether to pull it *out*.

Keep it in, because:

- It is required for the encryption features that make the release's compatibility story
  (WinZip AES ZIP, header-encrypted RAR, 7z AES). A "recommended" install that cannot open
  an AES ZIP is not recommendable.
- It ships wheels for every platform the project tests.

But record the cost honestly, because it is the reason `free-threaded` must exist as a
separate extra: `cryptography` depends on `cffi`, and **cffi refuses to build on
free-threaded CPython 3.13**. So `recommended` cannot be installed there today. That is a
property of the wheel ecosystem, not of archivey, and it is expected to resolve on 3.14t.

## Why `free-threaded` is a separate extra, and the risk

It is the only honest way to give free-threaded users an install line that works. The set
is measured, not guessed (each package verified to leave `sys._is_gil_enabled()` false
after import), and CI already installs exactly this set on 3.13t and asserts the GIL stays
disabled — so the extra cannot silently rot.

**The risk, now confirmed with data rather than predicted:** an extra named for a
*runtime property* is a moving target. Measured on 3.14t, `cryptography` **joins** the
GIL-safe set while `pyppmd`, `inflate64`, `brotli` and `rapidgzip` still re-enable the
GIL. So the set is already version-dependent, which is why the extra uses environment
markers, and it will keep widening until it eventually collapses into `recommended`. It could also be
misread as "this makes archivey free-threaded" (it does not; it avoids dependencies that
switch the GIL back on). Mitigations: the spec states both facts, and the extra is
documented as "the subset that currently keeps the GIL disabled — expected to widen".

The alternative — document the pip line in prose instead of shipping an extra — was
rejected because a prose install line rots silently, while an extra is exercised by CI on
every run.

## Should the crypto library change? (asked 2026-07-29 — no)

`internal/streams/crypto.py` is a one-method `CryptoBackend` ABC
(`aes_cbc_decrypt_stage`), and `zip_aes.py` adds AES-ECB for the CTR keystream. Every
other primitive — PBKDF2, SHA-1/256, HMAC — is stdlib `hashlib`. So **AES block
operations are the only third-party crypto need**, and swapping backends is genuinely
cheap, exactly as the encapsulation intended.

Measured, since the question was whether a free-thread-safe alternative exists:

| Library | 3.13t | 3.14t |
| --- | --- | --- |
| `cryptography` | **cannot install** (cffi rejects FT 3.13) | installs, AES-CBC works, **GIL stays disabled** |
| `pycryptodome` 3.23 | installs, AES-CBC + AES-ECB work, **GIL stays disabled** | not tested |

So yes, a free-thread-safe alternative exists today. **Do not switch anyway**, and do not
add a second backend:

- The gap is **one pre-release runtime**, and it is already closed upstream on 3.14t —
  which is the first free-threaded build with real support behind it. Trading a widely
  audited, ecosystem-default security library for a workaround to a transient packaging
  hole in an experimental interpreter is a bad exchange.
- A second crypto backend is not free even when the plug point is: it doubles the
  security surface to audit, adds divergence risk in padding and error paths (precisely
  where crypto bugs live), and turns "which backend am I on?" into a support question
  that affects observable behaviour.

Keep the abstraction, though: it is what makes this a paragraph rather than a project, and
it means the decision is cheap to revisit if `cryptography` ever becomes a problem for a
reason that is not self-resolving.

## Alternatives considered

**Capability extras with format aliases** (`codecs`, `crypto`, … plus `7z` = alias).
Non-breaking and more granular. Rejected on the maintainer's "err on the side of fewer"
instruction: it *adds* names, and every added extra is permanent. It also keeps the
format names that caused the confusion, merely making them correct by construction.

**Keep the table, fix only the hint strings.** Cheapest, and it does fix the reported
symptom. Rejected because `[rar]`-cannot-deliver-`unrar` and the free-threaded
uninstallability remain, and because the window to remove extras closes at `0.2.0`.

**Drop `[all]`.** It currently resolves to exactly `[recommended]` + `[seekable]`. Kept:
it is the conventional name people try first, and it costs one line.

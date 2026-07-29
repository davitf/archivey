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

**The risk, recorded rather than hidden:** an extra named for a *runtime property* is a
moving target. When `cryptography`/`pyppmd`/`rapidgzip` gain free-threaded support, the
set grows and eventually `free-threaded` collapses into `recommended`. It could also be
misread as "this makes archivey free-threaded" (it does not; it avoids dependencies that
switch the GIL back on). Mitigations: the spec states both facts, and the extra is
documented as "the subset that currently keeps the GIL disabled — expected to widen".

The alternative — document the pip line in prose instead of shipping an extra — was
rejected because a prose install line rots silently, while an extra is exercised by CI on
every run.

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

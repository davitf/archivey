## Why

The extras are **named by format but scoped by capability**, and the mismatch is
user-visible. `[7z]` pulls seven packages, six of which are member codecs shared with ZIP
and TAR — so a ZIP member using Deflate64 or PPMd raises
`PackageNotInstalledError: pip install archivey[7z]`. That hint is *correct* and reads
like the library misidentified the file. The name is what lies, not the message.

Two more consequences of the same mismatch:

- `[rar]` and `[crypto]` are **byte-identical** (both `cryptography` only), and `[rar]` is
  doubly misleading: RAR member *data* needs the RARLAB `unrar` **binary**, which no pip
  extra can supply. The packaging metadata promises something it cannot deliver.
- `[zstd]` and `[lz4]` are strict subsets of `[7z]`, so the same codec is reachable under
  two names with different implied meanings.

Separately, and measured: **`pip install archivey[recommended]` fails outright on
free-threaded CPython 3.13** — `[recommended]` → `[7z]` → `cryptography` → `cffi`, and
cffi rejects free-threaded 3.13 ("upgrade to free-threaded 3.14 or newer"). The
recommended install is therefore uninstallable on a runtime the project tests in CI, and
nothing in the extras table says so.

Verified on **3.14t**, `cryptography` installs, decrypts and leaves the GIL disabled — so
this is a 3.13t-only packaging gap, already fixed upstream, not a property of the library.
That is why `[free-threaded]` carries `cryptography` behind a `python_version >= '3.14'`
marker rather than omitting it outright.

`0.2.0` is the first public release, so the extras table has no users yet. Removing or
renaming an extra is breaking **after** the tag and free **before** it. This is the only
window.

## What Changes

Collapse 11 extras to **4**, chosen so each answers a question a user actually asks.

| Extra | Pulls | Answers |
| --- | --- | --- |
| `[recommended]` | `pyppmd`, `inflate64`, `brotli`, `lz4`, `pybcj`, `backports.zstd` (<3.14), `cryptography`, `pycdlib`, `tqdm` | "give me everything that works everywhere" |
| `[seekable]` | `rapidgzip` | "I want gz/bz2 random access and speed" — kept separate because it is a heavy native build, re-enables the GIL on free-threaded builds, and carries the accelerator process-abort known issue |
| `[free-threaded]` | `pycdlib`, `lz4`, `tqdm`, `backports.zstd` (<3.14), `cryptography` (>=3.14) | "I am on a free-threaded build" — the measured subset that keeps the GIL **disabled**. Version-conditional: `cryptography` is unusable on 3.13t and fine on 3.14t |
| `[all]` | `[recommended]` + `[seekable]` | "everything" |

**Removed:** `[7z]`, `[rar]`, `[crypto]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`,
`[recommended-lite]`. `[recommended-lite]` disappears by construction: with `[seekable]`
no longer inside `[recommended]`, the lite variant *is* `[recommended]`.

- Every `MissingComponent` install hint changes from `pip install archivey[7z]` to
  `pip install archivey[recommended]` (or `[seekable]` for rapidgzip) — true regardless
  of which container the member came from, which is the actual fix.
- The `[rar]` extra's stale TODO goes away with the extra. Record in the spec that
  BLAKE2sp is implemented natively on stdlib (`internal/hashing/blake2sp.py`, zero-dep),
  which is what that TODO was waiting for.
- `docs/formats.md`'s per-format extra column and `docs/support-matrix.md` are updated to
  the new names.

## Impact

- **Breaking** for anyone pinning `archivey[7z]` etc. Nothing is published on real PyPI
  (only `0.2.0.dev0` on TestPyPI), so the real blast radius is this repo and its CI.
- CI legs using `--extra all` are unaffected; the free-threaded job's explicit extra list
  becomes `--extra free-threaded`, which makes the job self-describing.
- **Trade-off accepted deliberately:** a user who wants only ISO now installs
  `[recommended]` and gets the codecs and `cryptography` too. This is a real regression in
  install minimalism, chosen because "err on the side of fewer extras" is the stated
  preference and because an extra, once published, can never be removed. It also
  contradicts the current spec sentence requiring each extra to avoid pulling unrelated
  dependencies — that requirement is modified here rather than quietly broken.

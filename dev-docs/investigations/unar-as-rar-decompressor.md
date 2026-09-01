# `unar` as a RAR data backend — investigation

**Status:** finished evidence (explore, not implemented).  
**Date:** 2026-09-01  
**Trigger:** Homebrew disabled the `rar` cask (Gatekeeper / notarization); CI and
`setup-dev-env.sh` now compile RARLAB UnRAR from a pinned GitHub mirror
([PR #276](https://github.com/davitf/archivey/pull/276)). Question: should archivey
also accept The Unarchiver’s `unar` CLI for member **data**, since it is easy to
install (`brew install unar`, `apt install unar`) and advertises stdout extract?

**Measured on:** Ubuntu noble; system `unar`/`lsar` **v1.10.1** (Debian
`unar 1.10.7+ds1+really1.10.1`); also a locally built MacPaw `XADMaster` tag
`v1.10.8` binary that reports **v1.10.7**; RARLAB `unrar` 7.00; fixtures under
`tests/fixtures/rar/` plus freshly `rar a`-built probes.

Related settled policy: ADR
[`0002-native-rar-metadata-unrar-data`](../decisions/0002-native-rar-metadata-unrar-data.md),
threat-model **C1** (RARLAB `unrar` only — no silent `unar` / `unrar-free` / `7z`
fallback), `src/archivey/internal/backends/rar_unrar.py`.

---

## Verdict (short)

**Not worth adding as a RAR data backend right now** — not even as an opt-in
fallback — unless upstream fixes RAR5 solid + empty-file extraction *and* we
deliberately reopen C1.

`unar -o -` *does* give a concatenated stdout pipe that matches `unrar p` on the
happy path (nonsolid, solid-without-empty, volumes, encryption-with-correct-password).
That is not enough: common solid RAR5 archives that contain any empty member are
broken (SIGSEGV on 1.10.1; silent empty / “Unknown error” on 1.10.7), wrong
passwords and missing members often exit 0 with empty stdout, and WinRAR
file-version members cannot be addressed the way archivey’s demux expects.

The install-friction problem on macOS is real; the better levers are documenting
`scripts/install-rarlab-unrar.sh` for end users, pinning the Windows download
(already in `IDEAS.md`), and optionally packaging guidance (conda-forge
`libunrar`, etc.) — not a second decompressor with divergent solid/password
semantics.

---

## What archivey needs from a RAR data helper

From `rar_unrar.py` / `rar_reader.py` / `format-rar`:

```
                    ┌─────────────────────────────┐
  native parser     │  list / sizes / solid flag  │
  (always)          └──────────────┬──────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
   nonsolid stored          nonsolid other              solid archive
   (direct slice)           named `unrar p`             ONE unnamed pipe
                            `-n./member`                + SolidBlockReader
                                                        size demux
```

Concrete requirements of the subprocess:

| Need | Why |
| --- | --- |
| Raw payload bytes on **stdout** | Streaming API; no tempdir for the common path |
| **ALL** payload members concatenated in archive order (solid) | One process per solid `stream_members()` pass |
| **Single** member by stable identity (nonsolid / random solid open) | Per-member `open()` |
| Dirs / RAR5 redirects omitted from the pipe | Demux size map = `is_payload_file()` only |
| Honest non-zero exit on wrong password / missing member / fatal decode | Typed `EncryptionError` / `CorruptionError` / `TruncatedError` |
| Password not forced onto argv (archivey feeds stdin after bare `-p`) | Process-list hygiene |
| Safe addressing of hostile names (`-inul`, `@…`, `*`/`?`) | Already hardened for `unrar` via `-n./` |

---

## What `unar` actually provides

```
unar [options] archive [files ...]
  -o -     write all selected members' data to stdout (no files on disk)
  -q       quiet
  -D       no containing directory
  -p PWD   password (argv value — no stdin-password mode in the help)
  -i       select by lsar index instead of name
  files…   name filters (and, empirically, wildcards)

lsar [-j|-l|-L] archive   # listing / JSON metadata; archivey would not need this
                          # for RAR (native parser already lists)
```

### Stdout shape (when it works)

| Scenario | `unrar p -inul` | `unar -o - -q -D` | Match? |
| --- | --- | --- | --- |
| Nonsolid ALL (`basic_nonsolid__.rar`) | 41 B concat | 41 B concat | **yes** |
| Solid RAR4 ALL w/ empty (`basic_solid__rar4.rar`) | 41 B | 41 B | **yes** |
| Solid RAR5 ALL, no empty members (fresh `rar a -s`) | N B | N B | **yes** |
| Solid RAR5 ALL w/ any empty member | 41 B | **broken** (below) | **no** |
| Two named members | concat in **archive order**, not argv order | same | **yes** |
| Hardlink/symlink solid | payload files only (16 / 13 B) | same | **yes** |
| Multi-volume (`tinyvol.part1.rar`) | 1600 B | 1600 B | **yes** |
| Encrypted data, correct `-p password` | match | match | **yes** |
| Encrypted solid, no empty, correct password | match | match | **yes** |
| Progress / banners on stdout | none (`-inul`) | none observed with or without `-q` on these fixtures | OK |

So the *mechanical* stdout model is close to `unrar p`: raw concatenation, no
framing, directories and RAR5 redirects absent from the pipe. That is exactly what
`SolidBlockReader` wants — **when the tool actually emits the bytes**.

---

## Hard failures

### 1. RAR5 solid + empty file (blocker)

Reproduced with committed `basic_solid__.rar` and with fresh archives:

```bash
# empty first / middle / last — all fail the same way
rar a -m3 -s empty_mid.rar file1.txt empty_file.txt file2.txt
```

| `unar` | ALL / single-member stdout | Disk extract |
| --- | --- | --- |
| **1.10.1** (apt / Ubuntu) | **SIGSEGV** (exit −11), 0 bytes | SIGSEGV; may leave 0-length stubs |
| **1.10.7** (built from MacPaw `v1.10.8` tag; Homebrew ships 1.10.8 bottles) | **exit 0, 0 bytes**, no stderr | exit 1; non-empty solid members written as empty with “Failed! (Unknown error)” |

RAR4 solid with empty members works on both versions. Solid RAR5 *without* any
empty member works and matches `unrar`.

Empty files in solid RARs are common (our own basic fixture includes one). A
backend that cannot stream `basic_solid__.rar` cannot satisfy `format-rar`’s solid
`stream_members()` requirement.

```
  RAR5 solid member stream (archivey demux)
  ─────────────────────────────────────────
  [empty 0 B][file1 13 B][file2 16 B][…]     ← unrar p emits this

  unar 1.10.1:  💥 SIGSEGV
  unar 1.10.7:  (nothing)  rc=0              ← silent total failure
```

#### Follow-up: can we skip empty members (we already know size=0)?

**No — that does not avoid the crash.** The native parser already knows
`file_size == 0`, and archivey’s solid demux already treats those members as
zero-length slices (`pipe_offset += 0` in `rar_reader._iter_with_data`). We never
needed `unrar`/`unar` to *emit* empty files.

The bug is not “unar wrote 0 bytes and we sliced wrong.” An empty RAR5 solid
member still occupies the **shared compression context**. On
`basic_solid__.rar`, `unrar vt` reports:

| Member | Unpacked | Packed | Flags |
| --- | --- | --- | --- |
| `empty_file.txt` | 0 | **28** | (first in solid block) |
| `file1.txt` | 13 | 10 | solid |

Those 28 packed bytes are dictionary/setup, not a no-op header. Later members
continue that stream. `unar` has to decode through that slot even if we never
ask it to write the empty file. Measured:

| Request | apt 1.10.1 | built 1.10.7 |
| --- | --- | --- |
| ALL (includes empty) | SIGSEGV | rc=0, 0 bytes |
| `file1.txt` only (skip empty) | SIGSEGV | rc=0, 0 bytes |
| `-i 1,2,3` (skip empty by index) | SIGSEGV | rc=0, 0 bytes |
| `-i 0` (empty only) | rc=0, 0 bytes | rc=0, 0 bytes |

Empty **position does not matter**: fresh `rar a -s` archives with the empty
file first, middle, or last all SIGSEGV when extracting a *non-empty* member —
including `empty_last.rar` / `file1.txt`, where the requested member sits
*before* the empty slot. Presence of any empty FILE in the solid RAR5 archive
poisons `unar`’s decoder for the whole archive, not just the 0-byte slice.

Stripping empty FILE headers from a temp copy before handing it to `unar` is
not a workaround either: the packed 28 bytes are part of the solid bitstream;
removing the header would desync every later member.

Skipping empties *is* still a valid optimization on the `unrar` path (return
`b""` from metadata, never spawn a process for a 0-byte payload). It just does
not make `unar` viable.

### 2. Error signaling is too weak for archivey’s contract

| Case | `unar` (1.10.1 / 1.10.7) | `unrar` | archivey today |
| --- | --- | --- | --- |
| Wrong password, encrypted **data** | **rc=0**, empty stdout, no stderr | rc=11 | `EncryptionError` |
| Missing / wrong password, encrypted **headers** | rc=1/2 + message | rc=11 | `EncryptionError` |
| Missing member name | **rc=0**, empty stdout | rc=10 | mapped to read error |
| RAR5 solid+empty (1.10.7) | **rc=0**, empty stdout | rc=0 + full pipe | would look like truncation / empty archive |

CRC/`VerifyingStream` would still catch many wrong-byte cases after a full read, but
VISION’s “damaged input is a first-class citizen” rule wants an honest error at the
decompressor boundary — the solid path already depends on that. Silent `rc=0` + empty
is the same class of gap the RAR deep review closed for `unrar` exit codes.

### 3. File-version members (`-ver`)

WinRAR history rows are presented by archivey as `path;n`. Behavior:

| Request | `unrar p -ver -n./…` | `unar -o - …` |
| --- | --- | --- |
| ALL pipe | history + live, in order | **same byte stream** as `unrar -ver` (good for solid demux *if* we always size-map with history) |
| `file.txt` (live name) | live bytes only | **all versions concatenated** |
| `file.txt;1` | that history row | **empty** (name not understood) |
| `-i` index N | n/a | correct single version |

So:

- Solid ALL-pipe demux can match *if* archivey always includes history rows in the
  size map (it already passes `-ver` when any history payload exists).
- Nonsolid / named `open("file.txt")` would get the wrong bytes from a name filter
  (first N bytes of version-one…, not the live revision). Indexes would work but
  require a parallel `lsar` index map, not the parser’s `path;n` identity.

### 4. Hostile / special names

| Member name | `unar` by name | `unar -i <index>` | Notes |
| --- | --- | --- | --- |
| `canary.txt` | OK | OK | |
| `-inul` | treated as **unar option** (“Unknown option”) | OK | need `--` and/or indexes; unlike `unrar`, not neutralized by a `-n` value |
| `@atfile` | extracts the member (no listfile expansion observed) | OK | safer than positional `unrar` was before `-n./` |

Wildcard characters in names were not deeply fuzzed; `unar` documents name filters as
patterns, so the same “no unambiguous address” refusal archivey uses for `unrar` `*`/`?`
would likely be needed.

### 5. Password on argv only

`unar -p <string>` puts the secret in `argv`. archivey’s `unrar` path deliberately uses
bare `-p` + stdin. Not a deal-breaker alone, but a regression on an axis we already
cared about.

---

## How a hypothetical backend would map

```
archivey path                    unrar (today)              unar (candidate)
───────────────────────────────  ─────────────────────────  ──────────────────────────
Solid stream_members()           unrar p -inul [-ver]       unar -o - -q -D
                                 (no member args)           (no member args)
                                 SolidBlockReader demux     same demux *if* pipe OK

Nonsolid open(member)            unrar p -n./member         unar -o - -q -D -i IDX
                                                            or name (unsafe for
                                                            versions / -names)

Password                         -p + stdin                 -p PWD on argv

find helper                      RARLAB banner sniff        look for `unar` / version
Refuse lookalikes                unar/unrar-free rejected   (would invert today’s
                                                            finder message)
```

Integration cost is non-trivial even where bytes match: separate finder, argv builder,
exit-code map, index↔member table for safe selection, version-control policy, tests
duplicated across both tools, docs/`format_availability` wording, and a deliberate
reopening of threat-model C1 / ADR 0002 (“refuse silent fallbacks”).

---

## Install friction (the original itch)

| Platform | RARLAB `unrar` after #276 | `unar` |
| --- | --- | --- |
| Linux | `apt install unrar` (multiverse) — still fine | `apt install unar` (universe) |
| macOS CI / dev | **compile from pinned mirror** (`install-rarlab-unrar.sh`); Homebrew `rar` cask disabled | `brew install unar` — **bottles through Tahoe** |
| Windows CI | unpinned rarlab SFX download (`IDEAS.md`) | scoop/chocolatey exist; not investigated in depth |

So yes: on macOS, `unar` is currently *easier* to get than RARLAB `unrar`. That is
exactly why #276’s PR body listed `unar` as an alternative and rejected it — archivey’s
finder requires the RARLAB banner. Ease of install does not fix solid+empty or silent
`rc=0` failures; shipping `unar` support would trade “hard to install” for “installs
easily and then corrupts/omits solid members.”

---

## Comparison to policy already on the books

Threat-model **C1** closed a multi-tool matrix because coverage and solid/password
behavior diverge into “works on my machine.” This spike **confirms** that divergence:

- solid+empty RAR5: `unrar` OK, `unar` not;
- wrong data password: `unrar` rc=11, `unar` rc=0;
- file versions: different name addressing.

ADR 0002’s “refuse silent fallbacks to `unrar-free` / `unar`” remains the right default.
An *explicit* opt-in (`ArchiveConfig` / env) would still inherit the solid+empty hole
and would need a loud availability story (“RAR solid with empty members unsupported
under `unar`”) — poor UX next to a tool we already tell people to install.

---

## Recommendation

1. **Do not implement an `unar` RAR data backend** until (at least):
   - RAR5 solid archives that contain empty members extract correctly to stdout on a
     current release (no SIGSEGV, no silent empty, non-zero rc on failure);
   - wrong-password / missing-member signaling is reliable enough to map to
     `EncryptionError` / read errors without relying solely on CRC after the fact;
   - maintainer explicitly reopens C1 / ADR 0002 for an opt-in second engine.

2. **Address macOS install friction directly** (cheaper, aligned with #276):
   - Document `scripts/install-rarlab-unrar.sh` (or a thin user-facing wrapper) in
     `docs/install.md` / `docs/formats.md` for Homebrew users;
   - Keep pursuing the Windows pin already parked in `IDEAS.md`.

3. **If revisiting later**, prefer a spike that only claims nonsolid + solid-without-empty
   with a hard `UnsupportedFeatureError` when the native parser sees `is_solid` and any
   zero-size payload — still a product compromise; measure real corpus frequency before
   promising it. Skipping empty members in the extract argv / demux map does **not**
   recover those archives (`unar` still SIGSEGV / silent-empty on the non-empty members).

4. **Not recommended as a silent PATH fallback** under any design — that is the C1
   failure mode.

---

## Evidence commands (re-runnable)

```bash
# Happy-path stdout parity
unrar p -inul -p- tests/fixtures/rar/basic_nonsolid__.rar | sha256sum
unar -o - -q -D tests/fixtures/rar/basic_nonsolid__.rar | sha256sum

# Blocker: solid RAR5 + empty
unar -o - -q -D tests/fixtures/rar/basic_solid__.rar ; echo exit=$?
# 1.10.1 → SIGSEGV; 1.10.7 → exit 0, empty stdout

# Control: solid RAR4 + empty still OK
unar -o - -q -D tests/fixtures/rar/basic_solid__rar4.rar | wc -c   # 41

# Wrong password silent success
unar -o - -q -D -p wrong tests/fixtures/rar/encryption__.rar ; echo exit=$?
# exit 0, 0 bytes

# File versions: name filter concatenates all
unar -o - -q -D tests/fixtures/rar/file_version__.rar file.txt | wc -c  # 40
unrar p -inul -ver -p- -n./file.txt tests/fixtures/rar/file_version__.rar | wc -c  # 16
```

---

## Open questions (only if we reopen this)

- Is the empty-in-solid RAR5 bug tracked upstream in MacPaw/XADMaster? (Not filed from
  this spike.)
- Would a **nonsolid-only** opt-in still be worth the dual-backend maintenance cost for
  macOS users who never see solid RARs?
- Is documenting source-built `unrar` enough for the Homebrew gap, or do we also want a
  conda-forge / pinned binary story for end users?

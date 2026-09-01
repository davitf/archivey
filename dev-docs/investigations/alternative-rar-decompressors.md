# Alternative RAR decompressors (`unar`, `7z`, `bsdtar`, `unrar-free`)

**Status:** finished evidence (explore, not implemented).  
**Date:** 2026-09-01  
**Trigger:** Homebrew disabled the `rar` cask (Gatekeeper / notarization); CI now
compiles RARLAB UnRAR from a pinned GitHub mirror
([PR #276](https://github.com/davitf/archivey/pull/276)). Question: should archivey
accept another CLI for member **data** — first `unar` (easy `brew install`), then
whatever rarfile already wraps?

**Measured on:** Ubuntu noble. RARLAB `unrar` 7.00 (gold). `unar`/`lsar` **v1.10.1**
(Debian `unar 1.10.7+ds1+really1.10.1`); locally built MacPaw `XADMaster` `v1.10.8`
binary reporting **v1.10.7**. `7z` **23.01** from Ubuntu `7zip` (DFSG) **without**
and **with** the `7zip-rar` plugin. `bsdtar` 3.7.2 (libarchive). `unrar-free` 0.1.3.
Fixtures under `tests/fixtures/rar/` plus `rar a -s` probes.

Re-run: `python3 scripts/exploration/rar_decompressor_matrix.py`

Related policy: ADR
[`0002-native-rar-metadata-unrar-data`](../decisions/0002-native-rar-metadata-unrar-data.md),
threat-model **C1**, `src/archivey/internal/backends/rar_unrar.py`.

The earlier unar-only notes live in
[`unar-as-rar-decompressor.md`](unar-as-rar-decompressor.md) (pointer).

---

## Verdict

| Candidate | As a RAR **data** backend |
| --- | --- |
| **`unar`** | **No.** Stdout concat is real, but RAR5 solid + any empty FILE SIGSEGVs (1.10.1) or returns empty (1.10.7) — **including disk extract**. Skipping empties does not help. |
| **`7z e -so`** | **Only with the RAR codec actually loaded**, and even then not a silent fallback. Distro `7zip` lists RAR5 and extracts **stored** (`-m0`) members, then says `Unsupported Method` on solid / typical compressed members. `apt install 7zip-rar` adds `Codecs: Rar5` and then the ALL-pipe matches `unrar p` on `basic_solid__.rar`, encryption, hostile names, volumes. Remaining CLI gaps: no stdin password, missing-member **rc=0**, `path;n` unused, link members can set **rc=2** while the payload concat is still correct. The plugin is still a RARLAB-derived non-free codec (multiverse) — same licensing neighbourhood as `unrar`. |
| **`bsdtar`** | **No.** Documents no solid / no password. Solid RAR4: `RAR solid archive support unavailable`. Stored nonsolid: **unbounded stdout** (7 GB in 8 s until timeout) — not safe to spawn. |
| **`unrar-free` 0.1.3** | **No.** Extract-to-disk only; no stdout. |

**Still the cheapest macOS fix:** document `scripts/install-rarlab-unrar.sh` for
users. If we reopen C1, the only candidate worth an opt-in spike is **`7z` with an
extract-a-compressed-member codec probe** (not `7z i` format lines). Do not treat
`unar` / `bsdtar` / `unrar-free` as substitutes.

---

## What archivey needs

From `rar_unrar.py` / `rar_reader.py` / `format-rar`:

```
  native parser ──► list / sizes / solid flag
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   stored nonsolid  named unrar p   ONE unnamed pipe
   (direct slice)   -n./member      + SolidBlockReader
```

| Need | Why |
| --- | --- |
| Raw payload on **stdout** | Streaming API |
| ALL payload members concatenated in archive order | Solid `stream_members()` |
| Single member by stable identity | Nonsolid / random solid `open()` |
| Dirs / RAR5 redirects omitted from the pipe | Demux size map = `is_payload_file()` |
| Honest non-zero exit on wrong password / missing / fatal | Typed errors |
| Password not forced onto argv | Process-list hygiene (`unrar` bare `-p` + stdin) |
| Safe hostile names (`-inul`, `@…`) | Already hardened via `-n./` |

rarfile’s probe order is `unrar` → `unar` → `7z`/`7zz` → `bsdtar`, with
`7z e -so -bb0` as the 7-Zip cmdline.

---

## `unar`

`unar -o -` concatenates selected members to stdout (no framing). Matches
`unrar p` on stored/nonsolid, solid **without** empty files, solid RAR4 **with**
empty files, volumes, correct-password encryption, hostile names (by `-i` index).

### Solid RAR5 + empty FILE — stdout **and** disk

Empty members are packed (28 bytes on `basic_solid__.rar`). Skipping them in the
extract argv does not avoid the crash: `unar` still decodes that solid slot.
Empty first / mid / last all fail when reading a **non-empty** member.

| Mode | apt 1.10.1 | built 1.10.7 |
| --- | --- | --- |
| `unar -o -` ALL / `file1.txt` only | **SIGSEGV**, 0 bytes | **rc=0**, 0 bytes |
| extract to a directory | SIGSEGV; 0-byte stubs | rc=1; non-empty files written empty (`Failed! (Unknown error)`) |
| empty member only (`-i 0`) | rc=0, 0 bytes | rc=0, 0 bytes |

**Tempdir fallback does not help.** The bug is the RAR5 solid decoder, not the
stdout writer.

Other gaps: wrong data password **rc=0** + empty; named `file.txt` concatenates
all `-ver` revisions; `-inul` parsed as an unar option unless `-i` is used;
password on argv only.

---

## `7z`

### The codec lottery (this is the spike)

Ubuntu `7zip` 23.01 **advertises** `Rar` / `Rar5` under **Formats** in `7z i`.
The **Codecs** list has Copy / LZMA / … and **no** `Rar5` until `7zip-rar` is
installed. Listing (`7z l basic_solid__.rar`) works either way. Data does not.

| `7z e -so` | Distro `7zip` only | After `apt install 7zip-rar` |
| --- | --- | --- |
| Stored `-m0` (basic nonsolid, blake2sp, volumes, corpus `large.rar`) | matches `unrar p` | same |
| Nonsolid `-m3` (fresh `rar a -m3`) | matched here without the plugin | matches |
| Solid RAR5 + empty (`basic_solid__.rar`) | **rc=2, 0 bytes, `Unsupported Method`** | **41 B, match** |
| Solid RAR4 + empty | `Unsupported Method` | match |
| Encrypted compressed (`encryption__.rar` `-m3`) | `Unsupported Method` | match, wrong password **rc=2** |
| Hostile `-m5` names | `Unsupported Method` | match with `-spd` |
| Disk extract of `basic_solid__.rar` | 0-byte files, rc=2 | files + concat match |

`7z i` showing a Rar5 **format** is not an availability probe. A backend would
need `Codecs:` containing `Rar5`, or a trial extract of a compressed (ideally
solid) member.

`7zip-rar` is Ubuntu **multiverse**, same non-free neighbourhood as `unrar`.
It does not magically make RAR “just a pip extra.” On Windows / official 7-Zip
builds the codec is usually bundled; on Debian/Ubuntu it is an extra package;
Homebrew `sevenzip` bottles exist but must be probed the same way.

### With the codec loaded — CLI vs `unrar p`

`7z e -so -bb0 -bd -spd -- archive [members…]` (rarfile’s shape plus `-spd`).

| Case | Result vs `unrar p` |
| --- | --- |
| Solid ALL-pipe including empty members | **match** (RAR5 and RAR4) |
| Two named members, argv reversed | concat in **archive order** (good) |
| Nested path `subdir/file2.txt` | match; **basename** `file3.txt` of a nested solid member is **empty** (need the archive path) |
| Missing member | **rc=0**, empty stdout (same hole as `unar`; `unrar` rc=10) |
| File-version ALL | matches `unrar p -ver` (includes history), not default `p` |
| `file.txt` (live) | live bytes only (good) |
| `file.txt;1` | **empty** (no `path;n`) |
| Hostile `-inul` / `@atfile` with `-spd` | match |
| Password | `-ppassword` on **argv**; bare `-p` + stdin is **not** accepted (wrong-password error) |
| Wrong password | rc=2, empty, `ERROR: Wrong password` (honest — better than `unar`) |
| Symlink / hardlink solid | **payload concat matches**, but **rc=2** `Data Error` on the redirect rows. Mapping 7z’s 2 → `CorruptionError` would false-positive a correct pipe. |
| `-bb0 -bd` | stderr empty; stdout stays gold |

---

## `bsdtar` / `unrar-free`

rarfile already warns: no solid, no password, no RARVM filters, no multi-volume.

Measured:

- Solid RAR4: `RAR solid archive support unavailable.`
- Solid RAR5: `Unsupported block header size`.
- **Stored nonsolid** `basic_nonsolid__.rar`: `--to-stdout` ran until the 8 s
  timeout after writing **~7 GB**. Do not spawn this as a decompressor.
- Encrypted: fails fast (header error), no password switch.
- `unrar-free` 0.1.3: `-x` extract only; `--help` has no stdout option.
  (rarfile’s docs mention a newer libarchive-based unrar-free with stdout; that
  is not what Ubuntu ships.)

---

## Mapping if we ever add a second engine

```
                    stdout concat of payload members
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
           unrar              7z + Rar5 codec           unar
        full, non-free     works when probed          easy brew,
        (what we use)      still non-free plugin      solid+empty dead
              │                     │                     │
              └────── bsdtar / unrar-free ────────────────┘
                     no solid / no stdout / stdout bomb
```

A `7z` opt-in would still need: codec probe, argv builder (`-spd`, `--`, full
member paths), password-on-argv, ignore or reinterpret rc=2 on redirect-only
errors, version-control policy (`-ver` size map vs `path;n`), tests in all
three dependency configs, and a C1/ADR 0002 reopen. That is more than
documenting the UnRAR source build.

---

## Recommendation

1. **Do not add `unar`.** Disk extract is not a workaround. Skipping empty
   members is not a workaround.
2. **Do not add `bsdtar` / `unrar-free`.**
3. **Do not silently fall back** to whatever is on `PATH` (C1).
4. **macOS install friction:** document `scripts/install-rarlab-unrar.sh`.
5. **If a second engine is wanted later:** opt-in `7z e -so` gated on a **Rar5
   codec** probe (trial extract, not `7z i` Formats). Treat missing
   `7zip-rar` as “not available”, same as missing `unrar`. Accept that this is
   still a non-free RAR decompressor, just a different package name.

---

## Open questions (only if we reopen 7z)

- Does Homebrew `sevenzip` bottle the Rar5 codec on Tahoe, or is it Formats-only
  like Debian `7zip`?
- Can we ignore 7z rc=2 when every payload member’s CRC verifies (symlink
  archives), or is that too much special-casing?
- Is `7zip-rar` any easier for macOS users than compiling UnRAR?

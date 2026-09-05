# Vendored RAR fixtures for the native reader tests

Most archives here are **generated** by:

```bash
uv run python scripts/gen_rar_fixtures.py
```

That script shells out to RARLAB `rar` (and, when the system `rar` is 7.x and
lacks `-ma4`, downloads a pinned RAR 6.24 linux-x64 binary into the user cache
solely to write RAR4 fixtures). Re-run the script after changing member layouts
or compression flags, then commit the updated binaries.

Multi-volume fixtures:

| Files | Notes |
| --- | --- |
| `tinyvol.part1.rar` / `tinyvol.part2.rar` | RAR5 `-v` / `.partN.rar` naming |
| `tinyvol_rnn.rar` / `tinyvol_rnn.r00` | RAR4 `-ma4 -vn` classic `.rar` + `.r00` naming |

File-version (`-ver`) fixtures:

| Files | Notes |
| --- | --- |
| `file_version__.rar` | RAR5 nonsolid `-m0 -ver`; three revisions of `file.txt` |
| `file_version__rar4.rar` | RAR4 nonsolid `-ma4 -m0 -ver`; same revisions |
| `file_version_solid__.rar` | RAR5 solid `-s -m3 -ver`; `a.txt` history + `b.txt` |

Encrypted / hash fixtures of note:

| Files | Notes |
| --- | --- |
| `encryption__.rar` | RAR5 `-ppassword`; HASHMAC tweaked CRC32 |
| `encryption_blake2sp.rar` | RAR5 `-m0 -htb -ppassword`; HASHMAC tweaked BLAKE2sp |
| `blake2sp.rar` | RAR5 `-m0 -htb`; plaintext BLAKE2sp (no encryption) |

## Tests that still need the `rar` writer at runtime

CI and macOS `setup-dev-env.sh` install **unrar only** — the writer is trialware
(ADR [0016](../../../dev-docs/decisions/0016-committed-rar-corpus-fixtures.md)).
The corpus RAR column and the fixtures in this directory already run without it.

These tests still shell out to `rar a` and **skip** when the writer is absent.
They are the leftover of that decision, not a new gap from dropping the Homebrew
cask. Linux `setup-dev-env.sh` still `apt-get install`s `rar`, so they run on a
provisioned Linux laptop; they do not run on CI.

| Test | Why it still writes |
| --- | --- |
| `test_sfx.py::test_sfx_rar_behind_a_low_entropy_stub_is_not_brotli` | `rar a` for a payload matching `_FILES` |
| `test_sfx.py::test_a_real_sfx_archive_auto_opens` | `rar a -sfx` — a real ~250 KB stub, not a hand-rolled `MZ` |
| `test_sfx.py::test_auto_open_matches_an_explicitly_sliced_stream[rar]` | same payload as the stub test |
| `test_volumes.py::test_multi_volume_rar_real_roundtrip` | live `rar a -v400b`; listing/read of committed volumes is already covered by `tinyvol*` above |

Committing a small SFX payload (and a real `-sfx` stub) the way this directory
already does for volumes would close the gap. Tracked in `dev-docs/IDEAS.md`.

## Many-member listing fixtures

Structural benchmark gate; CI has `unrar` but not `rar`, so these are committed
rather than built on demand:

| Files | Notes |
| --- | --- |
| `many_list_store__.rar` | RAR5 `-m0 -s -ep1`; 1000 tiny `fNNNNN.txt` members |
| `many_list_store_nonsolid__.rar` | RAR5 `-m0 -s- -ep1`; 256 tiny members |

Wildcard member names (`*` / `?` in the stored path). Windows cannot create these
on disk, so they are committed rather than built in the test. Compressed (`-m3`)
so the read takes the named-`unrar` route; stored members never would.

| Files | Notes |
| --- | --- |
| `wildcard_names__.rar` | RAR5 `-m3`; padded so members compress; add-order `subdir/aY.txt` then `a*.txt` / `aX.txt` / `b?.txt` / `b1.txt` / `only*.dat` |
| `wildcard_names_solid__.rar` | RAR5 `-s -m3`; same members and add order |
| `wildcard_names__rar4.rar` | RAR4 `-ma4 -m3`; same members as the nonsolid RAR5 |
| `wildcard_dirglob__.rar` | RAR5 `-m3`; `aaa/x.txt`, `dX/x.txt`, `d*/x.txt` — directory-component glob, refused |
| `wildcard_backslash__.rar` | RAR5 `-m3`; `a/b1.txt` readable; `a\b_TGT.txt` and `a\b*.txt` refused (Windows `unrar` treats `\` as a separator) |
| `wildcard_ver__.rar` | RAR5 `-m3 -ver`; `data.bin` two revisions, `data_TARGET`, `data*` — live glob must skip history |

Seek-respawn (named `unrar p` with `seekable_members=True`):

| Files | Notes |
| --- | --- |
| `seek_respawn_solid__.rar` | RAR5 `-s -m3 -ds`; add-order `prefix.bin` (1 MiB repeating) then `tail.txt`. `-ds` keeps that order so the later-member rewind sees a 1 MiB prefix. The prefix also fills the pipe so a mid-stream seek hits a live process |

(`-m0` store archives do not set the solid bit even with `-s`; the flags still
match the regeneration commands in `scripts/gen_rar_fixtures.py`.)

Extended timestamps (`-tsmca`: modification, creation, access). Default `rar a`
stores mtime only; these two are the ones that carry `accessed` / `created`.

| Files | Notes |
| --- | --- |
| `xtime__.rar` | RAR5 `-m0 -tsmca`; one `file.txt`; mtime 2020-01-15 12:00:00 UTC, atime 2021-06-20 18:30:00 UTC |
| `xtime__rar4.rar` | RAR4 `-ma4 -m0 -tsmca`; same member and pinned mtime/atime |

## Legacy (not regenerated)

| File | Provenance |
| --- | --- |
| `rar15-comment.rar` | Copied from [markokr/rarfile](https://github.com/markokr/rarfile) `test/files/` (ISC) |
| `rar202-comment-nopsw.rar` | Same |

Modern `rar` cannot emit RAR 1.5 / 2.0; keep these as-is.

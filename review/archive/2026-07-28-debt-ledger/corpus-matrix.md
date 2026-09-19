# T7 — corpus-matrix audit

> Ledger item **T7** ("corpus matrix thin spots", `tests.md`): *audit + cheap
> extensions; record deliberate exclusions*. Audited against `main` @ `41977c2`;
> extensions landed in this change. Measurements from `pytest tests/test_corpus_sweep.py`
> on a Linux host with `[all]` + `unrar`, **without** the `rar` writer or the `7z` CLI
> (the CI-equivalent binary set).

The corpus (`tests/sample_archives.py`) is the cross-format regression net: every
`(shape × format)` row feeds **both** the conformance sweep (`test_corpus_sweep.py`)
and mutation fuzz (`test_mutation_fuzz.py`), which iterate `CORPUS` directly. So a row
added here buys coverage in both nets at once — that leverage is why T7 is worth paying.

## The matrix after this change

**20 shapes / 74 rows.** Rows per format:

| Format | Rows | Format | Rows |
|---|---:|---|---:|
| `zip` | 13 | `dir` | 4 |
| `zip-aes` | 3 | | |
| `tar` | 10 | `iso` | 3 |
| `7z` | 8 | `tar.zst` | 2 |
| `rar` | 8 | `iso-joliet` | 1 |
| `tar.gz` | 7 | each remaining `tar.*` | 1 |
| | | each single-file codec + `gz-meta` | 1 |

Shape → formats:

| Shape | Built in |
|---|---|
| `basic` | `zip`, all 9 `tar.*`, `dir`, `iso`, **`iso-joliet`**, `7z`, `rar` |
| `comments` | `zip`, `rar` |
| `encoding` | `zip`, `tar`, `tar.gz`, `dir`, **`iso`**, `7z`, `rar` |
| `symlinks` | `zip`, `tar`, `tar.gz`, `dir`, **`iso`**, `7z`, `rar` |
| `symlink-loop` | `zip`, `tar` |
| `hardlinks` | `tar`, `tar.gz`, `rar` |
| `hardlinks-duplicate` | `tar` |
| `hardlinks-forward` | `tar`, `tar.gz` |
| `permissions` | `zip`, `tar`, `dir`, `7z` |
| `zip-compression-methods` | `zip` |
| `duplicates` | `zip`, `tar` |
| `large` | `zip`, `tar.gz`, `tar.zst`, `7z`, `rar` |
| `adversarial` / `adversarial-tar` | `zip` / `tar`, `tar.gz` |
| `encrypted` / `encrypted-mixed` | `zip`, **`zip-aes`**, `7z`, `rar` |
| `encrypted-multi` | `zip`, **`zip-aes`** |
| **`encrypted-header`** | **`7z`** |
| `single-file` / `single-file-meta` | 8 codecs / `gz-meta` |

Bold = added by this change.

## Finding 1 — the thin spots the ledger named, and what closed them

| Gap (from `tests.md`) | Status |
|---|---|
| ISO only in `basic` | **closed** — `iso` joins `encoding` (Joliet carries the non-ASCII names) and `symlinks` (Rock Ridge `SL` records, via a new `add_symlink` branch in `_iso_build`) |
| header-encrypted 7z outside sweep+mutation | **closed** — new `encrypted-header` shape (`-mhe` equivalent: py7zr `header_encryption=True`), so it is now in **both** nets |
| header-encrypted RAR outside sweep+mutation | **closed for mutation** — `encrypted_header__.rar` / `__rar4.rar` fixtures added to the static-RAR mutation intake. Not in the sweep: the declarative builder cannot emit `-hp` (see exclusions) |
| multi-volume outside sweep+mutation | **partially closed** — `tinyvol.part1.rar` added to mutation as a lone volume-flagged stream. Volume *joining* stays out of both nets (see exclusions) |

The `encrypted-header` shape matters beyond matrix-filling: it is the only corpus row
whose member **names** are ciphertext, so it drives the native `kEncodedHeader` path
that threat-model **O8** hardened. The sweep now also asserts the header-encryption
contract directly — `open_archive` without a password raises `EncryptionError` rather
than yielding a plausible empty listing (`test_corpus_sweep.py`, `entry.encrypt_header`
branch), which is O8's residual failure mode expressed as a corpus assertion.

## Finding 2 — 11 of 71 rows never ran in CI, and only 8 of them deliberately

> Counts are as of the audit (71 rows). The `zip-aes` rows added when closing residual
> 5 bring the corpus to 74; the CI-skipped set is unchanged at the 8 `rar`-writer rows.

This is the audit's real result, and it is not visible from the matrix alone.

| Gate | Rows | Deliberate? |
|---|---:|---|
| `rar` **writer** binary | 8 | **Yes** — `ci.yml` installs `unrar` only and explicitly removes the writer on macOS ("keep writer off the PATH here"), because corpus RAR digest expectations are Linux-fixture-oriented |
| `7z` **CLI** (encrypted-ZIP builder) | 3 | **Was not** — no workflow installed it. **Closed 2026-07-29**: `ci.yml` now installs `p7zip-full` on the Linux `all` / `all-lowest` legs |

The RAR half is a recorded decision, and RAR keeps real CI coverage through the
committed fixtures in `tests/fixtures/rar/` (which is what this change extended).

The `7z`-CLI half is not a decision anyone made. `encrypted`, `encrypted-mixed`, and
`encrypted-multi` build their **ZIP** rows by shelling out to `7z` (stdlib `zipfile`
cannot write encryption), and no workflow installs it — so whether encrypted-ZIP
listing and per-member password behaviour is swept at all depends on whatever the
runner image happens to ship, and differs across the Linux/macOS/Windows legs. The
coverage is not absent, it is **unpinned**, which is worse: it can disappear on a
runner-image bump with no test turning red.

**Closed in the follow-up (2026-07-29).** `ci.yml` installs `p7zip-full` on the Linux
`all` / `all-lowest` legs, plus a `command -v 7z` verify step so the coverage fails
loudly instead of silently skipping if the package ever stops providing that name
(`p7zip-full` is transitional on Ubuntu 24.04 → `7zip`, but still ships `/usr/bin/7z`,
which is the name `_zip_build_encrypted` invokes). No test changes were needed: the
three rows pass as written, and sweep + mutation go from 304 passed / 55 skipped to
**319 / 40** with `7z` present.

**Linux only, deliberately.** The other two candidates were rejected:

- *macOS / Windows legs* — Homebrew's `p7zip` formula is deprecated in favour of
  `sevenzip` (which ships `7zz`, not `7z`), and Windows needs its own path handling. The
  cost is real and the signal is not: these rows exercise ZipCrypto/AES **reading**,
  which is pure Python, over flat files with no symlinks or mode bits — none of the
  path/symlink/junction surface those legs exist for. Linux pins the coverage; the
  other legs would be near-duplicates.
- *Teaching the builder to write encrypted ZIPs in-process* (generalising
  `tests/zipcrypto.py`, which already writes single-entry ZipCrypto members, to
  multi-member with mixed per-member passwords) — this would drop the binary dependency
  everywhere including for local contributors, but it costs the **independent-oracle**
  property: today 7z writes the fixtures and archivey reads them, so the rows cross-check
  two implementations. Building them with our own writer risks writer and reader sharing
  the same wrong assumption. Keep the CLI for the corpus; `zipcrypto.py` stays for the
  targeted cases that need a hand-built archive.

### Which cipher each encrypted-ZIP builder actually emits

Worth stating explicitly, because the repo has three encrypted-ZIP builders and they are
**complementary, not redundant** — they write different ciphers for different consumers:

| Builder | Cipher emitted | Consumers | In sweep? | In mutation? |
|---|---|---|---|---|
| `_zip_build_encrypted` (7z CLI), `zip` key | **ZipCrypto** (PKWARE; 7-Zip's ZIP default — verified STORED + encrypted flag, no `0x9901` field) | corpus `encrypted*` rows | yes | yes |
| `_zip_build_encrypted` with `-mem=AES256`, `zip-aes` key | **WinZip AES** (AE-2 / AES-256, verified via the `0x9901` field) | corpus `encrypted*` rows | yes | yes |
| `tests/zipcrypto.py` | ZipCrypto, single-entry | `test_cli.py`, `test_zip_multipassword.py` (the 1-in-256 check-byte hazard) | no | no |
| `tests/zip_aes_fixture.py` | **WinZip AES** (method 99, AE-1/AE-2) | `test_zip_aes.py`, `benchmarks/fixtures.py` (structural gate) | no | no |

AES support was well covered by targeted tests and the bench gate, but originally **no
corpus row was AES** — WinZip AES members reached neither the sweep's cross-format
conformance assertions nor mutation fuzz. **Closed 2026-07-29** with the `zip-aes`
builder-variant key (`7z a -tzip -mem=AES256`), added to all three encrypted shapes so
single-password, mixed plain/encrypted, and multi-password AES all sweep and mutate.
Verified the rows really carry AE-2/AES-256 members rather than silently falling back
to ZipCrypto.

## Finding 3 — ISO exercised only one of the reader's three name sources

`iso_reader.py:300-305` picks its namespace in order: Rock Ridge → Joliet → plain
ISO-9660. Every corpus ISO was built with `rock_ridge="1.09", joliet=3`, so **only the
Rock Ridge branch was ever taken** — the Joliet fallback was corpus-dead.

Closed with a builder-variant key, `iso-joliet` (the same pattern `gz-meta` uses for
`gz`): same shape, `iso.new(interchange_level=3, joliet=3)` with no Rock Ridge. Joliet
round-trips the real names, so `basic`'s expectations hold unchanged. Verified the two
rows take different branches — `iso` → `namespace='rock_ridge'`, `iso-joliet` →
`namespace='joliet'`, both listing all 7 members.

The third branch (plain ISO-9660, no extensions) stays uncovered by the corpus: its
names are the mangled `8.3` `F1.TXT;1` forms, so it needs its own expectations rather
than a shared shape. Recorded as a residual.

## Deliberate exclusions (not gaps — do not re-open these)

| Not covered | Why |
|---|---|
| Multi-volume as a `CORPUS` shape | Structural: a corpus row is one `(entry, key)` → **one** archive path (`corpus_archive_path`), and a volume set is N sibling files that must be discovered together. Covered instead by `test_volumes.py` + the `tinyvol*` / `tinyvol_rnn*` fixtures (RAR5 `.partN` and RAR4 `.r00` naming), now plus lone-first-volume mutation |
| RAR solid (`-s`), `-hp`, file-version rows in the sweep | The declarative `_rar_build` emits none of these flags, and the `rar` writer is off CI's PATH anyway. Covered by committed fixtures (`basic_solid__*`, `encrypted_header__*`, `file_version__*`), which is where new RAR shapes should go |
| Hardlinks in `zip` / `7z` / `iso` | The formats have no hardlink record; `dir`/`tar`/`rar` carry the semantics |
| `permissions` in `iso` / `rar` | The sweep only asserts mode bits for `_MODE_FORMATS` (tar family + zip), so these rows would add a row without adding an assertion |
| `adversarial` beyond `zip` / `tar` | The shape targets extraction-time name/link safety, which is enforced above the backend; the two rows cover both name models (per-member paths vs. tar link records) |
| Single-file codecs outside `single-file*` | One member by definition — multi-member shapes are meaningless for them |
| `zip-compression-methods` outside `zip` | Per-member ZIP `compress_type` has no analogue elsewhere |

## Residual gaps (recorded, not paid)

1. ~~**Encrypted-ZIP corpus rows are unpinned in CI**~~ — **closed 2026-07-29**;
   `p7zip-full` on the Linux legs + a verify step (Finding 2). The 8 `rar`-writer rows
   remain skipped by design, so 8 of the now-74 rows still do not run in CI.
2. **`encrypted-multi` is ZIP-only.** For `7z` the builder rejects it outright
   (`_7z_build`: py7zr takes one archive password); for `rar` the builder *does*
   support per-member password groups since v7, but the row would only ever run where
   the `rar` writer exists — i.e. never in CI.
3. **Plain ISO-9660 (no RR, no Joliet) is corpus-dead** (Finding 3): needs its own
   expectations for the `8.3` mangled names.
4. **RAR multi-volume joining** is in `test_volumes.py` only, never under mutation —
   mutating a volume *set* needs a multi-path harness the mutation net does not have.
5. ~~**No corpus row uses WinZip AES.**~~ — **closed 2026-07-29** by the `zip-aes`
   variant key on all three encrypted shapes, gated on the `[crypto]` extra via the new
   `READER_PACKAGES` table so the rows skip (not fail) in the core-only leg.

## Verification

- Sweep: **60 passed, 11 skipped** (was 56 / 11) — the three new sweep rows
  (`encoding-iso`, `symlinks-iso`, `encrypted-header-7z`) plus `basic-iso-joliet`.
- Mutation: static-RAR intake 8 → 20 params, all passing with `unrar` present; a
  deeper sweep (`ARCHIVEY_FUZZ_MUTATIONS=60`, 10× default) over the new sources is
  clean — no raw exceptions or hangs from the native RAR header decryptor or the 7z
  encoded-header path.
- `GENERATOR_VERSION` bumped 7 → 8 so cached archives regenerate for the changed
  7z / ISO builders.

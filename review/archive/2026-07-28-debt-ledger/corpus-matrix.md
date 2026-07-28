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

**20 shapes / 71 rows.** Rows per format:

| Format | Rows | Format | Rows |
|---|---:|---|---:|
| `zip` | 13 | `dir` | 4 |
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
| `encrypted` / `encrypted-mixed` | `zip`, `7z`, `rar` |
| `encrypted-multi` | `zip` |
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

## Finding 2 — 11 of 71 rows never run in CI, and only 8 of them deliberately

This is the audit's real result, and it is not visible from the matrix alone.

| Gate | Rows | Deliberate? |
|---|---:|---|
| `rar` **writer** binary | 8 | **Yes** — `ci.yml` installs `unrar` only and explicitly removes the writer on macOS ("keep writer off the PATH here"), because corpus RAR digest expectations are Linux-fixture-oriented |
| `7z` **CLI** (encrypted-ZIP builder) | 3 | **No** — no workflow installs it |

The RAR half is a recorded decision, and RAR keeps real CI coverage through the
committed fixtures in `tests/fixtures/rar/` (which is what this change extended).

The `7z`-CLI half is not a decision anyone made. `encrypted`, `encrypted-mixed`, and
`encrypted-multi` build their **ZIP** rows by shelling out to `7z` (stdlib `zipfile`
cannot write encryption), and no workflow installs it — so whether ZipCrypto/AES ZIP
listing and per-member password behaviour is swept at all depends on whatever the
runner image happens to ship, and differs across the Linux/macOS/Windows legs. The
coverage is not absent, it is **unpinned**, which is worse: it can disappear on a
runner-image bump with no test turning red.

Not fixed here — it is a CI-workflow change, and pinning it properly means deciding
between installing `p7zip` on every leg or teaching the builder to write encrypted ZIPs
in-process. Recorded as a residual below rather than silently carried.

Note that ZIP *AES* decryption itself is separately covered by `tests/test_zip_aes.py`
and the structural bench gate (T3), both using in-process fixtures — so this gap is
about the corpus sweep's cross-format assertions, not about AES support being untested.

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

1. **Encrypted-ZIP corpus rows are unpinned in CI** (Finding 2). Fix is a workflow
   decision: install `p7zip` on every leg, or write encrypted ZIPs in-process.
2. **`encrypted-multi` is ZIP-only.** For `7z` the builder rejects it outright
   (`_7z_build`: py7zr takes one archive password); for `rar` the builder *does*
   support per-member password groups since v7, but the row would only ever run where
   the `rar` writer exists — i.e. never in CI.
3. **Plain ISO-9660 (no RR, no Joliet) is corpus-dead** (Finding 3): needs its own
   expectations for the `8.3` mangled names.
4. **RAR multi-volume joining** is in `test_volumes.py` only, never under mutation —
   mutating a volume *set* needs a multi-path harness the mutation net does not have.

## Verification

- Sweep: **60 passed, 11 skipped** (was 56 / 11) — the three new sweep rows
  (`encoding-iso`, `symlinks-iso`, `encrypted-header-7z`) plus `basic-iso-joliet`.
- Mutation: static-RAR intake 8 → 20 params, all passing with `unrar` present; a
  deeper sweep (`ARCHIVEY_FUZZ_MUTATIONS=60`, 10× default) over the new sources is
  clean — no raw exceptions or hangs from the native RAR header decryptor or the 7z
  encoded-header path.
- `GENERATOR_VERSION` bumped 7 → 8 so cached archives regenerate for the changed
  7z / ISO builders.

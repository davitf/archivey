# Parity residuals (Path, password, is_current, STREAM_REWIND)

## F6 — Path-gated `compressed_size` on single-file (CONFIRMED residual)

After `#225`, seekable-stream probes use `_with_seekable_source` for trailer /
CRC / lzip index (`single_file_reader.py:215–316`). **Still Path-only:**

```173:177:src/archivey/internal/backends/single_file_reader.py
        compressed_size = (
            os.path.getsize(self._source)
            if isinstance(self._source, Path) and self._source.exists()
            else None
        )
```

**Repro:**

```text
BytesIO gzip: size=None compressed_size=None hashes={CRC32: …}   # CRC filled
Path gzip:    size=None compressed_size=41  hashes={CRC32: …}
```

**lzip digest:** `_probe_lzip_index` is seekable-gated, not Path-gated — **fine**.

**Other `isinstance(..., Path)` in backends** (post-#225 classification):

| Site | Role |
|---|---|
| `zip_reader.py:433` | Measurement: open Path FD | Legitimate open |
| `tar_reader.py:304,324` | Codec / tarfile open | Legitimate open |
| `tar_reader.py:586` | Path → SEEKABLE capability | Correct (Path always seekable) |
| `single_file_reader.py:159` | Measure SharedSource | Legitimate |
| `single_file_reader.py:225,259` | Peek via `_with_seekable_source` | Legitimate (streams too) |
| `rar_reader.py:390,431` | Volume Path vs stream materialize | Legitimate |
| `iso_reader.py:294` | `open` vs `open_fp` | Legitimate |
| `directory_reader.py:324` | Path required | Format law |

**Fix vehicle:** bug fix — `compressed_size = seek_end` for seekable streams.

---

## F15 — Password laziness residual (CONFIRMED format law + docs caveat)

| Path | When password runs | Verdict |
|---|---|---|
| Header-encrypted RAR | `open` / parse (`rar_reader.py:473–507`) | Format law — listing needs plaintext headers |
| Header-encrypted 7z | Encoded-header decode at open (`sevenzip_reader.py:215–275`) | Format law; documented `formats.md` |
| Solid folder / data encrypt 7z | First member of folder (`_password_for_folder`) | Lazy (`#225`) |
| Solid RAR pipe | First read into pass (`rar_reader.py:672–678`) | Lazy |
| Data-only RAR encrypt | Member read via unrar | Lazy (repro: `encryption__.rar` lists without pw) |
| ZIP ZipCrypto confirm | Member open (`zip_reader.py:887–895`) | Lazy; STORED confirm expensive (documented) |
| ISO | `SUPPORTS_PASSWORD=False`; central reject | N/A |

**Docs tension:** `reading-members.md:74–77` says no password until you read —
true for **data** encryption / `stream_members` skips; **false** for
header-encrypted archives (fail at open). `formats.md` already documents header
cases. **Fix vehicle:** docs-only — one caveat on the laziness bullet.

ISO open does no password work (nothing to residual). ZipCrypto does not confirm
at archive open.

---

## F12 — Duplicate `is_current` (CONFIRMED fine)

```87:106:src/archivey/internal/base_reader.py
def _apply_last_entry_wins_is_current(members: list[ArchiveMember]) -> None:
    ...
    # unique names left unchanged so RAR path;N history keeps backend flag
```

Applied on materialization paths (`base_reader.py:817,854,946`).
RAR sets `is_current=not version_history` and presents `path;n`
(`rar_reader.py:134–137,604`). Guide story in `opening-and-listing.md` matches.

---

## F5 — `STREAM_REWIND_REDECOMPRESSES` is usage, not archive (CONFIRMED)

**Emission:** `archive_stream.py:441–451` inside seek path when
`RewindWarning` is set and the caller seeks backward (`from_offset` /
`to_offset` are stream positions).

Codecs attach `RewindWarning` via `rewind_warning()` (`codecs.py`). Message text
is explicitly about **seeking backward** / installing `[seekable]`.

O-23 rule: diagnostics describe the **archive**, not caller usage. This code is
the awkward residual the brief flagged — do not churn without a decision
(QUESTIONS Q-rewind).

Solid random `open` deliberately emits **no** diagnostic
(`archive-reading` `:512`) — that half is settled.

---

## Encryption wrong-password shapes (matrix note)

Not a new accident: shapes differ where formats differ (7z AES+store can yield
garbage + `DIGEST_UNVERIFIABLE`; RAR/`EncryptionError`; ZipCrypto confirm).
Caller-visible divergence is mostly **format law** — keep as Gotchas / formats
rows; do not force one exception type across formats.

## TAR corrupt final header (seed A — CONFIRMED format law)

With the suite fixture (`tests/test_tar._tar_corrupt_final_header`):
RA → `CorruptionError`; streaming → soft `ARCHIVE_EOF_MARKER_MISSING`.
Already tested in the product suite; open-issues **P3** (native TAR walker) is
the product follow-on. Not an accident to delete before tag — label as
format-forced residual so Gotchas stay honest.

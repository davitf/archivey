## 1. Join

- [x] 1.1 Generalise `internal/volumes.py`'s `.7z.NNN` discovery to `.7z|.zip` — one
      `_NUMBERED_VOLUME_RE` and one `_validate_numbered_volume_sequence`, not a third
      near-identical branch; the fast-reject keeps skipping the `stat` for plain names
- [x] 1.2 Allow `ContainerFormat.ZIP` through `core.py`'s joined-source check
- [x] 1.3 Stop the two rejoin-first refuses (`core.py`, `ZipReader.__init__`) firing on a
      set that was just joined — the joined source keeps part one's name
- [x] 1.4 Report `is_multivolume` / `extra["zip.volume_count"]` from the join, as 7z does

## 2. Keep refusing

- [x] 2.1 Info-ZIP `.zNN`, its final `.zip` (EOCD disk fields), and a `.zip.NNN` part with
      no siblings on disk are untouched — verified against a real `zip -s` set as well as
      the synthesised fixtures

## 3. Docs

- [x] 3.1 `dev-docs/formats/zip.md` §2.2, §3, §5, §6, §7
- [x] 3.2 `dev-docs/open-issues.md` P2; `dev-docs/topics/prefixed-archives.md` (ZIP now
      shares P17's `vol.exe.001` blind spot)

## 4. Verify

- [x] 4.1 Red–green tests in `tests/test_zip.py` and `tests/test_volumes.py`; the joined
      set is built with real `7z -tzip -v`, the Info-ZIP shapes are synthesised because
      `zip` is absent on CI, macOS and Windows
- [x] 4.2 `./scripts/check.sh --fix` and `./scripts/test.sh --all-configs`
- [x] 4.3 `openspec validate --strict 2026-09-03-zip-numbered-volume-joining`, plus a
      dry-run archive diffed and reverted: the RENAMED + MODIFIED pair rewrites the
      requirement in place rather than moving it to the end of `format-zip`
- [x] 4.4 `openspec archive 2026-09-03-zip-numbered-volume-joining --yes`

# Tasks — tar, ISO and link names

## 1. Code

- [x] 1.1 `tar_reader._recover_raw_name` picks the codec from where the name came from; `_close_archive` releases the owned stream in a `finally`.
- [x] 1.2 `iso_reader` walks directory records; type from the PX mode; `;N` versions keep history rows; `rr_moved` hidden; members open from their record.
- [x] 1.3 `resolve_link_target_name` keeps `..` in hardlink targets.
- [x] 1.4 The collector logs the escaped `diagnostic.message`.

## 2. Proof and documents

- [x] 2.1 Red-green tests in `tests/test_tar.py`, `tests/test_iso.py`, `tests/test_naming.py`, `tests/test_diagnostics.py`.
- [x] 2.2 `docs/formats.md` ISO notes; `MemberExtra` gains `iso.version`.
- [x] 2.3 `openspec validate --strict tar-iso-link-names`, then archive this change.

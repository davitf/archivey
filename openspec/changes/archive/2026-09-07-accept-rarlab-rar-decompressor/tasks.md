## 1. Finder and banner

- [x] 1.1 Extend `_parse_unrar_banner` to accept standalone `RAR x.yy` without matching inside `UNRAR`
- [x] 1.2 `find_rarlab_unrar`: try `unrar` then `rar`; 6.0 floor; prefer usable `unrar`
- [x] 1.3 Error messages name both acceptable binaries; still name refused lookalikes

## 2. Tests and docs

- [x] 2.1 Banner and finder tests: writer accepted, `unrar` preferred, lookalike `rar` refused
- [x] 2.2 `docs/install.md` and handbook §1 / §3 / §10 #20

## 3. Verify and archive

- [ ] 3.1 `openspec validate --strict accept-rarlab-rar-decompressor`
- [ ] 3.2 `./scripts/check.sh --fix` and `./scripts/test.sh tests/test_rar_reader.py`
- [ ] 3.3 Archive this change in this PR (`openspec archive accept-rarlab-rar-decompressor --yes`)

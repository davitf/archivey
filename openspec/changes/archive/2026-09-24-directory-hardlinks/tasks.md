## 1. Implement

- [x] 1.1 Track `(st_dev, st_ino)` of multiply-linked regular files per walk in `directory_reader`; later names become `HARDLINK` to the first. On Windows, `lstat` regular files, since scandir's data has no inode or link count.
- [x] 1.2 Tests in `tests/test_directory.py`: hardlinks listed and read, a link count from outside the root, extraction of both names.

## 2. Verify

- [x] 2.1 `./scripts/check.sh` and `./scripts/test.sh`
- [x] 2.2 `openspec validate --strict directory-hardlinks`
- [x] 2.3 `openspec archive directory-hardlinks --yes`

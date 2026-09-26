# Sevenzip fixtures

`lz4.7z` is copied from py7zr's `tests/data/lz4.7z` (method `0x04f71104`).
Neither stock 7-Zip nor py7zr can extract it; Archivey decodes it via shared
`Codec.LZ4`. Used by `tests/test_sevenzip_reader.py`.

`links_mid_folder_solid.7z` and `links_mid_folder_nonsolid.7z` hold the same tree, written
by 7-Zip 23.01 on Linux with `-snl` (store symlinks as links). In path order: `a_link`
(→ `b.txt`), `b.txt` (3200 bytes of text), `c_link` (→ `b.txt`), `d.txt` (3153 bytes),
`e_link` (→ `d.txt`). A symlink's target is its member data, so in the solid archive the
three links sit at the start, the middle and the end of one LZMA2 folder of 6368 bytes;
link data ends at offsets 5, 3210 and 6368. The corpus's other 7z fixtures come from py7zr
and none has a link mid-folder. Used by `tests/test_one_member_listing.py` to measure how
much of a folder reading the link targets decodes.

```sh
mkdir -p tree && cd tree
# b.txt and d.txt: ~3 KB of space-separated words each
ln -s b.txt a_link && ln -s b.txt c_link && ln -s d.txt e_link && cd ..
7z a -snl links_mid_folder_solid.7z tree
7z a -snl -ms=off links_mid_folder_nonsolid.7z tree
```

`encrypted_link.7z` and `encrypted_link.zip` hold `a.txt` (`aaaa`), `b_link` (→ `a.txt`) and
`c.txt` (`cccc`), written by 7-Zip 23.01 on Linux with the password `SECRET` (AES-256 for
the 7z, ZipCrypto for the ZIP). The link's target is encrypted member data. Used by
`tests/test_one_member_listing.py` to check what `read_link_targets=False` reads and when
it asks for a password. They are committed because not every platform's `7z` stores a
symlink as a link.

```sh
mkdir tree && cd tree
printf aaaa > a.txt && ln -s a.txt b_link && printf cccc > c.txt
7z a -t7z -snl -pSECRET ../encrypted_link.7z a.txt b_link c.txt
7z a -tzip -snl -pSECRET ../encrypted_link.zip a.txt b_link c.txt
```

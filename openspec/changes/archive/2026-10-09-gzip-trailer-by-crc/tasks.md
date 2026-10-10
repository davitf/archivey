## 1. Implementation

- [x] 1.1 Share the output-prefix checksum between the zlib Adler-32 check and the gzip backstop
- [x] 1.2 Find the gzip trailer by the CRC-32 of the output and its length
- [x] 1.3 Tests; stop the `gzip_accel` target accepting a wrong last ISIZE
- [x] 1.4 Update the spec and `dev-docs/formats/gzip.md`

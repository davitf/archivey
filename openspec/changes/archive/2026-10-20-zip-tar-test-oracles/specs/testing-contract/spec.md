## MODIFIED Requirements

### Requirement: Cross-validate native readers against reference oracles

The system SHALL validate its native readers against reference implementations used only
as test oracles: `zipfile` for ZIP, `tarfile` for TAR, `py7zr` for 7z, `rarfile` and
`unrar` for RAR. The `7z` CLI builds 7z fixtures but is not an oracle: its output is not
compared, except for the two codecs named below. For representative corpora, native
member metadata and decompressed bytes MUST match the oracle. Oracle libraries are
dev-group dependencies only (`zipfile` and `tarfile` are the standard library) and the
library SHALL NOT import any of them at runtime. Oracle-backed tests SHALL skip, not
fail, when the oracle library or CLI is unavailable.

The 7z corpus MUST cover core codecs supported without extras (LZMA1, LZMA2, simple
BCJ filters other than ARM64, Delta, BZip2, Deflate, STORED), optional PPMd /
Deflate64 and AES-encrypted archives under `[recommended]`, and BCJ2 folders written
by the `7z` CLI. Two codecs are exceptions to the rule that the `7z` CLI is not an
oracle, because `py7zr` cannot read them and so the CLI is the only oracle for them:
BCJ2, and the ARM64 branch filter (`py7zr` decodes BCJ filters through `pybcj`, which
has no ARM64 filter). ARM64 is covered by dedicated tests that compare against the
bytes `7z` stores, not by the declarative corpus. Unrecognized method IDs MUST raise
the documented unsupported-codec error rather than returning bytes that diverge from
the oracle.

The RAR corpus MUST cover RAR4 and RAR5, solid and nonsolid, stored M0, symlinks,
hardlinks/`FILE_COPY`, multi-volume sets, header-encrypted RAR5 (under `[recommended]`), Blake2sp-only members, and at least one RAR5 `-ver` file-version
archive. After the native RAR reader registers, RAR corpus entries MUST run (not
skip solely for “reader not implemented”).

`rarfile` omits file-version history rows. Dedicated `-ver` tests SHALL assert
native listing/read/`unrar` behavior directly and MUST NOT require rarfile list
equality for those history members. Non-versioned RAR corpus entries continue to
cross-check metadata and bytes against rarfile/`unrar`.

#### Scenario: native-reader oracle matrix

| Case | Expected |
| --- | --- |
| ZIP fixture or corpus entry parsed natively and by `zipfile` | Central-directory fields match field by field; member bytes match `zipfile.read` |
| TAR corpus entry, or GNU tar archive, walked natively and by `tarfile` | Member fields and bytes match; GNU tar cases skipped if GNU tar is absent |
| 7z corpus entry read by native reader and `py7zr` | Metadata and bytes match; skipped if oracle unavailable |
| RAR corpus entry read by native reader and `rarfile`/`unrar` | Metadata and bytes match; skipped if oracle unavailable |
| 7z entry uses BCJ2 | Bytes match `7z x`; `py7zr` is not consulted |
| 7z entry uses the ARM64 filter | Bytes match what `7z` stored; `py7zr` is not consulted; skipped if `7z` cannot write ARM64 |
| 7z entry uses unknown method ID | Documented unsupported-codec error; no guessed output |
| RAR solid+links / multi-volume / header-encrypted entry | Exercised once native RAR is registered; skip only if `unrar`/crypto/oracle absent |
| RAR5 `-ver` history members | Native exposes `path;n` + live path; bytes match `unrar p` exact name / `-ver`; rarfile list equality not required for history rows |

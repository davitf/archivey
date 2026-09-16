## MODIFIED Requirements

### Requirement: Optional extras enable specific capabilities

The system SHALL expose exactly **four** user-facing extras, each answering a question a
user asks about their own environment rather than naming an internal dependency group.
7z and RAR reading are native for the common case, so extras cover less-common member
codecs, encryption, ISO, seeking accelerators, and the CLI.

| Extra | Pulls in | Enables |
| --- | --- | --- |
| *(none)* | stdlib only + native parsers | ZIP, TAR + stdlib compressed TAR variants including `.tar.Z`, GZ, BZ2, XZ, Z (unix-compress), directory, 7z read for common codecs (including LZMA2+BCJ), RAR metadata/listing; RAR data still needs RARLAB `unrar` |
| `[recommended]` | `pyppmd`, `inflate64`, `brotli`, `lz4`, `pybcj`, `backports.zstd` on Python <3.14, `cryptography`, `pycdlib`, `tqdm` | Every format and codec that installs everywhere: PPMd, Deflate64, Zstd, Brotli, LZ4, LZMA1+BCJ, AES/crypto (7z, RAR headers, WinZip AES ZIP), ISO, CLI progress |
| `[seekable]` | `rapidgzip` | Faster gzip/bzip2 decompression and random access via rapidgzip / bundled `IndexedBzip2File` |
| `[free-threaded]` | `pycdlib`, `lz4`, `tqdm`, `backports.zstd` on Python <3.14, `cryptography` on Python >=3.14 | The subset that keeps the GIL **disabled** on free-threaded builds |
| `[all]` | `[recommended]` + `[seekable]` | Everything |

The system SHALL make `[recommended]` the sensible all-useful install and `[seekable]`
an opt-in on top of it. `[seekable]` MUST remain separate rather than folded into
`[recommended]`: it is a heavy native build that can fail to compile, importing it
re-enables the GIL on free-threaded builds, and it carries the accelerator
close-before-finalize hazard recorded in `known-issues.md`.

The system SHALL NOT ship an extra per format. Removed names (`[7z]`, `[rar]`,
`[crypto]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`, `[recommended-lite]`) MUST NOT be
reintroduced as aliases: format-named extras whose contents are capability-scoped are what
made install hints name the wrong thing.

Installing an extra MAY pull dependencies unrelated to the capability the caller wanted —
`[recommended]` is deliberately a single broad bundle. This supersedes the previous
requirement that each extra avoid unrelated dependencies, and is an accepted trade for a
table small enough that no user has to reason about it. (Extras, once published, can never
be removed; fewer names is the durable choice.)

`PackageNotInstalledError` install hints MUST name the extra that provides the missing
package (`[recommended]`, or `[seekable]` for rapidgzip) and MUST NOT name a format,
since member codecs are shared across containers.

A missing package is reported through two channels — the hint on `MissingComponent` (what
listing and `format_availability()` surface) and the message of the
`PackageNotInstalledError` raised when a read actually needs it. Both MUST derive from a
single declared requirement per package, so a packaging change cannot leave one channel
naming an extra that no longer exists while the other is correct.

AES block operations are the only third-party crypto dependency (`CryptoBackend` in
`internal/streams/crypto.py`; PBKDF2/SHA/HMAC are stdlib). The system SHALL ship exactly
one crypto backend, `cryptography`. Alternate AES providers MUST NOT be added merely to
work around a runtime where `cryptography` cannot yet install, since that doubles the
security surface for a transient gap; the backend abstraction is retained so the choice
stays cheap to revisit.

The system SHALL treat `[free-threaded]` as a **measured, moving** set: membership is
determined by whether importing the package leaves the GIL disabled on a free-threaded
build, it is expected to widen as the ecosystem adds support, and it MAY eventually
collapse into `[recommended]`. It MUST NOT be read as a claim that archivey is only
free-thread-safe with it. CI MUST install exactly this set on a free-threaded interpreter
and assert the GIL is still disabled, so the set cannot rot silently.

The system SHALL keep `[all]` as the conventional superset; it MUST resolve to
`[recommended]` + `[seekable]`.

Missing optional libraries MUST degrade by one rule: raise `PackageNotInstalledError` or
`UnsupportedFeatureError` only when bytes cannot be produced, and skip any integrity check
that cannot be computed with an integrity diagnostic/warning instead of failing the read.

RAR5 BLAKE2sp verification is implemented **natively on stdlib `hashlib`**
(`internal/hashing/blake2sp.py`) and requires no third-party package; no extra pulls a
Blake2sp backend.

The system SHALL keep `py7zr` and `rarfile` as **dev-only** test oracles. 7z writing is
not shipped in the current release (no `[7z-write]` extra); when writing lands in a later
phase it MAY reintroduce a dedicated write extra. BCJ2-filtered 7z members MUST remain
unsupported by every extra. No user-facing extra SHALL pull an alternate RAR decompressor
library or tool wrapper, and no extra can supply the RARLAB `unrar` binary.

Development tools, oracle libraries, and fixture generators such as `ncompress` SHALL live
in the PEP 735 `dev` dependency group, not in user-facing runtime extras. The system SHALL
NOT list `uncompresspy` in any user-facing extra or the `dev` group.

#### Scenario: extras matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` | No third-party runtime packages; core formats work |
| `pip install archivey[recommended]` | Every format/codec/crypto/ISO/CLI capability that installs everywhere; no rapidgzip |
| `pip install archivey[seekable]` after `[recommended]` | Adds gz/bz2 random access and speed |
| `pip install archivey[recommended]` on free-threaded 3.13 | **Fails** — `cryptography` -> `cffi` does not support free-threaded 3.13; documented, not worked around |
| `pip install archivey[free-threaded]` on free-threaded 3.13 | Resolves without `cryptography`; importing every included package leaves the GIL disabled |
| `pip install archivey[free-threaded]` on free-threaded 3.14 | Resolves **with** `cryptography` (cffi supports 3.14t); GIL still disabled |
| `pip install archivey[all]` | `[recommended]` + `[seekable]` |
| ZIP member needs Deflate64 with no extras | `PackageNotInstalledError` naming `archivey[recommended]`, never a format-named extra |
| Any optional package absent, compared across both channels | The hint in the raised error and the one reported by `format_availability()` are the same string |
| `pip install archivey[7z]` | Fails: the extra no longer exists |

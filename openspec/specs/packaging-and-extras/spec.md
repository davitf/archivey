# Packaging and Extras

## Purpose

Install-time contract for Archivey: zero-dependency core, named optional extras,
supported runtimes, version exposure, and package layout. Runtime behavior when an
optional backend or system tool is absent belongs to `backend-registry` and the
format specs.

## Related specs

| Spec | Relationship |
| --- | --- |
| `backend-registry` | Runtime registration, graceful degradation, install hints |
| `format-7z` | Native 7z reading, optional codecs, AES, unsupported BCJ2 |
| `format-rar` | Native RAR metadata, `unrar` data path, crypto/checksum extras |
| `archive-reading` | Reader API exposed by core installs |
| `archive-writing` | 7z writing and writer codec availability |
| `access-mode-and-cost` | Seekable gzip/bzip2 capability and access-cost reporting |
| `reader-concurrency` | Supported `MemberStreams.CONCURRENT` contract |
| `cli` | `[recommended]` extra supplies `tqdm`; command-line dependency |
## Requirements
### Requirement: Zero-dependency core

The system SHALL install with no third-party runtime dependencies when no extras are
requested. Bare `pip install archivey` MUST fully support every native or
stdlib-backed reader: ZIP, TAR including `tar.gz` / `tar.bz2` / `tar.xz` / `tar.Z`,
single-file GZ / BZ2 / XZ / Z (unix-compress), directories, and 7z reading for common
codecs (LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/STORED) with CRC32 verification.

The system SHALL parse RAR metadata/listing natively in core with CRC32
verification. Reading RAR member data additionally requires the external `unrar`
system binary at runtime; no pip extra supplies that binary. RAR members that carry
only Blake2sp hashes are verified in core: BLAKE2sp is computed natively on stdlib
`hashlib` and needs no third-party package.

The build SHALL use `hatchling` and the distribution name `archivey`.

#### Scenario: core install matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` with no extras | No third-party runtime packages installed |
| Core read of ZIP/TAR/GZ/BZ2/XZ/Z/directory/common-codec 7z | Fully functional |
| Core read of `.tar.Z` / bare `.Z` | Native LZW decode; no `uncompresspy` |
| Core RAR listing | Native metadata/listing works |
| Core RAR data read with no `unrar` on `PATH` | Clear error says the external `unrar` tool is required |
| Core-only 7z write | Unavailable (writing not shipped); 7z reading still works |

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
not shipped in the current release (no 7z-writing extra); when writing lands in a later
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

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` as the sole supported external decompressor for
RAR member data. It MUST identify the binary on `PATH` as RARLAB `unrar` before use and
MUST NOT implement a fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other
tools when RARLAB `unrar` is missing or incompatible.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` on `PATH` | Used for compressed/encrypted member data |
| Only `unrar-free` / `unar` / `7z` on `PATH` | `PackageNotInstalledError` naming RARLAB `unrar`; no silent fallback |
| Listing without data reads | Succeeds without invoking any external decompressor |

### Requirement: Optional extras map only to libraries the code uses

User-facing extras SHALL list only libraries imported by `src/` at runtime for that
capability. A package used only by tests, decode oracles, fixture generation, or
fuzz harnesses MUST live in a PEP 735 dependency group (`dev`, `fuzz`, …) and be
absent from every user-facing extra.

The per-codec library choice and rationale SHALL be recorded in
`docs/internal/library-analysis.md`. A guard test or check script SHALL prevent dead or
test-only dependencies from returning to user-facing extras. A dependency pinned
ahead of its implementation phase, such as `tqdm` for the CLI, is permitted only
through an explicit documented allowlist in that guard.

#### Scenario: dependency-audit matrix

| Case | Expected |
| --- | --- |
| User-facing extra audited against `src/` imports | Every pinned package is reachable from runtime code or explicitly allowlisted |
| Library imported only by tests (`rarfile`, oracle `py7zr`, `ncompress`, fixture-only `pyzstd`) | Declared in `dev`; absent from runtime extras |
| `atheris` | Declared in `fuzz` group; absent from runtime extras and `[all]` |
| `pip install archivey[recommended]` on Python 3.11-3.13 | Installs `backports.zstd`; does not pull `zstandard` |
| `pip install archivey[recommended]` on Python 3.14+ | No third-party zstd package required; stdlib `compression.zstd` provides the backend |
| Extra lists a library no `src/` module imports and not allowlisted | Packaging audit fails |

### Requirement: CI-only fuzz dependency group

The system SHALL provide a PEP 735 dependency group named `fuzz` that installs
`atheris` (and any harness-only helpers it needs) for coverage-guided fuzz CI.
The `fuzz` group is **not** a user-facing runtime extra: it MUST NOT appear in
`[all]`, `[recommended]`, `[seekable]`, or `[free-threaded]`,
and MUST NOT be required to import or use `archivey` at runtime.

#### Scenario: fuzz packaging matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` / `archivey[all]` | `atheris` not installed |
| Fuzz CI job | Installs via `uv sync --group fuzz` (plus target runtime needs) |
| Runtime import of `archivey` without fuzz group | No `atheris` dependency |

### Requirement: Supported runtime environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API remains synchronous.

Readers and writers are not generally thread-safe. The supported
`MemberStreams.CONCURRENT` contract — what concurrent opens, materialization,
passes, close/lifecycle, and same-stream rules mean — lives entirely in
`reader-concurrency` (default single-live-stream rule: `archive-reading`). This
capability SHALL NOT restate that contract.

When that contract is declared, behavior SHALL be data-race-free on regular
CPython and on the backend/runtime combinations exercised by the required Linux
CPython `3.13t` `free-threaded-concurrency` CI job. It MUST NOT depend on
incidental GIL serialization. Optional backends without a free-threaded-compatible
wheel are not claimed covered until an equivalent dedicated job executes them.
This is a packaging/CI correctness claim, not a parallel-speed guarantee. Writers
remain not thread-safe.

#### Scenario: runtime-support matrix

| Case | Expected |
| --- | --- |
| Install on Linux, macOS, or Windows under Python 3.11+ | Core and installed optional formats are supported |
| Install on Python older than 3.11 | `requires-python >=3.11` prevents installation |
| Required `3.13t` core-backend job runs concurrent reader tests | Same bytes/lifecycle behavior as regular CPython for covered backends |
| Optional backend unavailable in `3.13t` job | Ordinary-build coverage remains valid; free-threaded support is not claimed for that backend |
| Public docs for `MemberStreams.CONCURRENT` | Point at the supported contract without labeling it provisional |

### Requirement: Version metadata exposure

The system SHALL expose the installed version as `archivey.__version__`, resolved
from installed distribution metadata via `importlib.metadata` rather than a
hard-coded string.

#### Scenario: installed-version metadata

| Case | Expected |
| --- | --- |
| Caller reads `archivey.__version__` | Returns the version recorded in installed package metadata, e.g. `"0.2.0"` |

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL keep the supported public API at the
package root. Only public API modules appear in `archivey.__all__`: `core.py`,
`types.py`, `exceptions.py`, `cost.py`, and `reader.py`. `archivey.__init__.py`
SHALL re-export the public API so supported callers do not import from
`archivey.internal.*`.

Implementation code SHALL live under `archivey.internal.*` without public
stability guarantees. Format backends SHALL live under
`archivey.internal.backends.*` and register with the registry at import time.
Importing top-level `archivey` SHALL still register all bundled backends. The
codec/stream layer SHALL remain under `archivey.internal.streams.*`. Phase 4
extraction modules SHALL follow the same implementation-under-`internal` rule while
public extraction types and `extract()` live on the public surface.

#### Scenario: package-layout matrix

| Case | Expected |
| --- | --- |
| Application uses documented API (`open_archive`, `ArchiveMember`, etc.) | `import archivey` or public re-exports suffice; no `archivey.internal` import required |
| Caller imports `archivey.internal.backends.zip` or old `archivey.formats.zip_reader` | Not documented, not in `__all__`, and not a stability promise |
| `import archivey` in a core-only environment | `list_supported_formats()` returns bundled formats without a prior `open_archive()` call |

### Requirement: archivey console entry points ship with the base package

The system SHALL install an `archivey` console script and support
`python -m archivey` from a base (no-extra) install. The `[recommended]` extra SHALL
continue to pull `tqdm` for progress output only; its absence MUST NOT
remove the command entry points.

#### Scenario: entry points vs progress extra

| Case | Expected |
| --- | --- |
| `pip install archivey` then `archivey --version` / `python -m archivey --version` | Command runs; version prints |
| `tqdm` not installed | Command runs; progress bars suppressed |
| `pip install archivey[recommended]` | Progress available when the CLI would show a bar |


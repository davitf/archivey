# testing-contract — fewer exception types

## MODIFIED Requirements

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented
attack category and verifies that the correct exception is raised or limit is
enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
| --- | --- |
| Zip bomb: quine-style and nested / 42.zip variant | `max_ratio` and `max_extracted_bytes` limits enforced before resource exhaustion |
| Ratio-floor false positive: tiny highly-compressible file (10 B -> 15 KiB, 1500:1) | Extracts without error while under `ratio_activation_threshold` |
| Path traversal: `../evil`, `../../etc/passwd`, `./../../outside` | `FilterRejectionError`; no outside write |
| Absolute paths: `/etc/passwd`, `C:\Windows\System32\evil.dll` | `FilterRejectionError` |
| Symlink escape: target `../../outside`, chained symlinks | `FilterRejectionError` |
| Symlink loop: cyclic `a -> b`, `b -> a` | `FilterRejectionError`; no uncaught `OSError` or crash |
| Corrupt archive: missing EOCD, truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` with original cause attached |
| Unicode bombs: null bytes, bidi control characters | Null bytes rejected as traversal; a bidi control emits exactly one `MEMBER_NAME_BIDI_CONTROL` on listing, and an **override/isolate** additionally raises `FilterRejectionError` on extraction |
| Giant claimed size: member claims 1 TiB while archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Regenerable adversarial archives SHALL be generated deterministically in memory or on
demand by `tests/create_adversarial.py` and SHALL NOT be committed. A hostile archive that
cannot be generated in the test environment MAY be committed under
`tests/fixtures/adversarial/` only with the fixture-policy JSON sidecar and an explicit
rationale.

The bidi-control outcome applies to every `ArchiveMember` presented by any backend,
including directory and single-file pseudo-archives. A backend SHALL NOT emit duplicate
diagnostics for one presentation of the same member.

**Listing and reading always present the name as stored.** Both branches are now
implemented and are named separately; the corpus SHALL cover each, and SHALL include at
least one **directional mark** case proving it is *not* rejected:

| Layer | Overrides / isolates (U+202A–202E, U+2066–2069) | Directional marks (U+061C, U+200E, U+200F) |
| --- | --- | --- |
| Listing / reading | Presented as stored; one `MEMBER_NAME_BIDI_CONTROL` | Presented as stored; one `MEMBER_NAME_BIDI_CONTROL` |
| Safe extraction | `FilterRejectionError` from `check_universal`, hence a `BLOCKED` result, under every policy | Extracted normally |

#### Scenario: adversarial-behavior matrix

| Case | Expected |
| --- | --- |
| Zip bomb extracted with default limits | `ExtractionError` before configured byte or ratio limit is exceeded |
| Archive member named `../evil` is extracted | `FilterRejectionError`; destination outside tree remains untouched |
| Truncated or CRC-invalid archive is read | `CorruptionError` or `TruncatedError`; original exception is `__cause__` |

#### Scenario: RTL warning is backend-independent

- **WHEN** any backend presents a member whose name contains U+202E RIGHT-TO-LEFT OVERRIDE
- **THEN** the name is presented as stored and exactly one `MEMBER_NAME_BIDI_CONTROL` diagnostic is emitted for that presentation

#### Scenario: directional marks are not swept into the rejection

- **WHEN** a member named with U+200F RIGHT-TO-LEFT MARK (a legitimate Arabic/Hebrew filename shape) is extracted
- **THEN** it extracts normally, proving the reject set is the override/isolate ranges and not the library's broader advisory set

### Requirement: Capability-gate behavior is tested on every format

The test suite SHALL cover the declared-capability gate uniformly for every
implemented format, including directory. A reader opened without
`concurrent_members=True` MUST raise `ArchiveyUsageError` on a second
overlapping `open()` while the first stream stays readable; sequential
`open -> read -> close -> open next` MUST succeed without any declaration. The
error message MUST include the recorded `open_archive()` call site and MUST name
`concurrent_members=True` as the parameter that would have allowed the operation.

Without `seekable_members=True`, member streams from random `open()` and
`stream_members()` MUST report `seekable() is False` and raise
`io.UnsupportedOperation` from `seek()` on every format, including real directory
files. With `seekable_members=True`, every member stream from random `open()`
MUST report `seekable() is True` and positioning MUST work (loud-slow-rewind
when there is no index). A `stream_members()` handle MUST report `seekable() is False`
and raise `io.UnsupportedOperation` from `seek()` on every format with
`seekable_members=True` too, both with `streaming=False` and with `streaming=True`,
and its `tell()` MUST work. One parametrized test SHALL cover all five cases (default
`open()`, default `stream_members()`, declared `open()`, declared `stream_members()`,
declared streaming `stream_members()`) over one matrix of format fixtures, so a
format cannot pass one case and be left out of another. `extract_all()`, including
hardlink recovery and symlink-target reads,
MUST succeed on readers with no declared capabilities. `ArchiveyUsageError` and
`ArchiveyUsageError` MUST NOT be `ArchiveyError` subclasses. Accelerator/index
activation MUST be demand-driven and match `seekable-decompressor-streams`.

#### Scenario: capability-gate matrix

| Case | Expected |
| --- | --- |
| Second overlapping `open()` on each implemented format without `CONCURRENT` | `ArchiveyUsageError` names the open site; first stream remains readable |
| Refused second `open()` on each implemented format | The member is never opened: no member data stream constructed, no helper process spawned |
| Sequential open/read/close loop without declarations | Succeeds on every implemented format |
| `ArchiveyUsageError` inside `except ArchiveyError` | Propagates out of that handler |
| Undeclared accelerator-eligible source | No seek index instantiated |
| Declared `SEEKABLE` accelerator-eligible source | `AUTO` accelerator resolves as specified |
| Each format fixture × {default `open()`, default pass, declared `open()`, declared pass, declared streaming pass} | Only declared `open()` seeks; every other handle is forward-only with a working `tell()` |

### Requirement: Property-based tests for safety logic

The test suite SHALL include bounded Hypothesis property tests over the
load-bearing safety functions: member-name normalization, the universal
extraction filter, link-target resolution, volume-name discovery, and format
detection over an arbitrary byte prefix on a peekable source. Tests SHALL assert
structural invariants (totality under typed errors, no escape introduced by
normalization, peek/replay preserved for detection) rather than golden outputs
from a second implementation. Shrunk counterexamples SHALL be pinned as explicit
regression examples. `hypothesis` is a `dev`-group dependency only; `[core-only]`
MUST still pass without it.

#### Scenario: property-test matrix

| Case | Expected |
| --- | --- |
| Generated traversal / absolute / NUL member names fed to `check_universal` | `FilterRejectionError` for every unsafe name |
| Arbitrary decoded names fed to `normalize_member_name` | Always returns `str`; idempotent; never introduces `..` or leading `/` absent from the input |
| Arbitrary byte prefixes on a peekable detection source | Typed result or typed error; peek source left unadvanced |
| Strategy discovers a shrunk failing input | Input is pinned as an `@example` or unit case |

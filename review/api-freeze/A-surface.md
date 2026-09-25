# A. Do the names belong on the surface?

Counted on `main` at `878c75f`. Every name in `archivey.__all__` (90) and every
`# noqa: F401` import (19) has a verdict below, plus the two side modules. The verdict
vocabulary is the brief's: **keep**, **demote** (importable from its module, out of
`__all__`), **rename**, **remove**.

## The surface, by module of definition

| Defined in | Names | Verdict |
| --- | --- | --- |
| `archivey.core` | `open_archive`, `open_stream`, `extract` | keep |
| `archivey` (pinned `__module__`) | `detect_format`, `format_availability`, `list_supported_formats`, `list_known_formats`, `ArchiveStream`, `enable_measurement` | keep; see A-2 on `detect_format` |
| `archivey.reader` | `ArchiveReader` | keep |
| `archivey.types` | 24 names (below) | keep 23, demote 1 (`MemberStreams`) |
| `archivey.config` | `ArchiveyConfig`, `DEFAULT_ARCHIVEY_CONFIG`, `DecoderLimits`, `ExtractionLimits`, `ListingLimits`, `AcceleratorMode`, `PasswordRequest` | keep |
| `archivey.cost` | `CostReceipt`, `ListingCost`, `AccessCost`, `StreamCapability` | keep |
| `archivey.detection` | `FormatInfo`, `DetectionConfidence` | keep |
| `archivey.diagnostics` | `Diagnostic`, `DiagnosticCode`, `DiagnosticSeverity`, `DiagnosticDisposition`, `DiagnosticPolicy`, `DiagnosticSummary`, `ExtractionReport`, `MemberListReport`, `ARCHIVE_INTEGRITY_CODES` | keep (Q3 on `DiagnosticSeverity`) |
| `archivey.exceptions` | 26 classes | keep; see `D-errors.md` |
| `archivey.measurement` | `IoStats` | keep |
| type aliases | `PasswordInput`, `PasswordProvider`, `OnDiagnostic`, `DiagnosticContext`, `MemberSelector`, `MemberFilter` | keep |
| `__version__` | | keep |

The 24 `types` names: `ExtractionPolicy`, `OverwritePolicy`, `OnError`, `AbortOn`,
`ExtractionStatus`, `ExtractionProgress`, `ExtractionResult`, `FormatSupport`,
`FormatAvailability`, `MissingComponent`, `ArchiveFormat`, `ContainerFormat`,
`StreamFormat`, `ArchiveMember`, `ArchiveInfo`, `ArchiveInfoExtra`, `MemberExtra`,
`MemberType`, `MemberStreams`, `CompressionAlgorithm`, `CompressionMethod`,
`CreateSystem`, `HashAlgorithm`, `crc32_digest`.

## The 15 arrivals since July

| Name | Is it API, or a leak? | Fits the vocabulary? | Documented in the guide? | Verdict |
| --- | --- | --- | --- | --- |
| `AbortOn` | API. A collection argument to `extract()`/`extract_all()`; the named opt-in for being stopped by an extraction outcome | Yes: sits beside `OnError` and `on_progress`, the same `on_`/`abort_on=` keyword shape the July review judged coherent | `extracting.md` | keep |
| `ARCHIVE_INTEGRITY_CODES` | API. A caller builds a policy from it | Yes: a `frozenset[DiagnosticCode]`, the same type its members carry | `errors-and-diagnostics.md` | keep |
| `ArchiveInfoExtra`, `MemberExtra` | API. The typed bag; an overloaded mapping whose known keys carry types (settled in #384/#421) | Yes | `formats.md` | keep |
| `DecoderLimits` | API. Third of three limits classes on `ArchiveyConfig`; all three have `UNLIMITED`, all use `None` to disable a guard | Yes, and the three are coherent with each other (fields: extraction 4, listing 2, decoder 2; same disable rule) | `extracting.md`, `errors-and-diagnostics.md` | keep |
| `DeceptiveNameError`, `NameCollisionError`, `NameRewrittenError` | API. See `D-errors.md` §The three name errors | Yes: one is a filter rejection, two are run outcomes a caller opted into raising | `extracting.md`, `errors-and-diagnostics.md` | keep |
| `HashAlgorithm`, `crc32_digest` | API. The key type of `member.hashes` and the encoder for a CRC into it | Yes | `formats.md` | keep |
| `IoStats`, `enable_measurement` | API now, not a leak: `#465` gave them a guide section ("Measuring what a read cost") and `ArchiveReader.io_stats()` is on the ABC. The July worry (a debugging affordance) is answered by the design: off by default, decided at open, costs nothing when off | Yes | `access-and-cost.md` | keep |
| `MemberListReport` | API. Returned by `members_report()`; a caller receives it and branches on `.error` | Yes: same shape as `ExtractionReport` (iterates, sizes, `.diagnostics`) | Described by method, not by name, in `opening-and-listing.md` and `errors-and-diagnostics.md` | keep |
| `OnDiagnostic`, `PasswordInput` | API. The types of a public config field and a public argument | Yes | Neither is named in the guide; both are on `api.md`. Acceptable for a type alias | keep |

## Findings

### A-1 · Medium · `archivey.detection_cost` is API without a page

With `#475`, the detection budget is `ArchiveyConfig.detection_budget`, typed
`DetectionBudget`, defaulting to `BALANCED_BUDGET`. The `ArchiveyConfig` docstring
tells a user that `FAST_BUDGET` and `THOROUGH_BUDGET` are "in `archivey.detection_cost`"
and to `dataclasses.replace` one to change a limit. `FormatInfo.cost_receipt` is a
`DetectionCostReceipt`. So the module's 11 public names (`DetectionBudget`,
`DetectionBudgetPreset`, `DetectionCapability`, `DetectionCostReceipt`,
`MutableDetectionCostReceipt`, `TierSkip`, `TierSkipReason`, the three presets,
`default_detection_budget`) are reachable from three documented places, and `docs/api.md`
mentions none of them (zero occurrences of `detection_cost`, `DetectionBudget` or
`FAST_BUDGET`). `terminal` got exactly this treatment in `#448`: a section on the API
page, a stability sentence, a layout-list entry. `detection_cost` has the layout-list
entry only (`__init__.py:15`).

At `0.2.0` that becomes a promise nobody wrote down. The brief's either/or (fold in or
move under `internal/`) has become a choice between two sizes of "fold in":

- **Document the module as it is**, the way `terminal` is: an "Detection cost" section
  on `api.md` with `:::` blocks for `DetectionBudget`, the three presets,
  `DetectionCostReceipt`, `TierSkip`, `TierSkipReason`, `DetectionCapability`. Keep it
  out of `archivey.__all__`. Twelve lines of docs, no code change.
- **Re-export the caller-facing half** (`DetectionBudget` and the three presets) from
  `archivey`, document the receipt types under `detection_cost`. Nicer for the caller
  who only ever writes `ArchiveyConfig(detection_budget=FAST_BUDGET)`; adds four names
  to `__all__`.

`MutableDetectionCostReceipt` and `default_detection_budget` are implementation either
way: the former is what detectors write into, the latter returns `BALANCED_BUDGET`. Both
belong under `internal/` or under a leading underscore.

**Recommendation.** The first option. It matches `terminal`, it is a docs-only change,
and it leaves `__all__` at 90. Move `MutableDetectionCostReceipt` and
`default_detection_budget` out of the public module. Q1.

### A-2 · Medium · `detect_format(collector=)` puts an internal type on a public signature

`inspect.signature(archivey.detect_format)` is
`(source, *, config=None, collector: DiagnosticCollector | None = None, follow_stub_volumes=True)`.
`DiagnosticCollector` is defined in `archivey.internal.diagnostics_collector` and is
not importable from any public module. The docstring is honest about who the parameter
is for ("when provided (e.g. from `open_archive`)"), but the API page renders it, a
typed caller sees it in completion, and `tests/test_argument_boundary.py` has to carry
a probe row for it. It is the one place on the surface where an `internal/` name is
part of the contract.

**Recommendation.** Give `open_archive` a private entry (`_detect_format_into(source,
collector, ...)` in `internal/detection.py`) and drop `collector` from the public
signature before the tag. `follow_stub_volumes` is a real caller choice and stays. Q4.

### A-3 · Low · `MemberStreams` describes itself as internal and is in `__all__`

Its docstring (`types.py:31`): "Callers declare these as booleans ... so there is no
need to construct a `MemberStreams` value ... This flag set is the internal
representation those booleans map to ... `reader.member_streams` ... is not on the
`ArchiveReader` ABC, so it is reachable at runtime but not part of the typed public
contract." Every sentence argues for demotion. It is on `api.md` under "The reader
interface", where a reader will try to use it and find no method that takes one. The
`archive-reading` spec still uses the flag vocabulary internally, which is fine.

**Recommendation.** Demote: keep it importable from `archivey.types`, drop it from
`__all__` and `api.md`, and let the `open_archive` docstring (which already explains the
two booleans) be the documentation. Q2. If the maintainer prefers to keep it, the
docstring's "internal representation" sentence should go, since a public name cannot
call itself internal.

### A-4 · Low · `reader.member_streams` exists at runtime and not on the ABC

The docstring above says so. A property every concrete reader has, that a user can
discover with `dir()`, is a de facto public attribute. Either it goes on the ABC (then
`MemberStreams` stays public, contradicting A-3) or it is renamed `_member_streams`.

**Recommendation.** Rename to `_member_streams` together with A-3. If A-3 goes the
other way, add it to the ABC.

### A-5 · Low · The 19 names outside `__all__`

`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (a tuning threshold), `WriteError` (write API not
shipped), and the 17 `*Context` payloads. All three groups were demoted on the July
review's recommendation and the reasons hold. Verdict: **keep demoted**, all 19.
`WriteError` is the only one worth a second look: an exception class for an API that
does not exist is a name a user can `except` on and never see raised. Removing it is
free today and a breaking change after the tag. Recommendation: remove it from the
package root before the tag, keep the class in `exceptions.py` for the day the write
path lands. Q5.

## What is actually fine

- **The count is honest now.** `#465` closed the 29-name gap between `__all__` and
  `api.md` and added the test that keeps it closed. Every name in `__all__` renders.
- **`ArchiveStream` and the six pinned-`__module__` names** show `archivey` as their
  home (`#415`), so `help()` and the API page agree on where they live.
- **`FormatInfo`** grew to eight fields (`corroborated`, `cost_receipt`,
  `unavailable_tiers` since July) and `encoding_hint` left. Each new field answers a
  question the detection series decided to answer; none duplicates another.
  `format_info` on the reader (`#468`) means detection runs once.
- **`ArchiveMember.ctime`** (`#470`) beside `created`, with the invariant that a member
  has at most one of the two, reads clearly and the docstring says which formats fill
  which. The `extra` keys it replaced would have been five spellings of one fact.
- **The `extra` bags** are exactly what `#384` decided: three `EXTRA_*` constants on the
  member side, typed overloads, and the docs list the known keys.

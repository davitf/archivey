## ADDED Requirements

### Requirement: AmbiguousFormatError is a FormatDetectionError carrying the tied candidates

The system SHALL define `AmbiguousFormatError(FormatDetectionError)`, raised when detection
finds two or more incompatible maximal candidates that no settled priority key separates. It
SHALL carry the tied candidates and their evidence.

Subclassing `FormatDetectionError` is the point: existing broad
`except FormatDetectionError` handling keeps working, while a wrong-format choice that used
to be made silently by registry order becomes loud.

#### Scenario: ambiguity reaches the caller

| Case | Expected |
| --- | --- |
| `detect_format` on tied candidates | `AmbiguousFormatError` with both candidates |
| `open_archive` / `open_stream` on the same source | Propagates; no registry-order fallback |
| `except FormatDetectionError` around either | Catches it |
| `format=` supplied | Detection is bypassed; never raised |

## MODIFIED Requirements

### Requirement: A decode failure on probe-only evidence names its provenance

`ArchiveyError.format_unconfirmed` SHALL be set on a decode failure when **archivey chose the
format** and the strongest content-evidence class supporting that choice is at or below
`BOUNDED_PROBE`.

| how the format was chosen | class | flag on decode failure |
| --- | --- | --- |
| content probe | `BOUNDED_PROBE` | **yes** |
| filename only | `NAME` | **yes** |
| caller's `format=` | `ASSERTED` | **no** |
| magic, structural hit, inner-TAR refinement, or whole-source completion | `SIGNATURE_ONLY` and above | no |

The clause carrying the weight is *"archivey chose the format"*. It excludes `ASSERTED`
without needing a name-based exception: when the caller passes `format=`, archivey is not
guessing, so it has nothing to be unconfident about. `ASSERTED` still projects to `GUESS` for
*confidence*, because confidence and this flag answer different questions.

**Filename-only belongs on the yes side** for a reason stronger than its rank: the extension
fallback is reached only because every content signal declined. That is the explicit absence
of evidence after trying — arguably a better case for the flag than a probe hit, which at
least decoded something.

A matching extension SHALL NOT suppress the flag. Measured on two independent trees, extension
corroboration caught **zero** fabrications (29 and 19 respectively, none corroborated), while
costing two genuine files on a tree where two others already pay the same cost through an
unregistered `.brotli` extension.

When set, the failure SHALL:

1. Keep the same exception **type** (`TruncatedError` / `CorruptionError`) — no new subclass.
2. Set `format_unconfirmed=True`.
3. Rewrite the **message** to report that the format identification was unconfirmed, naming
   the provenance. The message MUST NOT imply nothing was produced — a read may already have
   delivered a full buffer of bytes copied verbatim from the source — and MUST NOT name a
   confidence level.
4. Emit the provenance-neutral unconfirmed-format diagnostic (see `diagnostics`).

`format_unconfirmed` means *"the bytes did not confirm this identity"*, not *"the identity is
probably wrong"*. A genuinely truncated `x.br` may therefore carry it.

This replaces the `corroborated` predicate shipped in #267 and, with it,
`_brotli_probe_confidence`'s `.br`-to-`PROBABLE` rule — the same rule expressed twice, which
must move together.

#### Scenario: unconfirmed-format decode failure

| Case | Expected |
| --- | --- |
| Probe-only result, read fails | Same exception type; `format_unconfirmed=True`; message names unconfirmed identification |
| Probe plus a matching `.br` extension, read fails | **Now stamped** — the name is not content evidence |
| Probe upgraded to `TAR_*` by a checksum-valid inner TAR, read fails | Not stamped — refinement raised the content class |
| 40 000 zero bytes named `backup.gz`, chosen by extension alone, read fails | **Now stamped** — only the filename claimed gzip |
| Zero-filled `backup.rar`, chosen by extension alone | **Now stamped**, rather than blaming the bytes |
| `open_archive(..., format=ZIP)` over a `.tar.gz`, read fails | Not stamped — the caller took responsibility |
| Exact magic hit, read fails | Not stamped |
| Probe-only result, read succeeds | Success; no error, no downgrade |

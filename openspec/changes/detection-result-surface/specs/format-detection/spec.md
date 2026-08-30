## ADDED Requirements

### Requirement: The detection result is an always-present field on the reader and the stream

`ArchiveReader` and `ArchiveStream` SHALL each expose the detection result as a field. It
SHALL always be present — never `None` — so a caller can read it without first testing
whether detection happened to run.

Where detection did **not** run, the ledger SHALL say so as declared evidence, which is
truthful provenance rather than absent provenance:

| kind | source | class |
| --- | --- | --- |
| `DECLARED_BY_CALLER` | the `format=` argument, which skipped detection | `ASSERTED` — nothing verified it |
| `DECLARED_BY_CONTAINER` | a member stream's codec read from the archive's own metadata (ZIP's compression method, 7z's coder chain) | **inherits the container's achieved class** |

`DECLARED_BY_CONTAINER` is deliberately **not** a class of its own. A member of a
checksum-validated 7z is `SELF_VALIDATING`, not a guess: the container structurally declares
its codec and the container itself was validated. A single `DECLARED` value would rank it
with a caller's assertion, understating it as badly as ranking it with a bounded probe would
overstate a probe.

The result SHALL also carry the detection cost receipt and the search-completeness record.

#### Scenario: the field is present on every path

| Case | Expected |
| --- | --- |
| `open_archive(path)` auto-detecting | Field carries the winning candidate's evidence |
| `open_archive(path, format=ZIP)` | Field present, one `DECLARED_BY_CALLER` record at `ASSERTED` → `GUESS` |
| `open_stream(path)` | Field present and carries the container, not only the stream codec |
| A member stream inside a validated 7z | Field present, `DECLARED_BY_CONTAINER` at the container's class |
| A read error marked unconfirmed | Carries the evidence, or a stable reference to it, so the flag explains itself without a second detection |

### Requirement: confidence and detected_by are derived, not stored

`confidence` SHALL be a property over the winning record's **class**, and `detected_by` a
property over the winning record's **kind**. Neither SHALL be a stored field.

A stored scalar can be constructed inconsistent with the ledger it claims to summarize; a
derived one cannot. It also stops equality and golden-value tests pinning a redundant field.
That a record carries **both** kind and class is what lets the two coexist without being a
second ranking: `detected_by` names *which detector answered*, `confidence` names *how strong
the answer is*.

`detected_by` values:

| value | status |
| --- | --- |
| `magic`, `extension`, `content_probe` | unchanged spelling and meaning |
| `prefixed_scan` | **renamed** from `sfx_scan` |
| `zip_tail_probe`, `exhaustive_scan` | new — tiers that do not exist yet |
| `declared_by_caller`, `declared_by_container` | new — results currently discarded before any caller sees them |

`sfx_scan` is renamed because it asserts intent the tier cannot establish: the same tier that
finds a real self-extracting installer also finds a JPEG with an appended ZIP, a `zipapp`
(where the archive *is* the program and is meant to be run, not extracted), and junk prepended
to a tar. What sits in front is reported separately as a prefix kind and is never acted on.

The full ledger SHALL render in `__str__` / `__repr__`, where "bounded probe **and** a matching
name" can be shown to a human, a log line, or `archivey info` — the composition a single scalar
deliberately does not carry.

Per-record detail — `bytes_examined`, `estimated_random_bits`, anchors — SHOULD be treated as
advisory and unstable. The stable public commitments are the **kinds**, the **classes**, and
their ordering.

#### Scenario: derivation matrix

| Case | `confidence` | `detected_by` |
| --- | --- | --- |
| Validated 7z signature | `CERTAIN` | `magic` |
| Two-byte magic on a two-byte source | `PROBABLE` | `magic` |
| Bounded Brotli probe, name `x.br` | `GUESS` | `content_probe` |
| Filename only | `GUESS` | `extension` |
| `format=` supplied | `GUESS` | `declared_by_caller` |
| Member codec from a validated container | the container's | `declared_by_container` |
| Payload found by the prefix-cued scan | the hit's own class | `prefixed_scan` |

### Requirement: A previously produced detection result can be handed to open

`open_archive()` and `open_stream()` SHALL accept a `detection=` argument carrying a result
produced by `detect_format()`, and SHALL skip detection when given one.

```python
result = detect_format(source)
if result.confidence is not DetectionConfidence.CERTAIN:
    ...                                  # the caller's own policy
reader = open_archive(source, detection=result)
```

Three properties, each a constraint rather than a convenience:

- **It is not `format=`.** `format=` is an override: it records `ASSERTED`, skips detection,
  and suppresses `format_unconfirmed` because the caller took responsibility. `detection=`
  replays evidence **archivey itself** produced, so the reader's ledger, its confidence and
  its `format_unconfirmed` behaviour are exactly what a self-detecting open would have given.
  Routing a detection result through `format=` silently launders a `GUESS` into a trusted
  assertion.
- **The result names the source it came from.** A result handed to a *different* source SHALL
  raise rather than open the wrong bytes as the wrong format. This is a typo-catcher, **not**
  a security boundary: a path can change on disk between the two calls, and `detection=`
  inherits exactly the time-of-check-to-time-of-use window today's detect-then-open pattern
  already has.
- **On a non-seekable source it is the only way to look before opening.** Detection has
  already consumed the prefix and a second detection cannot re-read it. The replay buffer
  must therefore travel with the result, so on such a source the result is **not** a pure
  value object: it carries or references the buffered bytes and its lifetime is tied to the
  source's.

`format=` SHALL continue to perform **no detection I/O of any kind**. That contract — do
exactly what I said and no work I did not ask for — is what makes it usable as an escape
hatch.

#### Scenario: handoff matrix

| Case | Expected |
| --- | --- |
| `detect_format` then `open_archive(source, detection=result)` | Detection runs once; the reader's ledger equals the standalone result |
| A `GUESS` result handed through `detection=`, read fails | Stamped `format_unconfirmed`, exactly as a self-detecting open would |
| The same result handed through `format=` instead | `ASSERTED`; not stamped — which is why the two are different parameters |
| A result from a different source | Raises; no open attempted |
| Non-seekable source, `detection=` with its replay buffer | Opens without a second read of the prefix |
| Non-seekable source, `detection=` whose buffer was released | Raises rather than re-reading bytes that are gone |
| Both `format=` and `detection=` given | Usage error — two different claims about the same question |

## MODIFIED Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
    budget: DetectionBudget | None = None,
) -> FormatInfo
```

```python
class DetectionConfidence(Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    GUESS = "guess"

@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    evidence: tuple[DetectionEvidence, ...]   # the winning candidate's ledger
    encoding_hint: str | None
    payload_offset: int = 0
    search_complete: bool = True
    cost: DetectionCostReceipt = ...
    diagnostics: DiagnosticSummary = DiagnosticSummary.empty()

    @property
    def confidence(self) -> DetectionConfidence: ...   # derived from evidence class
    @property
    def detected_by(self) -> str: ...                  # derived from evidence kind
```

`config=None` → library default. `encoding_hint` is format-signal only (never a member scan).

`payload_offset` SHALL remain an `int`: zero means "confirmed at the detection origin" and a
positive value marks a payload starting later. Where an exact offset was not computed within
the index budget, `detect_format()` SHALL either pay to compute it or raise a
budget/incomplete-detection error; it SHALL NOT report zero. Widening the public type would
be its own API change, not an incidental consequence of this one.

Enumerating **non-winning** candidates is out of scope; the winner's ledger is required
because error-provenance semantics depend on it.

**Collectors:**

| Path | Behavior |
| --- | --- |
| Standalone `detect_format` | One finite collector; policy/callback/logging/budget; final summary on `FormatInfo.diagnostics` |
| Inside `open_archive` | Open creates prospective-reader collector + detection watermark, passes that collector into detection. On success the reader owns it — no seed/merge/replay/copy; each retained occurrence charged once. Internal detection-range `FormatInfo.diagnostics` is not retained after handoff; same events remain on the reader's cumulative summary |

#### Scenario: detect / handoff matrix

| Case | Expected |
| --- | --- |
| Standalone detect with magic/extension conflict | `FormatInfo.diagnostics` has exact conflict count + retained detail under default budget |
| Auto-detect inside `open_archive` retains conflict, open succeeds | Reader continues same collector/order/budget; no copied aggregate |
| Validated 7z signature (`StartHeaderCRC` passes) | `confidence=CERTAIN`, `detected_by="magic"` — both derived |
| Two-byte gzip magic on a source too short to validate | `confidence=PROBABLE` via `SIGNATURE_ONLY` — **not** `CERTAIN` |
| ISO descriptor tuple | `confidence=PROBABLE` via `DISCRIMINATING_HEADER` |
| Extension-only guess | `confidence=GUESS`, `detected_by="extension"` |
| Explicit `diagnostic_policy` on detect | IGNORE/COLLECT/RAISE applies to that finite detection |
| A budget skipped a tier that could have tied or dominated | `search_complete is False` |
| Exact `payload_offset` not computed within the index budget | Pay for it, or raise — never report zero |

# Archive format detection: independent design analysis

**Date:** 2026-08-23  
**Status:** complete analysis, not a normative specification  
**Inputs:** current `main` at `bee7735`, PR
[#257](https://github.com/davitf/archivey/pull/257), PR
[#262](https://github.com/davitf/archivey/pull/262), the measured Brotli investigation,
the current detector, and the format specifications linked in §14.

## Executive recommendation

Keep an **ordered acquisition plan**, but do not keep **first match wins**.

The detector should collect typed evidence for candidates, compare candidates by a
small dominance relation, and stop only when no unrun detector can change the winner.
This is not an additive score: adding a filename to a weak decode must never outweigh a
checksum-validated header, and two correlated weak signals must never be promoted above
one stronger signal.

The default acquisition order should be:

1. read the name, source capabilities, cheap size, and up to 4096 prefix bytes;
2. evaluate every near signature and its format-declared structural validator;
3. evaluate fixed-offset far signatures, currently 32,775 bytes for a structurally
   checked ISO descriptor;
4. for seekable sources, locate ZIP's EOCD in at most 65,557 tail bytes, then charge
   central-directory/local-header validation separately;
5. when a prefix cue fires, scan at most 2 MiB forward and validate every candidate,
   resuming after decoys;
6. run an exhaustive embedded-payload scan only under a separate, explicit
   try-hard/discovery policy;
7. run **all** applicable magic-less content probes and retain all hits;
8. use the filename as corroboration and, only now, as a last-resort candidate;
9. select a unique undominated candidate, return an explicit ambiguity if there is no
   unique winner, or raise `FormatDetectionError` if there is no candidate.

PR #257 is substantially right about putting far magic and validated prefix searches
ahead of content probes. I disagree with four load-bearing parts:

- raw near magic is not uniformly `CERTAIN`: two bytes of gzip or `.Z` are not the same
  evidence as an XZ flags CRC, a 7z StartHeader CRC, or a TAR checksum;
- a registry-order first hit is not a sound decision rule, especially when multiple
  magic-less probes can accept the same bytes and polyglots can be genuinely plural;
- "format-bounded" does not imply "cheap enough to run unconditionally": on 71,983
  sufficiently large files, the tail locator alone has a 4.39 GiB worst-case aggregate,
  before central-directory reads;
- an unbounded scan is embedded-archive discovery, not ordinary format detection, and
  the current prefix-only peek primitive makes it `O(source size)` in both retained bytes
  and copying for a non-seekable source.

Confidence and provenance must be separate. In particular, every probe-only failure
should carry `format_unconfirmed`, regardless of whether the probe result was labelled
`GUESS` or `PROBABLE`.

## 1. The result should be an evidence ledger, not one winning string

The backend registry should continue to own detection data, but a declaration needs more
than `(offset, magic, format)`:

```python
@dataclass(frozen=True)
class DetectionEvidence:
    kind: EvidenceKind
    strength: EvidenceStrength
    anchor: EvidenceAnchor
    offset: int
    bytes_examined: int
    validation: ValidationState
    estimated_random_bits: float | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FormatCandidate:
    format: ArchiveFormat
    payload_offset: int | None
    evidence: tuple[DetectionEvidence, ...]
    prefix_kind: PrefixKind
```

The important fields omitted by today's `FormatInfo` are:

- **all provenance**, not one `detected_by` string;
- **validation state** (`VALID`, `INVALID`, `INCOMPLETE`, `NOT_APPLICABLE`);
- **search completeness**: which tiers were unavailable or skipped by budget;
- **alternatives**, when two candidates survive.

`payload_offset=None` means "not computed within the index budget"; it must not be
collapsed into zero, which means "confirmed at the detection origin". The compatibility
`FormatInfo` view can require the exact offset and pay for it, while a candidate-reporting
API can expose a strongly identified ZIP before walking an arbitrarily large directory.

`detected_by` can remain as a compatibility summary of the winning candidate's primary
evidence. It is not rich enough to drive exception semantics.

### Evidence classes

Use a partial order with these classes, strongest first:

| class | examples | what it establishes |
| --- | --- | --- |
| `COMPLETE` | a decoder reached stream end on the entire source; a complete container parse succeeded | the bytes form a complete instance under the parser's contract |
| `SELF_VALIDATING` | ZIP EOCD + central directory; 7z StartHeader CRC and bounds; XZ flags CRC; TAR checksum; LZ4 header checksum | a format-specific identifier and an independent consistency check agree |
| `DISCRIMINATING_HEADER` | RAR marker; ISO descriptor tuple; bzip2 header + block marker; zstd magic + legal descriptor | enough mandatory structure to make accidental identity remote |
| `SIGNATURE_ONLY` | a strong fixed-position signature whose validator is unavailable, or a damaged header after a strong signature | identity evidence without structural confirmation |
| `BOUNDED_PROBE` | a prefix decoder accepted Brotli, zlib, or LZMA Alone but did not reach source end | the prefix is compatible with the format; it does not establish a complete stream |
| `NAME` | `.zip`, `.tar.gz`, `.br` | a prior supplied by the caller's namespace, not content evidence |

Do not assign points and add them. Evidence is correlated: a `.br` suffix and a Brotli
probe are not two random independent observations, and the base rate differs radically
between `/usr`, a browser cache, and a backup corpus.

### Anchoring and "bits of constraint"

Whether evidence is anchored to a fixed string/offset is useful metadata. It explains why
a decode behind a validated gzip or 7z header is categorically safer than an unconstrained
Brotli decode, and a declaration may carry a conservative random-data constraint estimate
for review and scheduling.

It is **not** the top-level ordering rule, and constraint bits are not a score:

- a short anchored gzip header can still be weaker than unrun far ISO evidence;
- zlib has mandatory header grammar but no single fixed byte string;
- v7 TAR has no `ustar` anchor but can have a checksum-valid 512-byte header;
- CRC bits and grammar fields are not automatically independent, and real corpora are not
  IID random bytes.

Therefore "all anchored evidence first; cost within that class; stop on a hit" recreates
the original defect in a different form: a weak near anchor can stop before stronger far
evidence. Anchor kind and estimated bits may refine evidence classes and schedule equal
classes, but the stopping rule remains "no unrun detector can tie or dominate".

Compare candidates lexicographically:

1. strongest content-evidence class;
2. semantic position: a format beginning at the detection origin outranks an unrelated
   embedded payload at a later offset;
3. end anchoring (`declared_end == source_end` before merely `<=`) within the same format
   and evidence class;
4. independent corroboration, including a matching extension, only as a tie-break;
5. if incompatible candidates remain tied, report ambiguity rather than using registry
   order.

The `(container, stream)` pair is refinement, not competition. A gzip candidate whose
decoded prefix contains a checksum-valid TAR header becomes `TAR_GZ`; it does not leave
independent `GZ` and `TAR_GZ` candidates tied.

### Confidence mapping

Keep the current enum for compatibility, but define it only as identity strength:

| confidence | meaning |
| --- | --- |
| `CERTAIN` | complete or self-validating evidence, or a format-specific header whose declared false-match risk is accepted as decisive |
| `PROBABLE` | signature-only or a well-calibrated bounded structural/decode probe |
| `GUESS` | name-only or an empirically weak bounded probe, notably uncorroborated Brotli |

`CERTAIN` does **not** mean the whole archive is uncorrupted. A 7z signature can identify
a damaged 7z archive even when its StartHeader CRC fails. Validation failure should often
lower confidence and attach a corruption diagnostic, not erase a highly specific format
identity and turn a useful `CorruptionError` into "unknown format".

The exact failure disposition is format-declared:

- for a short signature such as gzip's two bytes, a failed mandatory header check should
  normally reject the candidate;
- for a six- to eight-byte format identifier such as 7z or RAR, a failed checksum usually
  means "identified but damaged";
- absence of enough bytes is `INCOMPLETE`, never the same as a proved mismatch.

No error path should branch on `confidence`. Error provenance should ask whether the
winner was probe-only, name-only, structurally validated, or explicit.

## 2. Precise acquisition and stopping algorithm

### Step 0 — establish the detection origin and budget

Collect, without consuming:

- the longest matching filename suffix, if any;
- whether the source can seek;
- a cheap total size and, where provable, bytes remaining from the caller's current
  position;
- the configured byte, seek, decode-input, decode-output, and spool budgets.

The distinction between total size and remaining size matters for a caller-positioned
stream. An overestimated size is safe for Brotli's current overrun rejection but is not a
proof that a later offset is reachable. A size gate may skip a detector only when the
remaining source is provably too short.

The extension is now available as metadata, but it cannot answer yet.

### Bounded prefix workspace

Use one detection-owned, monotonically growing prefix workspace for steps 1, 2, 4, and 6:

- a path keeps one detection handle open;
- a seekable caller stream records its entry position, reads forward once, and restores
  once in an exception-safe exit path;
- a non-seekable source uses the same replay buffer the backend will consume.

Consumers request candidate-relative ranges from that workspace. Extending from 256 KiB to
1 MiB reads only the delta, so the 2 MiB SFX window costs 2 MiB of unique source I/O rather
than 3.31 MiB of overlapping path/stream reads. This is the bounded implementation of the
`peek_range` contract needed by makeself, TAR SFX, and the Brotli chain walk.

The workspace does not make exhaustive scan, tail access, or arbitrary range reads free.
Its retained-byte ceiling and copied bytes remain part of the budget.

### Step 1 — near evidence

Call `peek_more(4096)` once. Run every matching near declaration, not only the first table
entry. Each declaration returns zero or one candidate plus validation details.

Stopping condition:

- in single-result mode, a unique `SELF_VALIDATING` candidate at the detection origin may
  stop if the caller did not request alternatives;
- a short or unvalidated signature may **not** stop while an unrun far/tail/scan detector
  can produce stronger evidence;
- in alternatives/polyglot mode, do not stop.

This keeps cheap common archives cheap without allowing two-byte gzip magic in an ISO
system area to hide the ISO descriptor.

### Step 2 — far fixed-offset evidence

For every far declaration whose end offset is not proved beyond EOF, request the maximum
required prefix. Today that is `peek_more(32775)` for the seven-byte ISO descriptor tuple:
descriptor type, `CD001`, and version.

Cost under the current primitive:

- at most 32,775 retained prefix bytes;
- if `_peek_prefix` does not cache across calls, 4,096 + 32,775 = 36,871 requested bytes;
- no seek for a non-seekable source, but its replay wrapper must retain the prefix.

A strong direct-format result may stop in single-result mode. A miss falls through.

### Step 3 — ZIP tail evidence

When the source is seekable and the remaining size is at least 22 bytes:

1. inspect at most `min(remaining_size, 65535 + 22)` bytes at EOF;
2. search backwards for every EOCD candidate;
3. require the comment length to end exactly at EOF;
4. follow ZIP64 when sentinel fields require it;
5. validate disk/count fields according to supported split-archive policy;
6. derive and bounds-check the central directory, inspect referenced signatures, and
   reconcile local-header offsets;
7. on failure, resume the backward search rather than accepting a decoy.

The **EOCD locator** costs one tail-positioning operation plus restoration and at most
65,557 bytes. Strong validation additionally reads the referenced ZIP64 record (when
present), central-directory data, and at least one local header. Computing the exact
earliest local-header offset may require walking the entire central directory, whose size
is not bounded by 65,557 bytes. Therefore:

- a cheap candidate may be `SUPPORTED` after bounded geometry plus a few referenced
  records, with `payload_offset=None`;
- a `SELF_VALIDATING` result with exact `payload_offset` charges all central-directory
  bytes against an index budget;
- size discovery, each range read, and restoration count as actual seeks/requests rather
  than being hidden behind the phrase "one seek".

Exact comment-to-EOF is the standards path and preserves the 65,557-byte completeness
bound. A separate compatibility policy may accept trailing bytes only after CD/local
validation, prefer an exact-EOF candidate, record the trailer, and charge a configured
`max_trailing_bytes`. Once arbitrary trailers are accepted, 65,557 bytes is no longer a
complete search bound relative to physical EOF; the result must say that.

A validated tail hit is `SELF_VALIDATING`. If the prefix carries an executable/script cue,
continue the bounded scan before deciding: an executable can contain both a ZIP payload
and a different SFX payload, and stage order must not silently choose intent. With no cue
and no alternatives request, the ZIP candidate may stop.

### Step 4 — cue-gated bounded forward scan

Grade cues using mandatory structure:

- bare `MZ`, `\x7fELF`, or `#!` is weak and only authorizes the scan;
- validated PE, ELF, or Mach-O is strong and additionally prevents a raw stream probe
  from becoming the selected answer;
- `ca fe ba be` produces no Mach-O cue unless the fat header and arch table parse, so Java
  class files do not pay for a scan.

Search the backend-declared needles in 64 KiB, 256 KiB, 1 MiB, and 2 MiB prefixes. A miss
currently requests:

```text
64 + 256 + 1024 + 2048 KiB = 3,473,408 bytes
```

to cover a 2,097,152-byte window: 1.65625× for paths/seekable streams, 1× underlying I/O
plus repeated copying for `PeekableStream`.

The bounded prefix workspace above should reduce the recommended implementation to 1×
unique I/O for every source kind. The 1.65625× figure remains the cost of today's
`peek_more(first_n_bytes)` implementation, not a desirable permanent contract.

Every hit must pass its format-declared validator. Resume one byte past a rejected
candidate; do not let an early decoy terminate the scan. Prefer an exact declared EOF as a
tiebreak but allow `declared_end < source_end` where the format/tool permits trailers.

Needles need a declared **anchor offset**, and every validator/probe must receive a view
relative to the candidate origin. TAR's `ustar` occurs at candidate offset 257, so a hit at
absolute `H` means candidate origin `H - 257`, not `H`. A gzip needle begins at candidate
offset zero. The current `peek_more(length)` always starts at source origin; makeself and
TAR SFX support therefore require a bounded `peek_range(candidate_origin, length)` (or an
equivalent candidate-relative view) before they are implementable correctly.

Compressor needles belong only under the `#!` cue and must include every cheap mandatory
discriminator available. For gzip that is at least `1f 8b 08` plus legal flags, followed
by a bounded decode and inner-TAR check.

### Step 5 — exhaustive discovery is a separate mode

Do not describe this as just another ordinary detection tier. Without a cue, a valid
archive found in arbitrary bytes may be:

- the file's intended payload;
- an embedded resource;
- an archive nested inside another format;
- one member of a polyglot.

The result therefore needs `prefix_kind=UNKNOWN`, alternatives, and an explicit
embedded/discovery provenance. A validated hit is not evidence of producer intent.

The current `peek_more(n) -> first n bytes` interface also makes a whole-source scan
retain the whole source for non-seekable input and repeatedly re-request prefixes for
seekable input. A serious exhaustive mode needs one of:

- a reopenable independent handle and a streaming scanner;
- a seek-preserving `read_at(offset, n)`/range-peek primitive;
- an explicit bounded spool shared with the backend.

Until then, `exhaustive_prefix_scan=True` is an opt-in `O(size)` memory/copy operation,
not merely an opt-in CPU scan.

### Step 6 — magic-less content probes

Run all available content probes over the same bounded input. Do not return from the first
one that accepts. If zlib, LZMA Alone, and Brotli produce competing hits, compare their
declared evidence and report ambiguity when no candidate dominates.

Before selecting a probe result:

- all affordable stronger tiers must have run;
- a skipped stronger tier must set `search_complete=False`;
- a strong executable cue prevents a raw-stream probe from winning;
- an inner-TAR hit refines the stream candidate;
- a matching extension corroborates but does not promote the candidate above a stronger
  evidence class.

If the probe holds the entire source, "decoder needs more input" is a rejection. If it
does not hold the entire source, that outcome remains inconclusive/positive according to
the per-format probe.

### Step 7 — filename fallback and final selection

Add the longest-suffix filename candidate only after content acquisition. Use it in three
ways:

1. corroborate the same content candidate;
2. break a tie between otherwise equal weak candidates;
3. provide a last-resort `GUESS` when no content detector accepted.

Never use it to restrict near/far magic, ZIP tail detection, or container SFX needles.
Wrong extensions are a founding use case. It may authorize extra expensive work under a
caller-selected budget, but it must not suppress other formats.

Select the unique undominated candidate. If two incompatible candidates remain at the
same class, return an ambiguity (or expose them through `detect_formats`) rather than
using backend registration order.

For `open_archive`, a tentative raw-stream result must not silently expose a synthetic
member as though the format were confirmed. Either:

- require a caller policy that accepts tentative detection;
- validate the stream to an agreed bound before exposing the member; or
- raise a detection error carrying the candidate and evidence.

Container guesses are less dangerous because opening normally parses a real container
header immediately. The policy still belongs in the caller, not in confidence labels.

## 3. Where this disagrees with PR #257

1. **Ordered acquisition: agree. First match wins: disagree.** Acquisition should be
   cost-aware; selection should compare typed evidence after every relevant stronger tier
   and after all same-class probes.
2. **PR #257 still contains two orders: disagree.** Its canonical list puts far magic
   before tail/scan/probes, while the modified SFX requirement says a scan miss falls
   through to probes and then far magic. One requirement must own the complete order.
3. **Far magic before content probes: agree.** The bootable ISO is a decisive
   reproduction.
4. **Near magic always `CERTAIN`: disagree.** Two-byte magic without mandatory header
   fields is not comparable to self-validating structure.
5. **Validated ZIP tail: agree on the mechanism, not its stated bound or unconditional
   cost rationale.** EOCD location is bounded to 65,557 bytes; exact offset/strong
   validation can require the whole central directory.
6. **Cued scan and validated resume: agree.** The cue is a cost gate; validation is the
   correctness gate.
7. **Tail hit always skips scan: disagree when the prefix itself is a cue or alternatives
   were requested.** That is exactly where multiple SFX payloads are plausible.
8. **Exhaustive scan as a normal next tier: disagree.** It is embedded discovery with a
   different result and resource contract.
9. **Extension as up-front corroborator and last answer: mostly agree.** It must not
   promote a bounded probe above stronger evidence or restrict the candidate set.
10. **Brotli compressed-first `PROBABLE`: disagree for an uncorroborated hit.** The
   real-file measurement does not support that public label. Keep the class as telemetry.
11. **Strong executable cue suppresses content probes: agree as a selection rule.** It
    should not prevent probes from being run for diagnostics if a caller explicitly asks
    for alternatives.
12. **Confidence driving `format_unconfirmed`: disagree.** PR #262's provenance-based
    follow-up is the correct design.
13. **Per-probe `dict_size != 0` guard: disagree and remove.** Zero is legal LZMA Alone;
    the guard is a false-negative compatibility bug introduced to compensate for ISO's
    ordering.
14. **Candidate-relative probing is underspecified.** Makeself and TAR needles occur after
    a prefix, but today's probe/inner-TAR API reads only from source offset zero.

## 4. Required cases

| §6 case | result under this algorithm |
| --- | --- |
| (a) `zipapp` and concatenated ZIP | Seekable: bounded EOCD/geometry validation yields ZIP; walking the central directory yields `CERTAIN` and exact `payload_offset` at the earliest local header (EOCD-derived base for empty ZIP), with the two offset conventions handled explicitly. If the index budget is exhausted, retain ZIP with `payload_offset=None`. Non-seekable: only a cue scan can identify it, and automatic open still needs explicit spooling. |
| (b) PE/ELF + 7z/RAR | Cue-gated scan; reject decoys; validate 7z StartHeader CRC/bounds or RAR main header; return the container and payload offset. |
| (c) makeself `.run` | `#!` authorizes `1f 8b 08` and other sufficiently discriminating stream needles; bounded decode plus checksum-valid inner TAR returns `TAR_GZ` at the gzip offset. |
| (d) `ca fe ba be` | Parse the fat Mach-O header/arch table. A real Mach-O cues the scan; an ordinary Java class produces no cue and pays no 2 MiB scan. |
| (e) Mach-O stub claimed by a probe | A parsed Mach-O is a strong cue. The scan finds and validates 7z; raw-stream probes cannot win on the stub. |
| (f) bootable ISO | The descriptor tuple at 32,768–32,774 is checked before content probes; return ISO. No per-probe ISO workaround is needed. |
| (g) JPEG + ZIP | Tail validation returns ZIP with a nonzero payload offset and non-archive/other prefix. JPEG identity remains out of scope. |
| (h) extension/content agreement | Near evidence already answers the 98.9%. For the residual, agreement is recorded but cannot skip stronger unrun tiers. |
| (i) conflict diagnostic | Describe the actual evidence: e.g. "extension suggests ISO; bounded Brotli probe suggests Brotli; using ISO descriptor evidence." Never hardcode "magic bytes" for a content probe. |

## 5. Cheap structural validators

"Magic" should mean a signature declaration **plus an optional structural validator**.
The validator strengthens or diagnoses the match; it is not always a hard gate.

| format | recommended check | disposition and compatibility caveat |
| --- | --- | --- |
| gzip | `CM == 8`; FLG reserved bits 5–7 are zero; parse optional-field bounds; verify `FHCRC` when present and fully available | Safe mandatory checks. Do **not** require `XFL in {0,2,4}` or `OS in 0..13,255`; RFC 1952 defines customary values but does not make those fields an identity gate. |
| bzip2 | `BZh`, block size ASCII `1`–`9`, then block magic `314159265359` or the empty-stream EOS marker | Strong and cheap at stream start. Treat a short source as incomplete. Later blocks are bit-aligned, but the first marker after the byte-aligned stream header is suitable here. |
| compress `.Z` | `1f 9d`; max-code width at least 9 and supported up to 16; inspect block-mode and reserved bits | Width is structural. Historical decoders warn and continue when reserved bits are set, so reserved bits should downrank/warn rather than erase identity unless archivey intentionally rejects that compatibility. |
| XZ | six-byte magic; two Stream Flags; CRC32 over flags; first flag byte zero; reserved high nibble zero | CRC is mandatory and ideal. A CRC-valid but unsupported check ID is "XZ with unsupported feature", not "not XZ". |
| zstd | regular-frame magic or one of the 16 skippable-frame magics; reserved frame-header bit zero; parse the descriptor and present fields within bounds | Safe for the current frame version. A local check confirmed the installed decoder accepts a legal skippable frame before a regular frame while `detect_format` raises `FormatDetectionError`, so skippable-first detection is a current correctness gap. |
| LZ4 frame | four-byte magic; FLG version `01`; reserved bits/BD legal; parse optional fields; verify the one-byte xxHash header checksum | The checksum is the strongest cheap check. Failure identifies a damaged header; it need not erase the four-byte format identity. |
| lzip | `LZIP`; version 1; coded dictionary size resolves to 4 KiB–512 MiB | Mandatory for the current format. A future version is "lzip version unsupported", not arbitrary data. |
| TAR | parse the full 512-byte header and accept the stored checksum against both unsigned POSIX and historical signed sums | This should replace bare `ustar`. It also permits a conservative v7-TAR candidate without `ustar`; require plausible numeric/type/name fields because checksum-only detection has a different collision surface. All-zero "empty TAR" remains fundamentally indistinguishable from padding. |
| ZIP at origin | parse the local/empty header fields; for seekable sources prefer EOCD geometry plus referenced CD/local records | `PK\x03\x04` is useful; `PK\x05\x06` is an empty ZIP only with a valid EOCD; `PK\x07\x08` alone is too contextual to call `CERTAIN`. Exact earliest-local-header offset can require the whole CD. |
| 7z | six-byte signature; version support; StartHeader CRC over the next 20 bytes; NextHeader offset/size within known source | Use this at offset zero as well as for SFX hits. CRC failure after the six-byte identifier should normally mean damaged 7z, not unknown bytes. |
| RAR | RAR4/RAR5 marker plus parseable/CRC-valid main header | The long marker already identifies strongly; main-header validation makes scan hits safe and distinguishes corruption. |
| ISO 9660 | at offset 32,768 require type 0–3, `CD001`, version 1; optionally inspect consecutive descriptors for a PVD and terminator under a higher budget | Seven constrained bytes need a 32,775-byte prefix and are better than five. Type 255 at sector 16 cannot start a valid set because it terminates before the mandatory PVD; invalid surrounding fields should lower confidence or indicate damage. |
| zlib | any RFC 1950 header with `CM=8`, `CINFO<=7`, and `(CMF*256+FLG) % 31 == 0`; account for `FDICT` | The current four-header allow-list accepts only common 32 KiB-window, no-dictionary encodings. Valid streams with smaller windows begin `18`, `28`, … `68` and are currently missed. |
| LZMA Alone | legal properties byte; any 32-bit dictionary field; size field; then bounded decode/completeness | Do **not** reject dictionary size zero. The LZMA specification allows every 32-bit value and requires decoders to round values below 4 KiB up to 4 KiB. |
| Brotli | RFC-valid WBITS/meta-block framing; source-length overrun; whole-source completeness; bounded chain walk where permitted | No prefix check can manufacture a signature the format does not have. Keep this in the bounded-probe class unless a complete decode reaches EOS. |

Two local checks confirmed the compatibility points:

- a Python-produced LZMA Alone stream still decoded correctly after its dictionary field
  was changed to zero;
- a gzip stream still decoded after changing `XFL` to 1 and `OS` to 254;
- `backports.zstd` decoded a skippable-first stream that archivey's current detector did
  not recognize.

The first is not merely permissive decoder behaviour: the LZMA specification explicitly
allows the zero value. Therefore `_alone_header_plausible()`'s `dict_size != 0` rejection
is an ordering workaround that creates a real false negative. Move ISO ahead of probes and
remove that workaround.

## 6. The filename's role

The filename belongs in several places, but never as an exclusion filter:

- **prior/corroborator:** available from the start and recorded when it agrees;
- **work authorizer:** under a constrained budget, it may justify an otherwise optional
  expensive format-specific check;
- **tie-break:** only between candidates with the same content-evidence class;
- **last-resort answer:** `GUESS`, after all enabled content detectors decline.

It must not:

- prevent a detector for a different format from running;
- promote extension + bounded probe above unconsulted exact/validated evidence;
- suppress a conflict diagnostic;
- change whether a later failure is marked probe-only.

The 98.9% number in the prompt argues **against** an extension-agreement shortcut as a
default optimization: near evidence has already captured almost the entire population
where agreement is cheap. The two residual files are too small a sample to justify a new
control-flow rule.

## 7. When agreement may stop work

Agreement may skip only tiers whose maximum evidence cannot dominate the current winner.

Examples:

- validated XZ header + `.xz` may stop ordinary single-result detection after all
  fixed-offset evidence required by the selected policy;
- Brotli bounded probe + `.br` may **not** skip ISO far magic or a validated ZIP tail;
- gzip short header + `.gz` may not skip stronger fixed-offset evidence;
- extension + any probe may skip another equally weak optional probe only if the caller
  explicitly chose that budget and the result says the search is incomplete.

This is the exact branch-and-bound stopping rule:

> Stop when the winner is unique and every unrun detector is either incapable of
> producing a candidate that dominates it, unavailable by source capability, or excluded
> by an explicit budget recorded in the result.

The last arm does not make the result certain; it makes it budget-limited.

## 8. Candidate restrictions

Restrictions that preserve correctness:

- skip fixed-offset evidence only when remaining size proves the offset unreachable;
- restrict compressor SFX needles to `#!`, because that is a real wrapper grammar and
  materially reduces short-magic collisions;
- suppress raw-stream selection after a structurally valid executable cue;
- run an inner-TAR check only after an outer stream candidate exists;
- skip a probe when its backend is unavailable, while recording the unavailable evidence
  path.

Restrictions that are not acceptable:

- probe only the extension's format;
- exclude content candidates because the suffix disagrees;
- use the `(container, stream)` taxonomy to hide another outer container before its
  evidence was checked;
- treat "source is seekable" as proof that a 64 KiB tail read is within the caller's
  budget.

## 9. Brotli

### Confidence split

The compressed/uncompressed-first split is useful telemetry, but it should not determine
public confidence by itself.

The random-data result (`0.014%` compressed-first) does not transfer to the measured real
population (64 fabricated compressed-first claims versus 4 genuine streams). That is a
base-rate and data-distribution failure, not a contradiction in the random experiment.

Recommended mapping:

- complete decode to EOS with the entire source and no forbidden trailing bytes:
  `CERTAIN` content evidence;
- bounded probe + independent corroboration (`.br` or checksum-valid inner TAR):
  `PROBABLE`;
- bounded probe alone, compressed or uncompressed first: `GUESS`, with the first-block
  class retained as evidence detail.

More importantly, all bounded probe-only failures carry `format_unconfirmed=True`. PR
#262's provenance change is correct; confidence must not steer this signal.

### Completeness

The completeness rule is sound:

```text
if the probe holds every source byte:
    "needs more input" is rejection
```

It is not a "reject small files" heuristic. A nine-byte valid Brotli stream that reaches
EOS survives; an incomplete 15-byte prefix that merely asks for byte 16 does not.

The implementation must know that the prefix contains all bytes **remaining from the
detection origin**. A total underlying stream size that overestimates the remaining size
only loses the optimization; it must never be treated as an exact remaining length.

### Chain walk

The chain walk is worth implementing under an explicit read/link budget. It is derived
from framing, helps the large-source population where the 2²⁴ first-block limit is
vacuous, and does not create false negatives when budget exhaustion means "inconclusive".

It does not violate the non-consuming contract in principle: `peek_more` already exists
to obtain a larger non-consuming prefix, and the inner-TAR probe already reaches beyond
4096 bytes. It does widen the **cost and retention** contract. Keep Brotli knowledge in
the codec by giving the probe a bounded range-read/peek callback rather than moving its
parser into the generic detector.

No better zero-cost invariant is apparent. Once a compressed meta-block begins, finding
its successor requires decoding its Huffman/LZ77 body. The useful next steps are therefore
more structure, not a threshold:

- walk byte-aligned metadata/uncompressed links;
- parse compressed blocks through the real decoder under input/output limits;
- accept a strong result when EOS is reached on the whole source.

One suggested discriminator is
`decoded_output_bytes >= consumed_input_bytes` for compressed-first hits. It may be useful
telemetry, but it is not a Brotli invariant: the format permits a compressed meta-block
whose representation expands, and an unusual encoder need not make the reference
encoder's profitability choice. Never make it a hard rejection rule. It is worth adding
the ratio to the residual census to see whether it improves ranking without false
negatives on the positive corpus.

## 10. Cost model and presets

Expose budgets, not only booleans:

```python
@dataclass(frozen=True)
class DetectionBudget:
    max_prefix_bytes: int
    max_tail_bytes: int
    max_seeks: int
    max_scan_bytes: int
    max_decode_input: int
    max_decode_output: int
    max_index_bytes: int
    max_probe_links: int
    spool_non_seekable_up_to: int
    collect_alternatives: bool
```

Suggested policies:

| policy | behavior |
| --- | --- |
| `BALANCED` (default) | 4096 near; ISO far; validated ZIP tail; 2 MiB scan only on cues; bounded probes/inner TAR; no exhaustive scan; no implicit spool |
| `FAST` | caller-selected smaller tail/scan/decode budgets; returns `search_complete=False`; must not silently let a weak result stand in for skipped stronger evidence |
| `THOROUGH` | balanced plus alternative collection, bounded full-stream completion where affordable, and explicit embedded scan on a reopenable/seekable source |

The founding backup-corpus use case argues for `BALANCED`, not extension-first and not
exhaustive. However, the always-on ZIP tail tier is under-measured. The fact that 65,557
bytes is format-bounded proves completeness, not affordability. Before freezing it as the
default, measure aggregate bytes, seeks, and cold-cache wall time over the backup corpus.

The detector should return the actual cost receipt already aligned with archivey's cost
philosophy: prefix bytes requested, unique bytes read, tail bytes, index/central-directory
bytes, seeks/range requests, decoded input/output, and buffered/spooled bytes.

## 11. Non-seekable sources

Do not implicitly buffer an entire pipe to make ZIP detection possible.

Reasons:

- the ZIP backend itself requires random access, so detection alone does not solve open;
- unbounded memory use violates the cost contract;
- waiting for EOF changes a streaming open into a whole-input operation;
- a socket may not have an imminent EOF.

Default degradation:

- near/far prefix evidence and cue-gated forward scans still work through replay buffering;
- ZIP tail evidence is unavailable;
- the result records the skipped capability;
- `open_archive` raises the appropriate non-seekable/capability error if detection finds a
  format whose backend cannot consume the source.

Offer an explicit spool policy that writes at most `N` bytes to a seekable temporary file
and shares that object with the backend. Spill-to-disk is preferable to unbounded RAM.
A prefixed ZIP on a pipe is then detectable only when the caller chose spooling and the
source ended within budget. That is the right trade.

## 12. Polyglots and multiple truths

Archivey should represent multiple **archive** truths, but it should not become a general
file-type detector.

For JPEG + appended ZIP, archivey can truthfully report:

```text
ZIP payload at offset N; prefix is non-archive/other-format bytes
```

It need not identify JPEG. Callers needing both MIME identities should compose archivey
with libmagic or another file detector.

When two archive candidates survive:

- expose a ranked tuple through a new `detect_formats()` or an `alternatives` field;
- let `detect_format()` return the unique dominant candidate;
- if neither dominates, raise an ambiguity carrying both candidates rather than selecting
  whichever backend was registered first.

Without this, the acquisition order becomes an undocumented intent policy. For example,
an executable can contain both an EOF-anchored ZIP and a CRC-valid 7z payload. "ZIP tail
runs first" is not evidence that the producer intended ZIP.

## 13. What to measure next

The weakest support is not the Brotli mechanism; that is well explained. It is the use of
corpora whose base rates do not resemble the founding workload.

Run one labelled, stratified evaluation:

1. real backup trees with names preserved, including wrong/missing extensions;
2. genuine positives from old and current writers for every format;
3. damaged/truncated positives, so validators do not accidentally erase identity;
4. non-archive negatives stratified by file family (executables, source, media, databases,
   VM images, random/encrypted);
5. no-magic positives, deliberately oversampling Brotli, zlib, and LZMA Alone;
6. prefixed archives and deliberate polyglots.

For each detector declaration, report:

- true/false positives before and after structural validation;
- overlap matrix between probes;
- results at offset zero separately from arbitrary-offset scan collisions;
- confidence calibration/positive predictive value by corpus stratum;
- false negatives by writer/version;
- unique bytes read, requested bytes, seeks, retained bytes, decode input/output, and
  cold-cache wall time;
- how often the winner changes when tail/far/scan tiers are disabled.

Two current numbers should not carry decisions they do not measure:

- `1f 8b` appearing 423 times at arbitrary offsets in ELF files is directly relevant to
  an SFX scan, but it says little about false matches at **offset zero**;
- `/usr` having only two extension+probe residuals says little about a Brotli-heavy or
  backup corpus.

Specific experiments:

- measure the always-on ZIP tail tier over the actual backup corpus; its locator-only
  worst-case across 71,983 large files is 4.39 GiB, but the real tail total is
  `sum(min(size, 65557))`, and CD/local validation adds separately budgeted reads;
- enumerate every valid near-header shape and its offset-zero collision rate before/after
  the proposed validator;
- test TAR signed/unsigned checksums and v7 headers from historical writers;
- add legal LZMA Alone dictionary values below 4 KiB, including zero;
- generate zlib streams for every legal `CINFO` and with `FDICT`;
- build a `.br`-rich positive corpus with compressed-, uncompressed-, metadata-, and
  empty-first streams;
- record decoded-output/input ratios for every compressed-first Brotli hit, as a possible
  ranking feature rather than a validity gate;
- add a skippable-first zstd fixture and decide how concatenated/skippable frames compose
  into one detected stream;
- compare a one-shot 65,557-byte ZIP tail read with a 4 KiB-then-expand strategy on the
  residual population: ordinary ZIPs usually stop at near magic, so most tail attempts
  may be non-ZIP misses where the adaptive strategy reads more, not less;
- survey non-executable prefixed 7z/RAR files before treating the cue set as complete;
- seed multi-archive polyglots to force an explicit ambiguity policy.

Until those results exist, three choices remain underdetermined:

- whether the 65,557-byte tail probe belongs in the default corpus budget;
- numeric thresholds behind `CERTAIN`/`PROBABLE`;
- the product policy for two equally valid archive interpretations.

## 14. Primary format references

- [RFC 1952 — gzip](https://www.rfc-editor.org/rfc/rfc1952)
- [RFC 1950 — zlib](https://www.rfc-editor.org/rfc/rfc1950)
- [RFC 7932 — Brotli](https://www.rfc-editor.org/rfc/rfc7932)
- [RFC 8878 — Zstandard](https://www.rfc-editor.org/rfc/rfc8878)
- [XZ file format](https://tukaani.org/xz/xz-file-format.txt)
- [LZMA file format](https://github.com/tukaani-project/xz/blob/master/doc/lzma-file-format.txt)
- [LZMA SDK specification](https://github.com/jljusten/LZMA-SDK/blob/master/DOC/lzma-specification.txt)
- [LZ4 frame format](https://github.com/lz4/lz4/blob/master/doc/lz4_Frame_format.md)
- [lzip manual](https://lzip.nongnu.org/manual/lzip_manual.html)
- [7z format](https://github.com/ip7z/7zip/blob/main/DOC/7zFormat.txt)
- [PKWARE ZIP APPNOTE 6.3.9](https://pkwaredownloads.blob.core.windows.net/pkware-general/Documentation/APPNOTE-6.3.9.TXT)
- [ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/)
- [tar format notes](https://man.archlinux.org/man/tar.5.en)


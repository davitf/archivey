# Silent exceptions — the "one place does something different" sweeps

Three sweeps from `brief.md` §B, plus what each found. Two of the three came back
essentially clean, which is worth as much as the one that did not.

---

## 1. Spec promises without code (the O-23 class)

**Scope.** Landed capabilities only, per the §B carve-out. `archive-writing` (Phase 9)
and `seekable-gzip-and-block-writing` (Phase 8, live change with its own brief and
owner) were **not** audited — they are deliberately ahead of the code and are not spec
fiction.

**Method.** Grep every live spec for advisory/emission language (`SHALL emit`,
`MAY warn`, `warning`, `diagnostic`), then find the code that performs it and the test
that asserts it.

| Spec clause | Code | Verdict |
|---|---|---|
| `format-zip`: "SHALL emit a `diagnostics` warning identifying the member and the chosen encoding" when the UTF-8 sniff picks a non-default encoding | `DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED` (`diagnostics.py:61`) with a `NameEncodingContext` | **Honest** |
| `format-detection`: "magic wins and warning is emitted" | `FORMAT_EXTENSION_CONFLICT` (`diagnostics.py:62`) | **Honest** |
| `packaging-and-extras`: "integrity diagnostic/warning instead of failing the read" | `DIGEST_UNVERIFIABLE` (`diagnostics.py:68`) | **Honest** |
| `seekable-decompressor-streams`: slow-rewind "warns/names `[seekable]` accelerator" | `STREAM_REWIND_REDECOMPRESSES` (`archive_stream.py:442`) | **Honest** |
| `seekable-decompressor-streams`: optional seek index degrades | `SEEK_INDEX_DEGRADED` | **Honest** |
| `archive-reading`: solid random `open()` — "**no diagnostic, no warning**", discoverable via `cost.access_cost` and the docstring | correctly **absent**; `grep -rn "warnings.warn" src/archivey/` returns **nothing** | **Honest** (the #225 spec-drop landed cleanly) |
| `format-single-file-compressors`: "XZ, ZST \| Header size when encoder wrote it; otherwise `None`" | only surfaced under `seekable_members=True` | **Divergent** — filed as **F1**, not counted twice |

**Result:** one divergence, and it is F1's spec half. The class #225 opened (a
requirement describing behaviour that never shipped) did not recur elsewhere in the
landed specs.

---

## 2. Silent argument discard

The class `#225`/P8 opened: an explicit caller assertion overruled without error. #225
fixed exactly one instance (directory `format=`). The sweep asked whether the *rule* was
generalized. It was not.

### Confirmed instances

| Argument | Honoured by | Silently discarded by | Filed as |
|---|---|---|---|
| `encoding=` | ZIP, TAR + all compressed-TAR variants | ISO, 7z, directory, single-file | **F2** |
| `format=` (explicit, wrong, but plausible-as-empty) | every format that fails loudly | TAR over an ISO's zero-filled system area | **F7** |

### Checked and clean

| Candidate | Finding |
|---|---|
| `password=` on a format with no encryption | **Refused**, not ignored — `UnsupportedOperationError` on all 22 non-encrypting keys. A bare `PasswordProvider` is correctly allowed through (unused backends never call it). |
| `streaming=True` + `concurrent_members=True` | **Refused** at open on every key. |
| `seekable_members=True` where a backend cannot seek | Not a discard — the flag is honoured to the extent the backend can, per spec. (It also does something it should not: **F1**.) |
| `config.strict_archive_eof` on non-TAR formats | Inert by construction (the setting names an EOF marker only TAR has); no caller assertion is being overruled. Records as fine, but see **F7** — it is also inert in the one case where a caller would most want it. |
| `config.use_rapidgzip` / `use_indexed_bzip2` on formats with no such codec | Inert by construction; `AcceleratorMode.AUTO` is a preference, not an assertion. Fine. |
| `limits=` / `ExtractionLimits` per format | Applied uniformly by the extraction coordinator, not per backend. Fine. |
| CLI flags that no-op on some formats | None found; the CLI's format-specific behaviour is in what it *prints*, not in silently dropped flags. |

**The generalizable rule, if the maintainer wants one:** *an explicit argument that
names something the resolved backend cannot act on is refused at the entry point.*
That covers F2 directly and F7 partially. It is one rule replacing three special cases,
which is the §Values "predictability beats cleverness" trade the brief asks for. → **Q2**.

---

## 3. Error-translation consistency

**Method.** Grep `src/archivey/` for raw `ValueError` / `RuntimeError` /
`NotImplementedError` / `TypeError` / `AssertionError` raises, then check whether any can
cross the public boundary; cross-check against ~1000 probe calls that recorded the exact
exception type for every cell.

**Result: clean.** No raw non-`ArchiveyError`, non-`ArchiveyUsageError` exception
crossed the public boundary in the whole probe run, except the ones the spec *requires*:

- `TypeError` for `len(reader)` and `"name" in reader` — mandated by `archive-reading`.
- `io.UnsupportedOperation` for `seek()` without the capability — mandated.
- `ValueError` for I/O on a closed stream — mandated ("closed stream I/O continues to
  raise `ValueError`").
- `FileNotFoundError` for a missing path — mandated ("filesystem `OSError` … propagate
  unchanged"). The *directory* case is the exception, and it is **F11**.

The raw raises that exist in `src/` were inspected individually:

| Site | Assessment |
|---|---|
| `zip_reader.py:727` `RuntimeError` on a missing `ZipInfo._raw_time` | Deliberate and well-commented: a silent fallback would misreport correct ZipCrypto passwords as wrong. Loud is right. Reachable only on a future Python that drops a CPython private attribute. **Fine.** |
| `zip_reader.py:217` `AssertionError("unreachable: cp437 decodes every byte")` | Genuine invariant. **Fine.** |
| `zip_reader.py` ×5 `ValueError("Attempt to use ZIP archive that was already closed")` | Mirrors stdlib `zipfile`'s own message for the same condition; reached only through a closed-source path that the reader's own guard catches first. **Fine.** |
| `rar_unrar.py:157` `RuntimeError("unrar produced no stdout pipe")` | The only site where a raw `RuntimeError` could plausibly reach a caller, and only if `subprocess.Popen(stdout=PIPE)` returned no pipe — which does not happen. Not worth a change on its own; worth a line in a future error-contract sweep. **Fine, noted.** |
| `directory_reader.py:325` `TypeError("Directory backend requires a Path source")` | Internal invariant; `core.py` cannot route a non-Path here. **Fine.** |
| `volumes.py`, `detection.py`, `zip_aes.py` `ValueError`s | All argument-validation on internal seek/slice helpers, behind the public boundary. **Fine.** |

**Conclusion, corrected by the merge:** the S1 error boundary held on every newer path
*inside* it — the RAR `unrar` map, the 7z pipeline, single-file, and the extract
coordinator all route through the base reader's translation and stamping. But the second
pass found three holes **outside** it, which a probe that only exercises opened readers
cannot see: `resolve_source` runs before any translator exists (**F3**), ZIP's blanket
`ValueError` arm swallows a lifecycle fault on the way past (**F4**), and one `unrar`
spawn site sits outside `_translated_errors` (**F15**). Those are written up below; the
*inside* of the boundary remains a non-issue.

---

## 3b. F3 — raw `ValueError` crosses `open_archive` for volume-sequence misuse

**Sites** (all in `src/archivey/internal/volumes.py`): `:145` empty `ConcatenatedFile`
sources, `:167` non-seekable volume stream, `:269` `join_volumes` empty paths, `:314`
`resolve_source([])`.

**Why nothing types them:** `core.open_archive` calls `resolve_source` at `core.py:194`
— before format resolution, before the registry lookup, before any backend exists. There
is no translator on that path.

**Observed (re-verified in this tree):**

```python
open_archive([])
# builtins.ValueError: source sequence must not be empty

open_archive([pipe, pipe])           # two non-seekable streams
# builtins.ValueError: all volume streams must be seekable
```

**The inconsistency is the finding.** The *single*-source spelling of the second refusal
is already typed and carries a remediation sentence:

```
StreamNotSeekableError: Random access (streaming=False) requires a seekable source.
Open with streaming=True ... or buffer it to disk or a BytesIO and reopen.
```

So the same caller mistake gets a typed, actionable error when written one way and a
bare `ValueError` when written another. Empty-sequence is caller misuse
(`ArchiveyUsageError`); non-seekable volumes are a capability refusal
(`StreamNotSeekableError`).

**Classification: accident.** → **Q3**. Guardrails:
`test_empty_source_sequence_raises_raw_valueerror`,
`test_non_seekable_volume_sequence_raises_raw_valueerror`, plus two red halves.

---

## 3c. F4 — ZIP reports an already-closed handle as archive damage

ZIP's `_translate_exception` maps **every** `ValueError` to `CorruptionError`. The ZIP
reader also raises `ValueError("Attempt to use ZIP archive that was already closed")` in
five places (mirroring stdlib `zipfile`). The two meet:

```
CorruptionError: Corrupt ZIP member offset/structure:
ValueError('Attempt to use ZIP archive that was already closed')
```

`ArchiveStream._fail` special-cases the substring `"closed file"`, which
`"already closed"` does not match.

**Not this finding:** a normal `reader.close()` followed by `open()`/`members()` already
raises `ArchiveyUsageError` — settled in `#225` and pinned by the uniform-surface
guardrail. Only the path where the **underlying handle is closed while the reader still
believes it is open** lands in the blanket arm.

**Why it matters beyond taxonomy:** `CorruptionError` tells a caller the *archive* is
damaged. Here the bytes are fine and the handle is not, so the error sends them hunting
a bad file. Compare the translator breadths — ZIP's is by far the widest:

| Backend | `_translate_exception` catches |
|---|---|
| ZIP | `BadZipFile`, `RuntimeError`(pw), `UnsupportedOperation`, `NotImplementedError`, zlib/lzma, **all `ValueError`**, `OSError`(bz2), `UnicodeDecodeError`, `EOFError` |
| TAR | `ReadError`, `EOFError` |
| 7z / RAR | `EOFError` only |
| ISO | PyCdlib + `IndexError`/`struct`/`ValueError`/… → corruption |

**Classification: accident.** Narrowest fix: carve `"already closed"` out ahead of the
blanket arm. Broader fix: narrow ZIP's `ValueError` arm to known-corruption substrings —
worth considering, since it is the arm most likely to mislabel the *next* lifecycle bug
too. → **Q4**. Guardrails: `test_zip_underlying_close_is_reported_as_corruption` (pin),
`test_zip_underlying_close_is_a_usage_error` (red half).

---

## 3d. F15 — one raw `RuntimeError` on the `unrar` spawn path

`rar_unrar.py:157` raises `RuntimeError("unrar produced no stdout pipe")` when
`proc.stdout is None`. Its call sites (`rar_reader.py:682`, `:883`) are **outside**
`_translated_errors`, and the RAR translator returns `None` for anything but `EOFError`,
so it would escape raw.

The two passes disagreed mildly on disposition: unreachable in practice with
`subprocess.Popen(..., stdout=PIPE)` (so "note it"), versus cheap to close (so "map it").
Both are defensible; it is a one-line change either way. `PLAUSIBLE`, not `CONFIRMED` —
no repro exists because the condition cannot be provoked. → **Q15**.

---

## 4. The diagnostics boundary (O-23's "one awkward code")

The brief asked to **flag, not churn**. Flagging:

`STREAM_REWIND_REDECOMPRESSES` still sits on the usage side of the O-23 rule
(*diagnostics describe the archive, not the caller's usage*). It fires because the
caller seeked backwards, not because the archive has a property. `SEEK_INDEX_DEGRADED`
is the neighbouring code and is on the right side (it describes the stream's index).

The review found **no cleaner cut**, so per the brief this stays flagged and unchurned.
One observation that may matter if it is ever revisited: the rewind event is also the
only diagnostic whose trigger a caller can eliminate entirely by passing a flag, which
is arguably what makes it feel like usage rather than archive.

The genuinely open half of O-23 — whether to emit a plain `warnings.warn` on a solid
random `open()` — is **still undecided**, and there is no such call anywhere in `src/`.
→ **Q13**.

---

## 5. F10 — the one advisory that is not queryable data

`VISION.md`, twice over:

> Anything the library can only *warn* about should ideally also be **queryable as
> data** — a logging warning most applications never see is a surprise deferred, not
> avoided.

Every advisory in the library satisfies that — `MEMBER_NAME_NORMALIZED`,
`MEMBER_NAME_ENCODING_INFERRED`, `FORMAT_EXTENSION_CONFLICT`, `MEMBER_TIMESTAMP_INVALID`,
`SYMLINK_TARGET_UNAVAILABLE`, `DIGEST_UNVERIFIABLE`, `SEEK_INDEX_DEGRADED`,
`STREAM_REWIND_REDECOMPRESSES`, `ARCHIVE_EOF_MARKER_MISSING`, the extraction codes —
**except one**. `naming.py:38–53` warns about bidirectional control characters in a
member name (the RTL-override disguise trick) through a bare `logger.warning`, with no
`DiagnosticCode`, no context dataclass, and therefore no `reader.diagnostics` entry, no
`DiagnosticPolicy` escalation, and no way for a security-conscious caller to *query* it.

Its immediate neighbour in the same helper — name normalization — does have a code. So
this is an omission, not a policy.

**The spec angle, and a correction to the second pass's framing.**
`testing-contract:55` says *"RTL warns or rejects"*, and the scenario at `:76–79` says
*"the member is rejected **or** exactly one warning is emitted"*. The second pass filed
this as spec fiction. It is not: the clause is a **disjunction** and the code implements
one branch, so no requirement goes unperformed. The defect is that the clause is
**uninformative** — a reader cannot tell which outcome ships, which is the opposite of
what a conformance spec is for. Tightening it to "warns exactly once via `logger`; null
bytes reject as traversal" is right, and is the cheaper half of Q10.

The rankable finding is the VISION gap underneath: promoting bidi to a `DiagnosticCode`
would make the library's advisory surface uniform for the first time. → **Q10**.
Guardrail: `test_bidi_name_warning_has_no_diagnostic_code`.

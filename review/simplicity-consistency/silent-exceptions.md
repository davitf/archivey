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
| `format-single-file-compressors`: "XZ, ZST \| Header size when encoder wrote it; otherwise `None`" | only surfaced under `seekable_members=True` | **Divergent** — filed as **P1**, not counted twice |

**Result:** one divergence, and it is P1's spec half. The class #225 opened (a
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
| `encoding=` | ZIP, TAR + all compressed-TAR variants | ISO, 7z, directory, single-file | **P4** |
| `format=` (explicit, wrong, but plausible-as-empty) | every format that fails loudly | TAR over an ISO's zero-filled system area | **P3** |

### Checked and clean

| Candidate | Finding |
|---|---|
| `password=` on a format with no encryption | **Refused**, not ignored — `UnsupportedOperationError` on all 22 non-encrypting keys. A bare `PasswordProvider` is correctly allowed through (unused backends never call it). |
| `streaming=True` + `concurrent_members=True` | **Refused** at open on every key. |
| `seekable_members=True` where a backend cannot seek | Not a discard — the flag is honoured to the extent the backend can, per spec. (It also does something it should not: **P1**.) |
| `config.strict_archive_eof` on non-TAR formats | Inert by construction (the setting names an EOF marker only TAR has); no caller assertion is being overruled. Records as fine, but see **P3** — it is also inert in the one case where a caller would most want it. |
| `config.use_rapidgzip` / `use_indexed_bzip2` on formats with no such codec | Inert by construction; `AcceleratorMode.AUTO` is a preference, not an assertion. Fine. |
| `limits=` / `ExtractionLimits` per format | Applied uniformly by the extraction coordinator, not per backend. Fine. |
| CLI flags that no-op on some formats | None found; the CLI's format-specific behaviour is in what it *prints*, not in silently dropped flags. |

**The generalizable rule, if the maintainer wants one:** *an explicit argument that
names something the resolved backend cannot act on is refused at the entry point.*
That covers P4 directly and P3 partially. It is one rule replacing three special cases,
which is the §Values "predictability beats cleverness" trade the brief asks for. → **Q4**.

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
  unchanged"). The *directory* case is the exception, and it is **P6**.

The raw raises that exist in `src/` were inspected individually:

| Site | Assessment |
|---|---|
| `zip_reader.py:727` `RuntimeError` on a missing `ZipInfo._raw_time` | Deliberate and well-commented: a silent fallback would misreport correct ZipCrypto passwords as wrong. Loud is right. Reachable only on a future Python that drops a CPython private attribute. **Fine.** |
| `zip_reader.py:217` `AssertionError("unreachable: cp437 decodes every byte")` | Genuine invariant. **Fine.** |
| `zip_reader.py` ×5 `ValueError("Attempt to use ZIP archive that was already closed")` | Mirrors stdlib `zipfile`'s own message for the same condition; reached only through a closed-source path that the reader's own guard catches first. **Fine.** |
| `rar_unrar.py:157` `RuntimeError("unrar produced no stdout pipe")` | The only site where a raw `RuntimeError` could plausibly reach a caller, and only if `subprocess.Popen(stdout=PIPE)` returned no pipe — which does not happen. Not worth a change on its own; worth a line in a future error-contract sweep. **Fine, noted.** |
| `directory_reader.py:325` `TypeError("Directory backend requires a Path source")` | Internal invariant; `core.py` cannot route a non-Path here. **Fine.** |
| `volumes.py`, `detection.py`, `zip_aes.py` `ValueError`s | All argument-validation on internal seek/slice helpers, behind the public boundary. **Fine.** |

**Conclusion:** the S1 error boundary held on every newer path the brief asked about —
the RAR `unrar` map, the 7z pipeline, single-file, and the extract coordinator all route
through the base reader's translation and stamping. This seed is a **non-issue** and is
recorded in SUMMARY §What is actually fine so it is not re-derived.

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
→ **Q7**.

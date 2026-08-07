# QUESTIONS — simplicity & consistency

Maintainer decisions. Analysis-only until pay items are picked. Behaviour churn
is free until `0.2.0` — prefer deleting accidents over documenting them.

## Q1 — Empty / non-seekable multi-volume sources (F1)

`open_archive([])` and non-seekable volume streams raise raw `ValueError`.

- **Recommend:** map to `ArchiveyUsageError` (empty) and `StreamNotSeekableError`
  (non-seekable), matching pipe refusal.
- **Vehicle:** bug fix PR + tests from `repro/repros.py` R8.
- **Pay before tag?** Yes — cheap, error-contract honesty.

## Q2 — `encoding=` on formats that ignore it (F2)

Password on TAR raises; encoding on 7z/RAR/dir/ISO/single-file is silent.

| Option | Effect |
|---|---|
| A — Reject (password-parallel) | Loud; may break callers who pass encoding everywhere |
| B — Document ZIP/TAR-only | Status quo + honesty |
| C — One diagnostic | Soft middle |

- **Recommend:** A for static `encoding=` when backend would `del`/ignore; allow
  `None`. Mirror password gate with `SUPPORTS_ENCODING` or a central allowlist
  (ZIP, TAR).
- **Pay before tag?** Yes if choosing A (uniform interface); else B is docs-only.

## Q3 — ZIP already-closed → CorruptionError (F3)

- **Recommend:** carve out → `ArchiveyUsageError`; consider narrowing the blanket
  ValueError→Corruption arm.
- **Vehicle:** bug fix PR.
- **Pay before tag?** Yes.

## Q4 — RTL "warns or rejects" (F4)

- **Recommend:** spec edit to "warns once via logger" (what ships). Optional
  later: diagnostic code (VISION: queryable > ambient).
- **Vehicle:** OpenSpec change on `testing-contract` (landed capability).
- **Pay before tag?** Spec honesty — yes (small).

## Q5 — `STREAM_REWIND_REDECOMPRESSES` (F5 / O-23)

Diagnostics-describe-archive rule vs rewind-on-seek usage signal.

| Option | Effect |
|---|---|
| Keep | Document as the one usage-side diagnostic |
| Demote | `warnings.warn` / logger only; drop from taxonomy |
| Split | Archive capability diagnostic vs usage warning |

- **Recommend:** Keep for `0.2.0` with an explicit taxonomy note (cheapest); do
  not invent a solid-open warning (already decided silent).
- **Pay before tag?** Decision only; code churn optional.

## Q6 — single-file `compressed_size` Path gate (F6)

- **Recommend:** fill from seekable `SEEK_END` (parity with CRC probe).
- **Vehicle:** bug fix PR.
- **Pay before tag?** Yes — finishes the `#225` Path/seekable sweep.

## Q7 — Vocabulary C1: `seekable_members` vs `open_stream(seekable=)` (F8)

- **Recommend:** pick one spelling before tag, or explicitly accept dual with a
  one-line guide note.
- **Pay before tag?** Freeze-cost argument for deciding; rename is free now.

## Q8 — CLI vs library defaults (F9)

Already decided in `cli-product` Q1. Reconfirm: **accept** as product law;
must-explain #23 + cli.md must stay accurate. No re-litigation unless product
direction changed.

## Q9 — Header-encrypt password vs "lazy until read" docs (F15)

- **Recommend:** docs-only caveat on `reading-members.md` laziness bullet.
- **Pay before tag?** Cheap honesty.

## Q10 — RAR stdout-pipe RuntimeError (F10)

- **Recommend:** map in RAR `_translate_exception` or spawn wrapper.
- **Pay before tag?** Nice-to-have (defensive).

---

## Proposed pay list (if agreeing with recommends)

1. F1 volumes ValueError translation  
2. F3 ZIP closed misclassification  
3. F6 compressed_size seekable fill  
4. F4 testing-contract RTL wording  
5. F2 encoding policy (after Q2)  
6. F7 + F15 docs  

Park / accept: F5 (decide), F8 (decide), F9 (accept), F11–F13 (fine), F14 (signal).

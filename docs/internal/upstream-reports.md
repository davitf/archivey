# Upstream reports — tracker

Archivey ships mitigations for several **defects in its optional native
dependencies**. Because VISION's load-bearing claim #2 is a *safety* claim, the
mitigations are not the whole obligation: the underlying bugs should be reported
upstream so a fixed release exists to depend on, and so the `[safe]`-adjacent
story is honest ("we work around known bug X, filed as issue Y").

This page is the **single index** of those reports — what is filed, what is
ready to file, what still needs a write-up — so the state does not scatter across
`known-issues.md`, per-library report files, and OpenSpec changes. Each row's
detail lives in its canonical report; this page tracks **status and pointers**,
not the analysis.

Related: fuzzing / OSS-Fuzz onboarding is [threat-model](threat-model.md) O5;
the mitigations themselves are in [`known-issues.md`](known-issues.md). Retiring
the CI bandages these bugs force (e.g. `--allow-exit-after-green`) is gated on a
fixed upstream release, so filing is on the release-hygiene path even though it
does not block the `0.2.0` tag.

## At a glance

| Dep (pinned) | Defect | Class | **Status** | Canonical report | Repro(s) | Unblocks when fixed |
|---|---|---|---|---|---|---|
| **pyppmd** `>=1.3.1` | Output-buffer **use-after-free** (`ThreadDecoder.c:134`) on a parked worker resumed at teardown; heap corruption on *valid* PPMd7 data | **bug** (memory-safety) | **Ready to file — not filed** | [`ppmd-native-investigation-results.md` §J](ppmd-native-investigation-results.md) (pointer: [`pyppmd-upstream-report.md`](pyppmd-upstream-report.md)) | `scripts/pyppmd_crash_repro.py` (probabilistic); `scripts/ppmd_uaf_valgrind.py` (deterministic) | Drop CI `--allow-exit-after-green`; the bounded-decode / `pack_size` guards stay regardless (old wheels persist) |
| **rapidgzip** `>=0.16.0` | (a) Soft/empty EOF on truncated gzip; (b) `std::terminate` after some path-source errors / dual-load / Python-source `terminate()` | (a) **by design** (→ feature request, not opening); (b) **bug** | (a) documentation-only; (b) **not filed** | [`rapidgzip-upstream-report.md`](rapidgzip-upstream-report.md) + deep dive `openspec/changes/rapidgzip-truncation-investigation/UPSTREAM_TRUNCATION_REPORT.md` | `scripts/rapidgzip_truncation_sweep.py`; `scripts/dual_accelerator_repro.py`; `scripts/macos_accelerator_debug.py` | An `is_stream_complete()`-style API would let the ISIZE backstop go; the abort fixes would let AUTO widen |
| **pycdlib** `>=1.16.0` | Infinite loop on a directory-record **cycle** (child extent pointing back at an ancestor) — hang on hostile ISO input | **bug** (DoS/hang) | **Needs write-up — not filed** | none yet — described in [`known-issues.md`](known-issues.md#importing-the-iso-backend-patches-pycdlib-process-globally-by-design) | `tests/test_iso.py::test_pycdlib_directory_cycle_does_not_hang` (+ `_pycdlib_directory_cycle_image` builder) | The process-global cycle-guard monkeypatch (`iso_reader._install_pycdlib_directory_cycle_guard`) could be removed |

**Status legend:** *Filed* — issue open upstream (link it here). *Ready to
file* — a self-contained, paste-able report + repro exists; just needs
submitting. *Documentation-only* — behavior is upstream-by-design, so we record
the contract we work around rather than filing a bug. *Needs write-up* — we have
a repro and a mitigation but not yet a paste-able report.

## pyppmd — native use-after-free (memory-safety)

- **Repo:** <https://github.com/miurahr/pyppmd> — no matching issue as of
  2026-07-23.
- **What to file:** [`ppmd-native-investigation-results.md` §J](ppmd-native-investigation-results.md)
  is the canonical, self-contained report (title, summary, reproduction, root
  cause, prioritised fixes, verification checklist). `pyppmd-upstream-report.md`
  is now just a pointer to §J plus the corrected root-cause note (the first
  corrupting write is the output-buffer UAF, **not** the vendored 7-Zip model
  walk the earlier draft hypothesised). Attach **both** repro scripts.
- **Archivey ships regardless:** bounded decodes, required `unpack_size` /
  `pack_size` (incl. the encrypted-folder plumbing), single capped NUL flush,
  and quiesce-on-close. Exact-sized decode is 0 valgrind errors; the residual is
  the `Ppmd7T_Free` teardown race on truncated/abandoned members, which is why
  CI still carries `--allow-exit-after-green`.
- **Verification when a fixed release ships:** run the §J checklist; the
  deterministic `ppmd_uaf_valgrind.py` gate is the evidence a fix must clear
  before the CI bandage is retired.

## rapidgzip — soft EOF (by design) + abort defects (bug)

- **Repo:** <https://github.com/mxmlnkn/rapidgzip> (pinned `0.16.0` ≡
  librapidarchive `1221a30`).
- **Soft EOF / empty-short success on truncated gzip is by design** — the
  parallel reader does trial-and-error mid-stream decode for random access.
  Filing it as a bug would be wrong; an `is_stream_complete()`-style API is a
  **feature request we are not opening now**. The report records the contract
  Archivey mitigates (empty→stdlib fallback + single-member ISIZE backstop on
  seekable sources).
- **The abort defects *are* bug-class** — `std::terminate` after some
  path-source errors, the rapidgzip+indexed_bzip2 dual-load conflict, and
  process termination when the Python source raises (see
  [`known-issues.md`](known-issues.md) Bugs 1–3). These are the parts worth
  filing; none is filed yet.

## pycdlib — directory-cycle hang (bug)

- **Repo:** <https://github.com/clalancette/pycdlib> (pinned `>=1.16.0`).
- **Defect:** a directory record whose extent points back at an ancestor makes
  pycdlib's walk loop forever. Archivey guards it by installing a visited-extent
  `deque` proxy *into pycdlib's own namespace*
  (`iso_reader._install_pycdlib_directory_cycle_guard`) — a strict superset of
  pycdlib's behavior on valid trees, confined to pycdlib (not a global
  `collections.deque` swap).
- **Gap:** unlike pyppmd/rapidgzip there is **no dedicated report doc** — only
  the `known-issues.md` note and the test. Before filing, promote the test's
  `_pycdlib_directory_cycle_image` builder into a standalone repro and write the
  root cause up here (or in a `pycdlib-upstream-report.md`). A fixed pycdlib
  would let the process-global monkeypatch be dropped — worth the write-up
  because the patch is the most invasive dependency workaround Archivey carries.

## Adding a new report

When a new dependency defect is found: land the mitigation + a regression test
first (as today), write the canonical report next to the analysis it came from,
then add a **row here** with its status and pointers. Keep the analysis in the
canonical report; keep only status + links on this page.

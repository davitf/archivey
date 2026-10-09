## MODIFIED Requirements

### Requirement: Coverage-guided fuzz gate for parsers and entry points

The test suite SHALL provide an Atheris (libFuzzer) coverage-guided fuzz harness
over archivey-owned hostile-input entry points. The harness MUST seed from the
declarative corpus and adversarial fixtures, force accelerators off outside the
accelerator targets below, and treat success as: within each time budget, only
typed `ArchiveyError` subclasses or clean returns — never an uncaught
non-`ArchiveyError` exception, process abort, or hang past the slice timeout.

The accelerator targets SHALL decode each input twice through `open_codec_stream`,
seekable, with the codec's accelerator forced `ON` and forced `OFF`, and MUST fail
when a byte the accelerated stream returns, on the first read or after a seek,
differs from the `OFF` decode at the same offset, or when the accelerated read ends
cleanly before the read cap where the `OFF` decode raised. zlib and raw DEFLATE
inputs SHALL carry a declared decompressed size that both decodes get as
`expected_decompressed_size`. These targets MUST bound each input with libFuzzer's
own per-input timeout and RSS limit rather than a Python alarm, which cannot
interrupt native code.

For CRC/checksum-gated targets (native 7z header parse at minimum; ZIP member
headers/payloads when the ZIP read path is exercised; RAR headers; other formats
when their interesting paths sit behind a header CRC), the harness SHALL apply a
**mutate-then-fixup** step that recomputes and patches valid CRC fields before
invoking the parser/reader, so coverage guidance reaches post-CRC logic. It MUST
NOT rely on unaided libFuzzer CMP feedback to solve CRC32. A minority of inputs
(or a small dedicated budget) SHALL retain broken CRCs so the reject path stays
exercised.

The default main-branch / nightly run SHALL partition wall-clock budget across the
targets below (exact seconds MAY be env-overridable). The partition MUST size the
total budget to include every required stream/codec target — there is no hard short
ceiling that permits dropping those slices. `workflow_dispatch` MAY lengthen
budgets further.

| Target | Role |
| --- | --- |
| Native 7z header parse | Deep coverage of the pure-Python header parser |
| 7z `open_archive` + member list/materialize | Reader/spine path after parse |
| `detect_format` over arbitrary/prefix seeds | Magic/peek-replay entry point |
| ZIP `open_archive` + member list + bounded member `open`/`read` | Wrapper + native codec/AES read path; mutate local/CD headers and compressed content with CRC fixup |
| TAR `open_archive` + member list | Shallow wrapper/translation coverage |
| ISO `open_archive` + member list | Shallow; MUST use a hard wall-clock kill timeout |
| Native RAR header parse | Deep coverage of the pure-Python RAR3/RAR5 metadata parser (CRC mutate-then-fixup) |
| RAR `open_archive` + member list | Reader/spine path after parse |
| Stream/codec: unix-compress, xz, lzip, gzip, bzip2, lzma-alone, zlib | Direct `open_codec_stream` hostile input per standalone archivey-owned codec; seekable indexing on when the codec supports it; MUST use a per-input wall-clock kill timeout when hang classes are known |
| Stream/codec optional extras (zstd, brotli, lz4, deflate64) | Same pattern when the backend is installed; skip-clean when absent |
| Accelerators: gzip, zlib, raw DEFLATE (rapidgzip), bzip2 (rapidgzip's bzip2 decoder) | Accelerator `ON` against `OFF` on the same input; skip-clean when the accelerator cannot run |

The Atheris CI workflow SHALL install RARLAB `unrar` on Linux so the RAR
open+list target is not skipped solely for missing decompressor binary. RAR
targets MAY still skip cleanly when the RAR backend is not registered.

Full member **extract** remains out of scope for this harness (covered by the
mutation harness). Filter-only codecs (BCJ, Delta) are not required as standalone
stream targets.

CI SHALL run a **short** partitioned harness on every pull request (same target
set, reduced per-target budgets, and MAY shard targets across parallel jobs so
wall time stays on the order of a few minutes — each Atheris target pays a large
process cold-start cost). The **full** partition SHALL run off the PR path as a
separate scheduled nightly job, guarded so the expensive run is SKIPPED unless
the default branch changed within roughly the past 3 days (commit-recency
guard), and MAY be forced on demand via `workflow_dispatch` (longer budgets
allowed). A plain always-on nightly and a full partition on every `main` push
are NOT required — same bursty / dormant trade-off as the change-guarded
benchmark wall job. On failure the job SHALL upload reproducing inputs as
artifacts and print a one-line local re-run command. The workflow job timeout
MUST accommodate the active partition (short for PRs; full for nightly/dispatch).

The existing corpus mutation harness and Hypothesis property tests remain
mandatory complementary layers; Atheris does not replace them. `atheris` is
installed only via the CI `fuzz` dependency group (`packaging-and-extras`).

#### Scenario: atheris gate matrix

| Case | Expected |
| --- | --- |
| Pull request | Short partitioned budgets over the full target set (MAY be sharded across parallel jobs); green if no crash/hang/raw exception |
| Scheduled nightly, HEAD commit within ~3 days | Full partitioned target set runs |
| Scheduled nightly, HEAD older than ~3 days | Expensive fuzz skipped (guard only) |
| `workflow_dispatch` (optional longer env budget) | Full partition; extended exploration allowed |
| Push to `main` alone | Full Atheris job not required (covered by PR short + change-guarded nightly) |
| RAR backend absent | RAR targets skipped; other targets still run |
| RAR backend present + `unrar` installed (Atheris CI) | RAR open+list target runs (not skipped for missing binary) |
| Fuzzer finds a crashing input | Job fails; repro bytes uploaded; re-run command printed |
| 7z / RAR header target with fixup enabled | Most iterations present a matching header CRC and enter post-CRC parse |
| ZIP target with fixup + bounded read | Post-CRC / post-local-header path reaches archivey codec or AES+codec stream; typed errors only |
| Each required stream/codec target | Hostile inputs exercise decode (and seek-index when enabled) without raw exceptions; hang → slice failure with artifact |
| Optional stream extra backend absent | That codec's target skipped; required stream targets still run |
| Accelerator target, accelerated bytes differ from `OFF` at an offset, or a clean end where `OFF` raised | Slice failure with artifact |
| Accelerator target, native hang or runaway memory | libFuzzer timeout or RSS limit kills the slice; artifact saved |
| `[seekable]` extra absent | Accelerator targets skipped; other targets still run |
| Broken-CRC sample / minority path | Typed CRC/corruption failure; reject path still hit |
| Mutation harness / `ARCHIVEY_FUZZ` | Still available and unchanged in role |

# Rationale gaps — what the code does, not why

Behaviours where the **what** is visible in code/tests, but a reasonable user
(or integrator) would ask **why**, and the answer is not recoverable from this
pass’s inputs. Do not guess — naming the gap is the output.

Where a comment *partially* explains, the remaining open question is listed.

---

## Packaging & backends

1. **Why is RARLAB `unrar` required, and why are `unrar-free` / `unar` / `7z`
   rejected as substitutes?**  
   Code states the rejection (`rar_unrar.py:21–23`) and that metadata is native
   while data goes through `unrar p`. Not visible: the compatibility / license /
   correctness decision that made substitutes unacceptable.

2. **Why does `[rar]` only pull `cryptography`, not a Blake2sp backend?**  
   `pyproject.toml:69–74` has a TODO that no package is decided. Gap: what the
   product promise is for RAR5 checksum verification until that lands.

3. **Why is indexed bzip2 reached only through `rapidgzip`, never
   `indexed_bzip2` as a direct dependency?**  
   `pyproject.toml:90–93` cites heap corruption from overlapping C++ symbols on
   macOS. Gap for docs: is this forever, and what should users who already import
   `indexed_bzip2` do?

4. **Why do Brotli / PPMd / Deflate64 install hints point at `[7z]` even when
   used from ZIP?**  
   Codec descriptors use `pip install archivey[7z]` (`codecs.py` requirements).
   Gap: is `[7z]` the intentional umbrella for “non-stdlib member codecs”, or
   should ZIP users see a different extra?

5. **Why keep `WriteError` importable when no write API is shipped?**  
   `__init__.py` comment: write API not shipped yet (`:107`). Gap: compatibility
   promise vs planned shape of writing.

6. **Why permanently monkeypatch `pycdlib.pycdlib.collections` for directory
   cycle guards?**  
   What is clear: hang prevention (`iso_reader.py:115–123`). Gap: why this
   approach over forking/wrapping walk, and what the supported coexistence story
   is for apps that embed both archivey and raw pycdlib.

---

## Access model

7. **Why does `MemberStreams.CONCURRENT` not reduce or gate solid open-order
   cost?**  
   Stated as fact (`types.py:32–33`). Gap: is the answer “impossible with one
   decoder”, “deferred”, or “use `stream_members` only”?

8. **Why is a filesystem directory `ListingCost.REQUIRES_SCANNING` rather than
   `INDEXED`?**  
   Comment points at a review/spec (`test_cost_receipt.py:87–88`) but the user-
   facing why (walk ≠ O(1) index) needs an explicit product sentence: when should
   callers treat directory opens as expensive?

9. **Why does `open_archive` use `MemberStreams` flags while `open_stream` uses a
   boolean `seekable`?**  
   Docstring says concurrency is not a concept for one stream (`core.py:287–290`).
   Gap: whether SEEKABLE-without-CONCURRENT on archives is meant to be the common
   case, and why the two APIs don’t share one vocabulary.

10. **Why is random-access (`streaming=False`) forbidden from implicitly
    buffering a pipe?**  
    Comment: never implicitly buffer (`core.py:231–232`). Gap: product stance vs
    competitors that buffer; memory/DoS rationale should be explicit for users who
    expect “just work”.

11. **Why can `stream_members` exceed `ListingLimits` but `extract_all` cannot?**  
    Config docstring: stream path unguarded (`config.py:106–107`); tests lock the
    asymmetry. Gap: the threat model sentence — why listing bombs matter more than
    streaming iteration bombs for the intended adversary.

---

## Extraction & safety

12. **Why does `OnError` never abort on the first unsafe (`BLOCKED`) member?**  
    Comment: “Aborting … on the first unsafe member is a separate future opt-in”
    (`extraction_types.py:79–80`). Gap: until that exists, what should security-
    sensitive callers do (policy RAISE? pre-scan?).

13. **Why reject Windows-reserved names and `:` under `STRICT`/`STANDARD` on
    POSIX?**  
    Code: portable-by-default / deterministic across OS (`filters.py:207–211`,
    `:291–302`). Gap worth spelling for Unix-only deployments that *want* a file
    named `NUL` or a rare `:` segment — is TRUSTED the supported escape hatch, and
    is that recommended?

14. **Why does `STRICT` rewrite trailing dots/spaces but `STANDARD` keep them?**  
    Behaviour is clear (`filters.py:304–309`). Gap: the threat vs compatibility
    trade that put the rewrite only on STRICT.

15. **Why follow a destination symlink-to-directory, but never write-through a
    per-member destination symlink under REPLACE?**  
    Tests encode both (`test_extraction.py:572–602`). Gap: the trust boundary
    sentence (“dest root is trusted; archive members are not”) belongs in user
    docs, not only tests.

16. **Why recover orphaned hardlinks by writing content at the link path (never
    creating the excluded source name)?**  
    Tests describe the shape (`test_extraction.py:1163–1165`). Gap: POSIX
    hardlink semantics vs user expectation that “the file” appears under the
    source name.

17. **Why do selector/filter skips and safety rejections not count toward
    `max_entries`?**  
    Decision noted in test comments (`test_extraction.py:761–798`). Gap: the
    bomb-model definition of “entry” (on-disk create vs archive row) should be
    stated once for operators setting caps.

18. **Why is the default `max_ratio` 1000 and activation threshold 5 MiB?**  
    Numbers exist (`config.py:85–87`); tests show the false-positive guard.
    Gap: what threat these defaults are sized for, and when operators should
    tighten them.

19. **Why is one-shot `extract()` deliberately without `members=`?**  
    Docstring gives the reopen argument (`core.py:400–403`). Remaining gap: why
    not accept names-only filters that don’t need a full list (glob?) — if
    rejected, say so; if deferred, say so.

---

## CLI

20. **Why does the CLI default overwrite to `rename` while the library defaults
    to `error`?**  
    Help text states the divergence (`cli/main.py` ~273). Gap: the interactive-
    vs-API product rationale; without it, each surface looks like a bug relative
    to the other.

21. **Why smart-dest always wraps scan-required formats, then maybe hoists?**  
    Comments cite D1/R4 (`extract_cmd.py:120–125`, `:225–232`). Gap: user-facing
    explanation of anti-tarbomb vs “unar-like” single-root reuse, and when
    `-d .` is required.

22. **Why exit code 3 for policy-blocked-only extracts?**  
    Defined (`exit_codes.py:8–10`). Gap: how automation should treat 3 vs 1
    (partial success? fail closed?).

---

## Formats & detection

23. **Why is missing TAR EOF a warning by default (`strict_archive_eof=False`)
    rather than corruption?**  
    Configurable (`config.py:135–137`). Gap: compatibility with real-world
    truncated producers vs integrity posture — who is the default protecting?

24. **Why prefer UTF-8 sniff then `cp437` for unflagged ZIP names?**  
    Default documented as APPNOTE (`config.py:138–142`). Gap: when to set
    `zip_unflagged_fallback_encoding` vs `encoding=` on open (disables sniff).

25. **Why can `foo.tar.gz` detect as bare `GZ` without a conflict diagnostic?**  
    Explicitly called a benign deferred case (`detection.py:282–288`). Gap: when
    open-time determination happens and whether callers should prefer
    `format=TAR_GZ`.

26. **Why does `ArchiveMember.type == ANTI` exist, and what should extractors
    do beyond “skip”?**  
    Comment: 7z incremental tombstone (`types.py:218–220`). Gap: user-visible
    semantics for update archives (does last-wins interact? listing honesty?).

27. **Why is `is_junction` a cross-format `extra` key rather than a
    `MemberType`?**  
    Comment in `types.py:305–307`. Gap: how junctions should be extracted /
    refused relative to symlinks on non-Windows.

28. **Why reject BCJ2 (and which other 7z methods) rather than depend on more
    packages?**  
    Pipeline raises `UnsupportedFeatureError` / `PackageNotInstalledError` paths
    exist (`sevenzip_reader.py` / pipeline). Gap from this pass: the supported
    method matrix and the product reason for hard-reject vs optional extra.
    (**Could not fully determine the reject list without reading more of the
    method registry — treat matrix documentation as required.**)

---

## Diagnostics & API curation

29. **Why is `DiagnosticSeverity` only `WARNING` today?**  
    Comment: axis reserved for later (`diagnostics.py:80–81`). Gap: whether
    callers should already branch on severity.

30. **Why demote context dataclasses and `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`
    from `__all__` but keep them importable?**  
    Test names “api-coherence Q4” (`test_public_api.py:69–70`). Gap: stability
    guarantee for demoted names.

31. **Why default `max_retained_diagnostic_references=256`?**  
    Field exists (`config.py:146`). Gap: what is dropped under flood, and whether
    `dropped_count` is enough for auditors.

32. **Why attach some diagnostics to members and also to the reader summary?**  
    Dual surfaces exist (`ArchiveMember.diagnostics`, `reader.diagnostics`).
    Gap: which one callers should query for which job.

---

## Could not determine from allowed inputs

These are findings that *felt* documentation-shaped but could not be closed
without forbidden files (or deeper oracle code):

- End-to-end **supported 7z method / filter matrix** (including BCJ vs BCJ2,
  AES, solid folder rules) — partially in code, not summarized for users.
- Exact **RAR3 vs RAR5** feature split visible to callers (encrypted headers,
  file versions `path;N`, solid pipe behaviour).
- Whether **writing**, **async**, or reserved CLI verbs (`create`/`convert`/…)
  have committed timelines — only stubs/comments visible.
- Threat-model numbering (O2/O3/…) appears in code comments referencing
  decisions; the **user-facing** mapping from those IDs to advice is not in
  `src/`/`tests/`/`pyproject.toml`.

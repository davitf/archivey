# Pre-release review, October 2026

Reviewed `main` at `229e4ab6` for the 0.2.0 release, against `AGENTS.md`,
`dev-docs/design-rules.md` (and `dev-docs/scenarios.md`), `VISION.md`, and the
ADRs. Two documentation claims did not match the code. Both are corrected in
this pull request. No reproduced correctness, safety, or concurrency defect
turned up in the areas read closely enough to file one. Nothing here changes
public behaviour or a public name.

## D1. Concurrent reads do not all decompress in parallel

**File:** `docs/reading.md:96` (text before this pull request).

**What happens.** The page said that concurrent member streams take turns
reading compressed data and then decompress in parallel. That is true of a ZIP:
`src/archivey/internal/backends/zip_reader.py:1358` holds the source lock for
one seek and one read, and the member decompresses after that. It is not true
of a TAR or an ISO. Those reads share one file handle, so one waits while
another has it (`docs/formats.md:317`,
`openspec/specs/reader-concurrency/spec.md` requirement "Random-access
member-open is reentrant and reader-state-free").

**Test.** None. The code already matches the spec and `docs/formats.md`. A
behaviour test would pass before and after the sentence change.

**Fix.** The sentence now says which formats wait. Done in this pull request.

**Confidence:** high.

## D2. A later gzip member with a bad header does not read clean under rapidgzip

**File:** `dev-docs/formats/gzip.md:347` and the sharp-edge row that was at
line 498 (text before this pull request).

**What happens.** The handbook said a member after the first, with a header
CRC that does not match or with a reserved `FLG` bit, reads clean when
`use_rapidgzip` is on, and that the standard library raises. On rapidgzip
0.16.0 both paths raise.

- A bad header CRC on the second member makes rapidgzip raise ("Incorrect
  checksum"). Archivey falls back to zlib and raises `CorruptionError`.
- A reserved flag on the second member is accepted by `RapidgzipFile` itself.
  Archivey's further-member scan skips a header with those bits set
  (`gzip_has_additional_member`), so the file is not treated as multi-member.
  The single-member trailer check then fails, and the read falls back to zlib,
  which raises `UnsupportedFeatureError`.

`openspec/specs/format-single-file-compressors/spec.md` requires those two
errors on a later member with the accelerator on or off.
`openspec/specs/compressed-streams/spec.md` lets an accelerator differ only on
a wrong ISIZE of a gzip member other than the last. The first member was
already checked before rapidgzip starts (`_gzip_header_refused`). The handbook
row described an older result.

**Test.** `test_later_gzip_member_header_raises_with_the_accelerator` in
`tests/test_audit3_gzip.py`. It is not marked `xfail`. The code already
raised, so an `xfail` would be an unexpected pass on this branch. The test
fails if a later change returns the payload.

**Fix.** §2.3 and the sharp-edge row now say the read raises. Done in this
pull request.

**Confidence:** high.

## Checked, and not filed

These matched a rule or a page that already records them.

- A seek that jumps, then a read to the end, skips the stored CRC and does not
  emit `DIGEST_UNVERIFIABLE`. DR-4a and `docs/errors-and-diagnostics.md` allow
  that. Reproduced on a stored ZIP member and a 7z copy member, then dropped.
- A wrong ISIZE on a gzip member other than the last reads under rapidgzip and
  raises on zlib. The compressed-streams spec allows it.
  `test_gzip_accelerator_reads_a_wrong_isize_on_a_member_before_the_last`
  already pins it.
- Under `ExtractionPolicy.STRICT`, these were contained: `../` and an absolute
  member name, a symlink to `/tmp` followed by a file through that name, a
  hard link to that symlink, and a write through a symlink that was already in
  the destination. A pre-existing symlink file is an overwrite error and is
  not replaced.
- Names in `archivey.__all__` match `docs/api.md`. `StreamFormat.ZLIB` is
  `"zz"` on purpose.
- `DecoderLimits` is charged where a header can ask for that resource: key
  derivation, PPMd in-process input, and decoder memory. A 7z header field is
  read from the header buffer already in memory, so a huge length raises
  truncation instead of allocating it.

## Coverage

Read closely:

- Extraction name, link, and overwrite checks, and the probes above.
- The member verifier, against DR-4a.
- gzip accelerator header checks, for the first member (existing tests) and
  for a later member (this pull request).
- ZIP Unicode Path CRC, the local-header CRC stand-in, and a symlink whose
  CRC does not match.
- `DecoderLimits` and the 7z header cursor.
- Concurrency: `streaming=True` together with `concurrent_operations=True` is
  refused, and TAR and ISO share one handle, against the reader-concurrency
  spec.
- `docs/api.md` against `__all__`. `docs/reading.md`, `docs/extracting.md`,
  `docs/security.md`, `docs/cli.md`, and the TAR and single-file parts of
  `docs/formats.md`, against the code they describe.

Skimmed:

- The RAR parser and the unrar/unar data path, past `dev-docs/known-issues.md`.
- The 7z coder graph, past the count caps and the folder CRC plan.
- The ISO record walk, past date conversion, image-end clamping, and the
  length check on open.
- zstd, LZ4, Brotli, xz, lzip, and `.Z`, past comments and a few call sites.
- CLI `extract` and `cat`.
- The fuzz harnesses.
- `docs/how-it-works.md`, `docs/philosophy.md`, `docs/install.md`, and
  `docs/acknowledgements.md`.

`./scripts/check.sh --fix` passed. `pytest tests/test_audit3_gzip.py` passed
(2 tests). The full `./scripts/test.sh` suite was not run.

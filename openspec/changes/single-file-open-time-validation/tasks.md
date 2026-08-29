## 1. Red tests first

- [ ] 1.1 Failing test in `tests/test_single_file.py`: for each of the ten single-file
      codecs, a 40 000-byte zero-filled source named for that codec raises from
      `open_archive`, not from the read
- [ ] 1.2 Failing test: the same for a zero-byte source of each codec
- [ ] 1.3 Pin the other direction: a *valid empty* stream of each codec still opens and
      reads `b""` — this is what the length floor must not break
- [ ] 1.4 Failing test (skips without the `[seekable]` extra, with the extra named in the
      skip reason): `open_archive(garbage.bz2, seekable_members=True).read(member)` raises,
      matching `seekable_members=False`, under both `AcceleratorMode.AUTO` and `OFF`
- [ ] 1.5 Same for a zero-byte `.bz2`, and a gzip control that already passes

## 2. The eager probe reads

- [ ] 2.1 Pull one byte in the seekable branch of `SingleFileReader.__init__` before
      closing the probe stream
- [ ] 2.2 Rewrite the comment: it currently claims a guarantee the code did not provide;
      state the depth (one byte) and that the non-seekable branch is deferred
- [ ] 2.3 Check the raised error's `member=` attribution — it names a member nobody
      requested; correct it if the error construction allows it cheaply

## 3. Minimum-header floor for decoders that accept empty input

- [ ] 3.1 Add a minimum framing size to the `unix-compress` codec descriptor and reject a
      shorter source at open
- [ ] 3.2 Express it as a per-codec property rather than a `.Z` branch in the reader, so a
      future codec with the same behaviour declares it as data
- [ ] 3.3 Confirm 1.2 and 1.3 both pass — the floor must reject a zero-byte non-`.Z` and
      still admit a valid empty stream of every other codec

## 4. Accelerator error parity

- [ ] 4.1 Establish what rapidgzip's bundled bzip2 decoder reports for garbage: whether
      "no output, no input consumed, no end-of-stream" is distinguishable through its API
      (this decides between 4.2 and 4.3 — see design §Open Questions)
- [ ] 4.2 If distinguishable: raise the translated error the stdlib path raises, in the
      accelerated bzip2 stream wrapper
- [ ] 4.3 If not: decline acceleration below the codec's minimum framing size and let the
      stdlib path produce the error
- [ ] 4.4 Confirm gzip's rapidgzip path is unaffected, as a control — it already raises
      correctly and must keep doing so

## 5. Docs and issue register

- [ ] 5.1 `docs/gotchas.md` and `docs/errors-and-diagnostics.md`: a wrongly-named
      single-file archive now fails at `open_archive`, not on read
- [ ] 5.2 Note the bzip2 open cost (about 14 ms on a 1.8 MB payload, proportional to the
      first block and not to archive size) wherever open costs are described
- [ ] 5.3 Close `dev-docs/open-issues.md` P15 **and P16** (the bzip2 accelerator defect,
      registered when it was found) — both are closed by this change
- [ ] 5.4 Cross-reference from `dev-docs/investigations/archive-format-detection-algorithm.md`
      §1 and §6, which both lean on P15 as the mechanism behind the extension-honesty gap

## 6. Verify

- [ ] 6.1 `uv run --no-sync pytest tests/test_single_file.py tests/test_streams.py`
- [ ] 6.2 Re-measure the bzip2 open cost after the change and record it beside the
      pre-change number, so the trade stays sized rather than asserted
- [ ] 6.3 `./scripts/check.sh --fix`
- [ ] 6.4 `./scripts/test.sh --all-configs` — the `[core-only]` leg has no `[seekable]`
      extra, so it is where a badly written accelerator test would fail rather than skip
- [ ] 6.5 `openspec validate --strict single-file-open-time-validation`

## 0. Order

- [x] 0.0 **Implement this change second — independent of the detection chain, but land it
      before `detection-evidence-ledger`.** It shares no code with the other four: it is a
      reader and codec fix, not a detection one, so it can proceed in parallel with
      `detection-format-gaps`.

  **Why before the ledger.** This change moves a wrongly-named single-file archive's failure
  from read time to open time; the ledger separately makes that failure *honest* by rekeying
  `format_unconfirmed`. Landing this one first means the ledger's open-time scenarios have
  something real to assert against. The spec delta states both halves explicitly, so neither
  change asserts something that is only true after the other ships.

  **Both defects here are one change.** The bzip2 accelerator returning `b""` on corrupt
  input (P16) defeats this change's own fix — the new one-byte probe read gets `b""` back and
  concludes the stream is a valid empty one. Splitting them ships a fix whose test cannot pass
  on the configuration that matters most.

## 1. Red tests first

- [x] 1.1 Failing test in `tests/test_single_file.py`: for each of the ten single-file
      codecs, a 40 000-byte zero-filled source named for that codec raises from
      `open_archive`, not from the read
- [x] 1.2 Failing test: the same for a zero-byte source of each codec
- [x] 1.3 Pin the other direction: a *valid empty* stream of each codec still opens and
      reads `b""` — this is what the length floor must not break
- [x] 1.4 Failing test (skips without the `[seekable]` extra, with the extra named in the
      skip reason): `open_archive(garbage.bz2, seekable_members=True).read(member)` raises,
      matching `seekable_members=False`, under both `AcceleratorMode.AUTO` and `OFF`
- [x] 1.5 Same for a zero-byte `.bz2`, and a gzip control that already passes

## 2. The eager probe reads

- [x] 2.1 Pull one byte in the seekable branch of `SingleFileReader.__init__` before
      closing the probe stream. *Done as `SingleFileReader._validate_at_open`, a
      `BaseArchiveReader` hook `open_archive` calls after it sets format provenance: in
      `__init__` the provenance is not set yet, so a probe-only format lost its
      `format_unconfirmed` stamp and `PROBE_FORMAT_UNCONFIRMED` diagnostic.*
- [x] 2.2 Rewrite the comment: it currently claims a guarantee the code did not provide;
      state the depth (one byte) and that the non-seekable branch is deferred
- [x] 2.3 Check the raised error's `member=` attribution — it names a member nobody
      requested; correct it if the error construction allows it cheaply. *The probe opens
      with `attribute_member=False`; `member_name` is `None`.*

## 3. Minimum-header floor for decoders that accept empty input

- [x] 3.1 Add a minimum framing size to the `unix-compress` codec descriptor and reject a
      shorter source at open. *Overtaken: the native LZW decoder now raises
      `TruncatedError` on a source shorter than its 3-byte header, so `read(1)` already
      rejects a zero-byte `.Z` and no floor is needed.*
- [x] 3.2 Express it as a per-codec property rather than a `.Z` branch in the reader, so a
      future codec with the same behaviour declares it as data. *Not needed, see 3.1.*
- [x] 3.3 Confirm 1.2 and 1.3 both pass — the floor must reject a zero-byte non-`.Z` and
      still admit a valid empty stream of every other codec

## 4. Accelerator error parity

- [x] 4.1 Establish what rapidgzip's bundled bzip2 decoder reports for garbage: whether
      "no output, no input consumed, no end-of-stream" is distinguishable through its API
      (this decides between 4.2 and 4.3 — see design §Open Questions). *Not
      distinguishable on rapidgzip 0.16.0: garbage, a zero-byte file, `BZh9` and a valid
      empty stream all give `size() == 0`, `tell_compressed() == 0`, offsets `{0: 0}`.*
- [x] 4.2 If distinguishable: raise the translated error the stdlib path raises, in the
      accelerated bzip2 stream wrapper. *Not distinguishable (4.1).*
- [x] 4.3 If not: decline acceleration below the codec's minimum framing size and let the
      stdlib path produce the error. *A size floor misses 40 000 zero bytes, so instead
      `_Bzip2EmptyStreamCheck` hands the first empty read to stdlib `bz2` over a fresh view
      of the source (the gzip empty→stdlib fallback's shape), which raises or confirms.*
- [x] 4.4 Confirm gzip's rapidgzip path is unaffected, as a control — it already raises
      correctly and must keep doing so

## 5. Docs and issue register

- [x] 5.1 `docs/gotchas.md` and `docs/errors-and-diagnostics.md`: a wrongly-named
      single-file archive now fails at `open_archive`, not on read
- [x] 5.2 Note the bzip2 open cost (about 14 ms on a 1.8 MB payload, proportional to the
      first block and not to archive size) wherever open costs are described
- [x] 5.3 Close `dev-docs/open-issues.md` P15 **and P16** (the bzip2 accelerator defect,
      registered when it was found) — both are closed by this change
- [x] 5.4 Cross-reference from `dev-docs/investigations/archive-format-detection-algorithm.md`
      §1 and §6, which both lean on P15 as the mechanism behind the extension-honesty gap

## 6. Verify

- [x] 6.1 `uv run --no-sync pytest tests/test_single_file.py tests/test_streams.py`
- [x] 6.2 Re-measure the bzip2 open cost after the change and record it beside the
      pre-change number, so the trade stays sized rather than asserted. *1.85 MB payload
      (600 KB random + text), median of 20 `open_archive` calls: bzip2 28.9 → 58.9 ms,
      gzip 1.64 → 1.58 ms. Recorded in `dev-docs/open-issues.md` P15.*
- [x] 6.3 `./scripts/check.sh --fix`
- [x] 6.4 `./scripts/test.sh --all-configs` — the `[core-only]` leg has no `[seekable]`
      extra, so it is where a badly written accelerator test would fail rather than skip
- [x] 6.5 `openspec validate --strict single-file-open-time-validation`

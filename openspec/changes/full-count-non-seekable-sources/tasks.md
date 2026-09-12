## Implementation notes

Red before green: task 1.1 is the failing test, and it must be watched failing before
`FullCountStream` exists. The double is the point of the change — the gap survived
because no existing double is short-returning *and* non-seekable.

Landable as one PR. `design.md` D7 closes the former open question — non-seekable volume
items are refused at `ConcatenatedFile` construction — so there is nothing to split out.

**Builds on #329** (Parcel C, merged as `0ed80b7`). That PR already corrected
`ensure_full_count_reads`' docstring: it names the read-ahead obstacle, records both of the
wrong reasons explicitly, inventories the three downstream layers the guarantee leans on,
and points at this change by name. The ordering is load-bearing — task 5.1 is now an edit
to accurate prose, not a correction of wrong prose.

## 1. Red — prove the gap

- [ ] 1.1 Add `ShortReadNonSeekable` to `tests/streams_util.py`. **Do not copy the cap
  logic** — `ShortReadBytesIO` already has it and the cap is the part that must not drift
  between the two. Parameterize the existing class instead (`seekable: bool = True`) and
  make `ShortReadNonSeekable` the thin subclass that flips it and raises
  `io.UnsupportedOperation` from `seek()`. Docstring says why neither existing double
  covers this axis pair (`ShortReadBytesIO` is seekable-only; `NonSeekableBytesIO`
  delegates to `BytesIO` and is therefore always full-count).
  - Two additions to `ShortReadBytesIO` while there, both needed below: a `consumed`
    byte counter for 1.3 (there is no counter on it today, and `CountingBytesIO` is a
    separate seekable double — do not land a third), and `cap_drain: bool = False`,
    which when set also caps `read(-1)`. `cap_drain` is deliberately *illegal*
    `RawIOBase` behaviour — `read(-1)` dispatches to `readall()`, which must drain — and
    exists only so 2.1's drain branch can be proved not to depend on the inner obeying
    that. Say so in its docstring.
- [ ] 1.2 Assert the boundary directly, not a backend:
  `ensure_full_count_reads(ShortReadNonSeekable(data, 1)).read(n)` returns `n` bytes.
  Watch it fail on `main` — the source is returned unchanged, so it returns 1 byte.
- [ ] 1.3 Assert exact consumption: after `read(n)`, exactly `n` bytes have been taken
  from the underlying source — read it off the `consumed` counter added in 1.1. This is the
  assertion that would fail if someone later "fixes" 1.2 with a `BufferedReader`
  (measured: `ensure_bufferedio` atop the boundary takes 8192 bytes for a `read(20)`).

## 2. Green — the wrapper

- [ ] 2.1 Add `FullCountStream(ReadOnlyIOStream)`. **Not in `binaryio.py`** — that is a
  circular import, verified: `base.py:26` imports `is_seekable`, `readinto_via_read`,
  `source_name` and `try_readinto` **from** `binaryio` at module level, so the reverse
  import fails with `ImportError: cannot import name 'ReadOnlyIOStream' from partially
  initialized module`. #329 already hit and recorded this constraint —
  `BinaryIOWrapper.mode`'s docstring says "Copied from `ReadOnlyIOStream` rather than
  subclassing it (`base.py` imports this module)" — and parks the shared-mixin cleanup as
  `review/backlog.md` #329 C4. Define the class in `base.py` next to the base it uses, and
  import it inside the body of `ensure_full_count_reads`. It changes where the code lands,
  so do not re-decide it silently mid-task.
  - `read(n)` for `n >= 0`: single-read fast path, `read_exact` fallback for the bytes
    still missing (D2 — `read_exact` alone copies every byte twice, and on this path that
    is every byte of every member). `read(-1)`: loop until empty; do not lean on the
    inner's `readall()` (D2).
  - `ReadOnlyIOStream` is the right base and the reason is worth a sentence in the
    docstring: its `readinto` routes through the subclass's `read`, so `readinto` inherits
    the full-count guarantee for free. `DelegatingStream` would instead pass `readinto`
    straight to the short-returning inner and silently lose it. It also composes with
    `ensure_bufferedio` without a second adapter layer: `type(obj).readinto is not
    io.RawIOBase.readinto` holds, so it is not re-wrapped in `BinaryIOWrapper`.
  - No buffer; `seekable()` stays `False`. Docstring carries D2/D3 inline: why no
    read-ahead, and why `read_exact` rather than `read_full_count` (the three gather
    policies enumerated in `slice.py`, per ADR 0014 — the enumeration is in `slice.py`,
    not in the ADR).
- [ ] 2.1a Keep the wrapper transparent to the metadata probes (maintainer decision,
  packet 2 — see `design.md` D8). Forward exactly two things, and no more:
  - A `name` property forwarding through `source_name(self._inner)`, re-raising
    `AttributeError` when the inner has none — the shape `BinaryIOWrapper.name` and
    `PeekableStream.name` already use, and required because `source_name` has no peel path.
    Test it against a **real FIFO**, not just a synthetic double: `open(fifo, "rb")` is a
    `BufferedReader` that is non-seekable *and* carries `.name`, which is the ordinary way a
    caller supplies a non-seekable source. The seekable branch keeps the name for free
    (`io.BufferedReader.name` forwards to `raw` at C level), so this is what stops the two
    branches of one function from disagreeing about the same source.
  - `peel_for_source_size = True` on the class — one line, the existing opt-in for a
    pass-through wrapper whose cheap size is the inner's. `source_byte_size` reads it via
    `getattr`, so it works on a `ReadOnlyIOStream` subclass even though it is documented on
    `DelegatingStream`. Do not hand-write `size` / `try_get_size` properties. Note when
    writing the test that a FIFO yields `None` either way; the case this covers is a source
    carrying an explicit integer `size` (the fsspec convention).
  - **Do not forward `fileno`.** Nothing reads it on a non-seekable source (D8), and neither
    sibling wrapper forwards it.
  - **Leave `tell()` raising.** D7's multi-volume refusal is triggered by it, and answering
    it would claim a position the wrapper does not track.
  - Assert all of it, including through `PeekableStream`: after 3.1 the detection path reads
    the source's name *through* this wrapper, which is how the opacity would reach
    `ResolvedSource.archive_name` and `compressed_source_size`.
- [ ] 2.2 `ensure_full_count_reads` wraps a non-seekable source in `FullCountStream`
  instead of returning it unchanged, and **is idempotent**: a `FullCountStream` argument is
  returned unchanged (maintainer decision, packet 1 — see `design.md` D5). The seekable
  branch already is, via `ensure_bufferedio`'s `isinstance(obj, io.BufferedIOBase)`
  short-circuit; state the whole-function property in the docstring, since D5 has callers
  relying on it. Replace the docstring's known-gap paragraphs per 5.1.
- [ ] 2.3 Confirm 1.2 and 1.3 pass; revert 2.1–2.2 and watch them fail again; restore.

## 3. Simplify the now-redundant gather

Order matters here: 3.1 establishes the guarantee 3.2 then depends on. Doing 3.2 first
regresses `open_stream` — see the measurement in `design.md` D5.

- [ ] 3.1 `PeekableStream.__init__` calls `ensure_full_count_reads` on its `underlying`
  argument (maintainer decision, packet 1). This is an **edit with a test**, not a
  checklist confirmation: `core.py:592` hands it the raw caller stream today, so without
  this the collapse in 3.2 breaks the `open_stream` non-seekable path. Needs 2.2's
  idempotence first, or the `open_archive` site at `core.py:372` double-wraps.
  - Red test: `open_stream(ShortReadNonSeekable(payload, 1))` with **`format=None`** for
    gzip, bzip2 and xz. Write it on the detected path — the explicit-`format=` variant
    *passes* even with the bug present, because `ensure_bufferedio` inside
    `DecompressorStream` rescues it, so a test written that way is green against the
    regression and would retire this wrongly.
  - `core.py` needs **no** edit under this decision. If you find yourself editing it, the
    wrap is in the wrong place — re-read D5.
- [ ] 3.2 `PeekableStream._fill_to`: collapse the `while` loop to a single `read` now
  that the inner is full-count. Comment why the loop is gone and what guarantees it
  (3.1, not a call-site convention).
- [ ] 3.3 Re-run 3.1's red test plus the existing detection suite. ~20 sites in `tests/`
  construct `PeekableStream` directly over a bare `NonSeekableBytesIO`; they pass today
  only because that double delegates to `BytesIO` and is therefore already full-count.
  After 3.1 they exercise the wrapper, so watch for anything that asserts on
  `PeekableStream.name`, `repr`, or the identity of `_underlying`.
- [ ] 3.4 `PeekableStream` stays — `peek` pushback is not what `FullCountStream` does.
  Do not delete it or fold the two. 3.1 is not the rejected "route everything through
  `PeekableStream`" alternative: the guarantee stays in `streamtools` and `PeekableStream`
  only calls it.

## 4. Coverage

- [ ] 4.1 Each streaming-capable format from `ShortReadNonSeekable(max_chunk=1)`,
  **with and without** explicit `format=`, parity-asserted against the full-count open.
  The explicit-`format=` case is the one that skips `PeekableStream`.
- [ ] 4.2 Plain uncompressed TAR is a required case: it is the path where only stdlib
  `tarfile._Stream` buffering stands between a short-returning pipe and a bogus
  corruption report today.
- [ ] 4.3 Multi-volume, per `design.md` D7 (the former open question, now closed): assert a
  non-seekable volume item still raises `StreamNotSeekableError("all volume streams must be
  seekable")` with `FullCountStream` in front of it — `FullCountStream.tell()` raises
  `io.UnsupportedOperation`, which is already in the caught tuple, so the refusal should
  survive unchanged. Optionally also assert a short-returning *seekable* volume item goes
  through the boundary. **Do not** add a streaming multi-volume path in this change.
- [ ] 4.4 Run all three dependency configs before pushing (`[all]`, `[all-lowest]`,
  `[core-only]`) per `CONTRIBUTING.md`.

## 5. Docs and specs

- [ ] 5.1 Replace the **known-gap** paragraphs of `ensure_full_count_reads`' docstring with
  the implemented behaviour. #329 already removed the wrong justification, so this is not a
  correction: on `0ed80b7` the docstring says the non-seekable source "is returned
  unchanged, and that is a known gap", lists the two wrong reasons, names the read-ahead
  obstacle, inventories the three downstream layers, and ends "The fix is a
  `FullCountStream` … Tracked in the `full-count-non-seekable-sources` OpenSpec change".
  Those last two paragraphs become a description of what the function now does. Keep the
  two corrections and the read-ahead explanation — they are why the seekable branch still
  differs, and deleting them is how the wrong prose regenerated before.
- [ ] 5.2 Note the `streamtools` layering win in the `binaryio.py` module docstring: the
  boundary now supplies its own guarantee rather than depending on `PeekableStream` (a layer
  above) or `tarfile` internals.
  - Fix `slice.py` in the same pass. It says a raw inner that shorts mid-stream "needs a
    **buffer** in front", which is wrong in exactly the way the old
    `ensure_full_count_reads` docstring was wrong — `FullCountStream` is not a buffer, and
    calling it one is what kept regenerating the bad prose. **Two sites** after the Parcel B
    rewrite (`482689e`), not one: the `SlicingStream` class docstring (~`slice.py:82`) and
    the gather-policy comment (~`slice.py:223`). "A full-count wrapper in front" in both.
    The comment's closing "Every inner a backend slices is full-count already" becomes
    honestly true rather than luckily true with this change — which is the whole point, so
    keep it.
- [ ] 5.3 Retire `FakeNonSeekable` from `openspec/specs/testing-contract/spec.md` when the
  delta lands (the `Non-seekable stream coverage for streaming backends` requirement and its
  matrix). The class has never existed in `tests/`; the double is `NonSeekableBytesIO`, and
  it answers `tell()` rather than raising. Grep for the name — `dev-docs/history/SPEC.md` and
  an archived change also carry it; those are history and stay as they are.
- [ ] 5.4 `openspec validate --strict full-count-non-seekable-sources`
- [ ] 5.5 `./scripts/check.sh --fix` and `./scripts/test.sh` clean.
- [ ] 5.6 `openspec archive full-count-non-seekable-sources --yes` in the finishing PR;
  commit the resulting `openspec/specs/` diff.

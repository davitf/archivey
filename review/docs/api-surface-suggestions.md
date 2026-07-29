# Two public-surface suggestions (A1 extras, C1 capability vocabulary)

From [`code-self-documentation.md`](code-self-documentation.md) A1 and C1, expanded at the
maintainer's request. Both are **public surface**, so both are free to change now and
expensive after `0.2.0`. Verified against `main` @ `49f221f`.

**The timing argument applies to both, and it is the strongest one available:** `0.2.0`
is the *first* public release. There are no external users to break. Neither change will
ever be this cheap again.

---

## A1 — the extras are named by format but scoped by capability

### The finding is bigger than the error message

The question was "should we rename the `7z` extra?" The measured answer: **the scheme is
already not one-extra-per-format**, and the mismatch between the names and the contents
is what produces the confusing message.

| Extra | Actually pulls |
|---|---|
| `7z` | pyppmd, inflate64, backports.zstd, brotli, lz4, cryptography, pybcj |
| `rar` | cryptography |
| `crypto` | cryptography |
| `iso` | pycdlib |
| `zstd` | backports.zstd |
| `lz4` | lz4 |
| `seekable` | rapidgzip |
| `cli` | tqdm |

Three things fall out:

1. **`7z` is a codec bundle, not a format extra.** Six of its seven packages are member
   codecs shared with ZIP and TAR. A ZIP user needing Deflate64 is correctly told to
   install it — the name is what lies, not the hint.
2. **`rar` and `crypto` are byte-identical.** And `rar` is doubly misleading: RAR *member
   data* needs the RARLAB `unrar` **binary**, which no extra can ever provide. A user who
   runs `pip install archivey[rar]` and expects to read RAR payloads has been told the
   wrong thing by the package metadata itself.
3. **`zstd` and `lz4` are strict subsets of `7z`**, so the same codec is reachable under
   two names with different implied meanings.

### Recommendation: capability extras as the truth, format names as aliases

Do **not** rename or remove anything. Adding extras is non-breaking; renaming or removing
them breaks `pip install` lines in the wild and in other people's lockfiles. So:

```toml
# The real groups — what the code actually needs.
codecs  = ["pyppmd", "inflate64", "backports.zstd; python_version < '3.14'",
           "brotli", "lz4", "pybcj"]
crypto  = ["cryptography"]
iso     = ["pycdlib"]
seekable = ["rapidgzip"]
cli     = ["tqdm"]

# Format names stay, as aliases, because users think in formats.
"7z" = ["archivey[codecs,crypto]"]
rar  = ["archivey[crypto]"]        # + document that data needs the unrar binary
zstd = ["backports.zstd; python_version < '3.14'"]   # granular, keep
lz4  = ["lz4"]                                        # granular, keep
```

Then the codec hints at `streams/codecs.py:1455,1523,1579` say `pip install
archivey[codecs]`, which is **true regardless of which container the member came from** —
that is the actual fix for A1, and it stops being a docs problem permanently.

Keeping the format aliases matters for discoverability: `archivey[7z]` is what someone
will guess, and PyPI's page lists extras, so they're a real part of the first-run
experience. The aliases become correct-by-construction instead of coincidentally correct.

### Also worth fixing while you're in there

`rar`'s docstring/comment should state the `unrar` binary requirement, since the extra
cannot express it. That is the single most misleading thing in the current extras table,
and it costs one comment.

**Cost:** one `pyproject.toml` edit plus three message strings. No behaviour change, no
breakage, no test changes beyond any that assert hint text.

---

## C1 — `member_streams` and the flag-vs-bool split

### What the ADRs already decided

Worth reading before touching this, because two ADRs are directly on point and they point
in the *same* direction rather than against the change:

- **ADR 0004** rejected an `Intent` enum in favour of `streaming: bool`, reasoning "two
  real modes, not three labels for two behaviors". The recorded taste is: **when the mode
  count is small, prefer a bool.**
- **ADR 0003** describes the member-stream defaults and then says, of `open_stream(...,
  seekable=False)`, "**same rule**". So the ADR already treats these as one concept —
  which means aligning the spelling is *implementing* ADR 0003's framing, not
  re-litigating it.

Neither ADR defends `member_streams` as a *name*, or the flags-vs-bool split between the
two entry points. That is accretion, not a decision.

### The two problems, which are really one

```python
open_archive(p, member_streams=MemberStreams.SEEKABLE | MemberStreams.CONCURRENT)
open_stream(p, seekable=True)
```

- **Not evidently a flag set.** `member_streams=` reads like it takes *streams*, or a
  count, or a mode. Nothing in the name says "OR these together". You must import
  `MemberStreams` to pass anything at all — friction on the common path, and invisible in
  autocomplete until you've already found the enum.
- **Two vocabularies for one concept**, which every user pays for twice.

### Recommendation: two booleans on `open_archive`

```python
open_archive(p, seekable_members=True, concurrent_members=True)
open_stream(p, seekable=True)
```

Why this one:

- It is what ADR 0004 already chose in the analogous case, so it is consistent with the
  codebase's recorded taste rather than a new direction.
- It closes the vocabulary split: `seekable` means the same thing in both entry points.
- **It is self-documenting in the IDE** — `seekable_members: bool = False` in the
  signature tells the whole story, no import, no enum lookup. Given the theme of this
  whole exercise (make the code say it so the docs needn't), that is the point.
- There are exactly two capabilities, and ADR 0003 frames them as two specific traps
  rather than an open-ended set.

**The honest counter-argument**, which you should weigh rather than take my word on: flags
are more extensible (a third capability is a new flag, not a new parameter) and
composable (you can compute a capability set and pass it). If you expect a third
capability, keep the enum and just **rename the parameter** — `require=` reads well
(`require=MemberStreams.SEEKABLE`) and fixes the flag-ness complaint at a fraction of the
cost. I do not think a third is coming, but you know the roadmap.

Either way I would **keep `MemberStreams` exported**: it is meaningful in `CostReceipt`
and diagnostics, and removing a public name buys nothing.

**Cost:** one public signature (`core.py:101`), ~52 internal references, ~38 in tests,
8 in docs. Mechanical, and the tests are the real work. Nothing external to break, since
nothing has shipped.

---

## Suggested handling

| Item | Type | When |
|---|---|---|
| A1 extras + hints | Additive, non-breaking | Any time; safe to land alone |
| A1 `rar`/`unrar` comment | Comment only | With the above |
| C1 vocabulary | **Breaking after `0.2.0`** | Decide before the tag, or accept it forever |

C1 is the one with a deadline. If the answer is "keep it as-is", that is a legitimate
outcome — but it should become an ADR, because this is the second time it has been
independently flagged (api-coherence, then the code-derived pass), and an unrecorded
decision will be re-derived a third time.

Both want an OpenSpec change before implementation, per `CONTRIBUTING.md`. Happy to draft
either once you pick a direction.

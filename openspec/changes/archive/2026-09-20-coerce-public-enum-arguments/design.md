# Design

## Decisions

### Coerce rather than refuse, reversing half of the `format=` precedent

The two candidate answers were refuse (match `2026-08-17-reject-wrong-typed-format-arguments`,
which already raised on `format="zip"`) and coerce.

The case for refusing, which was the recommendation put to the maintainer: coercing
promotes each enum's `.value` to public API, so `OverwritePolicy.SKIP.value` can no
longer be renamed from `"skip"` without breaking callers **silently** — a renamed
*member* breaks them at import, which is loud. The type checkers already reject a string
at every one of these call sites, so a typed caller never needed it.

The maintainer ruled for coercion, "for CLI/quick script friendliness", with the
requirement that conversion happen *immediately* rather than on use. The rationale is
that the untyped caller is exactly the exposure here — a shell one-liner, a notebook, a
script that never runs a type checker — and that caller is the one the silent
fall-throughs were destroying files for.

The cost is accepted, not overlooked, and is written into the module docstring and the
proposal so a later rename attempt meets it.

### Convert at the entry point, not at the point of use

This is what the bugs actually turn on. Every consumer tests these values with `is`:

```python
if self._overwrite is OverwritePolicy.ERROR: raise
if self._overwrite is OverwritePolicy.SKIP:  return False
# fall through: REPLACE — unlink the existing entry
```

A string matches neither arm, so it does not fail there; it takes the third. Validating
anywhere downstream of the first `is` is too late, and validating "on use" is not a
place — there are four `is OnError.STOP` sites alone. The entry point is the only point
where the value is guaranteed unexamined.

`ArchiveyConfig` converts in `__post_init__` for the same reason: `AcceleratorMode` is a
plain `Enum`, so `AcceleratorMode.AUTO == "auto"` is `False`, and a stored string reads
as "neither ON nor OFF" — the AUTO path. The dataclass is frozen, so the conversion goes
through `object.__setattr__`.

### The fields stay annotated `AcceleratorMode`, not `AcceleratorMode | str`

What the field *holds* after construction is a member, always. Annotating the union
would describe the constructor's input on the attribute every consumer reads, and would
oblige each of them to handle a string that cannot arrive. `extract()`'s parameters take
the `| str` union because there the annotation does describe the input.

### Check the wrong-enum case before the string case

`AbortOn`, `ContainerFormat`, `StreamFormat` and others mix in `str`. A member of the
wrong class is therefore also a `str`, and a string-first branch would report it as a
bad *spelling* when it is a wrong *type*. Ordering the `isinstance(value, Enum)` check
first gets the accurate message.

### Values win over names on a spelling tie

`_lookup` inserts names first and values second, so a value that happens to spell
another member's name resolves to the value — the spelling the CLI and the docs use. No
shipping enum has such a tie. Rather than leave that to be discovered, the test suite
asserts per enum that no two members share a normalized spelling, so the precedence rule
is a documented tiebreak for a case that is currently impossible and fails loudly if it
stops being.

The same guard exists for `ArchiveFormat`, where the tie would be between a format's
name and another format's file extension, and extensions win for the same reason.

### A bare string is not a collection of one

`abort_on` takes a collection. `abort_on="blocked_member"` is a plausible typo, and a
naive `frozenset(values)` iterates it into eleven single-character members — none of
which is an `AbortOn` spelling, so it would then raise, but with a message about the
character `"b"`. It is refused up front, with the list spelling in the message.

### Not in this change: hardening the branch chains

`_apply_overwrite_policy`'s fall-through and the `is OnError.STOP` sites are still
non-exhaustive. Boundary conversion makes the *string* case unreachable, but a fifth
`OverwritePolicy` member added later would still silently inherit "unlink it". That is a
separate defect with a separate cause — it needs no decision and it is not about
argument types — so it is tracked on its own rather than widening this change.

## Rejected alternatives

- **Per-enum `_missing_` hooks.** `Enum._missing_` would make `OverwritePolicy("skip")`
  work, but it does nothing for a value that is never passed to the constructor — which
  is the whole bug — and it would have to be written on each enum, including the ones
  that mix in `str` where the interaction is subtler. A boundary helper is one
  implementation and one place to look.
- **Accepting only the `value`, not the member name.** Half the cost (the value becomes
  API) for less of the benefit; `--help` output and `repr()` both show names, so a caller
  copying either would still be refused.
- **Normalizing more aggressively** (stripping dots, ignoring all punctuation). The dots
  in `"tar.gz"` are meaningful, and a looser fold raises the collision risk the guards
  exist to bound.

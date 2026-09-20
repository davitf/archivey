# Questions for the maintainer

## Q1 — Tighten public `Any` on `ArchiveMember` / `ArchiveInfo` before 0.2.0? ✅ **DECIDED: A**

**Maintainer (2026-09-18):** tighten to `object`. Callers should be explicit about
the type they expect. TypedDict is a later option, not this change.

Landed as `dict[str, object]` on `ArchiveMember.extra` / `ArchiveInfo.extra` and
`**kwargs: object` on `replace`. `listing_limits._extra_bytes` moved with them
because A5 already dispositioned it as a TIGHTEN on its own merits, and keeping
the two `extra` annotations in step is the point; it narrows with `isinstance`
anyway, so `object` costs it nothing. **Not** because `dict` is invariant —
invariance bites between two static types, and `Any` is consistent with every
type in both directions, so a `dict[str, object]` argument was always assignable
to a `dict[str, Any]` parameter. Measured on both checkers 2026-09-20: passing
each into the other is clean. Private `_raw: Any` is unchanged.

### TypedDict later? ✅ **DECIDED 2026-09-20: yes, as staged PR 8**

**Maintainer (2026-09-20):** make `extra` a `TypedDict` whose definition already
carries the correct type for every known key, while still allowing unknown keys
for third-party extensions — on its own PR, not this one.

That is PEP 728 `extra_items`, and it works here. The three costs PR 8 carries are
listed once, on [`SUMMARY.md`](SUMMARY.md) item 8 — do not keep a second copy here.

**The contract is now the TypedDict** (`MemberExtra` / `ArchiveInfoExtra` under
`TYPE_CHECKING`; the `EXTRA_*` constants remain the names). Unknown keys stay
legal and read as `object`.

What follows is the 2026-09-18 reasoning the decision supersedes, from "Not one
*closed* TypedDict" to "Two fields, two key sets" — with two exceptions inside that
run, both marked and both current: the cross-format-map disposition and the
paragraph headed "Correction, 2026-09-20".

Not one *closed* TypedDict on `ArchiveMember.extra`.

The bag is already a documented open map of *optional, per-format* keys. Writers
today:

| Bag | Keys | Value types |
|---|---|---|
| `ArchiveMember.extra` | `is_junction`; `rar.{created_is_ctime,extract_version,file_version,tweaked_crc32,tweaked_blake2sp}`; `zip.{compress_type,aes_vendor_version,aes_strength,aes_actual_method}`; `tar.{type,pax_headers,devmajor,devminor}`; `gzip.original_filename` | `bool`, `int`, `bytes`, `str`, `dict` |
| `ArchiveInfo.extra` | `iso.namespace`; `{zip,rar,7z}.volume_count` | `str`, `int` |

A single `total=False` TypedDict that lists every key would make
`member.extra["rar.extract_version"]` type-check on a ZIP member. That is the
opposite of catching bugs, and it is accepted as the cross-format-map cost on
SUMMARY.md item 8. Per-format TypedDicts (`RarMemberExtra`, …) remain possible
on top, used as `cast(RarMemberExtra, member.extra)` after the caller has
checked the format — helpers, not the field type on the uniform `ArchiveMember`.

The bag is **open**. `codecs.py` writes `gzip.original_filename` onto an
already-constructed member; new keys show up with format work. Closed
TypedDict rejects unknown keys. PEP 728 `extra_items` (keep known keys
precise, allow the rest as `object`) answers this. **Correction, 2026-09-20:
the line here previously said it "needs Python 3.13+; the library floor is
3.11", and that is wrong.** PEP 728 is in no CPython release; it comes from
`typing_extensions` and therefore works at the 3.11 floor. Measured that day on
pyrefly 1.1.1 and ty 0.0.60, both with `python_version = "3.11"`: a
`total=False, extra_items=object` TypedDict types known keys exactly (including
dotted keys via the functional syntax), rejects a wrong-type write to a known
key, and accepts an unknown key whose value reads back as `object`. The
`typing_extensions` import that measurement needs, and whether the definition
stays behind `TYPE_CHECKING` so the install stays zero-dep, is the third cost
on SUMMARY.md item 8. Binding the name at runtime (`else: MemberExtra = dict`)
would not pull in that dependency; it would make the name public API.

Two fields, two key sets. Do not merge member extras and `ArchiveInfo.extra`.

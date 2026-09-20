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

That is PEP 728 `extra_items`, and it works here. The rest of this section is the
2026-09-18 reasoning it supersedes; the two constraints it raises are real and
survive as costs the staged PR carries.

Not one *closed* TypedDict on `ArchiveMember.extra`.

The bag is already a documented open map of *optional, per-format* keys. Writers
today:

| Bag | Keys | Value types |
|---|---|---|
| `ArchiveMember.extra` | `is_junction`; `rar.{created_is_ctime,extract_version,file_version,tweaked_crc32,tweaked_blake2sp}`; `zip.{compress_type,aes_vendor_version,aes_strength,aes_actual_method}`; `tar.{type,pax_headers,devmajor,devminor}`; `gzip.original_filename` | `bool`, `int`, `bytes`, `str`, `dict` |
| `ArchiveInfo.extra` | `iso.namespace`; `{zip,rar,7z}.volume_count` | `str`, `int` |

A single `total=False` TypedDict that lists every key would make
`member.extra["rar.extract_version"]` type-check on a ZIP member. That is the
opposite of catching bugs. Per-format TypedDicts (`RarMemberExtra`, …) are the
shape that would help, used as `cast(RarMemberExtra, member.extra)` after the
caller has checked the format — helpers, not the field type on the uniform
`ArchiveMember`.

Two further constraints:

- The bag is **open**. `codecs.py` writes `gzip.original_filename` onto an
  already-constructed member; new keys show up with format work. Closed
  TypedDict rejects unknown keys. PEP 728 `extra_items` (keep known keys
  precise, allow the rest as `object`) answers this. **Correction, 2026-09-20:
  the line here previously said it "needs Python 3.13+; the library floor is
  3.11", and that is wrong.** PEP 728 is in no CPython release; it comes from
  `typing_extensions` and therefore works at the 3.11 floor. Measured that day on
  pyrefly 1.1.1 and ty 0.0.60, both with `python_version = "3.11"`: a
  `total=False, extra_items=object` TypedDict types known keys exactly (including
  dotted keys via the functional syntax), rejects a wrong-type write to a known
  key, and accepts an unknown key whose value reads back as `object`. What it
  does cost: `typing_extensions` where there is no required runtime dependency
  today, and a `TypedDict` that is assignable to neither `dict[str, object]` nor
  back on either checker.
- Two fields, two key sets. Do not merge member extras and `ArchiveInfo.extra`.

Until PR 8 lands, `dict[str, object]` plus the `EXTRA_*` constants (and string
keys in the format handbooks) is the contract.

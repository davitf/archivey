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

### TypedDict later?

Yes, but not one closed TypedDict on `ArchiveMember.extra`.

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
  precise, allow the rest as `object`) needs Python 3.13+; the library floor is
  3.11.
- Two fields, two key sets. Do not merge member extras and `ArchiveInfo.extra`.

Until then, `dict[str, object]` plus the `EXTRA_*` constants (and string keys
in the format handbooks) is the contract. A later change can add per-format
TypedDict aliases next to those constants without moving the field off `object`.

# safe-extraction — bidi override rejection delta

## MODIFIED Requirements

### Requirement: Non-Bypassable Universal Path-Safety Constraints

The system SHALL run universal safety checks on the faithful stored
`member.name` before any policy transform, user filter, or filesystem write;
`ExtractionPolicy.TRUSTED` does not bypass them. The default path-safety behavior
is reject/raise. A future sanitize policy is outside v1 scope and is not part of
this contract.

The implementation SHALL enforce defense in depth: first a string check rejects
absolute paths, Windows drive/UNC roots, any `..` component split on `/` or `\`,
null bytes, and names/link targets the platform filesystem encoding cannot represent; then
`(dest / member.name).parent.resolve()` must remain within `dest.resolve()` to catch
symlinked intermediate components without following a final-component symlink; link
targets are rechecked as described in the symlink and hardlink requirements. These
string checks SHALL raise typed `FilterRejectionError` subclasses, never a raw
`UnicodeEncodeError`/`ValueError`.

| Constraint | Violation type | Condition |
| --- | --- | --- |
| Path traversal | `PathTraversalError` | Any `..` component, escaping or internal |
| Absolute path | `PathTraversalError` | Leading `/`, Windows drive path, or UNC path |
| Null byte | `PathTraversalError` | `member.name` contains `\x00` |
| Unrepresentable name | `PathTraversalError` | `member.name` cannot be encoded by the platform filesystem encoding |
| Link-target NUL / unrepresentable | `SymlinkEscapeError` | SYMLINK/HARDLINK `link_target` contains `\x00` or cannot be encoded by the platform filesystem encoding |
| Symlink escape | `SymlinkEscapeError` | SYMLINK whose fully resolved target escapes `dest` |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK whose target path resolves outside `dest` |
| Special file | `SpecialFileError` | `MemberType.OTHER` device/FIFO/socket/etc. |

**Bidi overrides are rejected by the *policy*, not universally.** Every other
constraint in this requirement makes the **write itself** dangerous or impossible — it
escapes the destination, carries a NUL the OS truncates on, or names a device. A bidi
override does neither: the member lands inside `dest` under exactly its stored bytes, and
what is compromised is the name a person **reads back afterwards**. That is a
presentation property, and presentation is the axis `ExtractionPolicy` owns.

The rejection therefore lives in the portable-name policy below, which means
`ExtractionPolicy.TRUSTED` — defined as *faithful bytes, no name rejection or rewrite* —
SHALL extract such a member unchanged, while `STRICT` (the default) and `STANDARD` SHALL
reject it with `DeceptiveNameError`. Running after the caller filter also means a filter
that renames the member rescues it, which is the natural remedy for a name that is a lie.

Without this split a caller who wants the bytes — a mirroring tool, a format converter, a
forensic extract — has no route at *any* policy. That is the outcome ADR 0013 rejected for
unrepresentable names ("extracting beats refusing"), and it would couple two unrelated
axes. See ADR 0017.

**The rejected set is the reordering controls only.** Unicode bidi controls are not one
category, and the difference is load-bearing:

| Subset | Codepoints | Extraction |
| --- | --- | --- |
| Overrides and isolates — reorder *surrounding* text; what a `…gnp.exe` disguise requires | U+202A–U+202E, U+2066–U+2069 | **Rejected** |
| Directional marks — set the direction of one neutral character, reorder nothing, and occur in legitimate Arabic and Hebrew filenames | U+061C, U+200E, U+200F | **Accepted**; `MEMBER_NAME_BIDI_CONTROL` already reported it at listing |

The reject set SHALL be defined by enumerating those two ranges, and MUST NOT be derived
by subtracting from the library's broader advisory set: a subtraction leaves the three
marks one editing mistake away from rejecting legitimate RTL filenames.

Right-to-left **script** is unaffected: an Arabic or Hebrew filename takes its direction
from its own letters' properties, and contains no bidi control at all.

Listing and reading SHALL continue to present the name exactly as stored. Rejection
belongs to extraction, which is where a name becomes a filesystem path a person will
read back.

#### Scenario: universal safety matrix

| Case | Expected |
| --- | --- |
| `"../evil"` or `"../../etc/passwd"` | `PathTraversalError`; no write; all policies |
| `"foo/../bar"` | `PathTraversalError` under reject/raise behavior even if it would stay in root |
| Leading `/`, Windows drive, UNC path | `PathTraversalError`; no write; all policies |
| Earlier member creates symlink `foo` outside `dest`; later member writes `foo/x` | Parent resolution rejects `foo/x` with `PathTraversalError` |
| Name with lone surrogate unencodable by the platform filesystem encoding | `PathTraversalError` before path resolution; never raw `UnicodeEncodeError` |
| SYMLINK/HARDLINK `link_target` with `\x00` or unencodable surrogate | `SymlinkEscapeError`; never raw `ValueError`/`UnicodeEncodeError` |
| Name using only `surrogateescape` round-trip low surrogates (`\udc80`–`\udcff`) | Accepted when otherwise safe (representable on disk) |
| `MemberType.OTHER` | `SpecialFileError`; all policies |

#### Scenario: bidi name matrix

| Case | Expected |
| --- | --- |
| `"invoice‮cod.exe"` extracted under `STRICT` / `STANDARD` | `apply_name_policy` raises `DeceptiveNameError`; a `BLOCKED` result and no write |
| The same member extracted under `TRUSTED` | **Extracts**, under the stored name, unmodified — faithful bytes |
| `"a⁦b⁩.txt"` (isolates) extracted | Same split |
| Symlink whose `link_target` contains U+202E | Same split |
| A caller filter renames it to a clean name | Extracts at every policy — the check runs on the final name, after the filter |
| `"‏דוח.pdf"` (RLM, a directional mark) extracted | Extracts; `MEMBER_NAME_BIDI_CONTROL` was reported at listing |
| `"فهرس.txt"` (Arabic script, no controls) extracted | Extracts; no diagnostic, no rejection |
| Any of the above listed rather than extracted | Name presented exactly as stored |
| `DeceptiveNameError` under either `OnError` | `BLOCKED` result and `EXTRACTION_MEMBER_BLOCKED`, like any other `FilterRejectionError`; extraction proceeds |

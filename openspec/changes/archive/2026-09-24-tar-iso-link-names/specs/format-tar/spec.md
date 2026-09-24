## MODIFIED Requirements

### Requirement: Map TAR member metadata to ArchiveMember

The TAR backend SHALL map each `TarInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `TarInfo.mode` lower 12 bits |
| `modified` | `TarInfo.mtime` as timezone-aware UTC |
| PAX `mtime` | Overrides `TarInfo.mtime`, preserving sub-second precision / timezone information |
| `uname`, `gname`, `uid`, `gid` | Directly from `TarInfo` |
| `type` | TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.) to `MemberType` |
| hardlink target | `LNKTYPE` maps to `MemberType.HARDLINK`; `link_target` from `linkname` |
| `raw_name` | The stored name bytes: a PAX `path` record as UTF-8 (the codec tarfile decoded it with; under `hdrcharset=BINARY`, or when the name holds surrogateescape bytes from tarfile's fallback decode, the archive `encoding`); a ustar or GNU long name with the archive `encoding`. `None` when no codec reproduces the name — never an exception out of the listing |

If `TarInfo.mtime` cannot be represented as a Python `datetime`, `modified`
SHALL be `None` and `MEMBER_TIMESTAMP_INVALID` SHALL be emitted with typed,
JSON-safe member identity and source/value context. Under default policy it is
collected/logged and may attach to the member; under `RAISE`, listing halts with
`DiagnosticRaisedError`.

#### Scenario: TAR metadata matrix

| Case | Expected |
| --- | --- |
| PAX `mtime` present | `member.modified` derives from PAX value, overriding `TarInfo.mtime` |
| No PAX `mtime` | `member.modified` is timezone-aware UTC from `TarInfo.mtime` |
| `LNKTYPE` entry | `member.type=MemberType.HARDLINK`; `member.link_target=linkname` |
| PAX name `日本語.txt`, `encoding="latin-1"` | Lists; `raw_name` is the UTF-8 bytes the PAX record holds |
| ustar name, `encoding="latin-1"` | `raw_name` is the latin-1 bytes |
| Out-of-range `mtime` | `modified is None`; `MEMBER_TIMESTAMP_INVALID` counted and may attach |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |

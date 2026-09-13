## REMOVED Requirements

### Requirement: Round-trip test for every writable format

**Reason**: Required a `create -> extract -> compare` test per writable format, with
ZIP and TAR rows. There are no writable formats — the ZIP and TAR write requirements
it tested were removed in `2026-09-02-drop-unshipped-write-claims` and this change.
A testing requirement that cannot be satisfied is not a gate. Preserved verbatim in
`dev-docs/investigations/archive-writing-design.md` for reinstatement with the writing
phase.

# Tasks — a `stream_members()` handle never seeks

- [x] 1.1 Make every handle `stream_members()` yields forward-only, in one place for every backend.
- [x] 1.2 Make `tell()` work on an `unrar`-decoded RAR member.
- [x] 1.3 Test the five seekability cases over every format in the seek matrix.
- [x] 1.4 Update `docs/access-and-cost.md`, `docs/reading-members.md`, `CHANGELOG.md`; remove the `dev-docs/IDEAS.md` entry.
- [x] 1.5 Archive this change.

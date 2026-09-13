# Tasks

- [x] 1. Preserve the design first: `dev-docs/investigations/archive-writing-design.md`.
- [x] 2. Delete `openspec/specs/archive-writing/` by hand — OpenSpec cannot retire a
      capability (see README §Why the capability is deleted by hand). The five retired
      requirements and their reasons are recorded there.
- [x] 3. Drop TAR's write sentence and its stream-write scenario row.
- [x] 4. Remove `testing-contract`'s round-trip-per-writable-format requirement.
- [x] 5. Repoint the references: `Related specs` rows in `testing-contract`,
      `packaging-and-extras` and `documentation`; the capability map and phase table in
      `openspec/project.md`; phase 9 in `dev-docs/PLAN.md`. Prose, so no delta covers them.
- [x] 6. `openspec archive retire-archive-writing-specs --yes` and commit the
      `openspec/specs/` diff.

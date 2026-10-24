# plugin-api — let other packages add archive formats

**Status:** Design only, for the maintainer's review (2026-10-21). No code until it is
approved. Additive: no current call changes. Target: 0.3.0. Effort: large (four PRs).

**Why it matters:** Today every format lives in `archivey.internal`. A rare format (CPIO,
CAB, SquashFS, WIM, XAR) can only be added by changing archivey. A public interface lets
another package add one, and that package gets archivey's listing checks, name rules,
safe extraction and extraction limits without writing them.

**Your question, "will it make the code cleaner?":** Only a little. The four big readers
(TAR, ZIP, RAR, 7z) override three to six internal hooks each, for streaming passes,
solid blocks and link targets. A narrow interface cannot carry those, so they stay on
the internal base class, and the base class does not get smaller. What gets cleaner is
the contract: the four methods every reader must have are written down once, in a public
module, and tested by a reader that has no access to the internals. The real gain is
extensibility, not tidier code. The cost is about 1,000 new lines and a public surface
that later changes must deprecate.

**What it does:**

- A public submodule, `archivey.plugins`, with one class to subclass for the format
  (detection data and `open`), one for the open archive (list members, open a member,
  archive info, close), and `register_format()`.
- An internal adapter turns a plugin reader into an ordinary `BaseArchiveReader`, so
  every public method, limit and diagnostic works the same for a plugin format.
- A plugin names its format with its own `StrEnum` that subclasses a new public
  `PluginContainer` (the `ContainerFormat` enum stays closed). `ArchiveFormat` accepts it.
- The ISO reader moves onto the interface, which proves it on a real format with an
  optional dependency. A CPIO plugin is written outside `src/` as the second proof.
- Single-file and directory stay internal: neither has a container read from bytes
  (details in `design.md` §7).

**Your calls:**

1. Ship `archivey.plugins` as **provisional** (may change in a minor release without the
   deprecation step, until you mark it stable)? Recommended: yes.
2. Registration only by an explicit `register_format()` call, with no automatic loading
   from installed packages? Recommended: yes. Automatic loading lets a `pip install`
   change which parser reads an upload.
3. Port ISO (recommended) instead of the single-file and directory readers.
4. Where the CPIO proof lives: `packages/archivey-cpio/` in this repo, next to `archivey-cli`, unpublished (recommended),
   or a separate repository.

**Bottom line:** Worth building now, while the quota is high, if the aim is to let
others add formats after the quota drops. Do not build it to clean up the code.

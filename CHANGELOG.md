# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

How to update this file for a release: see
[`dev-docs/release-checklist.md`](dev-docs/release-checklist.md)
(commit walk since the previous tag + performance numbers vs that release).

## [Unreleased]

First public release will be **0.2.0**. Until then, notable work accumulates here;
at cut time the checklist moves this section under a dated `## [0.2.0]` heading.

This repository is the **v2** rewrite. The earlier v1 / alpha line (previously
published from what is now
[`davitf/archivey-old`](https://github.com/davitf/archivey-old)) is a separate
codebase — not a SemVer predecessor of this tree. There is no compatibility
promise with that line; treat `0.2.0` as the first release of this library.

### Added

- Unified archive reading for ZIP, TAR, RAR, 7z, ISO, directory trees, and
  single-file compressed streams (gzip / bzip2 / xz / lzip / zstd / lz4 / compress).
- Safe extraction defaults (`archivey.extract`) with policy-driven path and
  overwrite controls; CLI (`archivey list|test|extract`) as a safer unzip demo.
- Native 7z and RAR metadata readers (stdlib codecs for common 7z filters;
  external `unrar` for RAR member data).
- Four optional extras — `[recommended]` (every format and codec that installs
  everywhere), `[seekable]` (rapidgzip), `[free-threaded]` (the measured GIL-safe
  subset), and `[all]`. There is deliberately no extra per format: member codecs are
  shared across containers. See `docs/formats.md`.
- Declarative corpus + mutation / Hypothesis / Atheris testing contract;
  three-configuration CI (`[all]`, `[all-lowest]`, `[core-only]`).
- Benchmark harness: PR structural gate + change-guarded nightly wall-ratio drift.

### Changed

- Performance claims are **aspirational peer-ratio bands** with a published
  measured table in `docs/costs.md` / `VISION.md` (nightly realistic ratios;
  refresh at release time per the checklist).
- GitHub repository renamed from `archivey-2` → `archivey` (canonical name);
  the prior v1 repo was renamed to `archivey-old`.

### Security

- Threat model and open residuals: `dev-docs/threat-model.md`.
- Root [`SECURITY.md`](SECURITY.md) — private vulnerability reporting via
  [GitHub Security Advisories](https://github.com/davitf/archivey/security/advisories/new),
  scope, and guidance that optional `[seekable]` accelerators are not part of
  the defended fuzz surface for hard-latency untrusted input.

<!--
After 0.2.0 is tagged, add:

## [0.2.0] - YYYY-MM-DD

…and link compare URLs at the bottom, e.g.:

[Unreleased]: https://github.com/davitf/archivey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davitf/archivey/releases/tag/v0.2.0
-->

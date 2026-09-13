# Security Policy

## Supported Versions

Security updates target the latest released version of `archivey` on PyPI (and
the corresponding `main` tip while pre-release).

If you are on an older release, please try to reproduce against the latest
before reporting. Older-version reports are still useful for impact assessment;
fixes normally land on current `main` first.

This policy applies to **this** repository (`davitf/archivey`, the v2 library).
The previous implementation lives at [`davitf/archivey-old`](https://github.com/davitf/archivey-old)
and is not covered here.

## Reporting a Vulnerability

Please report security vulnerabilities **privately**. Do **not** open a public
GitHub issue for exploitable bugs, malicious archives, or proof-of-concept
payloads.

**Preferred path:** use
[GitHub private vulnerability reporting](https://github.com/davitf/archivey/security/advisories/new)
(Security → Advisories → Report a vulnerability).

If that flow is unavailable, contact the maintainer privately via the contact
information on the maintainer’s GitHub profile or the package metadata on PyPI.

Include as much of the following as you can:

- A clear description of the vulnerability and its impact
- Affected `archivey` version and/or commit
- OS and Python version used for testing
- Minimal reproduction steps (or a crafted archive attached privately)
- Expected vs actual behaviour
- Known impact class (path traversal, symlink/hardlink escape, arbitrary write
  outside the destination, DoS/hang, information disclosure, unexpected use of
  an external tool, etc.)
- Suggested fix or patch, if you have one

Please coordinate disclosure: allow time for investigation and a fix before
publishing exploit details.

## Scope

In scope (non-exhaustive):

- Extraction path traversal and destination escape
- Symlink / hardlink traversal during extraction
- Arbitrary overwrite outside the requested extraction directory
- Unsafe handling of archive metadata or member names
- Unexpected command execution through external extractors (e.g. `unrar`)
- Denial of service from malformed or malicious archives (including hangs that
  a caller cannot interrupt)
- Information disclosure via unsafe file access
- Failure to translate corrupt/truncated input into typed `ArchiveyError`s on
  the defended (non-accelerator) parse path

Out of scope for private security reports (use ordinary GitHub issues):

- Feature requests, docs typos, and non-security compatibility bugs
- Performance complaints that are not DoS
- Issues that only reproduce with optional accelerators left on when the
  caller’s threat model requires them off (see below) — still welcome as
  ordinary bugs; escalate privately if they enable escape/RCE on the default
  path

## Hardening notes for callers

Guidance for processing untrusted archives — accelerators and the defended fuzz
surface, `unrar` in your deployment's trust boundary, and extracting to a scratch
directory before promoting — lives in the user guide, next to the policies it
qualifies:

- [Safe extraction](https://davitf.github.io/archivey/extracting/) — trust
  boundaries, what is enforced, policies, limits, and the hardening notes
- [`dev-docs/threat-model.md`](dev-docs/threat-model.md) — the maintainer gap
  register

## Response Process

After a private report is received, the maintainer will try to:

1. Confirm receipt
2. Reproduce and assess severity / affected versions
3. Prepare a fix or mitigation
4. Release an updated version when appropriate
5. Publish a security advisory (or public summary) after users have had a
   reasonable chance to update

Response times vary with maintainer availability and issue complexity. This
project is maintained on a reasonable-effort basis.

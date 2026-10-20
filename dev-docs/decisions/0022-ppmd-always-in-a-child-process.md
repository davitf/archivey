# 0022 — Every PPMd member decodes in a child process

- **Status:** accepted (implementation pending, with the native-decoder isolation change)
- **Date:** 2026-10-20
- **Supersedes:** the in-process rule of the `ppmd-crash-isolation` change (2026-09-26,
  PR #481): members up to `DecoderLimits.max_ppmd_in_process_input` (16 MiB of compressed
  input) decoded in this process
- **Provenance:** `openspec/changes/archive/2026-09-26-ppmd-crash-isolation/proposal.md`;
  `dev-docs/known-issues.md` §"Intermittent `pyppmd` native aborts on PPMd streams";
  `dev-docs/investigations/ppmd-native-investigation-results.md` §I, §K.3, §K.4

## Context

pyppmd, the only PPMd decoder for Python, corrupts its heap in ways that kill the whole
process: SIGSEGV or SIGABRT on Linux, `STATUS_HEAP_CORRUPTION` on Windows. Two routes are
known. A decode that asks for more output than the stream holds frees a buffer that its
worker thread still writes to. A model that has ended early, which random bytes reach (a
wrong password, a crafted member), segfaults when it is asked to decode again. Nothing in
pyppmd's Python interface separates "the model ended" from "the worker waits for more
input". Both set the same flags. Upstream has no fix.

## The previous decision (2026-09-26)

archivey kept PPMd in this process for members up to 16 MiB of compressed input and used a
child process only above that. Inside the process it followed a discipline meant to keep
pyppmd out of both routes: it held the whole member, handed it to pyppmd in one call,
bounded every output request by the remaining size, and stopped at the first short
return. Before freeing a decoder whose worker was still parked, it sent one single-symbol
decode to finish the worker (the "quiesce" step), because freeing a parked worker was the
use-after-free that valgrind pinned.

The reasons were:

- **The discipline looked complete.** Every shape measured at the time ran clean under it,
  and valgrind reported no errors on archivey's own call sequences.
- **A child per member was expensive.** Starting one costs 12 to 18 ms, which made
  decoding every member in a child 70 to 154 times slower on archives of small PPMd
  members. Only for large members did the memory of holding the whole input outweigh that.
- **Isolation was reserved for crashes that could not be avoided.** The rapidgzip DEFLATE
  abort was one. Native code alone was not a reason to pay for a process boundary.
- **`multiprocessing` was ruled out.** Its spawn mode re-imports the caller's `__main__`,
  and it reports neither stderr nor the exit status. A child had to be a plain subprocess
  running a standalone worker script, which kept the child code small and per codec.

## What changed (2026-10-20)

- **The discipline has a hole, and it can't be closed from Python.** A re-measurement on
  2026-10-20 found that the quiesce step itself is a decode after the end when the member
  ended early. A spy on the native calls saw it on 183 of 200 hostile 7z members. Replaying
  that call order on bare pyppmd crashed 5 and 6 of 100 children for PPMd7 and 60 of 100
  for PPMd8. Leaving the step out brings back the use-after-free it exists to prevent. The
  safe choice depends on the state pyppmd does not expose, so no in-process call sequence
  is safe by construction. Through archivey no crash was seen in 860 fresh children, but
  that is a low rate, not a guarantee. The crash that `known-issues.md` used to describe
  (an oversized `unpack_size`) turned out to be the free-while-parked route, which
  quiescing already handles. PR #905 corrects the docs.
- **A child no longer costs a process start per member.** The native-decoder isolation
  design takes children from a pool of reused idle children. On a reused child a small
  member costs about 30 µs in the prototype, against 12 to 18 ms for a fresh one.
- **The protocol is no longer per codec.** One shared child protocol now serves every
  native decoder, so moving PPMd onto it adds no new copy of the child code.
- **One rule is easier to state than a threshold.** "PPMd always decodes in a child" needs
  no explanation of a 16 MiB split, of the memory held below it, or of when the split moves.

## Decision

By default every PPMd member decodes in a child process taken from the pool, whatever its
size. A crash costs that member and becomes an archivey error, as above the threshold
today. The `ProcessIsolation.NONE` setting keeps PPMd in this process, as a risk the
caller accepts for speed, for tests, or where processes cannot be started. The docs
discourage that setting.

## Consequences

- PPMd output on small members pays a pipe round trip per request: about 30 µs per member,
  plus about 0.5 to 0.8 ms per MiB of output copied through the pipe.
- `max_ppmd_in_process_input` loses its role under the default. Whether it is removed,
  renamed or kept for `NONE` is a public-name change. It is decided with the isolation
  change, not here.
- The "Why the split at 16 MiB?" note in `docs/security.md` and the field docstring change
  with the implementation.
- An environment that cannot start a process, such as a frozen application, already gets
  `ResourceLimitError` for PPMd members over the threshold. Under the new default this
  applies to every PPMd member unless the caller picks `NONE`.

## What would reopen it

- **A pyppmd release that fixes both routes.** It must pass the verification checklist in
  `ppmd-native-investigation-results.md` §J, on Linux, Windows and a free-threaded build.
- **A way to tell the two worker states apart from Python.** With that, the quiesce step
  could run only when it is safe.
- **A pooled child that costs much more than measured.** For example, if per-member cost on
  Windows comes out near a fresh process start.

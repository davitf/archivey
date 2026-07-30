# Supported platforms, Python versions, and threading

What Archivey commits to, and — just as importantly — what it deliberately does not
claim. Everything on this page is backed by a CI job; where a claim is narrower than you
might expect, it is because the job that proves it is narrower.

## Python and operating systems

Archivey requires **Python 3.11+** and is pure Python (no compiled extensions of its
own), so it runs anywhere CPython does. What CI *proves* on every pull request:

| OS | Python | Install |
| --- | --- | --- |
| Linux | 3.11, 3.12, 3.13, 3.14 | all extras |
| Linux | 3.11, 3.14 | zero-dependency core |
| Linux | 3.11 | all extras, **minimum** dependency versions |
| Linux | 3.13t (free-threaded) | core, and the GIL-safe extras — see below |
| macOS | 3.11, 3.14 | all extras |
| Windows | 3.11, 3.14 | all extras |

The minimum-versions leg matters more than it looks: optional libraries change behaviour
by their *version* as well as their presence, so the floor of each declared range is
tested, not just the newest release.

Other platforms (BSDs, other CPython builds) are expected to work and are not tested. A
platform-specific bug report is welcome and treatable; it is just not something a green
badge here is asserting today.

### Non-CPython interpreters

Not tested. Archivey's core is pure Python, but the optional accelerators and codec
backends are C/C++ extensions, so PyPy or GraalPy would at best run the core plus
whatever extras build there.

## Free-threaded Python (3.13t and later)

Free-threaded builds remove the GIL, which turns "we were accidentally safe" into real
data races. Archivey does not rely on the GIL for correctness — the reader uses explicit
locks — but the **claim is scoped to what CI actually exercises**, and that scope is
narrow on purpose.

### What is claimed

On a reader opened with `concurrent_members=True`, after member materialization, it is
safe for multiple threads to call `open()` and to work on **different** member streams
concurrently:

```python
from archivey import open_archive

with open_archive("photos.zip", concurrent_members=True) as reader:
    members = reader.members()          # materialize once, on one thread
    # Now: fan out. Each thread opens and reads its own member.
```

This is verified by a required CI job on **Linux, CPython 3.13t**, which runs the **whole
test suite** (not just the concurrency tests) in two stages: the zero-dependency core,
then the core plus the extras that keep the GIL disabled.

### Which extras are free-threaded

This is the part that catches people out. When a C extension has not declared
free-thread support, importing it makes CPython **silently re-enable the GIL** — your
program keeps working, but you are no longer running free-threaded. Measured on CPython
3.13.7t:

**`pip install archivey[free-threaded]`** is the install line for these builds: it is
exactly the measured subset that leaves the GIL disabled. Measured on CPython 3.13.7t:

| Package | In `[free-threaded]`? | Free-threaded on 3.13t? |
| --- | --- | --- |
| `pycdlib` (ISO) | Yes | Yes — covered by CI |
| `backports.zstd` | Yes | Yes — covered by CI |
| `lz4` | Yes | Yes — covered by CI |
| `tqdm` (CLI progress) | Yes | Yes — covered by CI |
| `cryptography` | 3.14+ only | **Cannot install on 3.13t** — its `cffi` dependency rejects free-threaded 3.13 outright ("upgrade to free-threaded 3.14 or newer"). Installs on 3.14t and keeps the GIL disabled |
| `rapidgzip` (`[seekable]`) | No | **No** — import re-enables the GIL |
| `pyppmd`, `inflate64`, `brotli` | No | **No** — import re-enables the GIL |

So on a free-threaded build today you can use the core formats plus ISO, zstd and lz4 and
stay genuinely GIL-free. Pull in the 7z codecs or the seek accelerator and you are back to
a GIL-ed interpreter.

Two consequences worth stating plainly:

- **`pip install archivey[recommended]` fails on free-threaded 3.13**, because it contains
  `cryptography`. That is the wheel ecosystem, not Archivey — use `[free-threaded]` there,
  or move to 3.14t where `[recommended]` installs.
- `[free-threaded]` is a **moving set**, not a guarantee about Archivey's own code. It is
  expected to widen as more wheels declare support, and may eventually stop being a
  separate extra at all.

None of this is Archivey's own code — it is the state of the wider wheel ecosystem on
3.13t, and it should improve on 3.14t. The CI job asserts the GIL is *still* disabled
after installing `[free-threaded]`, so if one of its packages regresses the job fails
rather than quietly testing a GIL-ed interpreter.

### What is *not* claimed

- **macOS and Windows free-threaded builds.** The job is Linux-only.
- **The packages in the "No" rows above.** They are not tested under free-threading, and
  since importing them re-enables the GIL, "free-threaded support" is not a meaningful
  question for them yet.
- **Everything except member streams.** Iteration, materialization, extraction,
  `stream_members()`, and `close()` are **single-owner** operations. Driving any of them
  from several threads is a usage error, not a race to be fixed.
- **Parallel speedup.** Nothing here promises that fanning out is *faster*; it promises
  it is correct. Decode work may still serialize on a backend lock.

### The default is fail-fast, not racy

If you do not pass `concurrent_members=True`, the reader allows **one live member
stream**. A second overlapping `open()` raises
[`ConcurrentAccessError`][archivey.ConcurrentAccessError] rather than quietly returning
interleaved bytes:

```python
with open_archive("photos.zip") as reader:      # no CONCURRENT declared
    s1 = reader.open("a.txt")
    s2 = reader.open("b.txt")                   # raises ConcurrentAccessError
```

That is the deliberate design: accidental cross-thread sharing fails loudly on the first
call instead of corrupting data on some later run. See
[decision 0003](decisions/0003-member-streams-opt-in.md) for why capabilities are opt-in.

Note that `ConcurrentAccessError` is an
[`ArchiveyUsageError`][archivey.ArchiveyUsageError], which sits **outside** the
`ArchiveyError` tree ([decision 0012](decisions/0012-usage-errors-outside-archiveyerror.md)).
A broad `except ArchiveyError` around your archive handling will *not* swallow it — which
is intended, because it reports a bug in the calling code, not a problem with the archive.

### One live-stream caveat

Closing a reader while member streams are still open defers backend teardown until the
last stream closes, so escaped streams stay readable. This is by design, but it means a
`close()` on one thread can block on I/O finishing elsewhere — don't treat reader close
as instantaneous under concurrency.

## Thread-safety summary

| Operation | Safe from multiple threads? |
| --- | --- |
| `open()` + reading **different** member streams | Yes, with `concurrent_members=True`, after materialization |
| Reading the **same** member stream object | No — one owner per stream |
| `members()` / `__iter__` / `scan_members()` | No — single-owner; materialize once, then share the result |
| `extract_all()` / `stream_members()` | No — single-owner passes |
| `close()` | Safe to call twice; not safe to race against in-flight opens |
| Separate `ArchiveReader` objects | Yes — independent readers share no mutable state |

The simplest correct pattern is: **materialize on one thread, then fan out reads.**

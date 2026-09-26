# PPMd child: only a crash is a verdict on the data

## Why

The capability spec says a crash of the PPMd child becomes `CorruptionError`, and a
member with no child to run it raises `ResourceLimitError`. It does not say what other
deaths of the child raise. The code now tells them apart: a child killed by SIGKILL (most
often the out-of-memory killer), or one that dies allocating the member's model, is a
resource failure; a child ended any other way (SIGTERM, a plain exit status) did not
crash on the data. The prose docs already say so; the spec did not.

## What Changes

- The `max_ppmd_in_process_input` sentence of "Explicit configuration object" names all
  four outcomes: a crash is `CorruptionError`; SIGKILL or an allocation death is
  `ResourceLimitError`; no child possible is `ResourceLimitError`; any other death is
  `ReadError`.
- One scenario row for the three child-death outcomes.

## Impact

- Spec only: `openspec/specs/archive-reading/spec.md`. The code and the prose docs
  changed in the same pull request.

# C. Surface size and the second-consumer evidence

## The number

| Where | Names |
| --- | --- |
| `archivey.__all__` | 90 |
| Importable from `archivey`, not in `__all__` | 19 |
| `archivey.detection_cost` (defined there, no leading underscore) | 11 |
| `archivey.terminal` | 3 |
| **Total importable from a public module** | **123** |

Of the 90: 26 are exception classes, 24 are data-model types, 9 are diagnostics
types, 7 are config, 6 are type aliases, 4 are cost, 3 are entry points, and the rest
are detection, measurement and the reader. The July review's calibration ("the three
canonical jobs need ~32 names; ~70 is the right freeze") still describes the surface.
The 15 arrivals since then (`A-surface.md`) each earn their keep by the July test
("would a user ever type this?"): every one is either the type of a public argument or
field, or something a caller receives and branches on.

**Which names does the "open, list, hash, extract" contract actually need?** The same
~32 the July review listed. Nothing in the 90 is mysterious. The names a user does
*not* type are the exception subclasses they never catch (they catch the parent), the
`*Extra` bags (they index `member.extra` and get a typed value), and the diagnostics
detail (they read `reader.diagnostics.counts`). Those are second-ring API, correctly
exported so the types a user *sees* have names.

The one genuine size question is `MemberStreams` (A-3): a public name that its own
docstring calls internal.

## The CLI as the second consumer

`tests/test_cli_uses_public_api.py` fails the build if any file under `src/archivey/cli/`
imports from `archivey.internal`. It passes on `878c75f`. So the July method (trace every
place the CLI reaches past the surface) now has a mechanical answer: nowhere.

What the CLI hand-rolls that the library could have offered was the July review's
richer question. Checked again against the current `cli/`:

- **`--track-io`**: uses `enable_measurement()` and `reader.io_stats()` (July E1,
  fixed).
- **`info`**: uses `reader.format_info` (`#468`) and `ArchiveFormat.display_name` (July
  S2, fixed); detects once.
- **`list -v`**: prints `member.diagnostics` messages. The library offers the tuple;
  the CLI formats it. Right split.
- **`test`**: still the 60-line manual loop the July review flagged (E2, the `verify`
  primitive). Parked in `dev-docs/IDEAS.md`, still parked, and the brief says not to
  reopen it. It remains the only place the CLI does something a library user would
  have to copy.
- **`extract`**: maps `--overwrite`, `--stop-on-error`, `--policy` onto
  `OverwritePolicy`, `OnError`, `ExtractionPolicy` one to one, and its two defaults
  that differ from the library's are now documented (`docs/cli.md`, "Defaults that
  differ from the library", `#465`).
- **Escaping**: through `archivey.terminal` (`#448`). The one helper family a front
  end needs beyond the core surface, and it is public.

No new gaps. The rule that produced this ("The CLI uses only public API",
`CONTRIBUTING.md`) is the durable fix the July review asked for; the test makes it hold.

## Finding

### C-1 · Low · The package docstring is the only place the surface's real size is stated

`src/archivey/__init__.py:1` lists the eleven modules and says which two are "public,
not re-exported". `docs/api.md:3` says "Everything documented here is re-exported from
the top-level `archivey` package ... except the front-end helpers ... imported from
`archivey.terminal`". After A-1 lands, that sentence needs `archivey.detection_cost`
beside `terminal`. A user reading the API page should not have to open `__init__.py`
to learn that a third module is part of the promise.

**Recommendation.** One clause on `api.md:3`, added with A-1.

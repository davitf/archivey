## MODIFIED Requirements

### Requirement: info and detect summarize archive identity

The system SHALL provide `archivey info` (alias `detect`) that reports detected
and/or opened format identity for a path without listing every member. It SHALL
be suitable for answering "what does archivey think this file is?" including
failure cases with a typed/clear error. After a successful open, `info` SHALL
print an `access:` line summarizing the archive's `CostReceipt` (listing /
member-access / stream axes) in human prose. With `-v` / `--verbose`, it SHALL
also print the raw cost axes (`listing`, `access_cost`, `stream`,
`solid_blocks`).

`info` SHALL read the detection result **from the reader** rather than calling
`detect_format()` and then `open_archive()` on the same path. Detecting twice was a
workaround for the reader not exposing the result, it is unavailable on a non-seekable
source, and the redesign makes the second detection more expensive than the first.

With `-v` / `--verbose`, `info` SHALL render the evidence ledger — every record's kind,
class and validation state — rather than only the derived `confidence` and `detected_by`.

#### Scenario: info vs list

| Case | Expected |
| --- | --- |
| `archivey info <archive>` / `archivey detect <archive>` | Prints format/identity summary including `access:`; does not dump full member listing |
| `archivey info -v <indexed-zip>` | Includes `access: random (indexed)` and raw cost axes |
| Unreadable/unknown file | Non-zero exit; clear error (no stack trace by default) |
| `archivey list <archive>` | Member listing; not a substitute for info's format summary |

#### Scenario: info detects once

| Case | Expected |
| --- | --- |
| `archivey info <archive>` | Detection runs once; the summary comes from the reader's field |
| `archivey info -v <archive>` | Full evidence ledger rendered, not only the two derived scalars |
| A source whose ledger holds a probe hit **and** a matching name | Both records shown — the composition a single scalar cannot carry |
| `archivey info` over an ambiguous source | The ambiguity error names the tied candidates |

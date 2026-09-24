## ADDED Requirements

### Requirement: Refuse raw CD sector images by name

The ISO backend SHALL recognise a raw CD sector image — a dump whose sectors begin with
the 12-byte sync pattern `00 FF×10 00`, as the `.bin` of a `.bin`/`.cue` pair does —
and SHALL refuse it with `UnsupportedFeatureError` naming the layout found, rather than
read it or let detection fail. Detection SHALL claim such a file as `ISO` from the sync
pattern at offset 0 so that the refusal is reachable, and the refusal SHALL NOT depend on
`pycdlib` being installed.

The layout named SHALL be the sector mode (Mode 1, Mode 2 Form 1, Mode 2 Form 2, or an
unknown mode byte) and, when a second sync is found, the sector size: 2352, or 2448 for a
dump carrying subchannel data. Reading a raw image by stripping sectors to their payload
is deferred past 0.2.0 and documentation SHALL NOT claim it.

#### Scenario: raw-sector matrix

| Case | Expected |
| --- | --- |
| Raw Mode 1 or Mode 2 Form 1 image, 2352- or 2448-byte sectors | `UnsupportedFeatureError` naming the layout and pointing at converting to `.iso` |
| Raw Mode 2 Form 2 image | `UnsupportedFeatureError` saying it holds no ISO 9660 filesystem |
| Unknown mode byte | `UnsupportedFeatureError` naming the mode |
| `detect_format` on a raw image | `ISO`, `CERTAIN`, `detected_by="magic"` |
| `pycdlib` not installed | The same refusal |
| Plain `.iso` | Unaffected: it starts with the zero-filled system area, never the sync |

## REMOVED Requirements

### Requirement: Read raw .bin CD images through sector stripping

**Reason**: Never implemented, while the docs and a verified claims row said it was
(#315 S22-K6). davitf ruled (2026-09-21) to recognise and refuse raw images for 0.2.0 and
defer reading them. Replaced by *Refuse raw CD sector images by name*.
**Migration**: Convert the image to a plain `.iso` first.

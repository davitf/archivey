# Review of `new_docs/`

Reviewed against `main` at `9611f85b`. The pages are the unpublished rewrite
(`new_docs/README.md`). `docs/` is still the live site.

The draft is in good shape. It sounds like one person explaining a library they
have used: an example first, then what the call does, then the choice a reader
is about to make. The behaviour I ran matches the text on almost every row of
the extracting tables, and on the opening, reading, and errors pages. The
problems are a handful of sentences a careful reader would trust and then be
wrong about. Two of them are on the security page, which is where that costs
the most.

I checked claims by running the library (`uv run python`, Python 3.11, the
`recommended` and `seekable` extras installed, UnRAR 7.00, 7-Zip 23.01, `rar`
on `PATH`). Scratch scripts stayed outside the repo. I did not remeasure the
timing and memory column in the hardening table, and I did not run the Windows
folder-source paragraph.

## Wrong

These sentences do not match what the library does.

### The bzip2 accelerator runs in the caller's process

`security.md`, "Where the guarantees stop", says rapidgzip runs in a separate
process, then: "Its bzip2 decoder hasn't been seen to crash, but it runs there
too as a precaution, since it's part of the same library."

Only the DEFLATE path (gzip, zlib, raw deflate) runs in a child process.
rapidgzip's bzip2 decoder runs in the caller's process. The code says so
(`rapidgzip_inprocess.py`: "Only the bzip2 decoder runs in-process"), and
`new_docs/README.md` already records the same fact: the page describes a
change that has not landed.

This is the paragraph that tells a reader where a native crash can take their
process down. As written, it tells them the bzip2 accelerator cannot. A crash
there can. Say that DEFLATE runs in a child, and that bzip2 runs in this
process until that change lands. The `OFF` switches in the same paragraph are
right.

### Extraction still enforces the listing limits when `streaming=True`

`security.md` says: "With `streaming=True` or `stream_members()`, `max_members`
and `max_metadata_bytes` aren't checked, except that 7z, RAR and ISO archives
still check the member count when they open."

A 5-member TAR opened with `ListingLimits(max_members=2)`:

- `stream_members()` yielded all 5 members, with and without `streaming=True`.
- `extract_all` with `streaming=True` raised `ResourceLimitError` at the third
  member, after `f0.txt` and `f1.txt` were already on disk.

So a streaming *read* does not apply those caps. A streaming *extraction*
does. `members()` and `members_report()` do too. The 7z, RAR, and ISO
exception is a third case (they count members while opening), and this run
did not re-check it. The sentence as written tells someone who extracts from
a pipe that the member cap is off. It is on, and the files already written
stay.

`max_metadata_bytes` goes through the same on/off switch as `max_members`. I
ran the member cap only.

### Trailing bytes are allowed, and sometimes only recorded

`opening.md` says a file object is read from the current position to the end,
and that an archive which starts partway through a larger file "opens as is,
as long as nothing follows it. If something does, pass a file object that ends
where the archive ends."

The first half is right. A ZIP starting six bytes into a buffer opened and
`read` returned the member. The second half is not a requirement:

- A ZIP with `TRAILING` appended after it opened, `read` returned the member,
  and nothing was recorded in `archive.diagnostics`.
- A gzip with `XXX` appended opened, `read` returned the full payload, and
  recorded `ARCHIVE_TRAILING_DATA`.
- The same gzip under `DiagnosticPolicy.strict()` raised
  `DiagnosticRaisedError` instead.

Say that trailing bytes do not stop the open. A compressed stream such as
gzip records `ARCHIVE_TRAILING_DATA`, and `strict()` turns that into an
exception. A ZIP's own directory says where the archive ends, and bytes after
that were ignored.

The mid-file case, and the rule that a pipe needs `streaming=True`, are right.
A non-seekable gzip without `streaming=True` raises `StreamNotSeekableError`
and the message names the flag. A ZIP from the same kind of pipe raises
`StreamNotSeekableError` even with `streaming=True`.

### `overwrite="rename"` puts the counter before the file suffix

`extracting.md` says the member "is written next to it, as `name (1)`."

For a file, the counter goes before the final suffix. `a.txt` was written as
`a (1).txt`. `numbered_name` in `archivey.paths` documents the same rule:
`photo.jpg` becomes `photo (1).jpg`, and `notes.tar.gz` becomes
`notes.tar (2).gz`. A directory has no suffix to move, so the counter goes
after the whole name (`photos (1)`). The page's spelling matches a directory
and not a file. Use the file example. That is the one a reader will look for
on disk.

The rest of that row is right. `"error"` raises `ExtractionError` and leaves
the existing bytes. `"skip"` keeps them and is not a failure
(`NOT_OVERWRITTEN`). `"replace"` replaces them.

### A stored RAR member can be read with no external program

`index.md` says RAR archives can be listed with nothing else installed, and
"To read the files inside, you also need" `unrar` or `rar` 6.0+, or `unar`
1.10+.

Listing is right. Reading is right for a compressed or encrypted member, and
too wide for a stored one. A RAR built with `rar a -m0`, opened with
`RarDecompressor.NONE`, listed `plain.txt` and `read` returned the bytes.
`security.md` already says this, in the hardening section. The install
paragraph on the first page is where someone decides whether they need the
program. Point the requirement at compressed and encrypted members.

The version floors match `RarDecompressor` (UnRAR 6.0, `unar` 1.10).

## Easy to misread

The words are defensible. A reader in a hurry takes the other meaning.

### "Hidden characters" are a narrower check than the phrase

The unusual-members table refuses, under `"strict"` and `"standard"`, "a name
with hidden characters that make an `.exe` look like a `.png`." Under
`"trusted"` it is written as is.

That is true for the reordering controls (U+202A-U+202E and U+2066-U+2069).
`photo` plus U+202E plus `gnp.exe` was refused under the first two policies
and written under `"trusted"`. It is not true for every character that
disappears in a name. A zero-width space (U+200B) in `zero\u200bwidth.png` was
written under all three policies, including `"strict"`.

The row is doing the right thing by describing the effect instead of naming
the controls before the reader has met them. "Hidden characters" still covers
the zero-width case, which is written. "Characters that change the order the
name is displayed in" matches the check.

Every other row of that table matched a TAR I built and extracted with
`on_error="continue"`, on this machine:

- `../evil.txt`, a symlink to `../../outside` or `/etc/passwd`, and a named
  pipe: refused under all three policies.
- `/etc/evil.txt` and `C:/evil.txt`: refused under `"strict"`; written as
  `etc/evil.txt` and `evil.txt` under `"standard"` and `"trusted"`. The same
  absolute-path result on a ZIP.
- `CON`, `aux.txt`, and `file:ads`: refused under `"strict"` and `"standard"`;
  written as is under `"trusted"`.
- `notes. ` (dot, then space): `notes` under `"strict"`; the original spelling,
  space included, under the other two.
- A TAR name whose bytes are `caf\xe9.txt`: `caf%E9.txt` under `"strict"` and
  `"standard"`; the raw spelling under `"trusted"`.
- `README` then `readme`: the second fails as "destination already exists"
  under `"strict"` and `"standard"`, and with the default `on_error` that
  failure raises. Under `"trusted"` both files were written.
- Mode `rwsr-xr-x`: `rw-r--r--`, `rwxr-xr-x`, and `rwsr-xr-x`.
- Mode `rw-rw-rw-`: `rw-r--r--`, then as stored, then as stored.

The permissions summary is more careful than a quick reading suggests, and it
is right. `"strict"` says files are *at most* `rw-r--r--`. A stored `rw-rw----`
came out `rw-r-----`, and a stored `r--------` stayed `r--------`. The mask
does not fill in bits the archive did not set. `"standard"` kept both of
those modes and only dropped the setuid bit on `rwsr-xr-x`. I am not root, so
I did not see `"trusted"` apply an owner. The code does that only when
`geteuid()` is 0.

### A refusal is not a failure, and the page says both without connecting them

`on_error="stop"` "raises" on the first failure. Later, "a refused member
isn't written, and the rest of the archive still extracts." Both are true,
and they sit far enough apart to read as a contradiction.

A TAR with `../evil.txt` and then `good.txt`, default options: the first
result was `BLOCKED`, `good.txt` was written, and the call returned. The same
archive with `abort_on=["blocked_member"]` and `good.txt` first raised
`FilterRejectionError` and left `good.txt` on disk. An existing `a.txt` with
`overwrite="error"` raised `ExtractionError`, and a later member in the same
archive was not written.

One sentence on the `on_error` table is enough: a refused member is not a
failure, so `"stop"` does not apply to it. `abort_on=["blocked_member"]` is
how you stop there. The page already says the second half.

`abort_on=["name_sanitized"]` has a similar trap under the default policy.
An absolute `/etc/evil.txt` is refused under `"strict"`, so that abort does
not fire (`good.txt` still extracted). Under `"standard"` the same name is
placed at `etc/evil.txt` and the abort raises `NameRewrittenError` before
anything is written. A trailing `notes. ` under `"strict"` does fire it
(`NameRewrittenError`), because that name is rewritten rather than refused.
The table's "or an absolute name is placed inside the destination" is the
`"standard"` and `"trusted"` event. A clause that `"strict"` refuses an
absolute name instead, so `blocked_member` is the abort that catches it,
would save a confused experiment.

### "16 MiB" of PPMd is compressed input

The same security list says PPMd members are decoded in-process up to
`max_ppmd_in_process_input` (16 MiB). That field is compressed bytes
(`DecoderLimits`). The table's "up to twice the limit in memory, 32 MiB at
the default" matches the config comment. The bullet never says the 16 MiB is
the compressed size, so it is easy to read as 16 MiB of output. Add
"compressed".

### "Two such members reach the default"

The password-hashing paragraph says a crafted archive can ask for 2^24 rounds
per member, "so two such members reach the default." The budget raises when
the total would be *greater than* 2^25 (`KeyDerivationBudget.spend`). Two
maximum derivations are allowed. A third is refused. "Reach" can be read as
"the second one is stopped." "Two such members use the whole default, and a
third is refused" is the same fact with one reading.

## Readability, style, and how the pages feel

The voice is already the one these pages are supposed to have. Headings name
the task ("Extracting", "Choosing how to read"). Defaults are a real call
with a short comment on each argument. Options become tables. The reading
page answers the objection a reader will have ("why do you raise on a ZIP,
where seeking is cheap?") and the answer is about their own bug showing up
in production. That section is the best prose in the set. The security page
opens with the guarantee and then the holes, which is the order a worried
reader wants. The errors page earns its first paragraph: an exception means
there is no correct answer, a diagnostic means the answer is right and
something along the way is worth knowing.

A few passages ask for a second pass. None of them are wrong. They are the
places I would break a paragraph if I were editing the page anyway.

- `opening.md`, "Names in other encodings." One paragraph holds the modern
  case, the guess, what an escape is, the `encoding=` argument, the café
  example, and which formats ignore the argument. The example is the sentence
  a reader came for, and it is exact: a TAR name stored as the byte `0xE9`
  lists as `'caf\udce9.txt'`, and `encoding="latin-1"` lists `'café.txt'`.
  Give that example its own short paragraph after the guess. The "which
  formats ignore it" sentence can follow. It is right: gzip with
  `encoding="latin-1"` recorded `ENCODING_ARGUMENT_UNUSED`.
- `extracting.md`, the `sanitize_names` paragraph. The rules are right (see
  below) and the hard-link sentence is doing a lot of work at the end of an
  already full paragraph. Start a new paragraph at "A hard link is a second
  name..."
- `errors-and-diagnostics.md`, the checksum paragraph. The facts belong on
  this page. A table (format, what is checked, what a short read skips) would
  scan faster than one sentence that visits TAR, gzip, ISO, Brotli, `.Z`,
  `.lzma`, `.xz`, `.zst`, and `.lz4`. I did not re-run that matrix. The TAR
  half of the damaged-archive section did match a run, below.

I would not chase sentence length anywhere else. The pages mix short and
long sentences on purpose, there are no em dashes, and the one semicolon is
inside a table cell on the reading page. Nothing reads as a brochure.
"Simple API" and "safely by default" on the first page are claims the
examples and the extracting page then pay for.

The hole a reader feels immediately is the links. `install.md` and `api.md`
are named from every page and neither file exists. `new_docs/README.md` says
so, and says nothing checks links in this tree. That is fine while the tree
is a draft. It is the thing that has to be true before this replaces `docs/`,
because the pages keep sending you to the reference for the rest of the
story. `SECURITY.md` and `dev-docs/threat-model.md` still point at
`docs/extracting.md`, which the README also notes.

`reading.md` never says that `streaming=True` together with
`concurrent_members=True` is rejected. The call raises `ArchiveyUsageError`.
The two options are explained as separate answers to separate problems, so a
reader can reasonably try both. One clause on the "several members at once"
section would do.

## Checked, and it matches

So the next pass does not re-run these.

**First page.** Format detection ignores a misleading name (`backup.bin` with
ZIP bytes opened as ZIP). `read` returns the member bytes. `stream_members`
gives `None` for a directory and a symlink. A folder path opens as an archive
of the files inside it. Wrong `format="zip"` on a TAR raises `CorruptionError`.
Enum spellings accept `policy="STRICT"` and `abort_on=["blocked-member"]`.

**Opening.** A password on a TAR records `PASSWORD_ARGUMENT_UNUSED`. With that
code set to `"ignore"`, `counts` still has 1 and `retained` is empty.
`DiagnosticPolicy.strict()` does not raise for that password. `pedantic()`
does. An empty ZIP under `strict()` lists no members and records
`EMPTY_ARCHIVE` without raising. A name with U+202E under `strict()` raises
`DiagnosticRaisedError` from `members()`. Any part of a 7-volume 7z set
(`split.7z.001` through `.007`) opened and returned the same 3000-byte
member. I did not build a multi-volume RAR.

**Reading.** On a streaming reader, `members()`, `open()`, and `read()` raise
`ArchiveyUsageError`. A second `stream_members()` after the first pass has
started raises too. A stream from the loop raises `ValueError` ("I/O
operation on closed file") once the loop has moved on. `seek` on a default
member stream raises `UnsupportedOperation`. With `seekable_members=True`,
`open` can `seek(0)`. `stream_members` still cannot, which is what the page
says: it talks about streams from `open`. A second `open` while the first
stream is open raises `ArchiveyUsageError` and names `concurrent_members`.

**Extracting.** `dry_run=True` left an existing `out/a.txt` untouched and
reported the member as `EXTRACTED` at `out/a.txt`. A folder member merged
into a folder that was already there. A file member whose path was that
folder failed and left the folder in place. A filter that renames `../evil.txt`
to `renamed.txt` extracts it. The same filter renaming a named pipe still
gets `BLOCKED`. Changing a symlink's target to a name inside the archive
extracts the link. Changing a hard link's target does nothing: the link was
still the same file as `good.txt`. `sanitize_names` wrote `../evil.txt` as
`evil.txt`, left the hard-link target as stored, and the symlink to
`../../outside` stayed refused. A corrupted ZIP member with `on_error="continue"`
was `FAILED`, and `bad.txt` was not left behind. `ok.txt` in the same archive
was written.

**Errors.** `TruncatedError` is a `CorruptionError`. `ArchiveyUsageError` is
not an `ArchiveyError`. A missing member raises `KeyError`. An `int` source
raises `_UsageTypeError`, which is a `TypeError` and an `ArchiveyUsageError`.
A file of zeros named `backup.gz` raises `CorruptionError` with
`format_unconfirmed=True`. A TAR cut between members, and one cut inside the
next header, lists the members that were whole, `members()` does not raise,
and `ARCHIVE_EOF_MARKER_MISSING` is the signal. The suite's
`test_members_report_recovers_prefix_on_corrupt_header` is the other case the
page describes (a header that is present and wrong: `members()` raises, the
report keeps the prefix). I did not re-run it. A ZIP cut in half fails at
`open_archive` with `CorruptionError`, so there is no member list to be
partial. Defaults I read off the objects: 2 GiB extracted, 262,144 entries,
ratio 1000 after 5 MiB, 262,144 members, 64 MiB of metadata, 2 GiB decoder
memory, 2^25 key-derivation rounds, 16 MiB PPMd in-process, 1 GiB spool,
256 retained diagnostics. `ExtractionLimits.UNLIMITED` sets the three guards
to `None`.

**Security, apart from the two wrong sentences.** The nested-archive warning
matches the way the limits are defined (each archive has its own budget). The
`read()` versus `stream.read(n)` distinction matches the methods. The
`unar` command-line password, and `RarDecompressor.UNRAR` / `NONE`, match
`RarDecompressor`. The crafted `.xz` / `.lz` seek is the case the single-file
handbook already records. I did not build one.

## If these pages are edited

Fix the five "Wrong" sentences in place. They are small, and each one is a
place a reader makes a decision (where a crash lands, whether a cap is on,
whether they must slice a file, what name appeared on disk, whether they need
`unrar`). The "Easy to misread" notes are the same kind of edit, one clause
each, except the PPMd and key-derivation lines, which are one word and one
sentence.

I would not rewrite the pages for tone. The next real gap is the two missing
pages the links already promise, and that is a different piece of work.

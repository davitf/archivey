# Design — bounded password confirmation

All numbers below were measured on 2026-09-09 in the Claude Code web container
(4 vCPU, `cryptography` 49.0.0, p7zip 16.02, py7zr 1.1.3, Python 3.11), against
PR #318's branch unless stated. They are ratios worth trusting and absolute times
worth re-measuring on other hardware.

## 1. Where the cost actually is

The starting premise, from PR #318, was that confirmation costs
`folder_size × candidate_count`. That is only true when the folder's first integrity
anchor sits at the folder end, because `_verify_decoded_folder` already raises on the
first mismatching member CRC. #318's fixture is a single-member store+AES archive, which
is exactly that worst case.

Wrong-candidate cost, 200 MiB folders:

| fixture | 1 wrong | 4 wrong |
| --- | --- | --- |
| store+AES, first member 4 KiB | 0.117 s | 0.413 s |
| store+AES, single 200 MiB member | 0.337 s | 1.355 s |
| LZMA2+AES, either shape | 0.103 s | 0.405 s |

Key derivation is 0.119 s per distinct password at the default `NumCyclesPower=19`
(3.76 s at the 24 clamp). Rows 1 and 3 are therefore *entirely* key derivation — the
decode already stops early. Only row 2 scales with folder size.

The cost that hits every caller is the **correct** password, which walks every member CRC
to the end of the folder and is then followed by a second full decode to serve the member:

| `open()` + `read(1)` of the first member, one correct password | today | this change |
| --- | --- | --- |
| LZMA2+AES 200 MiB solid, first member 4 KiB | 0.471 s | **0.118 s** (first-member CRC) |
| store+AES 200 MiB solid, first member 4 KiB | 0.342 s | **0.110 s** (first-member CRC) |
| LZMA2+AES 200 MiB, single member | 0.446 s | ~KDF + 64 KiB prefix (codec settles a wrong key; do not walk 200 MiB) |
| store+AES 200 MiB, single member | 0.381 s | 0.261 s (walk to the CRC; Copy cannot self-verify) |

Rows 1–2 become pure key derivation. Row 3 is the rejecting-codec rule (D1-C): a late
CRC is not walked when the decompressor will raise on a wrong key. Row 4 still walks;
`sevenzip-aes-tail-key-check` is what collapses it.

Three defects found while measuring, all fixed here: `_verify_decoded_folder` tests
`folder.digest_defined` before per-member CRCs, so a folder carrying both anchors at the
end; its `if not member_digests` branch decodes the whole folder and checks only length;
and 7z runs confirmation even when the candidate set is unambiguous, where ZIP gates on
`is_ambiguous()` (`zip_reader.py:962`).

Anchors are normally present. Every archive built here with the `7z` CLI and with py7zr
came back `digest_defined=False` with a CRC on every substream, and
`_read_substreams_info` (`sevenzip_parser.py:819`) already copies a folder digest onto the
member when a folder has exactly one substream. The folder-digest branch is a fallback,
not the main path.

## 2. Codec rejection is near-total, so compressed folders need no anchor

Fresh raw decompressors fed random bytes — what a wrong AES key produces:

| codec | did not raise | max output before the error |
| --- | --- | --- |
| LZMA2 raw | 7 / 20000 | 0 bytes |
| LZMA1 raw | 0 / 2000 | — |
| BZip2 | 0 / 2000 | — |
| DEFLATE raw | 6 / 2000 | 5 bytes |
| PPMd7 | 2 / 200 | 65536 bytes |

Scanning all 256 possible first bytes of a raw LZMA2 stream: only `0x00` avoids an error,
and it is a bare end marker producing zero output, so it trips the short-read check
anyway. liblzma's raw LZMA2 decoder requires the first chunk to set properties, which is
why the `0x01` uncompressed-chunk escape — the one path that could have emitted 64 KiB of
garbage cleanly — is rejected.

So one decoded byte *can* settle a rejecting codec, but confirmation still reads
`CONFIRM_PREFIX_BYTES` (64 KiB) of plaintext, not one byte — the same bound ZIP already
uses, and enough margin that a decoder change of a few bytes does not flip the
verdict. PPMd is the exception and is grouped with `Copy` throughout. Filters (Delta,
BCJ) do not reject; `MethodKind.LZMA_FAMILY` includes Delta and is the wrong
predicate. Deflate64, ZSTD, Brotli and LZ4 are unmeasured and stay with Copy until
the test in task 5.2 says otherwise.

**The output bound is only half of it.** 64 KiB of plaintext does not bound the compressed
input a block-transform codec pulls to produce it, because such a codec emits nothing until
it has consumed a whole block. Measured on incompressible data, asking for 64 KiB of
output:

| codec | output | input consumed |
| --- | --- | --- |
| bzip2 (block-transform) | 65536 B | **905216 B** — 13.8× the bound |
| LZMA2 (stream) | 65536 B | 69632 B — 1.1× |

So the probe carries a compressed-input cap as well. This is not a new number to invent:
`_probe_inner_tar` in `internal/detection.py` learned the same thing and sized
`_INNER_TAR_MAX_PROBE_BYTES` at 1 MiB for bzip2's worst-case 900 KB block. bzip2 in
practice rejects a wrong key at its `BZh` magic long before any of this matters — the cap
is for the codec added later that does not.

That overlap is also the maintainer's "consider merging with the probing logic" (#319 D1).
The shared primitive is small and exact: *decode up to N output bytes from at most M input
bytes — did the codec object?* `_probe_inner_tar` is that question with a `ustar` check
bolted on the end, and confirm is that question with nothing bolted on. Whether they should
share one helper is task 1.5.

## 3. The cheap key check rung

The ladder names a rung above the integrity anchor: an O(1) test that confirms the *key*
without decoding payload data. It is not new to the library, only to 7z.

| format | cheap key check | strength |
| --- | --- | --- |
| ZipCrypto | header verification byte | 2⁻⁸ |
| WinZip AES | 2-byte `pw_verify` | 2⁻¹⁶ (HMAC-SHA1 is the authoritative check) |
| 7z AES | none in the format | — |

Neither ZIP check reaches 2⁻³², so neither confirms on its own; both eliminate. Measured
member payload overhead confirms neither ZIP cipher pads, so neither has anything else to
offer: exactly 12 bytes for ZipCrypto (the encryption header; byte-wise stream cipher) and
exactly 28 for WinZip AES (16 salt + 2 `pw_verify` + 10 HMAC; CTR).

7z can be given one, from the zero padding at the tail of its AES-CBC packed stream.
That is `sevenzip-aes-tail-key-check`, split out of this change: it is the one piece
resting on an empirical premise about what writers put in those bytes rather than on the
format, so it gets its own review and its own revert. This change leaves the rung empty
for 7z and starts its ladder at the integrity anchor.

## 4. The partial-read hole, in both readers

Verification runs at EOF and an abandoned read never reaches it. For a cipher whose only
real verifier is the trailing CRC, that is not merely an unchecked checksum — the bytes
can be garbage that decrypted under the wrong key. Reproduced on both readers:

| | `read(1)` | full read |
| --- | --- | --- |
| ZipCrypto STORED, wrong password passing the byte check (found by brute force in ~300 tries, as 1/256 predicts) | **OK, returns garbage** | `CorruptionError: Bad CRC-32` |
| ZipCrypto DEFLATE, same | **OK, returns garbage** | `CorruptionError: Bad CRC-32` |
| 7z store+AES, wrong password, confirmation bypassed | **OK, returns garbage** | `CorruptionError: digest mismatch` |

WinZip AES is clean: `zip_aes.py:195` drains the remaining ciphertext so a short-read
caller still gets the HMAC checked.

`ENCRYPTED_MEMBER_UNVERIFIED` is the answer here, and it is the *cheaper* of the two
answers. See §7.

## 5. Module layout

`internal/password_confirm.py` grows the shared vocabulary. It stays free of format types,
as it is today.

```python
class ConfirmVerdict(Enum):
    REJECTED
    CONFIRMED
    INCONCLUSIVE

def plan_confirm(substreams, tail_crc, *, budget, min_verified_bytes=4) -> ConfirmPlan
def run_confirm_plan(stream, plan) -> ConfirmVerdict      # chunked; short read -> REJECTED
```

The candidate loop stays `_PasswordCandidates.attempt`. The probe returns a verdict
(or raises `EncryptionError` on `REJECTED`); `attempt` already owns known-good order,
provider reentry, and exhaustion. `CONFIRMED` promotes; `INCONCLUSIVE` promotes only
when the set is unambiguous. ZIP compressed confirm already goes through
`_finish_password_attempt` → `attempt()` and keeps its candidate-failure exception
filter (`BadZipFile` “Bad CRC-32…” / `zlib.error` / `lzma.LZMAError` / BZIP2’s
`OSError("Invalid data stream")`).

`plan_confirm` takes `(size, crc | None)` substreams: 7z passes its member digests, ZIP
passes one. It encodes the earliest-anchor, 4-byte, and D1-C late-CRC rules in one place.

7z's `confirm` becomes: tail check (next change), then build a plan, open the pipeline,
run the plan, map codec errors to `REJECTED` with `UnsupportedFeatureError` /
`PackageNotInstalledError` passing through unchanged.

Budget: reuse `CONFIRM_PREFIX_BYTES` (64 KiB of decoded plaintext). Do not invent a
one-byte confirm, and do not merge with `DetectionBudget` — confirm measures
decompressed output, detection measures source peeks. The 64 KiB figure is already
the ZIP compressed-confirm bound.

## 6. Rejected

**Lockstep candidate evaluation** — one pass over the packed stream feeding every
candidate, the shape `zipcrypto.parallel_plaintext_crc32` uses. Rejected, but not because
the residual is empty. After §1 the only case left that re-reads more than a few KiB is a
`Copy` or PPMd chain whose earliest anchor is at the folder end, with an ambiguous
candidate set: 200 MiB store+AES, four wrong candidates, 1.355 s against 0.337 s for one.
Lockstep is a poor fit for it anyway. Each candidate needs its own AES stage, decompressor
and CRC, so only the source read is shareable — 0.218 s of the 0.337 s marginal cost is
AES and CRC, which lockstep cannot touch — and the pipeline is pull-based, so running
candidates in step past the AES stage needs a tee with a bounded window or threads.

The counter-argument is that a seekable source is not necessarily a *cheap* source: a 7z
over a network stream or an external disk, or one larger than page cache, pays a real N×
read where these measurements pay almost none. That is a fair objection and it is why the
case is not dismissed outright — it is answered by `sevenzip-aes-tail-key-check`, which
collapses the same case to a 32-byte read at EOF rather than by sharing a full pass.
Reopen lockstep only if that check turns out not to apply to a real writer.

**Skipping 7z confirmation entirely when unambiguous, as ZIP does** — would make a wrong
single password on Copy surface as `CorruptionError` from the digest instead of
`EncryptionError` at open. The ladder still confirms Copy against a CRC when one
exists. For a rejecting codec the prefix *is* the confirm: a wrong key dies there, so
skipping a 200 MiB late CRC is not the same as skipping confirmation.

**Walking a late CRC on a rejecting codec** — D1-A. A wrong LZMA key is already dead
inside the prefix; the extra 200 MiB is the correct-password tax this change exists to
remove. Maintainer picked D1-C (PR #319).

**Merging the confirm budget with `DetectionBudget`.** Same order of magnitude
(detection grows 4 KiB → 32 KiB → 2 MiB; inner-TAR probe is 1 MiB). Different
quantities: confirm is decompressed plaintext, detection is source peeks. Reuse
`CONFIRM_PREFIX_BYTES`. A shared budget object is a later change if anyone wants one
number.

**`stream.verified` plus `verify()` instead of the diagnostic** — the better answer, and
under the placement clause it would displace the code rather than join it. Deferred:
`dev-docs/IDEAS.md` §API & ergonomics, with the password case recorded as the reason it
matters. Revisit the diagnostic when that lands. `ENCRYPTED_MEMBER_UNVERIFIED` stays
out of `ARCHIVE_INTEGRITY_CODES` until then (D2-C).

## 7. Why the diagnostic is admissible

`dev-docs/discussions/2026-08-diagnostics/diagnostics-archive-vs-usage.md` was resolved on
2026-08-11: the archive-versus-caller cut is rejected as an admission test and survives
only as description. Two clauses replaced it, and a code must satisfy both.

**Admission** — the caller knows they stopped reading; they cannot know from the declared
contract that the password behind those bytes was accepted on a check weaker than the
digest they skipped. They can act: read to EOF, pass one known password, or treat the
bytes as untrusted. This is also what forces the narrow trigger — firing on every partial
read would restate what the caller already knows, which admission refuses.

**Placement** — `open()` returns a stream, not a structured per-item report, and extraction
reads every member to EOF so the code never fires against `ExtractionResult`. No
return-value home exists today. When `stream.verified` lands, one of the two has to go.

## 8. Sequencing and open items

1. PR #318 merges first (memory bound, already green). This change assumes it.
2. This change.
3. `sevenzip-aes-tail-key-check` — the 7z cheap-key-check rung. Closes what this change
   leaves of threat-model **O12**: the `Copy`/PPMd, anchor-at-folder-end, ambiguous case
   that still re-reads the packed stream once per candidate. O12 stays open until then.
4. A seekable AES-CBC stream, as its own change, justified on member access rather than
   confirmation: `AesDecryptStream` (`crypto.py:143`) has no `seek`, so a late member of a
   store+AES folder is positioned with `skip_forward` over the whole prefix and
   `MemberStreams.SEEKABLE` cannot be honoured. Seeking CBC is block-aligned re-init with
   the preceding ciphertext block as IV, ~30 lines, and the source is always seekable for
   7z. It only yields plaintext offsets when nothing sits between AES and the payload, so
   it does not make LZMA folders seekable. Once it exists, `plan_confirm` can pick the
   **smallest** anchored member rather than the earliest, which matters for a
   `[200 MiB, 4 KiB]` stored solid folder.

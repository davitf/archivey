# Design — 7z AES tail key check

Measured 2026-09-09 in the Claude Code web container (`cryptography` 49.0.0,
p7zip 16.02, py7zr 1.1.3, Python 3.11).

## The structure being exploited

7z AES is AES-256-CBC over the **packed** (post-compression) bytes, with no MAC and no
in-format verifier. The AES coder's declared output length in `UNPACK_INFO` is the
unpadded compressed size; the pack size is that rounded up to the 16-byte block. The
difference is padding the decoder is expected to discard.

CBC decryption of one block needs only that block and its predecessor
(`P_n = D(C_n) XOR C_{n-1}`, with the IV standing in when there is only one block), so the
check reads 32 bytes at EOF of the pack view and runs one block decrypt. No folder decode,
no decompressor, no new stream machinery — `_folder_pack_view` already returns a seekable
`SharedSource` view.

## What is actually in those bytes

Decrypted with the correct key:

| writer | archives | result |
| --- | --- | --- |
| p7zip 16.02 (`7z` CLI) | 14 — `Copy` and LZMA2, payloads 1…1000 bytes, padding 0–15 | all zero, every case |
| py7zr 1.1.3 | 14 — same matrix | all zero, every case |

Two independent implementations agree, and py7zr follows 7-Zip's reference code. Spot
check on a 200 MiB store+AES folder: correct password gives `000000`, a wrong one gives
`1fb6bc`.

Cost against the full pass it replaces on that folder: **0.24 ms versus 261 ms**. The
0.119 s key derivation is paid either way and dominates both.

## Two rules that are not optional

**Confirm-only, never reject.** Zero padding is a convention. A reject-on-mismatch rule
would refuse the *correct* password against a writer that pads with buffer residue — an
unrecoverable failure, bought for a fast path, against a premise verified on two writers.
A non-matching tail therefore drops nobody: the ladder continues with every candidate, and
the cost of an unknown writer is one wasted 32-byte read.

**Nothing below 4 padding bytes.** The two failure modes compound. A residue-padding writer
means the correct candidate has a non-zero tail; at one padding byte a wrong candidate has
a zero tail 1/256 of the time. Together they drop the right password, run the anchor pass
on the wrong one, and report "all candidates rejected" — a hard failure manufactured by an
optimisation. Padding length is uniform over 0…15, so the check applies to 75% of
archives, is skipped for the 19% at 1–3 bytes, and has nothing to read for the 6% at 0.

## What it attests

The key, not the data. It SHALL NOT suppress `DIGEST_UNVERIFIABLE` on a folder with no
integrity anchor — those bytes are still unauthenticated. What it does add there is real
though: such a folder can now pick the correct password among several, which the reader
cannot do today (it accepts the first candidate blind).

## Rejected

**Ordering candidates by the tail at padlen 1–3.** Trying zero-tail candidates first drops
nobody, so it is safe, and it would turn an N-candidate anchor pass into a 1-candidate one
for 19% of archives. Left out to keep one rule for the check rather than two conditioned on
padding length, and because `bounded-password-confirmation` already made most of that
population cheap. Cheap to add later if a workload asks.

**Applying the same idea to ZIP.** Neither ZIP cipher pads. Measured payload overhead on
1 MiB stored members: exactly 12 bytes for ZipCrypto (the encryption header; the cipher is
byte-wise, ciphertext length equals plaintext length) and exactly 28 for WinZip AES
(16 salt + 2 `pw_verify` + 10 HMAC; CTR). Both already fill the cheap-key-check rung with
their own verifier. What generalises across formats is the rung, not the padding.

## Open

The premise rests on p7zip 16.02 and py7zr 1.1.3. Fixtures from Windows 7-Zip ≥ 21, WinRAR
or Bandizip would strengthen it. Not blocking — confirm-only means an unverified writer
costs a fallback — but it is what §"What is actually in those bytes" can claim.

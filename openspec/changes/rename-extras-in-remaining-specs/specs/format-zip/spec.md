## MODIFIED Requirements

### Requirement: Report ZIP format properties

The ZIP backend SHALL expose these properties for every opened ZIP archive:

| Property | Value |
| --- | --- |
| Backend dependency | `zipfile` (stdlib) for central-directory parsing; shared codec layer for member data |
| Listing cost | `ListingCost.INDEXED` — central directory read at open |
| Access cost | `AccessCost.DIRECT` — independent local file offsets |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only; no implicit buffering/spooling |
| Write support | Yes, including streaming write |

`reader.get()` and other name lookups SHALL use the central-directory-derived
member map without extra archive I/O. Unencrypted ZIP member data SHALL decode
through the shared `compressed-streams` codec layer (bounded local-header parse
+ slice + method-id dispatch), including extended codecs when their backends are
installed: Deflate64 (method 9, via `[recommended]`/`inflate64`), ZSTD (method 93), PPMD
(method 98, ZIP PPMd8 framing via `[recommended]`/`pyppmd`). A missing optional backend
SHALL raise `PackageNotInstalledError`. An unknown/unsupported method id SHALL
raise `UnsupportedFeatureError`. `format_availability(ZIP)` SHALL report FULL
when every optional ZIP member codec is installed, else PARTIAL with the missing
components listed. Encrypted members (ZipCrypto / WinZip AE) MAY retain a
separate decryption path.

#### Scenario: ZIP property matrix

| Case | Expected |
| --- | --- |
| Open valid ZIP | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| `reader.get("some/member.txt")` | Satisfied from the in-memory central-directory name map; no additional archive I/O |
| Member uses unknown method id | Listing succeeds; reading raises `UnsupportedFeatureError` |
| Deflate64/Zstd/PPMd member, backend present | Decodes via the shared codec layer |
| Deflate64/Zstd/PPMd member, backend absent | `PackageNotInstalledError` |
| Optional ZIP codecs all installed | `format_availability(ZIP)` is FULL |
| Streaming write without pre-known size | Local header uses data-descriptor placeholders; data descriptor stores final CRC and sizes; standard ZIP tools can read the result |

### Requirement: Read WinZip AES-encrypted members

The ZIP backend SHALL read WinZip AES (AE-x) encrypted members: compression
method 99 with the AES extra field `0x9901` giving vendor version (AE-1/AE-2),
key strength (128/192/256), and the actual underlying compression method.
Decryption SHALL derive keys via PBKDF2-HMAC-SHA1 (1000 iterations) over the
password and per-member salt (strength/16 bytes) into encryption key ‖
authentication key ‖ 2-byte verification value, decrypt with AES-CTR
(little-endian counter), and authenticate the ciphertext with HMAC-SHA1
truncated to 10 bytes. Decrypted bytes SHALL be decompressed through the shared
codec layer for the actual method.

A wrong password SHALL fail fast on the 2-byte verification value with
`EncryptionError` (no bytes returned). A ciphertext HMAC mismatch SHALL raise
at the terminal read (`CorruptionError`). AE-2 members SHALL surface no
`crc32` (the ZIP CRC is 0; integrity is the HMAC) and run no CRC check; AE-1
members SHALL surface and verify `crc32` in addition to the HMAC. AES
decryption requires `cryptography` (`[recommended]`); when it is absent an AE member SHALL raise
`PackageNotInstalledError` (detection still identifies the member as
AES-encrypted). Traditional ZipCrypto behavior is unchanged.

#### Scenario: WinZip AES matrix

| Case | Expected |
| --- | --- |
| AE-1 or AE-2 member, 128/192/256, correct password, `cryptography` present | Decrypts, decompresses via codec layer, HMAC verified at EOF |
| Wrong password | `EncryptionError` on the 2-byte verification value; no bytes |
| Tampered ciphertext, correct password | HMAC mismatch → `CorruptionError` at terminal read |
| AE-2 member | `crc32` absent; no CRC check; HMAC is the integrity signal |
| AE-1 member | `crc32` present and verified alongside the HMAC |
| AES member without `cryptography` installed | `PackageNotInstalledError`; still reported as encrypted |
| Traditional ZipCrypto member | Unchanged (existing weak-check confirmation path) |

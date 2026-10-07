## ADDED Requirements

### Requirement: Password lists for data with no password check

RAR3/4 file data records no password check value, and neither does a RAR5 encryption
record without a usable PswCheck. When the caller gives more than one distinct password
(or a provider) for an archive whose headers are not encrypted, the reader SHALL judge
the candidates for such a member by decoding it, under the shared confirmation rule
(`archive-reading`, "Confirm candidates when a weak check permits retries"), before the
member's own read starts:

- the **bounded probe** reads at most `PASSWORD_CONFIRM_PREFIX_BYTES` of the member's
  output from the decompressor; a program error, a wrong-password exit or output that
  ends short SHALL reject the candidate. A member that fits the prefix is checked against
  its CRC, which confirms;
- the **full check** reads the whole member and compares its CRC;
- a **stored** RAR 2.9+ member in one part SHALL be judged by decrypting it natively
  (AES-128-CBC under the key the password and the member's salt give) and comparing its
  CRC, with no external program: every wrong key decrypts a stored member to bytes of
  the right length, so only the CRC can tell.

In a non-solid archive each such member is judged on its own data. In a solid archive,
and for the pass over a whole archive, the candidates are judged once, on the first
encrypted member (solid) or the smallest one (non-solid). One distinct password, and the
header password of a header-encrypted archive, SHALL go to the decompressor unjudged, as
before. A member whose password was confirmed against its own CRC SHALL NOT emit
`ENCRYPTED_MEMBER_UNVERIFIED` when its read is abandoned.

#### Scenario: RAR3/4 password list matrix

| Case | Expected |
| --- | --- |
| Non-solid, compressed, `password=[wrong, right]`, member larger than the prefix | Every member reads correctly |
| Same, `wrong` survives the 64 KiB prefix | The full check rejects it; every member reads correctly |
| Solid, compressed, `password=[wrong, right]` | One resolution on the first encrypted member; every member reads, by `open()` and `stream_members()` |
| Stored encrypted member, `password=[wrong, right]` | Judged natively by CRC; no process started for the check |
| `password=[wrong1, wrong2]` | `EncryptionError` |
| One password | Handed to the decompressor unjudged, as before |

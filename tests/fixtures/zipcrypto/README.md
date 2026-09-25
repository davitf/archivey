# ZipCrypto fixtures

`check_byte_collision.zip` holds two ZipCrypto members written by Info-ZIP `zip` 3.0 on
Linux with the password `secret`: `stored.txt` (9465 bytes, STORED) and `deflated.txt`
(9448 bytes, DEFLATE). Both carry a data descriptor, so the check byte is the high byte of
the DOS time. Each member's text is 1500 random words from a ten-word list.

ZipCrypto checks one byte of the key before decrypting, so about one wrong password in
256 passes that check and then decrypts to garbage. These wrong passwords pass it,
found by brute force over `wrong0`, `wrong1`, ...:

| member | wrong passwords that pass the check byte |
| --- | --- |
| `stored.txt` | `wrong896`, `wrong961`, `wrong1117` |
| `deflated.txt` | `wrong173`, `wrong207`, `wrong357` |

The archive and the passwords are committed so the tests do not brute-force in CI. Used by
`tests/test_encrypted_member_unverified.py`.

```sh
zip -P secret -0 check_byte_collision.zip stored.txt
zip -P secret -9 check_byte_collision.zip deflated.txt
```

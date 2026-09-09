# archive-reading — bounded confirmation ladder delta

> Replaces the existing weak-check confirmation requirement with an ordered ladder and a
> three-valued verdict, and states what confirmation is *for*. The ZIP behaviour it
> describes is what ships today; 7z is what changes.

## MODIFIED Requirements

### Requirement: Confirm candidates when a weak check permits retries

When a format's password check can admit wrong values, a candidate SHALL NOT be
accepted or added to known-good on that weak check alone if another distinct
candidate may be tried.

**Confirmation is a rejection filter, not a proof.** Its job is to stop a wrong candidate
from shadowing a correct one; the authoritative digest still runs on the caller's own
stream. Confirmation SHALL therefore be bounded, and SHALL obey "Bounded implicit
temporary storage" — no plaintext buffering proportional to unit size, and no decode work
proportional to unit size where a cheaper signal decides the same question.

A confirmation step SHALL yield one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| `REJECTED` | this candidate is wrong; try the next |
| `CONFIRMED` | a signal of at least 2⁻³² strength matched; accept |
| `INCONCLUSIVE` | the candidate survived its budget without reaching a deciding signal |

Backends SHALL apply the strongest signal reachable within budget, in this order:

1. **Cheap key check** — an O(1) test that confirms the *key* without decoding payload
   data. Confirms only at ≥ 2⁻³²; SHALL NOT reject on its own where a format permits
   writers to vary the bytes it inspects.
2. **Integrity anchor** — a stored checksum over a decodable prefix of the unit. The
   **earliest sufficient** anchor SHALL be used, and a plan SHALL stop once checksum-verified
   bytes reach 4; a shorter verified prefix carries less than 32 bits and SHALL NOT
   terminate the plan on its own.
3. **Codec rejection** — for a compressed unit with no anchor in budget, decoding a
   bounded plaintext prefix. Surviving the prefix yields `INCONCLUSIVE`, not `CONFIRMED`.

When no signal is reachable within budget and the unit is not compressed, the backend
SHALL run the unbounded pass **only when the candidate set is ambiguous**, because there
is then something to disambiguate. With a single distinct static candidate it SHALL stop
at the budget and let the caller's read-time digest be authoritative.

Accepting an `INCONCLUSIVE` candidate SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` if the
caller then abandons the member's stream before its declared digest is reached.

After confirmation, backend MAY re-open/re-decode the accepted candidate for the
caller's stream. Returned stream SHALL keep ordinary read-time integrity checking.

"Another candidate may be tried" includes ≥2 distinct known-good/static values and
a provider that can return another answer. Provider stays lazy (no advance
enumeration). Duplicates are not distinct. Provider-raised `EncryptionError` is
provider failure — propagate unchanged, not as candidate exhaustion.

If confirmation fails and candidates are exhausted, report the irreducible
ambiguity (wrong password **or** corrupt unit). MAY use `EncryptionError`. SHALL
NOT return an unvalidated candidate. A single distinct static candidate MAY keep
the format's normal lazy streaming path.

#### Scenario: weak-check confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes weak check first of two | Reject via confirmation; stream from correct candidate |
| Large member, many candidates | Confirmation bounded — not proportional to member size |
| Provider answer fails confirmation | Request next answer without pre-enumerating; accept only after confirm |
| Anchor reachable within budget | Decode to the anchor only, never past it |
| Unit carries both an early per-item checksum and a whole-unit checksum | The earlier one decides |
| Checksum-verified prefix shorter than 4 bytes | Plan continues to the next anchor |
| Compressed unit, no anchor in budget | Bounded prefix decode; survivor is `INCONCLUSIVE` |
| Uncompressed unit, no anchor in budget, one distinct candidate | Stop at budget; caller's digest is authoritative |
| Uncompressed unit, no anchor in budget, ambiguous candidates | Unbounded pass; the matching checksum wins |
| Cheap key check matches for one candidate at full strength | `CONFIRMED` with no payload decode |
| Cheap key check matches no candidate | Ladder continues with every candidate; no candidate dropped |

# format-iso — fewer exception types

## MODIFIED Requirements

### Requirement: Declare ISO format properties

The ISO backend SHALL expose these properties for every opened ISO image:

| Property | Value |
| --- | --- |
| Backend dependency | `pycdlib` |
| Listing cost | `ListingCost.INDEXED` — directory tree in header/catalog region |
| Access cost | `AccessCost.DIRECT` |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only |
| Write support | No; ISO writing is out of scope |

Write attempts SHALL raise `UnsupportedFeatureError`. Non-seekable read
sources SHALL be rejected at open because `pycdlib` requires seeking; the backend
MUST NOT implicitly buffer or copy the image to make it seekable.

#### Scenario: ISO property matrix

| Case | Expected |
| --- | --- |
| Open valid ISO | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| Attempt to create/write ISO | `UnsupportedFeatureError` |
| Open from non-seekable source | Seekability error at open; no implicit buffering |

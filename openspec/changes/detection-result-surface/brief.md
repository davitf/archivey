# detection-result-surface — let callers see why archivey chose a format

**Status:** Ready to implement. Depends on the evidence-ledger change, and its handoff half on the prefix-workspace change. Blocks nothing. Breaking for anyone matching the detection-method string. Effort: medium.

**Why it matters:** The detection result is not merely unexposed, it is discarded — open archive reads four fields off it and drops the object. A caller gets the answer with no grade, no provenance, and no way to tell why an error says the format was unconfirmed. The diagnostics channel is asymmetric in a telling way: you can learn that the filename contradicted the bytes, because that is emitted as a warning, but never that it agreed, nor how strong the evidence was. Archivey's own command-line tool already pays for the gap — the info command detects the format, then opens the archive, detecting twice, because the reader will not tell it. On a pipe that workaround does not even exist.

**What it does:** The detection result becomes an always-present field on readers and streams, so it is readable without first checking whether detection ran. Where detection did not run, the ledger says so as declared evidence — the caller's own format argument, or a member codec the container itself declares, which inherits the container's strength rather than being ranked as a guess. Confidence and detection-method become derived properties rather than stored fields, so they cannot drift from the ledger they summarise. And open archive gains a way to accept a result you already produced, so you can inspect before deciding to open without detecting twice.

**Decided:** The detection-method value sfx-scan is renamed, because the tier that finds a real installer also finds a JPEG with a ZIP stuck on the end and a Python zipapp meant to be run rather than extracted — the name asserts intent the tier cannot establish. Passing a result through the format argument instead would launder a guess into a trusted assertion, which is why the new parameter is separate.

**Your call later:** The exact public field and type names, and how a caller reaches the evidence from an exception. The exposure is required; the spelling is not settled.

**Bottom line:** Last of the five, and the one that makes the other four visible.

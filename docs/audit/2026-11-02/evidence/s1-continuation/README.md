# S1 diagnostic evidence

Folders for runs that reached Railway preserve the three original RAW artifact byte streams (JSON
observation, detached SHA256 line, hosted JSON verification). Their artifact IDs,
SHA256, byte lengths, UTC times and original run metadata are in `provenance.json`.
The observation is a deliberately bounded safe projection, not a full raw Railway
response. No token value, provider message text, trace ID or temporary download URL
is included.

`root-verification.json` and `independent-verification.json` record independent
fresh verification while the observation was still within its admitted freshness
window. `independent-review.txt` preserves the terminal comment body exactly;
`review-reference.json` records its URL/timestamps/body digest. `request.json`
retains the immutable operation request needed to check request binding.

For reproducible historical verification, use `verifyObservation` from the exact
Ops executor SHA in the observation, not the latest source. Supply the original
observation bytes, detached line and expected digest. Use the preserved `run`
metadata, and exactly the observation+detached artifact pair (total_count=2), not
the later third verifier artifact. Construct the credential-free GitHub context
from the receipt's run/executor IDs and the fixed workflow/ref documented in the
pinned verifier. Call with `now = Date.parse(hostedVerification.verifiedAt)` and
compare the complete returned object with the hosted verification JSON.

This is historical replay of a completed observation, not a fresh Railway read,
current readiness proof, renewal of a spent grant or permission to rerun Actions.
The archived `expired:false` metadata describes the original retrieval; preserved
bytes remain checkable after the original hosted artifacts expire. Real provider
state and release prerequisites require their own newly authorized observation.

The `run-41444775597` folder is different: it records ADMISSION_FAILED before
Railway, with zero artifacts. Its full admission log, job/run metadata and terminal
independent verdict prove the separate zero-provider workflow attempt. There is
no observation receipt or applicable native observation verifier for that run.

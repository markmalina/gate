# Fixed maintenance intent ledger — unregistered source

Issue [#824](https://github.com/markmalina/gate/issues/824), predecessor
[PR823](https://github.com/markmalina/gate/pull/823),
[regulation](https://github.com/markmalina/ai/blob/main/WORK-RULES.md).
This component is not wired to any caller, workflow, runner or stop primitive.
No actual state branch, seed, token or live operation is installed by this change.
The product result, full text of one saved Watch note, remains unachieved.

## Fixed boundary and evidence

Only `https://api.github.com/repos/markmalina/gate/contents/ops-state/gate-maintenance/v1.json`
is addressed. GET supplies the fixed `ref=ops-state/gate-maintenance`; PUT supplies
that exact branch and the current existing blob SHA. Repository ID1293746267 and
the fixed Gate project/environment/service IDs are strictly encoded in the slot.
The proposed branch and file remain uncreated/uninitialized. Missing state fails
closed: this adapter cannot create the seed or bootstrap a branch.

[Official Contents API documentation](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)
read 7 November2026 requires the blob `sha` when updating a file and permits an
explicit branch. It lists 200 update, 201 creation, 409 conflict and 422 validation
failure. The source accepts only200 and never retries. This is optimistic
concurrency using GitHub's existing-file update contract, not an independent
proof of service atomicity, ACLs, persistence, replication or production safety.
The tests implement a synthetic CAS server; they cannot establish those facts.

Requests pin API version2022-11-28, JSON media type, no redirect and one15s
header-plus-body deadline. Responses are capped at64KiB; slot bytes at8KiB.
Response URL, path, branch HTML URL, blob URL, computed Git blob SHA, content
length, canonical UTF-8 bytes and commit identity URLs are checked where exposed.
The content metadata URL may be the exact endpoint with no query or with exactly
the fixed encoded ref. Other selectors are rejected. GitHub Contents responses do
not independently attest repository ID, caller identity or grant authenticity.
A future trusted caller must establish those, the current context and token scope.
No credentials or raw response/error bodies enter diagnostics.

## API for a future coordinator (not implemented here)

`createMaintenanceLedger({token, fetchFn, clock})` returns an inert object.
No defaults read fetch, environment, filesystem, credentials or wall clock.
Missing dependencies leave all I/O disabled. Token/fetch/clock injection belongs
to a future trusted caller with a separate owner gate; source approval grants no
contents:write permission or live access.

`observe()` performs one bounded fixed GET and returns `status:"OBSERVED"` with
`snapshot:{blobSha,content,digest,state}`. `content` is exact canonical JSON text,
`digest` is SHA256 of those UTF-8 bytes. Observation always has
`durableIntent:false, stopCapability:null`, including an existing STOP_INTENT.
A later observation revokes a previously issued local capability.

`transition({expected,identity,to})` requires the complete exact snapshot and
identity `{operationId,grantDigest,executorSha,runtimeSha,deploymentId}`. The
operation/deployment identifiers are UUID strings, grant digest is64 lowercase
hex, source SHAs are40 lowercase hex. It rereads the fixed slot, compares all
snapshot fields, sends at most one PUT using `expected.blobSha`, validates the
acknowledged blob and commit metadata, then reads back the fixed slot and requires
exact byte/blob/digest equality. There is no retry, ref mutation or generic URL.
Successful transitions return `status:"COMMITTED"`, snapshot and acknowledged
commit SHA. Both bounded reads count separately; success is GET+PUT+GET.

Only an own successfully acknowledged and exactly read-back STOP_INTENT returns
`durableIntent:true` and an opaque process-local `stopCapability` object.
`consumeStopCapability(object)` returns true exactly once for that identical
object, on that same adapter instance, before another observation/transition.
Wrong/copied objects, fresh instances, prior observations, failed writes and
quarantine cannot yield the capability. `durableIntent` is evidence of that
completed write/readback; it must never alone authorize stop. A future coordinator
must consume the capability and separately enforce all owner/target/provider
admission gates. There is no stop method in this adapter. A fresh process that
observes STOP_INTENT must reconcile separately and cannot replay stop.

Terminal results can be appended via the same `transition` method using the exact
current snapshot and identity. They do not mint capability or authorize a read or
mutation at another provider. Observed stopped state alone never upgrades prior
UNKNOWN_STOP/FAILED. HELD readback may instead remain a separate observation.

## One-operation latch and uncertainty

The future separately authorized seed is the canonical document
`{schemaVersion:"gate.maintenance-ledger/v1",target:<fixed IDs>,identity:null,history:[]}`.
This document is only a schema description; this source never writes it.
Allowed append-only transitions are EMPTY→CLAIMED→STOP_INTENT→HELD_STOPPED,
UNKNOWN_STOP or FAILED, plus CLAIMED→FAILED. Identity is introduced on CLAIMED
and cannot change. Each entry preserves the prior history and SHA256 of the
complete previous document. Times are canonical UTC instants and nondecreasing;
there is no expiry, unlock, clear, new-operation reset or terminal rewrite.
The bounded history allows at most three entries. Unknown/corrupt schema denies.

Local validation failures return DENIED without HTTP. Stale current snapshot,
HTTP conflict/422, missing state during transition, timeout, network loss,
malformed ACK or uncertain readback permanently quarantine that adapter instance.
QUARANTINED is nonretryable, never carries mutation capability and is not rewritten
to success. A lost ACK may leave STOP_INTENT durably present: observing it on a new
process still cannot mint a stop capability. A separate reconciliation contract is
required. In-process quarantine cannot survive process loss; the persisted latch
and fresh-process denial of existing intent are the cross-process protection.

Future installation must separately establish exclusive ACLs against branch/file
reset and destructive history changes, seed correctness, GitHub context, writer
concurrency and actual durability. Source tests do not establish these guarantees.
No actual ledger state or token was accessed to validate this source.

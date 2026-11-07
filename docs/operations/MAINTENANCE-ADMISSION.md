# Maintenance admission source predicate

Issue [820](https://github.com/markmalina/gate/issues/820), based on PR819
`70e5fff7577ea99539154a2bcb8750df388d2c38` / tree
`ec0ba30aea7ee81986713ffc0857b277135be281`.
This is `SOURCE_PREDICATE_READY` only. No caller, command registration, protected
environment, provider access, stop, receipt consumer or operation ledger exists
in this slice. Proposed `.github/workflows/ops-maintenance.yml` is **UNCREATED**.

`admitMaintenanceRequest({context, grantBytes, command, now})` returns frozen
consistency data or throws the single safe error
`GATE_MAINTENANCE_ADMISSION_REJECTED`. It does not read time, env, FS or credentials.
All context fields must come from a future trusted caller, independently of the
grant JSON. Matching owner IDs or digests alone authenticate neither origin,
consent, metadata freshness, evidence nor provider ACL. The caller must verify
those, enforce the exact grant/protected environment, and reconcile operation
history before any separately authorized provider read or effect.

## Exact v1 input

Grant bytes: UTF-8, 1..8192 bytes, JSON object, recursively lexicographically
sorted keys, compact JSON.stringify encoding, no BOM, whitespace or final LF.
Duplicate fields, alternate escapes/number encodings, malformed UTF-8, unknown
fields and type coercion reject. No self-digest field exists in the grant.
The command contains SHA256 of those exact original bytes:

`/ops-gate-maintenance hold expected-source-sha=<runtimeSHA40> expected-deployment-id=<UUID> grant-digest=<SHA256>`

The same exact command must be the trusted event's `commentBody`. One owner
comment containing unrelated text cannot authorize another grant.

Grant fields (all mandatory; no others):

| Fields | Exact value / type |
| --- | --- |
| schemaVersion, operation | `gate.maintenance-owner-request/v1`, `GATE_MAINTENANCE_HOLD_V1` |
| operationId | lowercase UUID, stable for the logical operation; never mint a fresh ID to bypass a prior FAILED/UNKNOWN/HELD |
| repositoryId, ownerId | numbers `1293746267`, `23397294` |
| repository, target | `markmalina/gate`, `gate` |
| projectId | `972b6ab7-988a-421c-abfa-425451ad714f` |
| environmentId | `a148b138-44b3-4349-9d4b-292880503055` |
| serviceId | `099cbf8c-1d94-43fe-8714-90725bf93998` |
| expectedDeploymentId, expectedSha | lowercase UUID and full lowercase SHA40 of the expected deployed source |
| sourceRepository | raw `markmalina/gate` or explicit historical `AlphaCenter/gate`; no other aliases |
| expectedExecutorSha | full lowercase reviewed executor/client SHA40 |
| evidenceSha256 | lowercase SHA256 of authenticated, fresh evidence; equals trusted context evidence digest |
| fullGateDowntime | Boolean `true`, explicit proposal covering all Gate downtime |
| notBefore, expiresAt | canonical UTC `YYYY-MM-DDTHH:mm:ss.sssZ`, positive window ≤15min; start inclusive, expiry exclusive |
| caps | exact numeric object exported as `MAINTENANCE_CAPS`; stop1, restart/deploy/config/STT/messages0, requests20, total600000ms, HTTP15000ms, body524288bytes, retries/redirects0, polls12, interval5000ms |
| contractSha256 | `08965380aa92e4ab19e5f1a57aa0d436cf365f73d3c2219ba1e80f9ebfb16a67` |

Contract digest preimage is the exact UTF-8 body (no added final newline) of
[815 C1](https://github.com/markmalina/gate/issues/815#issuecomment-6699598571)
read on 2026-11-07. Editing that comment does not silently change this pin.
No current short-lived grant is embedded in source or examples.

Context is a plain object of enumerable data fields (accessors, symbols,
unknown/missing fields reject). Mandatory fixed fields: `repository`,
`repositoryId`, `ownerId`, `actor`, `actorId`, `sender`, `senderId`,
`eventName=issue_comment`, `action=created`, `issueNumber=815`,
`isPullRequest=false`, `ref=refs/heads/main`, `runAttempt=1`,
`workflowRef=markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main`.
Owner login is `markmalina`; numeric owner/actor/sender IDs are `23397294`.
`sourceContextSha`, `currentMainSha`, `checkedOutSha`, `workflowSha`, `clientSha`
and `executorSha` all equal `expectedExecutorSha`. `runId` is a positive decimal
string ≤20 digits. `commentBody` equals the exact command; `evidenceSha256`
equals the grant digest for evidence. `now` is a separately supplied canonical
UTC string from a trusted clock; the predicate performs no clock read.

## Output boundary

Canonical source returns `recordedSourceBinding=null`, `sourceMatches=true`.
Historical source returns the exact frozen `gate.recorded-source-binding/v1`
record expected by PR819, including raw historical repository, fixed IDs,
expected UUID/SHA, evidence digest and derived `ownerGrantSha256`.
It retains `sourceMatches=false` and `HISTORICAL_GATE_ALIAS`; it neither rewrites
provider source nor claims release readiness/authentication. Returned grant,
caps, binding and result are frozen copies, independent of input mutation.

Attempt1 and a stable ID are input checks, not durable idempotency, concurrency,
inflight proof, runtime flags, replay safety or full WATCH_QUIESCE. Original
FAILED/UNKNOWN/HELD ledger and actual orchestration remain for later slices.

Validation: `node --test tests/ops/*.test.mjs` (offline synthetic inputs and
actual PR819 transport compatibility); no full Python suite needed locally for
this selected source snapshot. Hosted CI remains separate evidence.

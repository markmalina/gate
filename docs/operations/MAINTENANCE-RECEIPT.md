# Maintenance receipt byte contract — source only

[Issue822](https://github.com/markmalina/gate/issues/822),
[contract815/C1](https://github.com/markmalina/gate/issues/815#issuecomment-6699598571),
[work rules](https://github.com/markmalina/ai/blob/main/WORK-RULES.md).
This pure, unregistered consumer performs no provider, filesystem, environment,
credential or clock I/O. It creates no caller, workflow, runner or ledger.
It checks supplied bytes and context for consistency. A future trusted caller
must authenticate GitHub artifact origin and every supplied context/admission
fact. Hash matching does not authenticate an artifact or authorize downtime.
No result grants a stop, restart, deploy, release or WATCH_QUIESCE clearance.

## API

`verifyMaintenanceReceipt({receiptBytes,digestBytes,context,admissionRequest,originalHeld})`
returns a deeply frozen object with `schemaValid:true`, `result`, `success`,
`receiptSha256`, `sourceMatches`, `sourceRepoState`, and the safe parsed `receipt`.
Every rejection throws only `GATE_MAINTENANCE_RECEIPT_REJECTED` without input data.

`admissionRequest` is the actual PR821 `admitMaintenanceRequest` input, including
original canonical grant bytes, exact command, supplied trusted context and
canonical `now`. The consumer calls that parser. An arbitrary admission-output
JSON object is insufficient. This revalidation still proves consistency only.
Receipt start/end must be within the grant window; end cannot exceed supplied
`now`. No live grant is registered by either module.

`receiptBytes` and `digestBytes` are Buffer/Uint8Array copied on entry, bounded
to 32768/128 bytes. Receipt UTF-8 is fatal-decoded, with exact compact sorted-key
JSON serialization, no newline/BOM/duplicate keys/alternate escapes or numeric
spellings. The detached digest is exactly:

```text
sha256:<64 lowercase hex>  maintenance-hold-receipt.json\n
```

Here `\n` denotes one final LF byte. `context.outputDigest` is `sha256:<hex>`.
All schema records require exact own enumerable data keys, types and enums.
No coercion, unknown raw provider fields, arbitrary diagnostic strings or
secret-bearing payloads are accepted. Supplied context accessors are rejected.

## Exact schema

SchemaVersion is `gate.maintenance-hold/v1`; operation is
`GATE_MAINTENANCE_HOLD_V1`; recovery is `HOLD_NO_AUTORESTORE`.
The following grouped rows together enumerate all receipt keys:

| Receipt keys | Constraint |
| --- | --- |
| `schemaVersion,operation,operationId,grantDigest` | Version/operation above; exact PR821 operationId and grant-byte SHA256 |
| `repository,repositoryId,ownerId,target,projectId,environmentId,serviceId` | Fixed markmalina/gate,1293746267,23397294,gate and imported PR821 target constants |
| `expectedSha,observedSha,expectedDeploymentId,observedDeploymentId` | Expected pair equals admitted full SHA/UUID. Observed pair equals it only with a verified deployment observation; otherwise both null |
| `sourceRepository,sourceRepoState,sourceMatches` | Exact admitted raw repo/state/Boolean; historical AlphaCenter/gate remains HISTORICAL_GATE_ALIAS/false |
| `actorId,workflowRef,workflowSha,runId,runAttempt,executorSha,clientSha` | Exact supplied artifact context; owner23397294, proposed maintenance main workflow, attempt1; workflow/executor/client all admitted full SHA |
| `evidenceSha256,contractSha256` | Exact grant evidence and pinned C1 digest |
| `counters,stopAttempted,ack` | Counters below; strict Booleans tied to stop count; ACK implies attempted |
| `beforeFleets,afterFleets,deployment,stopped,observedAt` | Safe observations below; stopped Boolean or null; canonical UTC time or null |
| `result,phase,diagnostic,recovery,startedAt,finishedAt,originalReceiptSha256` | Result semantics below; elapsed0..600000ms; canonical24-character UTC times; original hash only for NOOP |

Artifact context has exactly `repository,repositoryId,ownerId,actorId,workflowRef,
workflowSha,executorSha,clientSha,runId,runAttempt,artifactId,artifactName,
outputDigest,outputResult`. Artifact name equals `maintenance-hold-receipt.json`;
runId/artifactId are decimal positive strings, at most20 digits. Artifact name is
the receipt artifact contract name, not an instruction to fetch any resource.
Context workflowRef is the PR821 proposed `ops-maintenance.yml@refs/heads/main`;
that workflow remains uncreated. Result/digest must agree with original bytes.

Counters have exactly `providerRequests,stop,restart,deploy,config,stt,messages,
retries,redirects,polls,maxHttpMs,maxBodyBytes,pollIntervalMs`, all safe nonnegative
integers. Bounds: requests≤20, stop≤1, polls≤12, HTTP≤15000ms, body≤524288bytes;
interval exactly5000ms; all other effects/retries/redirects zero. Stop count equals
attempted. Requests cover stop+poll counts; polling intervals and maximum observed
HTTP duration fit total elapsed time. Zero requests imply zero HTTP/body/polls.

Each fleet array has exactly two slots, each null or a complete safe observation.
Null is permitted as non-success evidence and always denies success where a
complete observation is required. A fleet has exactly `projectId,environmentId,
serviceId,complete,repository,source,sourceRepoState,sourceMatches,autoDeploy,
pendingChanges,numReplicas,activeDeployments,latestDeployment,observedAt,sha256`.
Target/source are exact; source has exactly `{repo,image:null}`; complete=true;
autoDeploy/pendingChanges=false; replicas1..8; active length≤1. SHA256 is the hash
of canonical UTF-8 JSON of this fleet with only `sha256` omitted. A digest proves
internal consistency, not completeness of an actual provider query.

Safe deployment entries have exactly `id,sha,status,stopped`, bound to admitted
UUID/SHA, Boolean stopped, and status SUCCESS|FAILED|CRASHED|REMOVED|SKIPPED|SLEEPING.
Active entries must be running SUCCESS. Latest is explicit null or an exact
entry, never a transient/foreign deployment. A running latest equals the active
entry. `receipt.deployment` adds exact projectId/environmentId/serviceId and
observedAt; it agrees with receipt stopped/observedAt. No observed deployment
means stopped, observedAt, observedSha and observedDeploymentId are all null.
Expected identifiers cannot masquerade as observations before the first read or
after an authentication denial. Partial-null observed identifier pairs deny.
Every observation time lies within receipt start/end.

| Result | Accepted schema semantics |
| --- | --- |
| HELD_STOPPED | success=true; ACK/attempted/stop1; two identical complete running before fleets, then two identical complete empty after fleets with exact stopped latest. Exact stopped deployment is chronologically between after fleets. At least9 requests; nonzero bounded HTTP/body evidence. No original proof |
| NOOP_ALREADY_HELD | success=true; attempted=false/ACK=false/stop0; before slots null; at least4 requests; same complete stopped after chain as HELD. Separately supplied original HELD proof mandatory |
| FAILED | success=false; no attempted stop/ACK/mutation; bounded safe diagnostic; phase cannot be complete; nullable observations do not imply success |
| UNKNOWN_STOP | success=false; exactly one attempted stop, ACK may be lost; diagnostic required. Even complete stopped observations never rewrite UNKNOWN as success |

Success phase is `complete`, diagnostic null. Other phases are input|authenticate|
fleet|inspect|stop|wait-stopped|readback. Non-success diagnostic has exactly
`code,category,httpStatus`; code is one of the fixed E_* values in the module,
category INPUT|AUTH|SCOPE|TRANSPORT|PROVIDER|TIMEOUT|READBACK|BUDGET and HTTP status
null or integer100..599. Raw errors, exception messages and arbitrary payloads
have no representation. Drift/malformed observations are rejected; a future
producer can use null safe observation slots plus an allowlisted diagnostic.

For NOOP, `originalHeld` has exactly `{receiptBytes,digestBytes,context}` and is
validated once under the same actual admission. It must be HELD_STOPPED with its
original hash equal to `originalReceiptSha256`, same operation/grant/target/
UUID/SHA/raw source/executor/client, and finish no later than new receipt start.
Original HELD cannot itself contain a proof, so chains cannot recurse. Original
FAILED/UNKNOWN/NOOP are denied as proof. Caller bytes are never mutated. For all
other results originalHeld and originalReceiptSha256 must be null.

## Verification and remaining boundary

Run `node --test tests/ops/*.test.mjs`. Tests exercise the real byte/context API,
including rehashed semantic attacks, original-proof/provenance mismatches,
raw UTF-8/serialization failures and validation with I/O capabilities denied.
Synthetic fixtures are neither live observations nor owner consent. Durable
at-most-once ledgers, atomic locking, authenticated origin, full sequencing,
writer/inflight proof and full maintenance acceptance remain future work.

# Gate745 independent Ops control — prepared, not released

Gate owns `.github/workflows/gate-snapshot-cutover.yml`, control issue745,
`gate-production` and `agent-ops.gate-snapshot.lock.json`. The preparation pins
[Ops364](https://github.com/AlphaCenter/ops/pull/364) at
`c0e9087fc4d19c4512e2dc1ddba567bd42c0e5ae`. All action references are immutable.
This is reviewable source, not a runtime installation or source ACCEPT.

The standalone v2 profile inherits no AQUB manifest, caller, control issue,
workflow or credential. It preserves fixed Gate/PostgreSQL/volume identities,
whole-fleet/DB/backup bounds, schema0116 and same-source deployment. The adjacent
release is disabled: source PENDING, reviewed source null, fixture PENDING,
allocation null. Caller/profile/workflow/release admission precedes credentials.

The ordinary deploy's source-access repair in Ops363 uses its own workflow
token. Snapshot cutover additionally authenticates private Ops CI and uses a
distinct App requester, so its existing protected Gate App binding remains
required. Do not copy AQUB secrets, provision keys in an agent session or use
a provider dashboard. These two release paths have different dependencies.

## Commands and effects

After independent source ACCEPT, exact merged Ops pushCI/Security and separately
reviewed release-only observation admission, repin the client and recompute
workflow/profile byte digests. The preparation pin cannot authorize observation.

- Mark's exact `/ops-gate745 observe` on745 produces only OBSERVED_NOT_RELEASED,
  applyPlan=false and mutationEnabled=false. It never creates a usable plan,
  including after a later mutation release is enabled.
- `/ops-gate745 request-plan expected-client-sha=<full current Gate main>`
  invokes the App only after exact source/owner/control admission. Only the
  fixed App actor creates the canonical plan. Issue-write access is Gate-only.
- A real plan additionally requires accepted native disposable fixture,
  allocation policy, exact merged guarded source and enabled release. Mark
  then separately supplies the exact apply command with plan-run, artifact and
  detached digest. No automatic digest approval exists.
- The protected live observer performs no mutation. A separate terminal job
  has no protected environment, provider key or mutation imports and checks
  exact same-run artifact IDs/digests/time/source and full runtime evidence.

Both Gate production workflows share gate-production-deploy concurrency with
cancel-in-progress=false. Never race Gate751. Stable failed admission/preflight
receipts retain only redacted codes. FAILED/UNKNOWN does not establish readiness.

## Bootstrap and real prerequisites

This client is prepared in guard PR747, not installed on main. It cannot provide
the pre-merge proof required by that same PR. A runtime-unchanged Gate-owned
requester bootstrap therefore needs separate infrastructure review/installation,
coordinated with the active Gate751 lane. Do not merge the migration candidate
just to make its preflight workflow available. Do not claim this template ran.

The dedicated fixture observation failed E_FIXTURE_TOKEN_MISSING before fetch
(Delta282). An authorized operator must bind an actual isolated target and its
protected token. Source/hosted PostgreSQL checks or guessed IDs cannot replace
native fixture acceptance. Missing protected bindings remain real dependencies.

Before guard merge require source acceptance and fresh actual autoDeploy=false.
Before migration require reviewed writer exclusions, fixed DB/volume proof, all
old deployments stopped and one completed locked bound backup. Deploy guarded
false/schema0116, verify, then sameSHAtrue and independent verification. Unknown
journals remain intact. Check accessible MCP fields and the exact authorized
product scenario only after release. No private dialog/Telegram call is granted.

The owner745 body digest is fixed by the profile; body edits require a separate
reviewed repin. Keep progress in comments. This source does not grant credential
provisioning, acceptance or a broader operation.

# Gate745 independent control bootstrap

This source infrastructure stage prepares the Gate-owned control transport before the
dialog guard and schema0116 in PR747. It changes only this runbook,
`.github/workflows/gate-snapshot-cutover.yml` and
`agent-ops.gate-snapshot.lock.json`. It changes no application, migration,
Railway configuration or runtime source relative to current main. Gate745 remains the implementation outcome;
this bootstrap is one separate execution/branch/PR under the owner continuation
[authorization](https://github.com/AlphaCenter/gate/issues/745#issuecomment-6094199303).

The dedicated lock identifies the immutable Ops observation-release commit,
exact profile path and raw profile/control-workflow SHA-256 digests. Every Ops
action reference must equal that lock commit. Gate checkout uses the event's
exact main commit; the executor verifies current main again before provider
access. A changed pin, workflow, profile, actor, control issue or owner body
fails closed. Do not treat an installed workflow as operational acceptance.

## Installation and scope

Require independent source acceptance of the immutable bootstrap head, its
exact-head checks and a fresh duplicate/claim/active-run check before installation.
Only the root coordinator merges after those checks and watches exact main,
deployed source and redacted health. Current main differs from the observed
Production revision `8e6b870e5038140a8654eb6bd9a52106ca2362f5`; actual autoDeploy
is unproven. Even a source-only main merge can cause a provider autoDeploy.
Do not call this runtime-unchanged relative to Production or claim no deployment
effect without a timestamped exact provider/runtime observation.
The Ops source and release-only observation admission must each be independently
accepted, merged and successful in actual merged-push CI/Security. Mutation
remains disabled: `enabled=false`, native `fixtureAcceptance=PENDING` and
`allocationPolicy=null`. Source acceptance alone changes none of those gates.

Both this workflow and the existing Gate751 deploy route share
`gate-production-deploy` concurrency with `cancel-in-progress=false`. Coordinate
the exact stage with Gate751; never race an active operation. Installation must
not merge guard PR747 to obtain its own pre-merge proof. The source bootstrap
does not authorize a requester command or a provider observation by itself.

AQUB is frozen under `aqub.frozen.gate-independent.v1`. Old native Ops skill
instructions naming AQUB control72, workflows or credentials are superseded.
Use only `AlphaCenter/gate`, control745, protected `gate-production` and its
reviewed independent Gate profile. No AQUB operation, secret copy, provider UI
or direct API fallback, private-dialog read, model call or Telegram action is
part of this bootstrap.

The owner-only attestation is separate from scoped runtime evidence: Mark's
[attestation](https://github.com/AlphaCenter/gate/issues/745#issuecomment-6068183534)
and later745 comments6068573386/6077494786 record `owner_only_mode=true` and
`non_mark_product_users=0`. A fresh scoped status for exact `mark-personal`
reported Production `8e6b870e5038`, `send_enabled=true` and two accounts; it
does not prove a global product-user census. This source execution reads no
private payload and performs zero model calls and zero Telegram actions.

## Request and receipt boundary

Only after separately authorized operational gates pass:

- Mark's exact `/ops-gate745 observe` comment on745 can produce
  `OBSERVED_NOT_RELEASED`, `applyPlan=false`, `mutationEnabled=false`.
  Observation is permitted without a native fixture only after source,
  protected caller/credential and current-source admission; it is never usable
  as an apply plan and cannot establish native fixture acceptance.
- Mark's exact `/ops-gate745 request-plan expected-client-sha=<full current main>`
  invokes the distinct App requester. Only the fixed App actor posts
  `/agent-ops v1 plan operation=gate-snapshot-cutover target=gate-production`.
  With the pinned disabled release, that route may only return observation;
  it cannot create a usable mutation plan.
- Plan/apply additionally require accepted native disposable cutover fixture,
  complete reviewed allocation policy, enabled release, accepted exact guarded
  source and a fresh immutable digest-bound plan. Mark separately approves the
  exact plan run/artifact/digest. There is no automatic digest approval.
- After a successful separately authorized apply, the protected live observer
  emits only `OBSERVED`. The final pure verifier has no protected environment,
  provider token/private key or mutation imports. It authenticates exact
  same-run artifact IDs, source/actor/attempt/time provenance and detached
  raw-byte digests before `VERIFIED`.

Accept only schema-valid redacted receipt bytes with checked artifact provenance
and digests. A green workflow, logs, source merge or CI is not provider/runtime
or product readiness. Keep stable admission/preflight failure codes; stop on
FAILED, UNKNOWN, drift or missing bindings rather than retrying unchanged state.

## Historical protected bindings at initial preparation

Historical prerequisite state at preparation (10 October 2026); the dated successor below supersedes credential and diagnostic status, without implying release readiness:

| Dependency | Evidence and required resolution |
| --- | --- |
| Gate Railway credential | Gate751 run37979418103 failed with `E_AUTH` at auth-input because protected `RAILWAY_TOKEN` was absent in that job. This proves missing input there, not deletion or revocation. An authorized operator must prove the Gate-owned binding; never read/print/copy secret values. |
| Fixed Production target | The Gate profile fixes project `972b6ab7-988a-421c-abfa-425451ad714f`, environment `a148b138-44b3-4349-9d4b-292880503055`, Gate service `099cbf8c-1d94-43fe-8714-90725bf93998`, PostgreSQL service `561d3da3-ddb5-4d3d-8fa2-c9454b0996bf` and volume `7d2de9ed-c63e-454a-811e-4f206f3dfef3`. Protected credential scope and actual bindings must match; these Production IDs are not fixture identities. |
| Snapshot App binding | App4578389 and `AGENT_OPS_REVIEWER_PRIVATE_KEY` binding in Gate protected environment remain unproven. Requester token must have issue-write on Gate only; executor token needs contents/actions-read on Ops and Gate. Ordinary Gate751's ephemeral source token does not prove snapshot access. No credential provisioning is authorized here. |
| Native cutover fixture | Actual isolated Gate/PostgreSQL/volume/health identities and protected fixture token have not been supplied. Historical fixture failed `E_FIXTURE_TOKEN_MISSING` before fetch. The full native cutover acceptance is absent; backup-only rehearsal, hosted PostgreSQL or source tests cannot count as ACCEPT. Never infer IDs or adopt Production as a fixture. |
| Production pre-merge proof | Actual fresh whole-fleet evidence including `autoDeploy=false` is still required before guard747 merge. Missing or unauthenticated observation is not proof. Historical `E_FLEET` does not establish the root cause for a new Gate-owned receipt. |

For the later migration stage, require reviewed writer exclusions, exact
physical DB/volume proof, all old deployments stopped, one completed locked
bound backup, guarded-false/schema0116 verification, then same-SHA true and
independent runtime/MCP/product acceptance. Preserve unknown journals. Those
effects and their owner gates remain separate from this source installation.

## Integrity and later guard integration

The owner745 body is pinned to
`sha256:d8ae2e0e680c3876dd9baf872e5972e868e3b9b83aa7d2b449633f8935922d61`.
Keep progress in comments; do not edit that body or repin its digest in this
stage. This bootstrap starts from main
`afe0c6b8519381a0007c0dcc1c145207b5d54a3f` and derives its workflow/lock from
accepted guard candidate `dc83dac73238100ab9725b968ea3c4a5df4a969c`.

After installation PR747 will overlap the workflow and lock with its historical
preparation pin. Preserve the installed reviewed bootstrap values during later
integration; do not restore the obsolete pin. A changed guard candidate needs a
new independent Delta attempt and exact-head verification. This bootstrap's
runbook is separate from PR747's `GATE745-OPS-CONTROL.md`; the old prepared pin
in that document must not be mistaken for the installed contract. Main source
movement and source acceptance remain distinct from actual runtime release.

## Diagnostic client admission successor — 11 October 2026

This separate client source stage under existing Gate745 succeeds bootstrap754.
It keeps guard PR747 frozen at `5991635d53af96ec97819d5bec75a543c8ac55f6`.
Only this runbook, the four immutable Ops action references in the control
workflow, and the client lock commit/workflow-byte digest change.
Installed source and guard source remain distinct; root alone may merge this
client stage after independent acceptance and exact-head checks.

The locked Ops observation release is
`739a9e1c5703e0806d774bed824a50bebf8d40f2`, admitted by
[Delta292](https://github.com/AlphaCenter/delta/issues/292#issuecomment-6109087052).
Its independently reviewed executor source is
`6ba1bdb71b438ea85ecb097eb9c405cb08db6ef0`.
The exact profile bytes, fixed target, original owner745 body digest, caller,
distinct App/owner identities, command grammar, protected environment and
shared production concurrency remain unchanged. Mutation remains
`enabled=false`, `fixtureAcceptance=PENDING`, `allocationPolicy=null`.
Require successful original exact-source Ops CI/Security and actual merged-push
checks, plus independent Gate client acceptance and installed main, before
ONE changed-source protected observation. Never execute an unreviewed branch.

[Protected run38137606030](https://github.com/AlphaCenter/gate/actions/runs/38137606030)
authenticated the App/source and fixed Railway credentials on 11 October.
Its original authenticated failure receipt
[Gate745 proof](https://github.com/AlphaCenter/gate/issues/745#issuecomment-6108877537)
stopped at `E_PREFLIGHT_FLEET_PENDING_COUNT_SHAPE`; the raw276-byte digest is
`sha256:2fbbd27b65e3d19d2ae3636687007ef44e576538b51cde40730dceca99d7b387`.
The field concerns unapplied environment configuration, not queued processes.
That receipt does not disclose the actual value or distinguish unavailable
from invalid shape. The successor returns stable denials
`E_PREFLIGHT_FLEET_PENDING_COUNT_MISSING` for an absent field and
`E_PREFLIGHT_FLEET_PENDING_COUNT_NULL` for a null field; other invalid shapes
remain `E_PREFLIGHT_FLEET_PENDING_COUNT_SHAPE`.
It exports no provider value, type, payload or credential, grants no new query
and never interprets missing/null as zero. The actual cause remains UNKNOWN
until a fresh authenticated original receipt proves it. No unchanged retry.

Scoped status at 11 October12:08Z reported Production `afe0c6b85193`,
`send_enabled=true`; separately verified LKG is
`afe0c6b8519381a0007c0dcc1c145207b5d54a3f`/schema0115.
This proves only the returned Gate workspace/service, not global users,
fleet, deployment/source policy or `autoDeploy=false`.
Actual full active/latest fleet, autoDeploy, DB binding, volume and allocation
proof remain required. A source-only main merge may auto-deploy its source;
root must freshly observe exact revision/redacted health before and after
installation rather than claim zero runtime effect.

Native full isolated fixture identities, exact effect/cost/operator authority,
real whole-cutover fixture ACCEPT, writer/allocation policy and adjacent
baseline/candidate refresh remain pending. Historical8e6/303 release values
do not authorize the current LKG/candidate. No usable plan, apply, backup,
migration, configuration, deployment, credential provisioning or AQUB action
is authorized by this client stage. Guard747 may merge only after its separate
source and fresh whole-fleet `autoDeploy=false` gates pass.

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

## Unresolved protected bindings

Observed prerequisite state at preparation (10 October 2026):

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

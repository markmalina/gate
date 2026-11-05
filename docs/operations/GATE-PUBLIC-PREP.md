# Gate789 — public Gate / private Ops source preparation

Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md
Task: https://github.com/markmalina/gate/issues/789
State as of 2026-11-05: **SOURCE REGISTERED / deployment NEW_UNRELEASED / DEFAULT OFF**.
PR790 registered this package in main. Its original PR CI passed; the subsequent
main backend CI was cancelled during apt installation. One read-only history scan
completed and its findings were source-triaged; no application was deployed.
See the [durable checkpoint](https://github.com/markmalina/gate/issues/789#issuecomment-6666029141)
and [Delta515 source triage](https://github.com/AlphaCenter/delta/issues/515#issuecomment-6666171645)
for exact source, runs, limitations and cost. This does not declare publication ready.

## Exact source and minimal choice

Gate base: `062ffdd44bfc0780d5940203f8584a7edb117f97`.
Ops observed main: `cf849c8eddc50035ad57e711610b3dd4578c9ae5`.
Upstream executor: Ops `02ef5f1c6f4273ca2a3194643383d646dbf37648`.

Only two runtime files are vendored from that exact accepted upstream:

| Upstream path | Git blob |
| --- | --- |
| agent-ops/actions/gate-deploy/action.yml | 4cbf96b5fb61f8e215e54cc4e3eded4ae3e95e60 |
| agent-ops/actions/gate-deploy/gate-deploy.mjs | a9abe2b7a8afe462d941f35764bb859b51dd537a |

They use Node built-ins and the existing pinned setup-node action; no private Ops
module or npm dependency is imported. The 24 upstream synthetic tests are copied
with only import/identity fixture adaptation. Additional tests cover new provenance,
literal OFF, quarantine and safe scanner reporting. The rest of Ops is not copied.

### Why not execute inside private Ops now?

All 24 Ops workflow files at observed main were read. There is no registered
Gate751-to-private-Ops launcher with the current owner-command provenance.
GitHub issue_comment events belong to their own repository. Gate's ephemeral
contents:read token is not a cross-repository dispatcher credential. Existing Ops
release-aqub-gate is explicitly FROZEN; its old AQUB/staging tokens and authority
cannot become a Gate-only production binding. Ops gate781 has a different
disposable token binding. Existence of any secret name does not prove its value or
target scope.

The fixed production credential is currently referenced only through the Gate
protected environment in this route. Moving execution would therefore require
new launcher/admission/source authorization and evidence of a permitted credential
binding. This preparation does not invent those capabilities or copy credentials.

Private Ops still supports Gate by maintaining/reviewing the narrow source and
proposing exact reviewed updates to the vendored copy. Runtime execution of that
copy would be in Gate. Do not describe this as runtime execution inside Ops.

## New source contract

The job in ops-deploy.yml is unconditionally OFF with a literal false expression.
There is no environment variable, input or comment that activates it. Activation
is a separate reviewed source change and does not inherit any expired release grant.

When eventually activated after review:
- The same Gate repository and immutable repository ID, owner login/ID, main ref,
  original Gate751 command grammar or existing manual input, and attempt 1 remain.
- Checkout uses exactly github.sha and no persisted token. Workflow SHA and clean
  tracked checkout must match it before the local action runs.
- The local executor derives its repository/SHA from Gate contexts. It never
  fabricates the former github.action_repository/action_ref of a remote Ops action.
- The new receipt discriminator is gate.local-deploy/v1; it is deliberately not
  the historical agent-ops.fast-deploy/v1. Old receipts remain unchanged. External
  consumers of the old version are not declared compatible; a future activation
  must explicitly admit the new version. Outcome values and artifact names remain.
- The receipt distinguishes executorRepository=Gate / executorSha=Gate SHA from
  upstreamRepository=Ops / upstreamSha=02ef. Provider and receipt outcome behavior,
  fixed project/environment/service, ephemeral source token, CURRENT_MAIN equality,
  bounded responses and no retry remain.
- The shared concurrency group is retained. It serializes jobs and attempt 1 rejects
  reruns. It does NOT deduplicate two different original commands; operational
  claims and historical UNKNOWN reconciliation remain necessary.

No production runtime, model, database or provider configuration is changed.
A release still needs the exact owner grant, preflight/current fleet, accepted
source/CI, recovery, budget/time and unknown-effect checks required by Gate rules.

## Complete direct private-action inventory at the Gate base

Seven Gate workflows were inspected; five direct private Ops uses occur in two files:
- ops-deploy.yml:69 — gate-deploy@02ef; replaced by the local two-file package.
- gate-snapshot-cutover.yml:44 — github-app-requester@b099.
- gate-snapshot-cutover.yml:110 and :180 — gate-snapshot-cutover@b099.
- gate-snapshot-cutover.yml:234 — gate-snapshot-artifact-verifier@b099.

All four S1 jobs are explicitly literal OFF. Their historical references remain
for traceability; this is quarantine, not compatibility. The existing snapshot
lock is intentionally unchanged and cannot validate the modified workflow bytes.
S1/#781 stays NOT_ACCEPTED. No snapshot observation/request/apply or verifier run is
authorized. A future S1 route needs its own supported public/private contract.
We have not hosted-verified GitHub's skipped-job handling for those private uses.

## Manual read-only history scan preparation

Ordinary local git access to private Gate is unauthenticated. Connector source
reads work; that does not establish a full local history scan. The prepared
history-secret-scan.yml provides one future read-only workflow after review:
- workflow_dispatch only, exact Gate main SHA/owner/repository/attempt 1;
  contents:read; no protected environment, production secrets, App key or runtime.
- checkout fetch-depth 0 and verify non-shallow source. A local bare mirror in
  RUNNER_TEMP preserves the fetched refs and avoids source/.gitleaksignore.
- gitleaks 8.30.1 archive is checksum pinned to the already verified Ops lock:
  551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb.
- forced default-rule config, empty ignore file and --ignore-gitleaks-allow.
  No repository baseline/config suppressions are accepted. Built-in rule
  heuristics/allowlists remain part of the scanner, not a proof of all secrets.
- explicit -m history diffs include additions introduced only by merge commits;
  a four-commit synthetic regression distinguishes the former omitted diff stream.
- scanner output and raw JSON stay only in RUNNER_TEMP; logs/artifacts receive
  a whitelist projection: result/count, allowlisted class, validated commit SHA,
  line numbers, path hash, ref/commit counts and a fetched-refs digest.
- partial scan, timeout, missing/malformed report, nonzero tool failure or
  inconsistent finding/exit status yields INCOMPLETE, never NO_FINDINGS.
- genuine JSON null with successful tool exit is normalized as an empty findings
  slice; missing or invalid JSON is separately rejected.

Primary exact-version evidence:
https://github.com/gitleaks/gitleaks/blob/v8.30.1/cmd/root.go
https://github.com/gitleaks/gitleaks/blob/v8.30.1/cmd/git.go
https://github.com/gitleaks/gitleaks/blob/v8.30.1/report/json.go

### Launchability and limits

The reviewed workflow was registered in main and dispatched once on 2026-11-05;
the checkpoint above records its exact SHA and original run. No hidden PR/push
trigger is added. Another scan requires a concrete new reason and controller
admission; the existing receipt does not authorize a replay.

Coverage is the fetched Git branch/tag refs and reachable commits, with counts and
digest in the receipt. It does not include un-fetched closed PR refs, unreachable
objects, Git LFS object payloads, issues/comments/attachments, prior Actions logs
or artifacts, runtime variables, or credential validity. Use the original scan's
receipt and scoped triage; neither replaces those unexamined surfaces.
NO_FINDINGS is not universal history/private-data clearance.

## Migration and recovery order

1. Independent SOURCE review of this exact local package; preserve immutable
   terminal Delta attempts. Agree scanner registration and held release OFF state.
2. Only after a concrete next authorization: push/review/check the exact candidate.
   The Gate and Ops AGENTS notes are paired source-boundary documentation, not
   operation authorization.
3. Register/run the one reviewed scanner; investigate only safe locations/classes
   and distinguish real credentials from synthetic fixtures without printing values.
   Publication review separately covers issues/comments/attachments and logs.
4. Before changing visibility, confirm effective branch/check/environment/access
   settings with adequate read access. Do not infer protection from an environment
   name. Root observed no environment branch restrictions; this package therefore
   retains its own source owner/main/ID checks. Public fork CI must not receive
   deployment credentials.
5. Change visibility only under its exact owner authorization. Source identity,
   repository ID, URL, history and production source stay the same; this does not
   fix historical Railway BAD_USER_INPUT or prove exact provider source access.
6. Keep deployment OFF until separately reviewed activation and a new exact live
   release grant. Do not replay any historical UNKNOWN or call STT as a test.

Before visibility, source rollback may restore the old private-to-private
workflow from the recorded base. After visibility, restoring that workflow would
restore the incompatible private-action dependency: keep the new route OFF and
prepare a reviewed correction, or seek an explicit visibility reversal decision.
This is a source rollback, never a destructive DB/runtime rollback.

W1/#736 remains the functional goal: one full transcript in one private Mark issue.
Publication preparation is not its product acceptance and must not grow into
a general S1/support/resources project.


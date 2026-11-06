# Gate Work instructions

## Required current context for every Gate task

Before working on any Gate issue, read [docs/GATE-CURRENT.md](docs/GATE-CURRENT.md),
[the recovery program](docs/GATE-RECOVERY-PLAN.md), its active sprint/latest receipt,
and [AI WORK-RULES](https://github.com/markmalina/ai/blob/main/WORK-RULES.md).
Program [#787](https://github.com/markmalina/gate/issues/787) is the current coordination
pointer for Mark's Watch, Calls and reply-freshness routes. Read historical audits
only when needed for the current question; dated snapshots are not fresh authority.

Before implementation, the issue records one user outcome, dated baseline, target,
scope and responsible execution. Keep material findings/errors/decisions and
CI/runtime evidence in that issue as work progresses. Report actual before/after,
latency sample size, incomplete cases and cost/time under
[the metrics contract](docs/GATE-METRICS.md). Source, CI, merge, installation and
product acceptance are separate states. No heartbeat-only repository writes.
This documentation adds no provider, deployment, data or message authority.


Canonical company policy and ownership live in
[AlphaCenter/center](https://github.com/AlphaCenter/center). This repository
owns Gate transport/runtime implementation; Center coordinates portfolio,
requirements, handoffs and acceptance without gaining Gate mutation authority.


### Model access boundary

For every server-side model or transcription change, read the pinned
`use-model-access` skill, `model-access.lock`, and the exact project policy
named by that lock before mutation. A missing or mismatched pin, provider,
endpoint, model, retention rule, cost ceiling, timeout, retry count or output
limit fails closed. Credentials remain server-side, provider calls remain
default-off, and real provider smoke requires the exact owner gate recorded by
the current Work.

### AQUB frozen; independent Gate

Owner decision [Center #152](https://github.com/AlphaCenter/center/issues/152) activates `aqub.frozen.gate-independent.v1`
([boundary](docs/aqub-boundary.v1.json)).
AQUB is frozen historical source. Gate must not use its repository as a routine
dependency, forward messages/media to it, initialize its provider, run its
worker or product acceptance, or request any operation through AQUB issues,
workflows or credentials. Old routes/grants, including Gate745, are superseded.
Historical contracts and migrations do not authorize reactivation.

### Public visibility preparation — Gate789, NEW_UNRELEASED

This source package prepares a Gate-local copy of the narrow Ops Gate-only executor.
Its upstream is Ops `02ef5f1c6f4273ca2a3194643383d646dbf37648`; its actual executor
revision is the checked-out Gate workflow SHA, recorded separately in receipts.
In this unmerged PR797 activation candidate (#799), `.github/workflows/ops-deploy.yml`
removes only the literal OFF gate; owner/main/SHA/target/environment/token and receipt
checks remain. Main still has literal OFF. This source does not authorize a live
release: exact-source acceptance, current provider/UNKNOWN reconciliation, recovery
and a separate owner grant remain required. W1 defaults OFF; S1 stays OFF.
Ops remains private and continues source maintenance/review.

The four private Ops dependencies in `gate-snapshot-cutover.yml` are explicitly
quarantined. S1 is NOT_ACCEPTED; its old source/workflow lock is not rewritten to
claim compatibility. Do not execute it or treat this change as S1 acceptance.

As of 2026-11-05, the read-only history scanner ran once; source registration and
its scoped findings triage are recorded in [Gate789 checkpoint](https://github.com/markmalina/gate/issues/789#issuecomment-6666029141)
and [Delta515](https://github.com/AlphaCenter/delta/issues/515#issuecomment-6666171645).
Main's deployment route remains OFF; this unmerged candidate grants no live release.
Do not repeat the scan merely to obtain a green check.
NO_FINDINGS covers only fetched Git refs under its scanner rules; it cannot clear issues, comments, attachments,
unreachable objects, LFS payloads or runtime data. Publication remains a separate
owner action after review. See `docs/operations/GATE-PUBLIC-PREP.md`.

### Production deploy route (unmerged owner-gated activation candidate)

Gate owns `.github/workflows/ops-deploy.yml` and control issue #751.
After the exact owner Gate-only release gate and exact-head checks, post only:

`/ops-deploy production target=gate expected-source-sha=<reviewed full current main SHA>`

The requester accepts only Mark's immutable identity, the exact control issue
and reviewed current main; it invokes the pinned Ops Gate-only executor.
AQUB/all and an AQUB caller are forbidden. Source access uses this Gate workflow's ephemeral `GITHUB_TOKEN` at
`contents:read`; no permanent GitHub App source key is needed. Existing
protected Gate Railway credentials must validate against the fixed target.
Missing/mismatched credentials stop
before mutation: never copy AQUB secrets or use a provider UI/API fallback.
This grants no migration, source drift, credential provisioning or AQUB action.
Other Gate operations, including Gate745, need a reviewed independent
Gate-owned Ops contract; this short route does not authorize cutover.

On the first turn of every newly opened Gate Work:


1. Read Center `AGENTS.md`, this repository's `WORK-PARENT.json`,
   `docs/work-bootstrap.md`, `docs/GATE-CURRENT.md`, the program/active sprint,
   the current GitHub issue, latest receipt and
   next pointer.
2. Re-query current main, open PR/issues, executions, leases, receipts and
   scheduled automations before mutation.
3. Continue an existing matching outcome; do not create a duplicate issue,
   execution, lease, branch, PR or automation.
4. Do not create or require a per-project heartbeat. Center owns the single
   company reconciliation/reporting loop. A Gate schedule needs a separate
   exact purpose, supported cadence and duplicate check.
5. Use one bounded PR, deterministic checks and one necessary exact-head CI run.
   No zero-change or pointer-only commit/comment is allowed.
6. Follow the local lease/state protocol and the stricter current owner scope.
   A current task restriction overrides an older broader standing authorization.


## Owner standing authorization: Mark-only autonomy

Effective 25 July 2026; clarified by Mark on 23 September 2026. Gate workers
may complete bounded technical work autonomously while Mark is the only active
product user. Mark is the owner, not an external user. His Telegram contacts
and other counterparties are not Gate product users merely because their
messages are present in Gate. Their personal data and message payload remain
protected by the separate execution-scope rules below.

For this rule, `external_users: 0` means zero active **non-Mark Gate product
users**, not zero Telegram contacts, conversations, accounts or messages. An
owner-only mode claim must record `owner_only_mode: true` and
`non_mark_product_users: 0` in the current canonical Work, supported by a
current owner attestation and fresh redacted evidence for the exact target
workspace/installations. A scoped `gate_get_status` result can prove only its
own workspace; do not present it as a global user count. Any contrary evidence
or unknown non-Mark installation fails closed until reconciled.

Autonomous repository merge, Production deploy, verification and rollback
require all of the following for each bounded execution:

- the current owner-only mode claim above;
- no third-party payload or real-person data **in that execution scope**.
  Existing historical conversations in the database alone do not make an
  unrelated owner-only code/status change process their content;
- every owner-only live check names one exact installation and hard caps
  provider calls and outbound messages;
- a last-known-good Production revision and rollback condition are recorded.

Within that boundary, a worker may claim one lease/branch/PR, run local checks
and one necessary exact-head CI, merge the verified exact head, deploy via the
registered Ops route, verify the deployed fingerprint and redacted health, and
roll back on the recorded stop condition without repeating a permission request.

The standing authorization fails closed if a non-Mark product user appears,
the execution processes third-party payload, a third-party participant joins
the selected live check, or an external effect exceeds its cap. Mark-only
product use does not authorize reading contacts/history, sending messages,
creating groups, or sharing data with AI. Exact owner authorization remains
required for credentials, secrets, OAuth or identity actions; payments and
legal actions; content publication; destructive history/data operations; new
external-user processing; and provider/message effects outside exact owner-only
caps in the current Work.

CI, merge, deployment status and provider dashboards never prove runtime or
product readiness by themselves. Every autonomous Production change must end
with an exact deployed revision and redacted acceptance or rollback receipt.

<!-- WORK-HIERARCHY:START -->
## Work hierarchy


Before any mutation, read `WORK-PARENT.json`. This Work's direct parent is
`AlphaCenter/center`; the company root is `AlphaCenter/center`.


The parent coordinates portfolio, requirements, handoffs and acceptance. It
does not gain implicit permission to mutate this repository. This Work remains
the authority for its local domain and implementation. Cross-repository
mutation requires an explicit owner decision or accepted project-to-project
handoff. If the local pointer conflicts with Center's registry, stop
cross-repository mutation and reconcile the pointers first.
<!-- WORK-HIERARCHY:END -->


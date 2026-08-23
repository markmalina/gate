# Gate Work instructions

Canonical company policy and ownership live in
[AlphaCenter/center](https://github.com/AlphaCenter/center). This repository
owns Gate transport/runtime implementation; Center coordinates portfolio,
requirements, handoffs and acceptance without gaining Gate mutation authority.

On the first turn of every newly opened Gate Work:

1. Read Center `AGENTS.md`, this repository's `WORK-PARENT.json`,
   `docs/work-bootstrap.md`, the current GitHub issue, latest receipt and
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

## Owner standing authorization: pre-user autonomy

Effective 25 July 2026, the owner authorizes Gate workers to complete bounded
technical work autonomously while Gate is provably in pre-user mode. Repeated
confirmation is not required for repository changes, one exact-head CI, merge,
Production deployment, runtime verification, or rollback.

Pre-user mode exists only when the current canonical Work and fresh redacted
evidence both prove:

- `pre_user_mode: true` and `external_users: 0`;
- no third-party payload or real-person data is in the execution scope;
- every owner-only live check names one exact installation and hard caps
  provider calls and outbound messages;
- a last-known-good Production revision and rollback condition are recorded.

Within that boundary, a worker may autonomously create or resume one execution,
one lease, one branch and one PR; run local checks and one necessary exact-head
CI; mark the PR ready; merge the verified exact head; deploy it to Production;
verify the deployed fingerprint, redacted health and accepted runtime outcome;
and roll back to the recorded revision when the acceptance condition fails.

This standing authorization fails closed immediately when any external user,
third-party participant, third-party payload, or unbounded external effect is
detected. Exact owner authorization is still required for credentials, secrets,
OAuth or identity actions; payments or legal actions; publication; destructive
history or data operations; new external-user processing; and provider or
message effects outside the exact owner-only caps recorded in the current Work.

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

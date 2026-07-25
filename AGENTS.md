# Gate Work instructions

Gate Work follows the canonical AlphaVia Work bootstrap policy in
[AlphaVia issue #67](https://github.com/markmalina/alphavia/issues/67).
That issue owns the cross-repository policy; this repository records the
Gate-specific entrypoint and owner authorizations.

On the first turn of every newly opened Gate Work:

1. Read `docs/work-bootstrap.md`.
2. Read the current GitHub issue, its latest implementation receipt, and its
   `next-work` pointer.
3. List scheduled tasks visible to the current chat before creating anything.
4. Ensure exactly one enabled automation titled `Gate Work heartbeat` exists
   for this Work. Never create a duplicate.
5. Use the fastest recurrence actually supported by the current Automations
   surface. The desired active cadence is ten minutes; the current callable API
   caps recurrence at hourly. Report the schedule that was really created.
6. Follow the lease and state protocol in `docs/work-bootstrap.md`.

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

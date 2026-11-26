# Gate Work bootstrap

This is the repository-local execution guide. Canonical company policy and
ownership live in [AlphaCenter/center](https://github.com/AlphaCenter/center);
Gate retains authority for its transport/runtime implementation.

## Bootstrap

For every new Gate Work:

1. Read Center `AGENTS.md`, root `AGENTS.md`, `WORK-PARENT.json`, this
   document, [GATE-CURRENT](GATE-CURRENT.md), [the program plan](GATE-RECOVERY-PLAN.md),
   [AI WORK-RULES](https://github.com/markmalina/ai/blob/main/WORK-RULES.md),
   the active sprint issue, latest implementation receipt and next pointer.
2. Re-query current main, open PR/issues, execution/lease claims, receipts and
   scheduled automations.
3. Search for the exact outcome before creating an issue, execution, lease,
   branch, PR, comment or automation. Reuse an exact match and fail closed on a
   conflicting active claim.
4. Do not create or require a Gate-specific recurring heartbeat. Center owns
   the single company reconciliation/reporting loop. A new Gate schedule needs
   a separate exact purpose, supported cadence, duplicate check and owner scope.
5. Store only safe repository evidence. Never store automation/conversation
   identifiers, tokens, credentials, secrets or private payloads.

Create no zero-change receipt, pointer commit or comment when repository state
has not materially changed.

## Measured sprint and history

Use [#787](https://github.com/markmalina/gate/issues/787) as the current program
pointer for Watch/Calls/reply freshness. Before coding, record the bounded user
result, dated baseline, target, scope and claim in its issue. Record material
errors, decisions, candidates and CI/runtime outcomes while working, not only
a final retrospective after many iterations. Follow [GATE-METRICS](GATE-METRICS.md)
for durations, sample sizes, incomplete cases and cumulative cost estimates.
The accepted result is the observed user outcome; documentation preparation and
source/CI acceptance remain separate. Dated audit plans are historical sources,
not a competing active queue. Continue within existing authorization; ask only
for an actually missing decision or protected action.

## Execution states

- `blocked`: recheck dependencies; remain silent if unchanged.
- `ready`: claim a unique lease/execution id, then begin.
- `running`: continue that execution or inspect its single PR and CI.
- `waiting_user`: stop at a human gate not covered by standing authorization.
- `done`: accept the receipt and follow the latest `next-work` pointer.
- `standby` or `no_ready_work`: remain silent.

Before changing files or opening a PR, post or verify a claim containing:

- the issue and bounded stage;
- a unique opaque `execution_id`;
- status `running`;
- the expected branch;
- lease start and expiry times.

A start is allowed only when no unexpired `running` claim exists for the same
issue and stage. Re-running with the same `execution_id` resumes the existing
execution. A different execution id while that lease is active is a duplicate
and must fail closed. Each execution may own at most one branch and one PR.
An expired lease may be superseded only by an explicit receipt recording the
old execution id and the new claim.

## Synthetic duplicate-start check

Given issue `gate#example`, stage `docs`, and a fresh state:

| Step | Input | Result |
| --- | --- | --- |
| 1 | `ready`, claim `exec-a` | one `running` lease is created |
| 2 | `ready`, claim `exec-b` before expiry | rejected as duplicate |
| 3 | `running`, resume `exec-a` | existing execution continues |
| 4 | create PR for `exec-a` | one PR is recorded |
| 5 | create another PR for `exec-a` | rejected as duplicate |
| 6 | mark `exec-a` `done` with receipt | follow `next-work` pointer |

Expected invariant: one execution, one branch, one PR. The duplicate-start
rehearsal passes only when steps 2 and 5 fail closed.

## Authority gates

By default, schedule, lease, issue state, CI and merge never authorize an
external effect. Exact owner authorization is required before:

- production deployment or production/config mutation;
- live provider or paid-resource call;
- credential, secret, OAuth, or identity action;
- sending a message or publishing content;
- processing or exposing real-person data;
- any other irreversible or externally visible action.

The repository-local owner-only standing authorization in `AGENTS.md` is the
only exception. Telegram contacts and counterparties are not automatically
non-Mark product users; third-party payload in the execution scope remains a
separate restriction under `AGENTS.md`. A worker may use this authorization only
after the current Work records and
fresh redacted evidence prove all activation conditions. The execution claim
must include:

- `owner_only_mode: true` and `non_mark_product_users: 0`, supported by the
  current owner attestation and fresh redacted evidence for the exact target
  workspace/installations as required by `AGENTS.md`;
- the exact repository outcome and Production rollback revision;
- for an owner-only live check, one exact installation plus hard provider and
  delivery caps;
- automatic stop conditions for any third-party participant, payload,
  unexpected revision, duplicate delivery, health regression or unknown state.

When those facts are proven, repository work, exact-head CI, merge, Production
deployment, verification and rollback proceed without repeated confirmation.
If any activation fact is absent or stale, fail closed for the protected effect
and record the specific blocker. Continue independently authorized read-only,
documentation or default-off repository work; missing runtime proof does not
by itself require another general permission request. Set `waiting_user` only
when the missing fact actually requires an owner decision or action.

Credentials, secrets, OAuth, payments, legal actions, publication, destructive
history/data changes, external-user processing and effects beyond the recorded
owner-only caps always remain exact owner gates.

After a bounded stage is complete, post its implementation and runtime receipt,
then follow the repository's latest `next-work` pointer.

# Gate Work bootstrap

This document is the repository-local execution guide for
[AlphaVia WORK-BOOTSTRAP-001](https://github.com/markmalina/alphavia/issues/67).
The canonical policy remains in AlphaVia; Gate does not copy or acquire
portfolio architecture ownership.

## Bootstrap

For every new Gate Work:

1. Read this document, the current issue, the latest implementation receipt,
   and the latest `next-work` pointer.
2. List automations visible to the current chat.
3. If an enabled `Gate Work heartbeat` already exists, verify it and do not
   create another. Otherwise create one condition-watch automation at the
   fastest cadence the surface accepts.
4. Desired active cadence is ten minutes. The current callable Automations API
   supports at most hourly recurrence, so use hourly unless a finer recurrence
   is actually accepted. Record the exact observed cadence.
5. Store in GitHub only a non-secret receipt: title, enabled status, exact
   cadence, and creation or verification time. Never store an automation token,
   conversation identifier, or credential.

The heartbeat iteration must inspect the current issue, receipts, and pointer.
It stays silent when nothing material changed.

## Execution states

- `blocked`: recheck dependencies; remain silent if unchanged.
- `ready`: claim a unique lease/execution id, then begin.
- `running`: continue that execution or inspect its single PR and CI.
- `waiting_user`: stop at the human gate and notify once.
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

Heartbeat, lease, issue state, and CI never authorize an external effect. Exact
owner authorization is required before:

- production deployment or production/config mutation;
- live provider or paid-resource call;
- credential, secret, OAuth, or identity action;
- sending a message or publishing content;
- processing or exposing real-person data;
- any other irreversible or externally visible action.

When authorization is absent, set `waiting_user`, record the precise blocker
once, and stop. After a bounded stage is complete, post its implementation
receipt and follow the repository's latest `next-work` pointer.

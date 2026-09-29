# Reply sequence canary: durable attempt primitive

Migration 0113 adds `reply_canary_scopes` and `reply_canary_attempts`. The
synthetic service `reserve_canary_attempt` locks one workspace row, validates
the exact execution, principal, account/dialog/body grant or notification
destination/template, expiry and shared cap (1–6), then commits a never-refunded
attempt before external I/O. Distinct processes share the row lock and ledger.
Reusing an attempt key is denied. A crash after commit consumes capacity.
The scope trigger forbids deletion, reopening a closed scope, changing an
execution ID, cap, expiry, grants, principal or destination. Closing remains
monotonic. The unique execution ID cannot be recycled by normal writes.

This is **default-off application code, not an armed Production canary**. The
authenticated Ops executor, verified fleet/config/DB evidence and exact owner
scope remain required. When
`GATE_REPLY_SEQUENCE_CANARY_ENABLED=true`, each `sendMessage` checks its exact
saved sequence or notification context against the outbound JSON, then commits
an attempt before HTTP. The flag is false by default. Missing/expired scope,
absent or mismatched context, cap exhaustion and unrelated manual sends are
denied before HTTP. Sequence sends bind to the persisted dispatch claim and
draft; outbox notifications bind to the saved sequence principal and verified
route. A 429 retry reenters HTTP through a new admission. The unarmed migration
creates no scope. Do not enable dispatch or this flag without a protected exact
scope, reconciliation and rollback procedure; no Production canary is authorized.
This wiring does not establish release readiness or refresh the registered
ChatGPT tool snapshot. Those external predicates remain separately evidenced.

## Protected application controls

`gate-reply-sequence-control` now requires `--bot-resource-id` (a verified
64-hex digest). It constructs a DB-only runtime, never registers Telegram
transport and requires no real or placeholder bot token. Ops supplies actual
verified target configuration through its protected minimal environment;
the application does not authenticate the Ops caller or prove provider inventory.
No token, DSN, raw principal, destination, draft body or input JSON belongs in
workflow logs/receipts. Input is bounded to 64 KiB through stdin. CLI errors
return one redacted fixed code instead of dumping exceptions or SQL parameters.

- `epoch-arm`: protected JSON fields `evidence_sha256`, `verified_at`,
  `members` (`boot_id`, `source_sha`, `config_sha256`), `binding_sha256`,
  `expected_connector_version`. Installs the verified initial fleet epoch in
  disabled mode; checks observed fleet, bot digest, health and connector CAS.
  This command's evidence digest is an audit reference, not self-authentication.
  Ops must first verify the exact source/config/DB/provider inventory.
- `serial` / `parallel`: require the installed healthy epoch, matching verified
  bot identity and current flags. These operations no longer accept an arbitrary
  `--continuity-evidence-ref` or future `--continuity-until`. They cannot extend
  expired evidence. Runtime renews the unchanged verified epoch independently.
- `canary-arm`: protected JSON fields `execution_id`, `principal_subject`,
  `expected_generation`, `bot_resource_id`, `grants`,
  `notification_destination`, `expires_at`, `max_attempts`. Each grant has
  `account_id`, `recipient`, `draft_id`, `body_sha256`. Requires migrated,
  disabled/drained control, exact generation, existing verified bot identity,
  permission/connection/private owner route, unchanged stored draft hashes,
  flag enabled, deadline within ten minutes and shared cap 1–6. Destination
  must be an owner route of a granted account; another account's notification
  is not authorized merely because its sequence is allowed.
- `canary-status`: redacted state, digest, deadline, attempt count and ledger
  count only. No provider effects.
- `canary-close --execution-id …` / `canary-reconcile --execution-id …`:
  commit disabled/closed first; terminally stop remaining bound series under
  per-dialog locks; fail pending notices. Already sending/unknown stays honest.
  Repeat after a partial local failure completes the remaining DB work without
  resending. This reconcile operation is conservative closure, not re-arming.

Same immutable arm request reconciles existing state/count without resetting
quota or expiry. Different scope, execution or reopened closed scope is denied.
The original one-scope-per-workspace schema is deliberately retained: creating
a later canary needs a separately reviewed lifecycle change; rows are never
deleted/reset to recover quota. Mode serial→parallel uses the same scope/cap.

Migration 0115 binds every canary sequence to its execution. Queue validates
both draft ID/hash and account/dialog/body. Claim, admission and notification
check the persisted binding. HTTP reservation is required for bound sends even
if a process flag is later off. Unbound notification backlog cannot spend the
canary quota. The supervisor closes expired/exhausted/flag-off scopes; ordinary
mode cannot revive their tails or pending notifications. All multipart sends
remain denied in canary mode. Caps include retries and unknown outcomes.

Full migration graph from the baseline in #722 is now
`0111_reply_documents → 0112_reply_sequence_control → 0113_reply_canary_budget
→ 0114_reply_continuity_epoch → 0115_reply_canary_binding`.
Recompute against the exact final head if another additive migration is added.

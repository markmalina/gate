# Telegram ingress cutover plan

Status: transitional architecture baseline  
Production endpoint: `POST /telegram/webhook`  
Owner: Gate application

## Why this document exists

Gate currently exposes one public Telegram webhook path through a stack of historical sprint
routers. FastAPI registers every layer at the same path and dispatches the first match; each
layer either handles one narrow command/update type or calls the next Python handler directly.

This behavior is operational, but it is not the target architecture. The stack must be replaced
incrementally because draft, send, Business connection and inbox behavior already depend on it.

PR #109 establishes one registration boundary and a characterization test. It does not change
runtime behavior.

## Frozen production order

The current chain is intentionally explicit:

1. `sprint18b_telegram_webhook` — tap-first draft UX.
2. `sprint17_telegram_webhook` — approved Telegram-admin draft send.
3. `sprint16d_telegram_webhook` — clean scoped inbox commands.
4. `sprint16_telegram_webhook` — raw update diagnostics.
5. `sprint15b_telegram_webhook` — account-scoped diagnostics.
6. `sprint14b_telegram_webhook` — Business connection admin visibility.
7. `sprint14_telegram_webhook` — account-aware Business updates.
8. `sprint11b_telegram_webhook` — draft callbacks.
9. `sprint10_telegram_webhook` — draft edit commands.
10. `sprint9_telegram_webhook` — legacy admin send/test commands.
11. `telegram_webhook` — base persistence and normalized inbound processing.

The source of truth for this order is
`app.api.telegram_ingress.LEGACY_TELEGRAM_WEBHOOK_CHAIN`.

## Change rule

Until the canonical cutover is complete:

- do not add another `/telegram/webhook` sprint router;
- add new behavior behind an application service or the future canonical dispatcher;
- any intentional change to the frozen chain must update the characterization test and explain
  the migration impact in the PR;
- never remove a layer until its behavior has parity coverage and shadow comparison;
- preserve the exact public path and Telegram secret-token verification.

## Behavioral invariants

Every cutover phase must preserve these properties:

1. A missing or invalid Telegram webhook secret fails closed.
2. A Telegram `update_id` is durably deduplicated.
3. Unsupported or poison updates cannot create an infinite Telegram retry loop.
4. Business updates resolve the originating Account and Business connection.
5. Normalized messages retain account, project, conversation and sender context.
6. One inbound human message creates no more than one active reply task.
7. Admin commands remain restricted to the Telegram-admin identity and account scope.
8. Draft creation never sends a message.
9. A human-managed reply sends only after explicit confirmation through the originating account.
10. Send retries remain idempotent; an uncertain result is recorded as `unknown`.
11. Incoming message text is untrusted data and cannot authorize a write or send.
12. MCP output schemas and OAuth scopes remain backward-compatible during the ingress refactor.

## Target flow

```text
Telegram webhook
  -> authenticate and persist receipt
  -> acknowledge receipt
  -> transactional outbox
  -> worker
  -> normalize update
  -> resolve Account / Person / Conversation
  -> resolve project and ownership
  -> application command
  -> project-scoped inbox or agent session
  -> audit and metrics
```

The public HTTP handler should only authenticate, validate the envelope, persist the receipt and
return an acknowledgement. Business logic belongs in the worker and application services.

## Ordered cutover

### Phase 0 — freeze and characterize

Delivered by PR #109:

- central registration boundary;
- exact route-order fingerprint;
- CI guard against accidental layer additions or reordering;
- documented invariants and removal conditions.

### Phase 1 — shared application/query services

Move MCP and Telegram reads behind account/project-scoped services. Extract command behavior from
handlers without changing the registered webhook chain.

### Phase 2 — canonical dispatcher in shadow mode

Build one dispatcher that classifies every captured fixture/update and records the handler decision
it would make. Continue serving production through the legacy chain. Compare:

- selected account and Business connection;
- event/update identity;
- normalized message;
- project/context assignment;
- reply-task decision;
- admin-command decision.

No shadow path may send Telegram messages or mutate production workflow state.

PR #112 starts this phase with a deterministic, side-effect-free handler classifier, captured
fixture replay and one redacted observation at the frozen ingress seam. The observation contains
only update identity/type and decision metadata; it never logs message text, writes database state
or calls Telegram. Account, connection, project, normalized-message and reply-task parity remain
required before staged cutover.

### Phase 3 — durable receipt and worker

Persist the receipt before acknowledgement, enqueue it transactionally, process it through a
bounded worker, and add retry, dead-letter and replay controls. Prove idempotency with duplicate,
timeout and worker-restart tests.

### Phase 4 — staged cutover

Enable the canonical dispatcher for one update class at a time behind a kill switch. Keep parity
metrics and a rollback path. Admin send behavior is cut over last.

### Phase 5 — remove legacy layers

Delete a sprint router only when:

- its fixtures pass through the canonical path;
- shadow decisions match;
- production metrics are stable;
- rollback has been exercised;
- the associated direct handler-to-handler delegation is no longer reachable.

The final application must register exactly one `POST /telegram/webhook` route.

## Oko dependency

The future Mark Oko agent must be added after the canonical routing boundary exists. Its activation
belongs after normalization and identity resolution, never in a new webhook router.

A qualifying first message beginning with the approved `Око возрождения` keyword will eventually
create an Oko project binding and agent session. Agent-managed conversations must be excluded from
the normal personal unanswered inbox; only explicit human-handoff tasks should return to Mark.

Autonomous Oko replies require a separate, narrow permission, audit trail, rate limit and kill
switch. They must not weaken the existing explicit-confirmation rule for human-managed replies.

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
or calls Telegram.

PR #113 adds configured RU/BY account selection, the current project assignment, normalized
message-presence fields and a reply-task candidate decision. Unknown connection ids are marked for
a future isolated database lookup; owner/inbound direction remains explicitly unresolved rather
than guessed. Neither shadow stage reads or writes the database.

PR #114 adds the isolated SELECT-only identity snapshot boundary. It resolves enabled
database-backed connections, the account owner identity set, inbound/outbound direction and final
reply-task intent through an injected reader. It has no webhook integration and no transaction or
write methods; production-like replay must prove parity before it is attached to live ingress.

Live shadow wiring and stable parity evidence remain required before staged cutover.

### Phase 3 — durable receipt and worker

Persist the receipt before acknowledgement, enqueue it transactionally, process it through a
bounded worker, and add retry, dead-letter and replay controls. Prove idempotency with duplicate,
timeout and worker-restart tests.

PR #115 defines the application boundary first: one atomic receipt/outbox acceptance per
`(bot_key, update_id)`, stable identities for duplicates, validation before persistence and a
bounded exponential retry policy with a hard dead-letter limit. It does not add tables, a worker
or webhook wiring; those follow only after the contract is green.

PR #116 adds the reversible persistence schema: a unique receipt per `(bot_key, update_id)`,
exactly one outbox event per receipt, bounded processing metadata and due-work indexes. The tables
are dormant until an atomic adapter is merged; the legacy webhook does not write them.

PR #117 adds the PostgreSQL adapter that creates the receipt and outbox event in one transaction,
returns stable identities for duplicate deliveries and covers concurrent acceptance. It remains
dormant: neither the legacy webhook nor a worker calls it.

PR #118 defines one bounded worker iteration: claim at most one item, process it once and atomically
transition it to processed, scheduled retry or hard dead-letter. Only a bounded error code crosses
the application boundary; no exception detail or message content is exposed.

PR #119 implements the queue boundary with PostgreSQL row locks and `SKIP LOCKED`, exact attempt
guards and atomic paired receipt/outbox transitions. It has no scheduler or runtime entry point, so
the durable queue remains inactive until worker composition is explicitly enabled.

PR #120 adds a one-shot command that is disabled unless `GATE_TELEGRAM_WORKER_ENABLED=true`.
When explicitly invoked it claims at most one item and runs only the side-effect-free shadow
classifier; it is not mounted in the web process and has no loop, scheduler or Telegram client.

PR #121 adds payload-free queue health aggregates and bounded abandoned-claim recovery. Stale
claims below the attempt ceiling return to scheduled retry; exhausted or inconsistent claims move
to hard dead-letter. Neither adapter is mounted in an endpoint, scheduler or worker loop.

PR #123 evaluates those aggregates with stable healthy/warning/critical reason codes and writes one
payload-free audit record for every bounded recovery transaction. Alert delivery and operator
acknowledgement remain intentionally uncomposed.

PR #124 defines the application lifecycle for durable payload-free alert incidents, delivery gating
and explicit operator acknowledgement. Delivery remains disabled unless a notifier and channel are
deliberately composed; no table, notification adapter, scheduler or runtime wiring is added.

PR #125 adds the dormant incident table and a serialized PostgreSQL adapter for open, notified,
acknowledged and resolved transitions. It stores only severity, bounded reason codes and operator
delivery metadata; no queue payload, account, project or Telegram peer identity is persisted.
No notifier, scheduler, endpoint or runtime composition is added.

PR #126 adds a dormant authenticated HTTPS notifier. It sends only the redacted incident envelope,
uses the incident id as the channel idempotency key and requires an explicit bearer credential.
No endpoint value, secret, environment setting, scheduler or runtime composition is added.

Durable receipt acceptance is now an invariant of the public Telegram Business
webhook rather than an operator-controlled release mode. Every authenticated update validates its
signed BIGINT Telegram update id and commits the receipt/outbox pair under the stable
`telegram_business` connector key before the unchanged legacy compatibility chain runs.

There is no `GATE_TELEGRAM_DURABLE_ACCEPTANCE_MODE` setting. A receipt failure returns `503` so
Telegram may retry; it is never converted to a legacy acknowledgement. Receipt and outbox
identifiers remain absent from responses and logs. Operational rollback is a normal deployment of
the last known-good release, not a partially enabled Railway configuration.

### Phase 4 — staged cutover

Move each update class to the canonical dispatcher in a reviewed, versioned release. Keep parity
metrics and deploy-level rollback evidence; do not expose temporary migration switches as Railway
configuration. Admin send behavior is cut over last.

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

A qualifying first eligible enrollment message will eventually create an Oko project binding and
agent session only after applying NFKC, trim/collapse whitespace and Unicode case-folding to both
input and canonical phrase `Око возрождения`, then requiring equality with normalized
`око возрождения`. Prefixes, suffixes and punctuation do not qualify. The dormant
`/start oko21_*` attribution path is separate and never activates the Business agent.
Agent-managed conversations must be excluded from the normal personal unanswered inbox; only
explicit human-handoff tasks should return to Mark.

Autonomous Oko replies require a separate, narrow permission, audit trail, rate limit and kill
switch. They must not weaken the existing explicit-confirmation rule for human-managed replies.

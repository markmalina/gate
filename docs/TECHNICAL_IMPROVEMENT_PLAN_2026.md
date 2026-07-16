# Technical self-improvement plan

See [Relationship CRM architecture](RELATIONSHIP_CRM_ARCHITECTURE.md) for the updated cross-product data owner.

## Goal

Improve Gate only where the improvement unlocks safer product delivery. The plan deliberately avoids an endless platform rewrite and avoids premature microservices.

## T0 — Baseline and architecture fitness

- Route every human-confirmed reply through a literal Telegram adapter with no inferred parse mode or keyboard.
- Make unsupported Alpha media a successful terminal ignored outcome; never retry product-ineligible input.
- Add retryable/terminal failure classification and tests around the Telegram worker.
- Add a delivered-assistant projection so actual sent replies, but never shadow proposals, enter future context.
- Publish audit, decision map and current production handoff.
- Add dependency rules for `transport`, `relationship_crm`, `alpha`, `mayaki` and `billing` modules.
- Add a data-ownership registry: aggregate/table/event/retention/export owner.
- Add CI checks for stale release evidence and migration-head consistency.

**Exit:** confirmed text is byte-for-byte the literal provider payload; non-text cannot poison Alpha; a pull request cannot make transport import a product module or introduce an ownerless table/event.

## T1 — Canonical content and authoritative worker

- Add normalized text/media content independent of raw Telegram receipt retention.
- Define one source of truth for receipt, connector, dialog, message and ownership; mark legacy adapters explicitly.
- Compose a process-scoped worker runtime, stale recovery, incidents and readiness.
- Migrate Telegram Business update classes from the legacy handler chain with parity fixtures and rollback flags.
- Make connector capability declarations enforceable at the external-effect adapter.

**Exit:** migrated webhook classes only authenticate/accept/acknowledge; deleting an expired raw receipt does not erase canonical/product context.

## T2 — Relationship CRM boundary, spaces and attention semantics

- Create module-owned models for `RelationshipSpace`, `Person`, `ChannelIdentityLink`, `Relationship`, `Interaction`, `Note`, `FollowUp`, `ConversationAttention`, `Pipeline`, `Stage` and `Deal`.
- Use explicit attention state; do not infer it from message direction.
- Backfill existing conversations to `untriaged`, never to guessed states.
- Add typed repository/service ports and read projections.
- Add portable versioned export/import.
- Keep the old unanswered endpoint as a deprecated compatibility adapter.

**Exit:** product code can list conversations without using “last sender = status,” and a person note exists independently of a reply task.

## T3 — Canonical attachment lifecycle

- Add immutable attachment metadata: kind, provider file reference, stable provider identity, MIME type, size, duration and lifecycle state.
- Group voice eligibility is verified reply-to-Alpha only; private voice requires active owner approval and versioned consent.
- Validate type/size/duration before download.
- Fetch files in a worker outside the webhook transaction and DB locks.
- Never log/store token-bearing download URLs.
- Add idempotent transcription job states, retry classification and deletion tombstones.
- Encrypt or minimize retained payload; define transport vs product retention.

**Exit:** duplicate Telegram updates and worker retries create one attachment and at most one admitted transcription job.

## T4 — Voice transcription and response contract

- Define `TranscriptionPort`; implement deterministic fixture adapter first.
- Add an OpenAI adapter behind provider readiness and budget admission.
- Add generic per-trigger provider usage operations so `transcription` and `response` reserve/settle independently while sharing the same monthly hard cap; token-only Alpha usage cannot represent both safely.
- Support Telegram voice formats through a controlled normalized upload path.
- Persist transcript as a scoped product artifact, not a log.
- Version and evaluate the “Я понял так” + uncertainty + answer response schema.
- Track duration, latency, error category and cost without transcript text in metrics.

Telegram currently permits bot downloads up to 20 MB while the OpenAI transcription endpoint accepts uploads up to 25 MB. The runtime uses the lower limit with safety headroom, streams into bounded temporary storage and sends Telegram OGG/Opus directly when the pinned transcription profile supports it.

**Exit:** fixtures pass end-to-end; production adapter cannot run before billing/preflight; no Telegram delivery occurs in tests.

## T5 — Channel-neutral Alpha installations

- Replace group-only validation with dialog-kind/access-mode policy.
- Group invocation remains command/mention/reply.
- Private invocation requires active owner-approved installation and human sender.
- Create private discovery as pending/default-deny.
- Reuse the same rate, token, model, revision, grant and delivery invariants.
- Add privacy-safe owner projections and MCP controls.

**Exit:** group behavior is regression-safe; a private fixture is discoverable/approvable/pausable; inactive private messages never reserve a model turn.

## T6 — Durable module events and projections

- Introduce transactional outbox rows with versioned schemas.
- Add idempotent consumer checkpoints and dead-letter/operator views.
- Project transport receipts into space-scoped Relationship CRM interactions.
- Project model/provider/infra usage into billing.
- Add replay tests from sanitized fixtures.

**Exit:** deleting/rebuilding a projection does not replay Telegram delivery or duplicate charges.

## T7 — Unified billing and entitlements

- Immutable usage/cost/revenue entries with workspace/product/install attribution.
- Preserve synchronous hard admission for the $50 OpenAI cap.
- Add per-plan limits, alerts and default-deny entitlement decisions.
- Reconcile provider usage against internal reservations.
- Add synthetic month rollover and concurrent-spend tests.

**Exit:** no admitted work can exceed its configured hard budget under concurrency; revenue and cost are reportable separately.

## T8 — Scale and resilience proof

- Load test ingress, claims, attachment workers and projections.
- Enforce short transactions, indexed claims and per-workspace fairness.
- Add backpressure and queue-age automatic pause thresholds.
- Exercise provider timeout, Telegram timeout, duplicate webhook, poison media, DB failover and partial projection failure.
- Verify restore plus outbox replay in a disposable environment.

**Exit:** published SLO/error budget and evidence for expected MVP load ×10, with no cross-workspace leakage or duplicate delivery.

## T9 — Extraction readiness, not forced extraction

- Document module APIs/events and ownership.
- Generate versioned JSON export schemas and compatibility tests.
- Remove cross-module direct table reads.
- Measure which module actually needs independent scaling/deployment/team ownership.
- Extract only that module when evidence justifies it.

**Exit:** Relationship CRM or billing can be moved to another deployment without changing Telegram connector semantics or user-facing IDs.

## Engineering standards

- Typed contracts at module boundaries; no raw ORM objects crossing them.
- Workspace/installation scope required in every repository method.
- Network calls outside DB transactions and locks.
- Idempotency key on every external effect and billable operation.
- `unknown` is a valid terminal/reconciliation state; do not guess success.
- No message, transcript, prompt, token or secret in logs/metrics.
- Additive migrations first; backfill; switch reads; switch writes; remove only in a later release.
- Feature flags default off for new external behavior.
- Architecture, product, migration, security and rollback tests accompany each slice.

## Deliberate non-goals

- No broker, Kubernetes or service mesh for the MVP.
- No universal provider abstraction before a second provider is approved.
- No autonomous semantic status overwrite.
- No network split merely to simulate modularity; Relationship CRM is a separate product now and is repository-extractable through stable contracts.
- No Oko live activation under Alpha’s authorization.

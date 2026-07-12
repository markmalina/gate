# Gate

> **PROJECT STATUS: ACTIVE / ORDERED STABILIZATION AND MVP DELIVERY**  
> Development resumed by Mark: **2026-07-12**  
> Frozen application-code baseline: `81163dcd079ad0ccbc58859832cdef571d65f314` (Sprint 21).  
> Current active scope: **Sprint 24 — scoped ChatGPT reply drafts**, starting from `main` at `48e6e8ac`.  
> Do not start feature development from an old sprint branch. Read this README completely before touching code.

Gate is a separate cloud-first communication gateway between Telegram accounts/bots and Mark projects such as Alpha and individual Mayaks.

Gate is not Alpha, not a Mayak, not SysteM and not the business logic of a practice. Gate receives communication facts, resolves transport and project context, creates reply work, delivers only permitted confirmed replies and records audit history.

```text
Telegram → Gate → scoped ProjectSpace → draft → explicit approval → Telegram → audit
```

Primary bot: `@gate_m_bot`  
Repository: `markmalina/gate` (private)  
Production stack: FastAPI, PostgreSQL, SQLAlchemy async, Alembic, Railway, GitHub Actions.

## 1. Handoff authority and active scope

This README is the current handoff and next-work authority. Mark explicitly resumed Gate development on 2026-07-12. The older documents remain useful references, but when they conflict with the status or delivery order below, this README wins.

Reference documents:

- [Full product and engineering specification](docs/GATE_FULL_TECHNICAL_SPEC.md)
- [Team workflow](docs/TEAM_WORKFLOW.md)
- [Gate operator regulations](docs/GATE_OPERATOR_REGULATIONS.md)

During the current ChatGPT connection scope:

- do not add Telegram features;
- do not add MCP tools;
- do not build Oko / Oko Vozrozhdeniya behavior;
- do not merge old branches into `main`;
- do not deploy or change production configuration;
- do not activate write/send MCP capabilities;
- do not add autonomous AI replies;
- do not paste or commit secrets;
- provider-neutral OAuth token verification and the explicitly active ChatGPT connection implementation are allowed;
- work proceeds in the ordered PR sequence below, with production deployment remaining a separate Mark decision.

## Active delivery strategy

Gate now advances on two synchronized tracks that share one application layer:

1. **Safe MVP:** expose current inbound messages, draft assistance, explicit approval and send through ChatGPT as early as possible.
2. **Target architecture:** replace the sprint-router chain, hardcoded account/project assumptions and unsafe delivery semantics incrementally without a rewrite freeze.

MVP adapters must use the same scoped application services being built for the target architecture. Temporary direct ORM access, unscoped MCP reads and autonomous sending are not acceptable shortcuts.

## Current ChatGPT connection boundary

Sprint 24 adds the first safe write capability: ChatGPT may save proposed text as a scoped reply draft for an unanswered task. The tool requires OAuth scope `gate:drafts:write`, validates explicit account/project grants and never approves or sends the message. Production MCP remains disabled until a separate activation decision.

## 2. Exact repository state at freeze

Frozen application-code baseline (the last functional change before this handoff):

```text
81163dcd079ad0ccbc58859832cdef571d65f314
```

At freeze time:

- open pull requests: `0`;
- GitHub CI for the latest MCP sprints: green;
- Railway status for current `main`: success;
- current Telegram production loop exists and has been exercised;
- MCP has already moved from separate branches into `main`.

Latest merged work:

| Sprint | PR / commit | Result |
|---|---|---|
| Sprint 18b | PRs #67–69 | Telegram draft replies show one clean `Действия` section with clickable `/send_...`, `/drafts_...`, `/done_...`, `/dismiss_...`, `/snooze_...` commands |
| Sprint 19 | PR #70 / `6619d1fc` | Minimal read-only `/mcp`, `gate_get_status` |
| Sprint 20 | PR #71 / `cbeca147` | Read-only `gate_list_unanswered` |
| Sprint 21 | PR #72 / `81163dcd` | OAuth protected-resource metadata endpoints and declared future scopes; authentication is explicitly not enforced |

Sprint 21 added:

```text
GET /.well-known/oauth-protected-resource
GET /.well-known/oauth-protected-resource/mcp
GET /mcp/metadata
```

It publishes future scopes such as `gate:status:read`, `gate:inbox:read`, `gate:drafts:write` and `gate:messages:send`, but no OAuth validation, write tool or send tool is implemented.

Do not continue development in:

- `agent/sprint19-mcp-readonly-foundation`;
- `agent/sprint20-mcp-list-unanswered`;
- any other already merged sprint/hotfix branch;
- `gate/mayaki-inbound-forwarder` — old closed/unmerged work, far behind `main`, reference only.

All resumed work must start from the then-current `main`. If `main` has advanced beyond `81163dcd`, inspect and document the new commits before coding.

## 3. What currently works

- Railway-deployed FastAPI service;
- PostgreSQL persistence and Alembic migrations;
- Telegram webhook reconciliation and diagnostics;
- RU and BY Telegram Business connections;
- account-scoped Telegram admin context;
- durable Telegram updates, Gate events, Messages and AuditLog records;
- Conversations and unanswered ReplyTasks;
- `/inbox`, draft creation/history/editing and task actions;
- explicit `/send_<draft>` / `/approve_<draft>` send path;
- send through the origin Business connection;
- global and current RU/BY send switches;
- safe Telegram error details;
- cleanup of test/service inbox tasks without deleting data;
- read-only MCP tools `gate_get_status` and `gate_list_unanswered`;
- MCP protected-resource metadata describing planned OAuth scopes.

The current operator loop is:

```text
Telegram inbound
→ Gate
→ /inbox
→ tap /draft_...
→ tap /send_...
→ Telegram outbound through origin connection
→ task closed
→ outbound Message + AuditLog
```

No free-form outbound text may be sent without Mark explicit confirmation.

## 4. Current MCP security boundary

PR #75 closed the unauthenticated MCP data-access risk: MCP is disabled by default, temporary bearer access is fail-closed, and every query requires explicit account/project grants. PR #76 added the ChatGPT OAuth discovery contract without exposing data.

Sprint 23 replaces the OAuth validation placeholder with provider-neutral JWT/JWKS verification. Production remains unchanged until a separate IdP configuration and activation decision. MCP remains read-only; no draft, approval or send tool is part of this sprint.

## 5. Why a full refactor is required before further MCP

The current system grew through fast production sprints and hotfix layers. Confirmed debt:

- several routers register the same `POST /telegram/webhook`;
- newer sprint routers call older sprint routers directly;
- behavior depends on `include_router` order;
- one SQLAlchemy engine/pool is created per request;
- one `httpx.AsyncClient` is created per Telegram API call;
- RU/BY UUIDs and per-country switches are hardcoded;
- an unknown Business connection can fall back into the RU context;
- `Account.linked_project_id` assumes one project per transport account;
- Conversation identity includes `project_id`, although one Telegram dialog can change context;
- raw payloads are duplicated across JSONB records;
- an update may be marked processed before all effects complete;
- the global webhook error handler may answer HTTP 200 after an internal failure, causing silent loss;
- send status conflates approval and actual delivery;
- concurrent confirmation can race and cause duplicate sending;
- Telegram timeout is not represented as `unknown`;
- short references are found with `CAST + LIKE` UUID prefix scans;
- current MCP performs direct ORM/SQL queries;
- `gate_get_status` runs sequential exact counts over large tables;
- `gate_list_unanswered` has no real cursor and no principal/project isolation;
- critical modules use blanket `mypy: ignore-errors` and `ruff: noqa`;
- TelegramClient can create a callback send button although the reliable callback handler was not merged;
- draft editor links need a dedicated expiring signing key instead of fallback reuse of other secrets.

## 6. Non-negotiable product model

The next architecture must separate:

1. `Workspace` — ownership and hard data boundary.
2. `ChannelAccount` — one Telegram account; transport, not project.
3. `ChannelConnection` — bot installation / Business connection and rights.
4. `ProjectSpace` — isolated logical context: personal Alpha, each individual Mayak, unclassified or system.
5. `AgentProfile` — Alpha or the agent of a specific Mayak, operating only through an explicit AccessScope.

One Telegram account must support many projects at the same time:

```text
telegram_mark_by
├── alpha_personal
├── mayak_a
├── mayak_b
├── mayak_c
├── ...
└── mayak_j
```

Alpha may be a responder in multiple authorized ProjectSpaces, but receives only bounded context for the selected project. A Mayak must never receive personal Alpha data or data from another Mayak.

Every Mayak must have an independent scoped inbox, routing policy, AgentProfile, drafts, audit history, memory/adapter reference, MCP grants and kill switch, even when several Mayaks share the same Telegram account.

## 7. Target architecture

Use a strict modular monolith, not premature microservices:

```text
app/domain/          # entities, states, invariants, policies
app/application/     # commands, queries, services, ports, AccessScope, UoW
app/infrastructure/  # SQL repositories, outbox, queue, Telegram, auth, telemetry
app/adapters/        # canonical Telegram, admin HTTP, MCP
app/workers/         # normalize, route, deliver, retry/DLQ
app/bootstrap/       # lifespan and dependency composition
```

Dependency rules:

- domain imports no FastAPI, SQLAlchemy, Telegram or MCP;
- application depends only on domain and ports;
- adapters contain protocol mapping, not business rules;
- Telegram and MCP use the same application services;
- MCP imports no ORM models and executes no SQL;
- exactly one canonical `/telegram/webhook` is registered;
- engine/sessionmaker and HTTP clients live for the application lifespan;
- API instances are stateless and horizontally scalable;
- architecture tests prevent reverse dependencies.

## 8. Target domain and storage model

Required entities:

| Entity | Responsibility |
|---|---|
| `Workspace` | Owner/security/data boundary |
| `Principal`, `RoleBinding`, `ProjectGrant` | Read/draft/send authorization |
| `ChannelAccount` | Provider account as transport |
| `ChannelConnection` | External connection, capabilities and lifecycle |
| `ProjectSpace` | Personal or individual Mayak context |
| `AgentProfile`, `ProjectAgentBinding` | Responder identity/policy reference |
| `AccountProjectBinding` | Many-to-many accounts ↔ projects |
| `Dialog` | Transport dialog independent of project |
| `ContextBinding` | Time-bounded project segment of a dialog |
| `MessageContextAssignment` | One primary project per message |
| `RoutingDecision` | Versioned auditable routing fact |
| `InboundEnvelope` | Single durable raw update receipt |
| `Message` | Immutable normalized communication fact |
| `ReplyTask`, `ReplyTaskTrigger` | One active scoped reply work item with its triggers |
| `DraftRevision`, `Approval` | Versioned text and explicit approval fact |
| `SendAttempt` | Reserved/sending/sent/failed/unknown/cancelled delivery |
| `OutboxEvent` | Reliable internal delivery |
| `AuditLog` | Append-only actor/action/causation history |

Required invariants:

- no hidden global default RU account;
- unknown connection goes to quarantine, never to RU/BY by fallback;
- ChannelAccount is many-to-many with ProjectSpace;
- Dialog natural identity excludes project;
- a dialog changes project by ContextBinding, not duplicate Dialog rows;
- every Message has one confirmed primary project or explicit unclassified state;
- manual routing overrides preserve history;
- external projects receive a scoped `ProjectContact` projection, not global Person notes;
- new high-volume records use time-sortable IDs such as UUIDv7;
- existing UUIDs are not rewritten in one destructive migration;
- Telegram commands use indexed unique `public_ref`, not prefix scans.

All schema changes use:

```text
expand → checkpointed backfill → dual write/shadow read
→ comparison → cutover → contract cleanup in a later release
```

## 9. Durable inbound pipeline

```text
Telegram webhook
→ validate secret, size and schema
→ INSERT unique InboundEnvelope
→ INSERT OutboxEvent in the same transaction
→ commit
→ HTTP 200
→ normalize worker
→ resolve connection/account/dialog/person
→ persist routing decision and message context
→ create Message
→ create/update scoped ReplyTask
→ emit scoped project event
```

Rules:

- acknowledge only after durable receipt;
- return retryable failure when durable storage is unavailable;
- store poison updates in DLQ and support safe replay;
- process one Dialog sequentially and different Dialogs concurrently;
- use at-least-once delivery with idempotent domain effects;
- do not claim impossible end-to-end exactly-once;
- enforce unique source/update/message/task/delivery keys.

## 10. Safe send lifecycle

```text
explicit approval
→ validate Principal, AccessScope, revision, account, project and connection
→ reserve unique SendAttempt and commit
→ call Telegram outside a long DB transaction
→ persist sent / failed / unknown
→ close ReplyTask only after sent
```

Rules:

- concurrent approval produces at most one external call;
- timeout becomes `unknown`; never blind auto-retry;
- failed/unknown leaves ReplyTask open;
- global env kill switch remains break-glass;
- account/project switches are dynamic DB policy;
- handle Telegram `429 retry_after` and rate limits per bot/account/connection/chat;
- keep audit bounded and redacted;
- preserve current clickable command flow;
- do not reintroduce callback send until a separate tested sprint.

## 11. MCP compatibility during refactor

Preserve the existing read-only contracts:

- `/mcp`;
- `initialize`, `tools/list`, `tools/call`, `notifications/initialized`;
- `gate_get_status`;
- `gate_list_unanswered`;
- protected-resource metadata endpoints from Sprint 21.

Move implementations behind `StatusQueryService` and `InboxQueryService`. Every query requires Principal and AccessScope. Add real opaque keyset cursors. Replace operational exact counts with counters/read models. Remove blanket lint/type suppression.

Do not add new MCP tools, writes, send tools or autonomous behavior during this refactor.

## 12. Billion-message scaling target

The goal is not to claim that one Railway/PostgreSQL instance holds a billion messages. The goal is a domain/application architecture that can scale beyond one billion stored messages without rewriting the business core.

Required path:

- stateless API replicas;
- ports for EventBus, repositories, ObjectStore and TelegramGateway;
- PostgreSQL outbox/worker is allowed first; broker can later be replaced by Kafka/Redpanda/SQS adapter after measured thresholds;
- partition large append-only tables by time and tenant/account shard key;
- keep hot state separate from cold history;
- store compressed raw payload/media in object storage with hash/ref in PostgreSQL;
- use keyset pagination, never OFFSET for core lists;
- no runtime exact counts on billion-row tables;
- maintain an InboxProjection/read model;
- use BRIN/time indexes for history and composite B-tree indexes for hot queries;
- add read replicas/search/sharding only at documented capacity thresholds;
- define retention, archive, PITR and restore plans;
- structured logs contain no message text or secrets;
- metrics include ingress latency, queue lag, duplicate rate, DLQ, send failed/unknown, Telegram 429 and DB pool saturation.

## 13. Required delivery order after unfreeze

Do not implement as one mega-PR.

1. **PR 0 — MCP containment.** Close the current unauthenticated data-access risk.
2. **PR 1 — Characterization and ADR.** Golden tests for Sprint 13–21 and ADRs for ownership, account/project mapping, dialog identity, delivery semantics, storage and MCP security.
3. **PR 2 — Runtime foundation.** Singleton database engine, pooled HTTP client, UoW, live/ready health and telemetry.
4. **PR 3 — Canonical ingress.** One webhook, durable receipt, outbox, worker, retry/DLQ and shadow comparison.
5. **PR 4 — Generalized accounts.** Dynamic accounts/connections; RU/BY backfill; add a third account without code/env changes.
6. **PR 5 — Project isolation.** Workspace, ProjectSpace, AgentProfile, grants, account-project bindings, Dialog and context segments.
7. **PR 6 — Application services.** Inbox, tasks, draft revisions, approvals, send attempts, audit and routing.
8. **PR 7 — Adapter cutover.** Telegram/admin use application services; remove the sprint router chain after parity.
9. **PR 8 — MCP compatibility/security.** Move Sprint 19–21 to scoped query services, real auth boundary and cursors; no new tools.
10. **PR 9 — Scale hardening.** Partitions, hot/cold data, projections, load tests, backup/restore and runbooks.

Every PR must include scope, current head SHA, changed files, migrations, tests, CI result, manual plan, rollback plan, known limitations and next risk. Do not mix workflow-policy rewrites into a functional PR.

## 14. Required acceptance scenarios

### Multiple accounts

- RU, BY and a third test account complete receive → inbox → draft → send;
- the third account is added through DB/configuration without a code change;
- equal Telegram IDs in different accounts stay separate.

### One account, Alpha and ten Mayaks

- one ChannelAccount binds to `alpha_personal` and ten `mayak_*` ProjectSpaces;
- every ProjectSpace has an isolated inbox;
- one Dialog can change context by segment without duplication;
- Alpha receives only bounded current-project context;
- cross-Mayak and personal leakage tests return no data.

### Idempotency and concurrency

- 100 concurrent copies of one update create one durable/domain effect;
- no duplicate Message or active ReplyTask;
- out-of-order updates do not corrupt dialog/task state;
- 100 concurrent sends of one draft produce at most one external Telegram call;
- `sent` closes the task; `failed/unknown` does not;
- `unknown` is not retried automatically.

### MCP

- unauthenticated access returns no data;
- one project token cannot read another project;
- Sprint 19–21 contract tests remain green;
- MCP adapter imports no ORM and performs no direct SQL;
- pagination is real and bounded;
- no unbounded lists or exact operational counts.

### Quality

- no blanket `ignore-errors/noqa` in production critical paths;
- `ruff check .`;
- `mypy app tests`;
- unit and real PostgreSQL integration tests;
- Alembic upgrade/downgrade;
- Docker build;
- architecture and isolation tests;
- migration test against a production-like snapshot.

## 15. Performance evidence

On a dedicated performance environment:

- 1,000 synthetic durable webhook receipts/sec for 15 minutes;
- 5,000/sec burst for 60 seconds;
- p95 durable ACK below 250 ms;
- zero lost domain effects;
- backlog drains completely;
- database and HTTP pools remain bounded.

On 10–100 million metadata rows:

- first inbox page p95 below 300 ms;
- conversation page p95 below 500 ms;
- partition pruning and critical `EXPLAIN ANALYZE` plans are recorded;
- no sequential scan in critical operational queries;
- capacity thresholds and archive/sharding plan to 1B are documented.

If the baseline does not meet a target, report measured bottlenecks and propose a justified revised target. Never replace measurements with the word “scalable”.

## 16. Explicit non-goals

This refactor does not include:

- Oko / Oko Vozrozhdeniya business logic;
- Mayak CRM/LMS, prompts or billing;
- automated LLM routing or project moves;
- autonomous free-form replies;
- MCP write/send tools;
- identity-provider provisioning or a custom authorization server;
- Telegram Desktop import;
- voice/media/transcription;
- mass messaging;
- callback-send UX;
- a new user UI;
- mandatory Kafka, Kubernetes or microservices without benchmark evidence.

## 17. Final Definition of Done

```text
Telegram update
→ durable receipt
→ idempotent processing
→ correct ChannelAccount
→ correct isolated ProjectSpace
→ ReplyTask
→ DraftRevision
→ explicit Approval
→ one SendAttempt
→ origin ChannelConnection
→ outbound Message
→ append-only AuditLog
```

The refactor is complete only when:

- current Telegram-first behavior has no regression;
- one account serves personal Alpha and many isolated Mayaks;
- account count is not limited to RU/BY;
- unknown data cannot fall into personal context;
- MCP is protected and uses the shared application layer;
- there is one webhook and no sprint router chain;
- events are not silently lost and messages are not sent twice;
- the billion-message path is supported by measurements and capacity plans;
- migrations are reversible during expand/cutover;
- CI is green and architecture review is complete;
- production deployment occurs only after a separate direct command from Mark.

## 18. Instructions for the next executor

When Mark unfreezes Gate and sends the repository link:

1. Read [TEAM_WORKFLOW](docs/TEAM_WORKFLOW.md) first.
2. Re-fetch `main`, open PRs, latest CI and deployment status.
3. Compare current `main` with the frozen baseline `81163dcd`.
4. Do not reuse an old sprint/MCP/Mayaki branch.
5. Start with PR 0, MCP containment.
6. Keep the PR draft until its exact head CI and diff are reviewed.
7. Do not merge or deploy unless the repository workflow and Mark authorization permit it.
8. Do not call a local commit “done”; GitHub PR and CI are the source of truth.

The project is intentionally frozen at this handoff. The next action is not another feature. The next action, after explicit unfreeze, is the ordered stabilization/refactor above.

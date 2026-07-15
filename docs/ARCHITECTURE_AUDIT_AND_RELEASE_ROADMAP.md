# Gate architecture audit and agent release roadmap

Status: active implementation roadmap  
Audit baseline: `e103bc577308a1a341de1d12d3ff37d2601933ee`  
Verified application baseline before the handoff documentation PR: `3a4ba31d41ac11e6cf5ecf43ac0a23e77f34405f`  
Audit date: 2026-07-14  
Execution status verified: 2026-07-15

> Current execution status: Sprints 1 and 2 are delivered. Sprint 3 foundations and the
> transactional owner-admin lifecycle are delivered through PR #150, but Alpha is not released:
> its connector remains disabled and the worker, delivery-adapter and canary work is still required.
> BY contact enrichment, the third Business account and Oko remain deferred. For the exact handoff and next PR order, start with
> [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md).

## 1. Outcome

Gate has a working owner-operated Telegram Business and MCP product, strong confirmation and
idempotency rules, and several good dormant building blocks for durable ingress. It is not yet a
safe base for autonomous agents or external users.

The next release program must do two things together:

1. replace the synchronous legacy Telegram path with one durable canonical pipeline;
2. ship two bounded products on that pipeline:
   - an Alpha agent used through a separate bot in an approved Telegram group;
   - an Oko Vozrozhdeniya agent used in an explicitly agent-managed RU Telegram Business dialog.

This is an incremental strangler refactor, not a rewrite. Every cutover follows:

```text
contract → expand schema → fixture replay → shadow → canary → cutover → cleanup
```

## 2. Boundaries that remain binding

- Gate is the only communication transport. Do not create another message bridge.
- ManyChat remains acquisition/DM attribution only, never the communication source of truth.
- Human-managed replies keep the current exact-draft and explicit-confirmation send contract.
- Autonomous delivery is a different, narrower capability and must not reuse account-wide
  `AUTO_FULL` or silently weaken human confirmation.
- The group agent uses a separate Telegram bot credential and cannot access Telegram Business,
  Gate M, Mark's inbox or personal dialogs.
- Oko is RU-only during this program. BY contact work and a third personal account remain deferred.
- AI must identify itself as a digital assistant where relevant and must not impersonate Mark.
- Raw personal messages and live memory belong in PostgreSQL, not Git history. Git stores
  instructions, policies, schemas and evaluation fixtures. A future repository export may contain
  only approved structured projections in a separate private data repository.
- Voice/media and speculative billion-message infrastructure remain outside these releases.

## 3. What is already sound

- OAuth-protected, account/project-scoped Gate M tools with explicit output schemas.
- Exact account, recipient and text confirmation for human sends.
- An idempotent human `SendAttempt` path with `unknown` as a terminal non-retry state.
- Dynamic Telegram Business connection reconciliation and fail-closed account selection.
- Account-scoped imported contact identities.
- Application contracts and PostgreSQL adapters for receipt/outbox, bounded retries, stale claim
  recovery, queue health and payload-free incidents.
- Exact-SHA CI, automatic Railway deployment verification and production read smoke checks.
- Reversible migrations and concurrency coverage for the newest durable components.

These paths are preserved while the legacy ingress is replaced.

## 4. Audit findings

### Critical — block autonomous release

#### 4.1 Live ingress can acknowledge lost work

`app/api/routes.py` still performs normalization, persistence, task creation and Telegram calls
inside the webhook request. `app/main.py` converts an unhandled webhook exception into a successful
`200 ignored` response. The durable receipt/outbox and worker exist, but are dormant and only run a
shadow classifier.

Required correction: authenticate, persist a receipt/outbox pair and acknowledge. All business
processing moves to a worker. Poison work is durable and visible in DLQ; it is never only logged and
discarded.

#### 4.2 The route chain is the architecture

The base route and ten historical `sprint*` webhook layers contain roughly 5,800 lines and delegate
directly to one another. `app/api/routes.py` alone is about 1,360 lines. Several modules disable
Ruff or mypy. This makes routing order, persistence and sending inseparable.

Required correction: one public Telegram ingress adapter and one canonical application dispatcher.
Legacy routes are removed only after fixture and production-shadow parity.

#### 4.3 There is no durable agent ownership decision

The legacy base path creates `ReplyTask` before a future decision between human-managed and
agent-managed work. Text filters cannot safely keep Oko out of the human inbox.

Required correction: after an immutable `Message`, resolve `WorkOwnership` first. Create exactly
one of `ReplyTask`, `AgentTurn`, `AgentHandoff`, or an explicit ignored/quarantined outcome.

#### 4.4 Current persistence is not tenant-ready

`Account.owner` is a string, `Project.alias/name` are globally unique, and `Message`, `ReplyTask`
and `ReplyDraft` retain RU/default-project defaults. `Conversation` uniqueness includes
`project_id`, so one Telegram dialog can fragment when its project context changes.

Required correction: introduce an explicit `Workspace`, remove implicit default ownership from new
writes, identify a `Dialog` independently of project, and apply project through time-bounded context
bindings. Every new agent aggregate carries `workspace_id` from its first migration.

#### 4.5 Outbound calls have multiple authorization models

MCP human sending uses the guarded `SendAttempt` service, while legacy/admin/ping paths call
`TelegramClient.send_message` directly. A group/Oko agent must not become another direct path.

Required correction: all external sends enter a durable delivery ledger. Human sends require an
approval capability. Agent sends require a separate one-time `DeliveryGrant` bound to one trigger,
connector, dialog, recipient, exact text hash and expiry.

`app/infrastructure/reply_sends.py` also acquires row locks before live Telegram Business
validation, so a network call occurs while the database transaction remains open. Refactor send as
short reservation transaction → network call without row locks → short outcome transaction. Keep
the existing `unknown` rule when the external result is ambiguous.

### High — complete before pilot expansion

#### 4.6 Database engines are created per request

`app/api/deps.py:get_db_session` builds a new engine and pool for every dependency call and never
disposes that engine. This wastes connections and prevents meaningful pool health.

Required correction: create one engine/session factory in application lifespan, inject it, expose
readiness and dispose it once at shutdown.

#### 4.7 Application boundaries still depend on SQLAlchemy

`app/application/mcp_status.py` and `app/application/mcp_inbox.py` are large direct ORM query
modules and disable mypy. `app/api/mcp_routes.py` remains a large protocol/router/composition file
with direct grant queries.

Required correction: keep optimized SQL in infrastructure read repositories; application use cases
depend on typed ports/read models; the MCP adapter only authenticates, validates and serializes.

#### 4.8 Multi-bot transport configuration is singleton and scattered

The live app assumes one bot token, username, webhook secret and path. Some configuration is read
through `Settings`, while webhook secret and Telegram API URL use direct `os.getenv` calls.

Required correction: use typed configuration and a `TransportConnector` registry with one secret
reference and webhook identity per connector. The group credential can never resolve a Business
send capability.

#### 4.9 LLM-facing contracts expose provider identifiers

MCP structured content currently contains chat/user IDs and Business connection IDs. These are
also used as UI confirmation fields. They increase coupling and disclose infrastructure identity.

Required correction: introduce opaque Gate references for user-facing contracts. Keep provider IDs
inside transport/persistence adapters. Evolve MCP schemas compatibly before removing old fields.

#### 4.10 Privacy and retention are unspecified

Raw Telegram payloads are stored in more than one table and no retention/deletion classes are
implemented. Agent memory would amplify this problem if it copied whole histories.

Required correction: one raw receipt source, normalized immutable facts, bounded context material,
structured memory with source references/sensitivity/retention, and tested deletion/projection
rules.

#### 4.11 Operational evidence is incomplete

`/health` proves only that the process answers. There is no DB/queue readiness, verified
backup/restore result, delivery SLO, Telegram 429 metric, pool pressure metric or production parity
dashboard. Queue alert components are dormant.

Required correction: readiness, redacted metrics, controlled incident composition, runbooks,
restore drill and failure exercises before broader pilots.

### Medium — remove continuously, not in a big bang

- The ORM domain file is about 920 lines and mixes unrelated aggregates.
- Several legacy modules use file-wide lint/type exemptions.
- Status uses exact counts and inbox advertises a cursor that is always `null`.
- CI installs open dependency ranges instead of the committed lock and checks mypy only for `app`
  despite documentation claiming broader coverage.
- The web Docker image runs as root; only the contact-sync image switches user.
- The authoritative README snapshot is older than the current production commit.
- Production-like migration fixtures, schema-drift checks, dependency/security scanning and
  retention tests are missing.

## 5. Target architecture

```text
interfaces/
  http/telegram_webhook       authenticate + durable accept only
  mcp                         OAuth + JSON-RPC + typed presentation
  worker                      queue and agent runtime entry points

application/
  ingress                     normalize, resolve, own and route
  inbox                       human task use cases/read models
  agents                      session, turn, policy and handoff use cases
  delivery                    human approval and agent grant use cases
  ports                       persistence, Telegram, instruction and model ports

domain/
  tenancy                     Workspace and grants
  messaging                   Connector, Dialog, Message, ContextBinding
  agents                      definition, release, session, turn, ownership
  delivery                    grant/attempt state machines

infrastructure/
  postgres                    repositories and projections
  telegram                    Bot API and Business adapters
  instructions                deployed Git instruction bundles
  models                      provider adapter behind AgentRuntimePort
```

This is a dependency rule, not a mandate to rename every package at once. New code follows it;
legacy code moves only when its use case is cut over.

### 5.1 Canonical message flow

```text
bot-specific webhook authentication
→ durable Receipt + Outbox
→ immediate acknowledgement
→ canonical worker
→ Connector + Dialog + Participant resolution
→ immutable Message
→ ContextBinding + WorkOwnership
   ├─ human_managed   → ReplyTask
   ├─ agent_managed   → AgentTurn
   ├─ handoff_required→ AgentHandoff + ReplyTask
   └─ paused/ignored  → durable reason
→ optional Agent worker
→ structured response intent
→ policy decision
→ one-time DeliveryGrant
→ durable AgentSendAttempt
→ Telegram adapter
→ sent / failed / unknown
```

### 5.2 Agent aggregates

- `AgentDefinition`: stable identity such as `alpha_group` or `oko`.
- `InstructionRevision`: source repository/path, Git SHA, content hash, schema and validation state.
- `AgentDeployment`: revision, runtime/model profile, policy version, staged/active/paused state.
- `AgentInstallation`: one deployment allowed on one connector/dialog.
- `AgentSession`: participant/dialog state pinned to an instruction revision.
- `AgentTurn`: unique trigger message, bounded context references and structured result.
- `AgentResponseIntent`: exact candidate text and hash; cannot select its own target.
- `DeliveryGrant`: one-use capability for the exact target/trigger/text and expiry.
- `AgentSendAttempt`: reserved/sending/sent/failed/unknown/blocked.
- `AgentHandoff`: bounded reason linked to one human `ReplyTask`.
- `AgentMemoryEntry`: structured scoped fact with source, sensitivity and retention metadata.

Human `SendAttempt` and `AgentSendAttempt` stay separate until both paths are proven. This avoids
accidentally granting agent behavior through the existing human approval API.

### 5.3 Git instruction supply chain

Authoring may remain in a product repository such as `markmalina/oko`. Promotion creates a reviewed
instruction bundle pinned to the source commit and content hash. The first runtime loads the bundle
from the deployed artifact; it does not receive a GitHub token and does not read mutable `main` for
each message.

Each bundle contains:

```text
manifest
system instructions
policy
structured response schema
evaluation fixtures
source repository + commit + content hash
```

CI validates schema, allowed capabilities, size, secret patterns, prompt-injection fixtures,
handoff fixtures and deployment/revision linkage. Activation and rollback always name an immutable
revision.

The current `markmalina/oko/prompts/gate/oko-intake-v0.md` is draft and requires human approval for
every outbound intent. It remains valid only for fixtures and approval-bound shadow evaluation. It
must not be reinterpreted as permission for autonomous delivery. Sprint 5 requires a new separately
reviewed Oko revision that explicitly emits bounded response intents governed by Gate
`DeliveryGrant`; the source repository and this deployment must pin the same commit and digest.

### 5.4 Model data boundary

No real personal message may be sent to a model merely because execution is called “shadow”.
Before real-data evaluation, the selected provider/model profile must be owner-approved and
allowlisted, with documented no-training use, retention/deletion terms, data location/transfer
constraints and an egress allowlist. Provider logs and Gate telemetry contain IDs, reason codes,
latency and token counts, never message text or full prompts.

The runtime receives only a minimized, project/person/session-scoped context projection; it cannot
query arbitrary history. Context items carry source, sensitivity and retention classes. Deletion
must remove provider-bound cached material and derived memory according to the same policy.
Until these controls and user disclosure/consent are released, agent evaluation uses only synthetic
or explicitly approved anonymized fixtures.

### 5.5 Owner control plane and external release gates

Group approval, instruction promotion, deployment activation, pause, resume and revoke require an
owner-authenticated Gate principal scoped to the workspace, initially a dedicated
`gate:agents:manage` control surface. Telegram usernames, group-admin status and commands inside a
group never grant control-plane authority.

Creating a bot in BotFather, adding it to a real group, provisioning model credentials and changing
production configuration are external owner actions. Code and tests may be prepared in advance, but
none of those actions is implied by a merge or automatic Railway deployment; each requires Mark's
separate direct authorization at the release gate.

## 6. Product 1 — Alpha group agent

MVP rules:

- separate Telegram bot and token;
- group/supergroup only, Telegram Privacy Mode retained;
- unknown group creates at most a `pending_approval` installation and never calls a model;
- an owner-authenticated Gate principal approves the group, agent and exact instruction revision;
- invoke only by command/mention or reply to the bot;
- text-only, one inbound invocation to at most one reply;
- no browsing, arbitrary tools, proactive messages, member administration or Gate inbox access;
- context is installation-scoped and cannot cross groups;
- every turn audits instruction SHA, model profile, policy version and result code;
- removal, pause or any kill switch blocks delivery immediately.

Release acceptance:

- duplicate Telegram updates create one turn, at most one model generation and at most one Telegram
  `sendMessage` attempt;
- the group credential cannot technically send through Telegram Business;
- an unapproved group receives no generated response;
- instruction rollback affects new turns only and preserves the revision of in-flight turns;
- model output is delivered as plain text and cannot enable parse mode or choose another target.

## 7. Product 2 — Oko through Telegram Business

Activation requires all conditions:

- inbound RU `business_message` in a private dialog;
- exact permitted connector with current `can_reply=true`;
- sender is not Mark, a bot or service identity;
- no conflicting Oko enrollment/session;
- apply NFKC, trim/collapse whitespace and Unicode case-folding to both the first eligible
  enrollment message and the canonical phrase `Око возрождения`, then require exact equality
  (canonical normalized value `око возрождения`); prefixes, suffixes and punctuation do not
  activate;
- an existing personal history with Mark requires review rather than silently taking ownership.

`/start oko21_*` attribution from PR #122 remains an independent dormant acquisition signal; it is
not equivalent to the Business-message activation rule. The historical CTA text “Привет, Марк.
Хочу заниматься Око Возрождения.” and broader variants recognized by the draft Oko prompt also do
not activate this Business flow. The exact Gate enrollment contract must be published as a new
versioned Oko instruction revision before release.

Session states:

```text
absent → consent_pending → active → paused/completed
                           └→ handoff_required → human_managed
```

The exact enrollment phrase is the user's opt-in request. It creates `consent_pending` and permits
only one fixed reviewed disclosure. A confirmed delivery of that disclosure moves the session to
`active`; no second confirmation is required for the MVP. A failed or unknown disclosure delivery
atomically moves to `handoff_required`, creates exactly one human task and is never automatically
resent. Stop, human/Mark and pause commands are deterministic transport policies evaluated before
any model call in every non-closed state.

The first reply discloses a digital assistant and offers a simple stop/human path. In `active`,
messages remain Gate facts but do not create human reply tasks. Handoff atomically revokes agent
delivery, creates exactly one human task and preserves the current explicit-confirmation reply flow.

Every autonomous grant is bound to the exact RU connection, dialog, recipient, trigger message,
text hash, policy and Telegram reply-window expiry. BY never falls back. `unknown` is never retried.

Mandatory handoff includes medical/safety, legal/financial, complaint/refund, explicit request for
Mark, aggression, low confidence, invalid model output, prompt injection, limit exhaustion, repeated
provider failure or unavailable Business permission/window.

### 7.1 Dialog ordering, fencing and human takeover

Agent work is serialized per dialog/session. Canonical acceptance assigns a monotonic dialog
sequence; at most one turn for that session may be running or eligible for delivery. A newer inbound
message supersedes a response proposal that has not yet received a grant, so stale context cannot
produce a late answer.

`WorkOwnership` and `AgentSession` carry monotonic versions. Every turn, response intent and
delivery grant records the expected versions and trigger sequence. The worker re-reads them before
model execution, before reserving delivery and immediately before changing an attempt to
`sending`. A mismatch terminates the artifact as `superseded`; model output can never restore a
capability.

Handoff, stop, pause, removal, a kill switch, or an observed human outbound from Mark atomically
increments the relevant ownership/session version and invalidates all not-yet-sending intents and
grants. Human takeover moves the dialog to `human_managed`; resuming the agent is a separate
explicit action. No autonomous Telegram call may start after that takeover transaction commits.

A Telegram call that was already atomically marked `sending` before takeover cannot be recalled.
The takeover records `delivery_in_flight`; the eventual outcome is audited as sent, failed or
unknown and is never retried automatically. This unavoidable boundary is exercised in concurrency
tests and shown to the operator.

Per inbound, the two independent budgets are at most one model generation and at most one Telegram
`sendMessage` attempt. Connection/permission checks may make read-only provider calls, but cannot
deliver text and do not relax either budget. Model jobs and delivery attempts have independent
terminal/error state machines; an ambiguous model outcome never grants delivery, and an ambiguous
Telegram outcome remains terminal `unknown`.

Before the first agent pilot, every legacy direct-send path is either moved behind the common
delivery ledger or proven unreachable from agent and untrusted input. Physical deletion of
unreachable legacy code may remain in Sprint 6, but it is never a prerequisite deferred past an
agent security boundary.

## 8. Six sequential release sprints

Each sprint is delivered through several small PRs. A sprint is complete only after exact-head CI,
merge, exact Railway SHA and production smoke evidence.

### Sprint 1 — Reliable transport and refactor foundation

Engineering:

- create one lifespan-owned DB engine/session factory; add DB/queue readiness and clean shutdown;
- reuse bounded pooled HTTP clients and keep network calls outside database lock transactions;
- centralize typed configuration, fail-safe defaults and per-connector secret references;
- finish production-like identity fixture replay for the PR #114 boundary;
- introduce `Workspace`, connector-neutral `Dialog` identity and explicit `WorkOwnership` contracts;
- dual-write durable receipt/outbox behind a disabled/canary switch;
- tighten CI: lock-based installs, schema drift, production-shaped migrations, a non-root web
  image, dependency/secret/container scanning and progressive removal of file-wide mypy/Ruff
  exemptions;
- define redacted transport metrics and controlled queue alert composition.

Product increment: Gate can classify a group/private/Business update and produce a durable ownership
decision in shadow without changing the current inbox or sending anything.

Definition of done:

- no per-request engine creation;
- every accepted canary update has a durable receipt;
- replay decisions match legacy fixtures; mismatches are redacted and measurable;
- no current MCP/send regression; rollback and queue recovery are documented and exercised.

### Sprint 2 — Canonical durable ingress and versioned Agent Kernel

Engineering:

- make webhook success depend only on authenticated durable acceptance;
- run normalization, identity, context and ownership through the canonical worker;
- canary and then cut over one update class at a time; remove a legacy layer only after parity;
- add `AgentDefinition`, `InstructionRevision`, `AgentDeployment`, `AgentSession` and `AgentTurn`;
- add validated deployed instruction bundles and CI evaluations;
- define `AgentRuntimePort` and structured `reply | handoff | no_reply` output;
- put every agent-reachable outbound path behind the common delivery ledger and prove legacy direct
  sends unreachable from agent/untrusted input;
- define retention/deletion classes and provider-egress policy before any real message reaches a
  model;
- run agent proposals only on synthetic or explicitly approved anonymized fixtures, with no delivery
  grants, until the model data gate is approved.

Product increment: an approved Git instruction revision can generate a traceable proposal for a
fixture or permitted shadow turn, but it cannot send.

Definition of done:

- worker restart/duplicate/poison tests prove no lost or duplicated domain effect;
- each turn pins instruction SHA/hash and bounded context;
- user text cannot alter system instructions or access secrets/other dialogs;
- current human inbox remains behaviorally unchanged.

### Sprint 3 — Alpha group bot MVP

Engineering/product:

- add a separate `telegram_bot` connector and bot-specific webhook authentication;
- add installation lifecycle `discovered → pending_approval → active/paused/revoked`;
- retain Telegram Privacy Mode and command/reply-only engagement;
- add installation-scoped context and rate/token budgets;
- add policy evaluation, one-time `DeliveryGrant` and durable `AgentSendAttempt`;
- canary one approved instruction revision in one allowlisted test group;
- expose approval/pause/revoke/status only through the owner-authenticated control plane, without
  giving group users MCP access;
- stop at the external release gate before BotFather creation, group installation, model credential
  provisioning or production activation.

Product increment: Mark can add a separate bot to an approved group and let another person use one
versioned Alpha agent safely.

Definition of done:

- unknown groups do not call the model or send;
- group isolation, duplicate suppression, target binding, kill switch and `unknown` behavior pass;
- group bot has no credential path to Business or personal inbox;
- pilot messages show correct product disclosure and audit evidence.

### Sprint 4 — Oko ownership, consent and handoff

Engineering/product:

- implement RU-only exact first-message eligibility and conflict review;
- add Oko session/ownership states and consent/stop behavior;
- keep active Oko turns out of normal unanswered by construction, not text filtering;
- create one atomic agent handoff task, increment the ownership fence and prevent agent sends while
  handoff is open;
- serialize turns per dialog and make observed human outbound an immediate human takeover;
- connect only a new approved `markmalina/oko` revision that defines the exact enrollment phrase;
  keep the current approval-bound v0 revision fixture/shadow-only;
- run dynamic proposals only after the model data gate; allow only a fixed reviewed onboarding
  disclosure initially.

Product increment: a real eligible person can enter the Oko contour, receive the disclosed first
step and reach Mark through a reliable handoff without polluting the personal inbox.

Definition of done:

- BY/non-exact/old-conflicting dialogs do not activate automatically;
- active Oko facts remain queryable in the Oko scope and absent from human tasks;
- handoff revokes capabilities before task visibility;
- Mark's handoff response still requires the existing exact confirmation.

### Sprint 5 — Controlled Oko agent pilot

Engineering/product:

- activate a new separately approved Oko instruction revision whose response intents are explicitly
  governed by one-time `DeliveryGrant`; never reinterpret the current draft v0;
- enable bounded structured Oko responses for approved low-risk states;
- add project/person/session-scoped structured memory with retention metadata;
- validate current Business connection, target and 24-hour reply window before every reservation;
- enforce mandatory handoff policy, per-person/deployment budgets and all kill switches;
- canary one or a small allowlist of participants before broader enrollment;
- record product events needed for practice progression without making Gate the Oko business-logic
  source of truth.

Product increment: Oko can conduct a bounded private Telegram Business dialog and return control to
Mark whenever the scenario or safety policy requires it.

Definition of done:

- one inbound produces at most one model generation and at most one Telegram `sendMessage`
  attempt; permission/connection validation calls cannot deliver text;
- target cannot come from model output;
- prompt injection, provider failure, Telegram 429 and unavailable reply window fail closed;
- stop, handoff, pause, human takeover and revision rollback work in production canary;
- concurrency tests prove that a stale proposal cannot receive a grant after an ownership-version
  change and that an already-sending attempt is surfaced without retry.

### Sprint 6 — Operational release and reusable agent platform

Engineering/product:

- physically remove sprint routers and direct Telegram send calls already made unreachable before pilot;
- add SLO dashboards for webhook acceptance, queue lag/DLQ, turns, handoffs, sends/unknown, 429 and
  DB pool pressure;
- exercise DB restart, duplicate flood, model outage, Telegram timeout and rollback drills;
- verify backup/restore, retention/deletion and secret rotation;
- add keyset pagination/read projections and measured partition/archive thresholds;
- validate multiple group installations and prepare a second agent without transport changes;
- decide whether an approved structured memory projection to a separate private repository is
  still required.

Product increment: both group Alpha and Oko Business pilots have an operator runbook, measurable
reliability, safe rollout/rollback and a reusable path for the next Mayak agent.

Definition of done:

- release acceptance suites pass for both products;
- restore and incident drills have dated evidence;
- no file-wide type exemptions remain in active paths;
- adding another agent requires a validated instruction bundle/deployment, not a new webhook stack.

## 9. Cross-sprint engineering rules

- New canonical application modules import neither FastAPI nor SQLAlchemy; legacy exemptions shrink
  until Sprint 6 removes them from every active path.
- New canonical interfaces/webhooks call neither Telegram nor a model directly; legacy calls are
  first made unreachable from agent/untrusted input and then removed.
- No new direct call to `TelegramClient.send_message` outside the delivery adapter.
- No agent can supply its connector, account, dialog or recipient target.
- Every external write has a durable idempotency key and terminal `unknown` behavior.
- All new roots are workspace-scoped and every query proves scope explicitly.
- New active-path code is strict-mypy and Ruff clean; exemptions may only shrink.
- Logs/metrics exclude message text, prompts, provider identifiers and secrets unless a separately
  authorized diagnostic contract says otherwise.
- Migrations use expand/backfill/validate/cutover and production-shaped fixtures.
- Human and agent delivery permissions remain separate.
- A sprint does not combine an architecture cutover with unrelated BY, third-account or media work.

## 10. Release acceptance matrix

### Existing Gate

- RU/BY receive, inbox, context, draft and explicitly confirmed send remain stable.
- OAuth and output schemas remain compatible.
- No unknown routing silently defaults to RU.

### Group Alpha

- approved group/revision only;
- Privacy Mode command/reply invocation only;
- one update → one turn → at most one model generation and one Telegram `sendMessage` attempt;
- no cross-group context;
- pause/revoke/kill switch and revision rollback proven.

### Oko

- RU exact eligibility only;
- disclosure/stop and conflict review;
- agent-owned messages excluded from human tasks without data loss;
- mandatory handoff creates one task and blocks the agent;
- one inbound → one exact-target grant → at most one send;
- human reply continues through explicit confirmation.

### Operations

- exact deploy SHA, health and readiness;
- no accepted update without a durable receipt;
- bounded queue lag and visible DLQ;
- backup/restore and failure drills;
- redacted audit includes connector, ownership, revision, policy and outcome references.

## 11. Audit closure matrix

| Finding | Release gate | Required evidence |
|---|---|---|
| Per-request engines and false health | Sprint 1 | one lifespan pool, dispose test, DB/migration readiness |
| Lost update and legacy router chain | Sprint 1 shadow; Sprint 2 cutover; Sprint 6 deletion | durable acknowledgement, parity, DLQ and one public handler |
| Missing ownership/tenancy/dialog seam | Sprint 1 contracts; Sprint 2 active path | workspace-scoped writes, dialog identity and versioned ownership |
| Multiple outbound authorization paths | Sprint 2 before any pilot; Sprint 6 cleanup | common ledger, unreachable direct paths, later physical deletion |
| Singleton transport configuration | Sprint 1 config; Sprint 3 connector | bot-specific secret/credential isolation |
| Model data, raw payload and memory retention | Sprint 2 before real-data shadow; Sprint 6 drill | provider gate, minimization, deletion tests and retention drill |
| Group control-plane authority | Sprint 3 before installation | owner OAuth scope, approval/pause/revoke audit |
| Opaque MCP references and ORM read separation | Sprint 6 before external control-surface expansion | compatible refs, typed query ports and isolation tests |
| Non-root image, dependency and CI gaps | Sprint 1 baseline; Sprint 6 enforcement | reproducible image, scans, schema drift and strict active-path typing |
| Operational evidence and restore | Sprint 1 basic signals; Sprint 6 release gate | readiness/metrics plus dated restore, rollback and incident drills |

## 12. Current implementation order

The original first implementation order has been completed through the durable canonical pipeline
and Agent Kernel foundations. Continue Sprint 3 in the following independently reversible PRs:

1. compose the default-disabled Alpha worker in shadow mode over the transactional lifecycle
   delivered by PR #150;
2. add the separate Alpha delivery adapter with grant consumption and terminal unknown handling;
3. add webhook reconciliation, kill-switch operations and a single approved-group canary.

Do not activate `AlphaViaBot`, add secret values, change production flags, start BY/third-account
work or begin Oko until these gates pass. The operational commands, owner-input boundary and
definition of done are maintained in [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md).

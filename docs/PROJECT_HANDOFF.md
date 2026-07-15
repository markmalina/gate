# Gate project handoff

> Canonical continuation guide for a new developer or implementation agent.  
> Verified against GitHub `main` and Railway production on 2026-07-15.  
> Application commit before this documentation PR: `3a4ba31d41ac11e6cf5ecf43ac0a23e77f34405f`.

## 1. Start here

Read these files in this order:

1. `docs/TEAM_WORKFLOW.md` — authority, secrets, PR, CI, merge and deployment rules.
2. This document — verified state, delivered scope and exact continuation order.
3. `docs/ARCHITECTURE_AUDIT_AND_RELEASE_ROADMAP.md` — target architecture and Sprints 1–6.
4. `docs/GATE_FULL_TECHNICAL_SPEC.md` — long-form technical specification.
5. `docs/GATE_OPERATOR_REGULATIONS.md` — production operating rules.

GitHub `main` is the source of truth. Chat history, agent memory and local workspaces are not.
Before changing anything, query GitHub and production again; all counts below are snapshots.

## 2. What Gate is

Gate is Mark's workspace-scoped communication gateway. It currently receives Telegram Business
communication facts, exposes a protected MCP interface to ChatGPT, prepares drafts and sends only
the exact draft that Mark explicitly confirms. Gate is transport and safety infrastructure; Alpha,
Oko and future Mayak products supply versioned instructions and product policy through isolated
agent deployments.

The protected human path is already operational:

```text
Telegram Business → Gate facts → unanswered task → bounded context
→ exact draft → Mark confirms account + recipient + text
→ one idempotent send attempt → Telegram → audit
```

Never weaken this path while adding agents. A saved draft is not approval. An ambiguous provider
outcome is terminal `unknown` and is not retried automatically.

## 3. Verified production snapshot

Verified through Gate M after PR #148 deployed:

| Item | State |
|---|---|
| GitHub repository/default branch | `markmalina/Gate`, `main` |
| Railway production commit | `3a4ba31d41ac` |
| Environment / mode | `production` / `manual` |
| Workspace | `mark-personal` |
| Existing Telegram bot | `gate_m_bot` |
| MCP | OAuth 2.0 enforced; 5 tools |
| Human send | enabled only through exact explicit confirmation |
| RU Telegram Business | `ready`, `can_reply=true` |
| BY Telegram Business | `ready`, `can_reply=true` |
| RU contacts | 90, source `telegram_desktop_export` |
| BY contacts | 0; import intentionally deferred |
| Snapshot counts | 27 conversations, 660 messages, 170 reply tasks, 3 unanswered, 28 drafts |

The five MCP tools are `gate_get_status`, `gate_list_unanswered`, `gate_get_reply_context`,
`gate_save_reply_draft` and `gate_send_approved_reply`.

The separate Alpha connector is present in code but disabled in production. No Alpha installation,
delivery permission, token value or autonomous send has been activated. The chosen Telegram username
is `AlphaViaBot`; treat it as reserved owner input until configuration and canary verification prove it.

## 4. Delivered release history

### Sprint 1 — canonical transport and ownership foundation: complete

PRs #137–#142 delivered:

- default-disabled durable Telegram receipt/outbox acceptance;
- one-shot retry/dead-letter worker with bounded failures;
- SELECT-only Business identity resolution;
- workspace-scoped connector-neutral `Dialog`, `CanonicalMessage` and `WorkOwnership` contracts;
- expand-only canonical schema;
- atomic idempotent canonical persistence;
- Telegram-to-canonical projection composed in the default-disabled shadow worker.

The product result is a durable, idempotent ownership decision in shadow without changing the human
inbox or sending.

### Sprint 2 — Agent Kernel and immutable instructions: complete for shadow

PRs #143–#145 delivered:

- transport-neutral `AgentDefinition`, `InstructionRevision`, `AgentDeployment`, `AgentSession` and
  `AgentTurn` contracts;
- structured `reply | handoff | no_reply` output with no target or delivery authority;
- workspace-scoped Agent Kernel tables and reversible migration;
- deployed-artifact instruction bundle loader with SHA-256 verification, path/size/capability checks;
- offline evaluation-fixture runtime and traceable proposal metadata pinned to instruction Git SHA,
  content hash and policy version.

No mutable GitHub `main` read, Git credential, model network call or agent delivery exists in this
shadow result.

### Sprint 3 — Alpha group bot: canary code delivered, activation evidence pending

PRs #146–#152 and PR #153 delivered the current disabled canary boundary:

- `discovered → pending_approval → active/paused/revoked` installation contract;
- transactional workspace-scoped discovery, approval, pause, revoke and read-only status;
- idempotent per-trigger rate reservation and bounded token accounting behind owner-admin control;
- default-disabled delivery-free shadow orchestration with one durable Agent turn per trigger;
- installation-pinned bundle verification and bounded same-group context reconstruction;
- persisted `reply | handoff | no_reply` trace with duplicate/restart/poison isolation;
- exact-target grant consumption and a literal bot-only Telegram delivery adapter;
- durable `sent | failed | blocked | unknown` outcomes with terminal-unknown replay;
- group/supergroup-only explicit command/mention/reply invocation policy;
- human-sender, active-installation and rate gates;
- five-minute, exact-target, exact-trigger, exact-candidate, one-use `DeliveryGrant` contract;
- agent-only `AgentSendAttempt` with terminal `unknown`, separate from human sending;
- separate default-disabled `/telegram/alpha/webhook` with its own secret and connector key;
- durable workspace-scoped tables for installations, grants and agent send attempts.

This is not yet a generally available Telegram agent. The one-group canary must remain disabled
until Mark supplies the owner-only configuration and explicitly authorizes activation.

## 5. Important code map

| Path | Responsibility |
|---|---|
| `app/api/telegram_ingress.py` | Business durable seam plus frozen legacy registration boundary |
| `app/api/alpha_telegram_ingress.py` | isolated, default-disabled group-only Alpha receipt endpoint |
| `app/application/canonical_messaging.py` | connector-neutral messaging/ownership contracts |
| `app/application/telegram_canonical_projection.py` | Telegram projection with fail-closed provider identity |
| `app/application/agent_kernel.py` | immutable revision/deployment/session/turn/runtime contracts |
| `app/application/instruction_bundles.py` | deployed bundle verification and offline fixture runtime |
| `app/application/agent_shadow.py` | traceable delivery-free shadow proposal service |
| `app/application/alpha_shadow_worker.py` | explicit invocation and idempotent shadow orchestration |
| `app/application/alpha_group.py` | installation, invocation, grant and send-attempt invariants |
| `app/infrastructure/alpha_shadow_worker.py` | transactional binding, context and turn persistence |
| `app/application/alpha_delivery.py` | opaque-grant-only delivery contract |
| `app/infrastructure/alpha_delivery.py` | exact-target consumption, attempt ledger and literal Telegram adapter |
| `app/application/alpha_canary.py` | single-group gates, disclosure and orchestration |
| `app/infrastructure/alpha_canary.py` | membership reconciliation and exact-revision grant issuance |
| `app/services/telegram_alpha_webhook.py` | isolated webhook reconciliation for two update types |
| `app/worker/alpha_shadow.py` | separate default-disabled Alpha runtime boundary |
| `app/infrastructure/canonical_messaging.py` | atomic PostgreSQL canonical ingress store |
| `app/models/canonical.py` | workspace/connector/dialog/message/ownership ORM |
| `app/models/agent.py` | Agent Kernel ORM |
| `app/models/alpha.py` | Alpha installation/grant/send-attempt ORM |
| `alembic/versions/0016_*` | canonical foundation |
| `alembic/versions/0017_*` | Agent Kernel |
| `alembic/versions/0018_*` | Alpha delivery capabilities |
| `app/api/mcp_routes.py` | current protected ChatGPT MCP adapter |
| `app/infrastructure/reply_sends.py` | existing human-confirmed idempotent send path |

## 6. Exact next development order

Continue Sprint 3 with the remaining small PRs in this order. Each PR must pass exact-head CI,
squash merge and exact Railway SHA verification before the next one.

### PR A — transactional Alpha repositories and operator lifecycle: delivered by PR #150

The lifecycle repository now persists idempotent pending discovery, binds owner-approved active
deployments and validated immutable revisions, blocks inactive/disabled installations, revokes
unused grants on pause/revoke and accounts for one bounded usage reservation per trigger. Its
control surface is protected by the existing owner-admin bearer boundary and is not exposed to
group members or MCP.

### PR B — Alpha worker orchestration in shadow: delivered by PR #151

The default-disabled worker now proves the workspace, bot connector, group dialog, active
installation, deployment and validated revision before reserving one usage record and one durable
turn per canonical trigger. It recognizes only `/alpha`, an exact bot mention or a verified
reply-to-bot, loads the installation-pinned deployed bundle, reconstructs at most 32 same-dialog
messages and persists a delivery-free `reply | handoff | no_reply` trace. PostgreSQL and application
tests prove duplicate replay, restart-unknown, poison, cross-group and prompt-injection behavior.
No `DeliveryGrant`, Telegram send or model network runtime is created by this step.

### PR C — separate Alpha delivery adapter: delivered by PR #152

The delivery command accepts only workspace and opaque grant identity. In one locked transaction it
derives the candidate from the matching proposed Agent turn, derives the target from the installed
group dialog, consumes the grant and commits a `sending` attempt before Telegram I/O. The literal
bot adapter exposes only `chat_id` and `text`: it accepts no Business connection, parse mode,
markup, tool call or model-supplied target. Outcomes are durable `sent | failed | blocked | unknown`;
a replay of `sending` becomes terminal `unknown` and never calls Telegram again. The adapter is
not yet connected to webhook reconciliation and remains unreachable in production.

### PR D — webhook reconciliation, kill switch and one-group canary: delivered by PR #153

- webhook reconciliation requests only `message` and `my_chat_member` for the separate Alpha bot;
- ingress rejects mixed or unapproved shapes before storage;
- connector, shadow, canary and delivery kill switches all default to false;
- the first verified membership webhook pins one group in the database without manual ID entry;
- any later discovery for another group fails closed;
- membership discovery/removal reconciles pending or revoked installation state;
- grant issuance re-proves the active installation, deployment and validated exact revision;
- every delivered candidate receives the fixed Alpha disclosure prefix;
- duplicate triggers replay the same turn/grant/attempt and never create a second provider call;
- owner status reports redacted activation checks and durable installation binding.

Only the live canary evidence remains: Privacy Mode confirmation, disclosure, duplicate suppression,
target binding, pause/revoke and the durable turn/grant/send-attempt audit trail. Mark Sprint 3
complete only after that evidence is captured.

After Sprint 3, proceed to Sprint 4 Oko RU ownership/consent/handoff. BY activation and a third
Business account remain explicitly out of scope until Mark changes that priority.

## 7. Owner inputs required only at the canary boundary

Do not request or store secret values in Git, issues, PRs, logs or chat. At the final Alpha canary,
Mark supplies through Railway secret management:

```text
TELEGRAM_ALPHA_BOT_TOKEN
TELEGRAM_ALPHA_BOT_USERNAME=AlphaViaBot
TELEGRAM_ALPHA_WEBHOOK_SECRET
GATE_ALPHA_CONNECTOR_ENABLED
GATE_TELEGRAM_WORKER_ENABLED
GATE_ALPHA_SHADOW_WORKER_ENABLED
GATE_ALPHA_CANARY_ENABLED
GATE_ALPHA_DELIVERY_ENABLED
GATE_ALPHA_PRIVACY_MODE_CONFIRMED
```

Keep every `GATE_ALPHA_*_ENABLED` flag false until PRs A–D are merged and Mark explicitly
authorizes the canary checklist.
Mark also creates one dedicated test group, keeps BotFather Group Privacy enabled and adds the bot
without personal-inbox or Business credentials.

Production configuration changes, manual deploys, retries and rollbacks require Mark's explicit
authorization. Normal green PR merges may use the standing auto-merge rule in `TEAM_WORKFLOW.md`.

## 8. Local development bootstrap

The supported shell for owner instructions is Windows PowerShell. A developer may use another shell,
but must preserve the same commands and checks.

```powershell
git clone https://github.com/markmalina/gate.git
Set-Location gate
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install ".[dev,contact-sync]"
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy app
& .\.venv\Scripts\python.exe -m pytest
```

Use a disposable PostgreSQL database for migration work, never production:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate"
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m alembic downgrade -1
& .\.venv\Scripts\python.exe -m alembic upgrade head
```

CI is authoritative and additionally exercises production-shaped PostgreSQL migrations and both
Docker builds. A local green run is not completion.

## 9. Environment-variable inventory

Never put values in documentation. Important names:

- core: `DATABASE_URL`, `GATE_ENV`, `RAILWAY_GIT_COMMIT_SHA`, `RAILWAY_SERVICE_NAME`;
- Business Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`,
  `TELEGRAM_WEBHOOK_SECRET`;
- canonical rollout: `GATE_TELEGRAM_DURABLE_ACCEPTANCE_MODE`,
  `GATE_TELEGRAM_WORKER_ENABLED`, `GATE_WORKSPACE_ID`;
- Alpha: `TELEGRAM_ALPHA_BOT_TOKEN`, `TELEGRAM_ALPHA_BOT_USERNAME`,
  `TELEGRAM_ALPHA_WEBHOOK_SECRET`, `GATE_ALPHA_CONNECTOR_ENABLED`,
  `GATE_ALPHA_SHADOW_WORKER_ENABLED`, `GATE_ALPHA_CANARY_ENABLED`,
  `GATE_ALPHA_DELIVERY_ENABLED`, `GATE_ALPHA_PRIVACY_MODE_CONFIRMED`;
- MCP/Auth0: see `app/core/config.py` and `docs/MCP_CHATGPT_AUTH0_RUNBOOK.md`;
- safe human sending: `GATE_SEND_ENABLED`, `GATE_ACCOUNT_RU_SEND_ENABLED`,
  `GATE_ACCOUNT_BY_SEND_ENABLED`.

Absence, emptiness or ambiguity must fail closed. Never introduce an RU fallback.

## 10. Non-negotiable engineering rules

- Gate is the only Telegram transport for these product paths.
- Human replies remain exact draft + explicit confirmation.
- Business and Alpha credentials, webhook paths and send permissions remain technically isolated.
- Agent/model output never selects workspace, connector, account, dialog, recipient or parse mode.
- Every accepted update and external write is durable and idempotent.
- Every new aggregate and query proves workspace scope.
- Network calls occur outside database lock transactions.
- `unknown` delivery state is terminal without automatic retry.
- Message text, prompts, provider identifiers and secrets stay out of logs and repr.
- Personal data belongs in the database, not Git.
- BY contact import, third Business account and Oko activation do not enter an unrelated PR.
- Keep PRs small; never combine a schema/cutover with unrelated product work.

## 11. Completion and handoff checklist

For every PR:

1. branch from the exact current `main` SHA;
2. review the complete diff and privacy/safety impact;
3. run Ruff, strict mypy, pytest, migration up/down and Docker CI;
4. inspect exact failure logs and fix only the observed cause;
5. merge only the exact green head, preferably squash;
6. wait for Railway to report the exact merge SHA;
7. smoke-check Gate M status and the affected safety boundary;
8. update this handoff when release state, activation requirements or next order materially changes.

A future owner should be able to continue from GitHub alone. If a decision exists only in chat,
write it here or in the relevant specification before relying on it.

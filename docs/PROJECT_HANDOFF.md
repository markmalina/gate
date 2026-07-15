# Gate project handoff

> Canonical continuation guide for a new developer or implementation agent.  
> Verified against GitHub `main` and Railway production on 2026-07-15.  
> Verified production commit before this release PR: `7c2781602448ac140e1e31ea27cb93414e3333d2`.

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

Verified through Gate M after PR #163 deployed:

| Item | State |
|---|---|
| GitHub repository/default branch | `markmalina/Gate`, `main` |
| Railway production commit | `7c2781602448` |
| Environment / mode | `production` / `manual` |
| Workspace | `mark-personal` |
| Existing Telegram bot | `gate_m_bot` |
| MCP | OAuth 2.0 enforced; 5 tools |
| Human send | enabled only through exact explicit confirmation |
| RU Telegram Business | `ready`, `can_reply=true` |
| BY Telegram Business | `ready`, `can_reply=true` |
| RU contacts | 90, source `telegram_desktop_export` |
| BY contacts | 0; import intentionally deferred |
| Snapshot counts | 29 conversations, 684 messages, 181 reply tasks, 5 unanswered, 28 drafts |
| Alpha canary audit | 4 accepted messages, 2 Agent turns, 1 delivery grant, 1 send attempt |

The five MCP tools are `gate_get_status`, `gate_list_unanswered`, `gate_get_reply_context`,
`gate_save_reply_draft` and `gate_send_approved_reply`.

The separate `AlphaViaBot` connector is active only for the first database-pinned test group. The
owner-run `/alpha` test delivered the reviewed fixed response with disclosure through one grant and
one send attempt. The active installation still uses the network-free evaluation fixture. The dormant Responses runtime
and reviewed `alpha-model-v1` artifact are packaged, while the model deployment is staged only:
ordinary model generation and provider data egress are not active.

Dated live silence evidence (2026-07-15): the owner sent an ordinary group message without
`/alpha`, a bot mention or a reply to the bot. After more than 12 seconds, Gate M still reported
4 accepted Alpha messages, 2 Agent turns, 1 delivery grant and 1 send attempt. Privacy Mode therefore
kept the message outside Alpha ingress; no turn, grant or delivery was created.

## 4. Delivered release history

### Sprint 1 — canonical transport and ownership foundation: complete

PRs #137–#142 delivered:

- default-disabled durable Telegram receipt/outbox acceptance;
- supervised, default-disabled retry/dead-letter worker with bounded failures;
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

### Sprint 3 — Alpha group bot: fixed canary happy path verified

PRs #146–#161 delivered and activated the owner-approved fixed one-group canary boundary:

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

This is not yet a generally available or model-backed Telegram agent. The fixed canary is active
only in the pinned group. Non-command silence has dated live evidence. Pause/revoke still needs a bounded live drill before
Sprint 3 is fully complete.

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
| `app/infrastructure/openai_agent_runtime.py` | dormant structured Responses runtime behind both model release gates |

## 6. Exact next development order

The fixed one-group canary remains the production rollback baseline. The data-gated Responses runtime,
reviewed `alpha-model-v1` bundle and staged `alpha-model-v1-openai` deployment are delivered. The
staged deployment is not installed in the group and both model release gates remain false.

Continue in this order:

1. Run a bounded pause/revoke live drill against the fixed canary, then restore the exact fixed
   revision. Ordinary non-command silence was proven on 2026-07-15.
2. Keep the reviewed fixtures as the network-free regression baseline. Do not interpret fixture
   success as permission to send group text to a model provider.
3. Only after Mark explicitly approves bounded same-group provider egress and provisions
   `OPENAI_API_KEY` in Railway, run a separate one-group model-shadow release with delivery disabled.
   That release must rebind only the pinned installation to the exact staged deployment and enable
   both model gates; unknown groups, inactive installations, ordinary Privacy Mode traffic and
   duplicate triggers must produce no provider call.
4. Review the redacted shadow evidence. Enable Alpha delivery for one bounded model canary only in a
   separate owner-authorized configuration step, with the fixed revision retained as rollback.
5. After the bounded Alpha model canary is stable, resume Sprint 4 Oko RU. BY and a third Business
   account remain out of scope.

## 7. Owner inputs required only at the canary boundary

Do not request or store secret values in Git, issues, PRs, logs or chat. The live fixed canary
configuration already exists in Railway; only variable names belong in repository documentation:

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

The existing transport/canary flags are owner-authorized for the one pinned test group. The model
path remains separately disabled. Its only secret is `OPENAI_API_KEY`; safe defaults are:

```text
OPENAI_API_BASE_URL=https://api.openai.com/v1
GATE_ALPHA_MODEL_RUNTIME_ENABLED=false
GATE_ALPHA_MODEL_DATA_GATE_APPROVED=false
GATE_ALPHA_MODEL_MAX_OUTPUT_TOKENS=512
GATE_ALPHA_MODEL_TIMEOUT_SECONDS=30
```

Do not set either model gate true merely because the code or credential exists. Activation also
requires a new reviewed immutable Alpha bundle/deployment and explicit owner approval of provider
egress for bounded same-group context.

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

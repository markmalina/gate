# Gate

Gate is a separate cloud-first communication gateway backend.

Primary Telegram bot: `@gate_m_bot`  
Repository: https://github.com/markmalina/gate  
Status: private

## 1. Development workflow

Development is managed by a controlled GitHub workflow:

```text
Mark → ChatGPT → GitHub PR → CI → ChatGPT review → Mark explicit merge approval → Mark explicit deploy approval
```

Rules:

- Mark defines intent, priorities and constraints.
- ChatGPT writes precise implementation tasks, reviews PRs and controls architecture quality.
- ChatGPT may create or update GitHub PRs directly when it decides this is the correct next development step.
- Codex may still be used to generate code or patches, but Codex local output is not the source of truth.
- GitHub PR + CI is the source of truth, not a local Codex workspace or textual summary.
- Duplicate PRs are not allowed; one task should have one active PR.
- Do not merge while CI is red.
- ChatGPT may merge through the GitHub connector only after Mark gives a direct merge command.
- Green CI alone is not merge approval.
- Production deployment happens only after Mark gives a direct deploy command.

Full team workflow: [`docs/TEAM_WORKFLOW.md`](docs/TEAM_WORKFLOW.md).

## 2. What Gate is

Gate is the new architecture name that replaces the old working name “Secretary”.

Gate is not Alpha.  
Gate is not Mayak.  
Gate is not SysteM.

Gate is a separate technical project that connects Telegram bots, Telegram accounts and Mark's internal projects.

Core formula:

```text
Telegram → Gate → Project → Reply → Approval → Telegram → Memory
```

Gate does not think by itself. Gate routes messages, delivers replies only when the configured mode permits it, and records communication facts.

## 3. Current technical assignment

Bootstrap Gate as a production-oriented backend using the same cloud-first stack pattern as the previous Telegram Gateway / Secretary project.

Current v0 scope:

- FastAPI backend;
- PostgreSQL database;
- SQLAlchemy 2 async domain models;
- Alembic migrations;
- `/health` endpoint;
- `/deployment/fingerprint` endpoint with no secrets;
- Railway deployment config;
- Dockerfile;
- GitHub Actions CI;
- default reply mode: `manual`;
- no automatic sending in v0.

The first functional direction after the backend foundation is a safe owner workflow:

```text
incoming Telegram message
→ Gate event
→ project routing
→ suggested reply / draft
→ Mark approval
→ explicit send
→ audit log / memory fact
```

## 4. Product principles

- Gate is a communication gateway, not the brain.
- Bots are adapters, not business-logic containers.
- Gate Core owns routing, permissions, reply modes and audit logging.
- Contexts must not be mixed: personal Mark ≠ Mayak ≠ Alpha ≠ client project.
- AI may suggest text in future versions, but must never approve or send by itself.
- Real secrets must live only in Railway or GitHub secret storage.

## 5. Stack

- Python 3.12
- FastAPI
- PostgreSQL
- SQLAlchemy 2 async
- asyncpg
- Alembic
- pydantic-settings
- pytest + anyio
- ruff
- mypy
- Dockerfile
- Railway deployment
- GitHub Actions CI

## 6. Cloud deployment

Gate is designed to run as a deployed backend rather than a localhost-only product path.

Railway should provide these environment variables:

```text
DATABASE_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=gate_m_bot
GATE_ENV=development
GATE_DEFAULT_MODE=manual
ACTION_BEARER_TOKEN=
ADMIN_BEARER_TOKEN=
```

Use `.env.example` as a safe template only. Do not commit real secrets or Telegram tokens.

Railway deployment must run migrations before app start:

```text
alembic upgrade head
```

## 7. Health endpoints

- `GET /health` returns service health and current non-secret mode metadata.
- `GET /deployment/fingerprint` returns non-secret deployment metadata such as service, environment, commit SHA, primary bot username and default mode.

## 8. Core entities

### Bot

A concrete Telegram bot adapter: username, secret reference, runtime state and permissions.

### Account

A concrete Telegram account context: Mark's personal account, Belarus account, Russian account, project account or future client account.

### Project

An internal project or agent that receives routed events through an adapter boundary.

### Route

A routing rule that maps bot, account, chat and event type to a project.

### GateEvent

A received communication event with bot, account, chat, sender, type, text and payload.

### AuditLog

An audit record for routing, approval, operational and future sending decisions.

## 9. Reply modes

| Mode | Meaning | v0 status |
|---|---|---|
| `manual` | Gate suggests or records a reply; Mark sends manually. | Default |
| `approval` | Gate prepares a reply; Mark confirms before any send path. | Planned |
| `auto-safe` | Gate answers only safe predefined cases. | Future |
| `auto-full` | Gate answers autonomously inside a project boundary. | Future, explicit permission only |

## 10. Repository structure

```text
app/
  main.py
  api/
    deps.py
    routes.py
  core/
    config.py
  models/
    domain.py
  services/
alembic/
  versions/
docs/
  TEAM_WORKFLOW.md
tests/
.github/workflows/ci.yml
Dockerfile
railway.toml
pyproject.toml
.env.example
```

## 11. Development checks

CI runs the production backend checks:

```bash
ruff check .
mypy app tests
pytest
alembic upgrade head
alembic downgrade base
docker build -t gate:ci .
```

## 12. Current status and next development step

This section is the handoff for any future AI assistant, ChatGPT session, or Codex task. Read this before proposing the next action.

### Current status

- Bootstrap PR `#2` was merged into `main` with green CI.
- The repository now contains the cloud-first FastAPI backend foundation.
- Railway project/service exists for Gate.
- Current Railway domain: `https://gate-production-bab1.up.railway.app`.
- Railway deployment is healthy.
- Verified health endpoint response:

```json
{"status":"ok","service":"gate","environment":"production","default_mode":"manual"}
```

- Verified deployment fingerprint response:

```json
{"service":"gate","environment":"production","commit_sha":"9c764170afaf42d0031f2da7fd056f0652db0444","bot_username":"gate_m_bot","default_mode":"manual"}
```

- `/deployment/fingerprint` does not expose `TELEGRAM_BOT_TOKEN` or other secrets.
- No Telegram webhook sprint is merged yet.
- No automatic Telegram sending exists in `main` yet.
- Current Codex Tasks environment cannot reliably push branches or create PRs; ChatGPT may create GitHub PRs directly when needed.

### Immediate operational step

Railway is ready. Do not keep debugging infrastructure unless `/health` or `/deployment/fingerprint` regresses.

Before testing Telegram product behavior, ensure these backend variables exist in Railway service `gate`:

```env
DATABASE_URL=<PostgreSQL URL using postgresql+asyncpg://...>
TELEGRAM_BOT_TOKEN=<set only in Railway; never commit or paste into chat>
TELEGRAM_BOT_USERNAME=gate_m_bot
GATE_ENV=production
GATE_DEFAULT_MODE=manual
TELEGRAM_WEBHOOK_SECRET=<random long secret>
ACTION_BEARER_TOKEN=<random long secret>
ADMIN_BEARER_TOKEN=<random long secret>
```

Do not paste secret values into ChatGPT or commit them to GitHub.

### Next product sprint: Gate Telegram Ping v1

The next development sprint must produce observable product functionality, not only infrastructure.

Goal:

```text
Telegram Business / Telegram bot message
→ Gate webhook
→ PostgreSQL event storage
→ exact safe keyword match
→ Telegram auto-reply
→ audit log
```

First product behavior:

```text
incoming text: ping
reply: Gate online
```

Only `ping` should trigger automatic reply in the first product test. Do not add broad automatic replies.

### Gate Telegram Ping v1 scope

Implement in a new PR:

- `POST /telegram/webhook`;
- verification of `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET`;
- parsing of Telegram `message`, `business_message`, and `business_connection` updates;
- persistence of incoming Telegram events into `gate_events`;
- `app/services/telegram.py` with safe `send_message(...)` using `httpx.AsyncClient`;
- exact keyword rule: `ping` → `Gate online`;
- no other outgoing auto-replies;
- `audit_logs` entries for incoming update and auto-safe reply;
- tests for secret validation, event persistence, audit log, and one mocked Telegram send;
- README update after implementation.

### Safety constraints for the next sprint

Do not:

- add AI replies;
- add free-form auto-replies;
- send a reply for arbitrary messages;
- commit real tokens or secrets;
- log BotFather token;
- expose secrets through `/health` or `/deployment/fingerprint`;
- merge without green CI and Mark's explicit merge command;
- deploy without Mark's explicit deploy command.

### PR, merge and deploy authority

- ChatGPT may create or update PRs directly when this is the appropriate development step.
- ChatGPT may merge only after Mark gives a direct merge command.
- ChatGPT may deploy or trigger production redeploy only after Mark gives a direct deploy command.
- Green CI is required for merge, but green CI alone is not merge approval.
- Merge approval is not deploy approval.

### Codex task completion rule

A Codex task is complete only when:

1. a GitHub PR is created or updated;
2. the PR branch is pushed to GitHub;
3. the PR head SHA changes;
4. GitHub Actions CI starts;
5. CI result is reported or explicitly still running.

A local Codex commit without GitHub push is not complete.

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
- default reply mode: `manual`.

The first functional product behavior after the backend foundation is a safe predefined Telegram response:

```text
incoming Telegram text: ping
→ Gate event
→ PostgreSQL persistence
→ Gate online
→ audit log
```

No AI replies or free-form automatic responses are allowed in this sprint.

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
- httpx
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
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_API_BASE_URL=https://api.telegram.org
GATE_ENV=development
GATE_DEFAULT_MODE=manual
ACTION_BEARER_TOKEN=
ADMIN_BEARER_TOKEN=
MAYAKI_INTAKE_URL=
MAYAKI_INTAKE_BEARER_TOKEN=
MAYAKI_INTAKE_TIMEOUT_SECONDS=5.0
```

MAYAKI forwarding is optional. If `MAYAKI_INTAKE_URL` is empty, Gate skips forwarding and preserves its Telegram behavior.

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
| `auto-safe` | Gate answers only safe predefined cases. | `ping` only |
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
    telegram.py
    mayaki.py
alembic/
  versions/
docs/
  TEAM_WORKFLOW.md
  MAYAKI_FORWARDING.md
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

## 12. Telegram Ping v1

`POST /telegram/webhook` receives Telegram updates and validates the Telegram secret header:

```text
X-Telegram-Bot-Api-Secret-Token
```

Supported Telegram update shapes:

- `message`;
- `business_message`;
- `business_connection`.

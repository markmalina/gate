# Gate

Gate is a separate technical project for a cloud-first communication gateway backend.
It is the new architecture name that replaces the old working name “Secretary”, but
Gate is not Alpha, not Mayak and not SysteM.

Primary Telegram bot: `@gate_m_bot`.

Repository: https://github.com/markmalina/gate
Status: private

## Core formula

```text
Telegram → Gate → Project → Reply → Approval → Telegram → Memory
```

Gate does not think by itself. Gate routes messages, delivers replies only when the
configured mode permits it, and records communication facts.

## Product principles

- Gate is not Alpha.
- Gate is not Mayak.
- Gate is not SysteM.
- Gate is a separate technical project.
- Railway is the primary deployment target.
- The default mode is `manual`.
- There is no automatic sending in v0.
- Bot tokens and bearer tokens must live only in cloud/runtime environment variables.

## Stack

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

## Cloud deployment

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

## Health endpoints

- `GET /health` returns service health and the current non-secret mode metadata.
- `GET /deployment/fingerprint` returns non-secret deployment metadata such as service,
  environment, commit SHA, primary bot username and default mode.

## Core entities

### Bot

A concrete Telegram bot adapter: username, secret reference, runtime state and permissions.
The current first bot is `@gate_m_bot`. The actual Telegram token is never stored in code.

### Account

A concrete Telegram account context such as Mark’s personal account, a Belarus account,
a Russian account, a project account or a future client account.

### Project

An internal project or agent that receives routed events through an adapter boundary.

### Route

A routing rule that maps bot, account, chat and event type to a project.

### GateEvent

A received communication event with bot, account, chat, sender, type, text and payload.

### AuditLog

An audit record for routing, approval, operational and future sending decisions.

## Reply modes

| Mode | Meaning | v0 status |
|---|---|---|
| `manual` | Gate suggests or records a reply; Mark sends manually. | Default |
| `approval` | Gate prepares a reply; Mark confirms before any send path. | Planned |
| `auto-safe` | Gate answers only safe predefined cases. | Future |
| `auto-full` | Gate answers autonomously inside a project boundary. | Future, explicit permission only |

## Repository structure

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
tests/
.github/workflows/ci.yml
Dockerfile
railway.toml
pyproject.toml
.env.example
```

## Development checks

CI runs the production backend checks:

```bash
ruff check .
mypy app tests
pytest
alembic upgrade head
alembic downgrade base
docker build -t gate:ci .
```

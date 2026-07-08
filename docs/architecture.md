# Gate Architecture

Gate is organized around five core entities:

```text
Bot / Account / Project / Route / Gate Core
```

## Boundary

Gate owns communication transport and auditability.

Gate does not own project reasoning, long-term memory policy or final business decisions.

## Components

### Telegram adapters

Telegram adapters receive events from Telegram bots and, later, Telegram account integrations.

They normalize incoming events into Gate events.

### Gate Core

Gate Core handles:

- identity resolution;
- route resolution;
- permission checks;
- reply mode selection;
- audit logging;
- communication fact creation.

### Project adapters

Project adapters send normalized events into internal projects.

Examples:

- Alpha adapter;
- Mayak adapter;
- SysteM / M Stack adapter;
- generic future-agent adapter.

### Memory adapter

Memory adapter records communication facts into the selected storage or memory target.

## Message flow

```text
Telegram event
  → Telegram adapter
  → Gate event
  → Gate Core
  → Route
  → Project adapter
  → Suggested reply
  → Manual / Approval / Auto mode
  → Telegram adapter
  → Audit log / Memory adapter
```

## Entity model

### Bot

Telegram entry point.

Important: use `token_secret_ref`, not raw token storage.

### Account

Telegram identity boundary.

Each account must define owner, type, permissions and default reply mode.

### Project

Destination for interpretation and reply generation.

Project adapters isolate Gate from implementation details of Alpha, Mayak and future agents.

### Route

Deterministic mapping from Telegram context to project.

Route priority allows specific chat/project rules to override generic defaults.

### Audit log

All outgoing actions require an audit record.

Minimum audit fields:

- event id;
- bot id;
- account id;
- chat id;
- sender id;
- project id;
- reply mode;
- proposed reply hash/body reference;
- final reply hash/body reference;
- confirming user;
- sent timestamp;
- status.

## v0 implementation principle

Start with the smallest useful loop:

```text
forwarded message → suggested reply → Mark confirmation → fact recorded
```

Full account automation is not required for v0.

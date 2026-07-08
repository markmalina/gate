# Gate

Gate is a communication gateway between Telegram bots, Telegram accounts and Mark's internal projects: Alpha, Mayak, SysteM and M Stack.

Gate does not think by itself.
Gate routes messages, delivers replies and records communication facts.

Primary Telegram bot:  
@gate_m_bot

Repository: https://github.com/markmalina/gate  
Status: private

## Core formula

```text
Telegram → Gate → Project → Reply → Approval → Telegram → Memory
```

Gate is not Alpha.  
Gate is not Mayak.  
Gate is not SysteM.  
Gate is a separate technical project.

## What Gate does

Gate connects Telegram accounts, Telegram bots and internal projects.

Gate must:

1. See incoming Telegram messages.
2. Understand which bot and which account received the message.
3. Resolve the target project: Alpha, Mayak, SysteM, M Stack or another agent.
4. Pass the message to the selected project adapter.
5. Receive suggested replies or actions.
6. Show the reply to Mark or another responsible user.
7. Wait for confirmation when manual or approval mode is enabled.
8. Send the reply when sending is permitted.
9. Record the communication fact: recipient, reply, timestamp and next step.

## Core entities

### Bot

A concrete Telegram bot: token, username, webhook settings, permissions and runtime state.

Current first bot: `@gate_m_bot`.

### Account

A concrete Telegram account: Mark's personal account, Belarus account, Russian account, project account or future client account.

Each account has owner, account type, linked project, connected bots, permissions, reply mode and audit log.

### Project

An internal project or agent: Alpha, Mayak, SysteM, M Stack or future agents.

### Route

Routing rule that maps bot + account + chat + event type to a project.

### Gate Core

Shared core for routing, security, permissions, reply modes, audit logging and communication facts.

## Multi-bot principle

Bots are Telegram adapters. They do not contain business logic.

Gate Core owns routing, permissions, modes and logging. Replacing a bot must not change Gate Core.

## Multi-account principle

Gate must never mix contexts.

Personal Mark ≠ Mayak ≠ Alpha ≠ client project.

Every action must be logged with account, bot, chat, project and confirming user.

## Reply modes

| Mode | Meaning | v0 status |
|---|---|---|
| `manual` | Gate suggests a reply; Mark copies/sends manually. | Primary |
| `approval` | Gate prepares a reply; Mark confirms; Gate sends. | Primary |
| `auto-safe` | Gate answers only safe predefined cases. | Future |
| `auto-full` | Gate answers autonomously inside a project boundary. | Future, explicit permission only |

## v0 scenario

1. Mark forwards a Telegram message into Alpha, ChatGPT or a future Gate interface.
2. Gate or Alpha identifies sender, context, relationship, risks and tone.
3. Alpha or Mayak suggests a reply.
4. Mark edits or confirms.
5. The reply is sent manually or through Gate.
6. The fact is recorded in memory: who, what, when and next step.

## v1 scenario

1. Telegram bot is connected.
2. Incoming messages land in Gate Inbox.
3. Gate resolves `bot_id`, `account_id`, `chat_id`, `sender_id` and `project_id`.
4. Gate loads person/project context.
5. Gate sends the event to Alpha, Mayak or another project.
6. The project returns reply options.
7. Mark confirms.
8. Gate sends the reply.
9. Gate writes the fact to Alpha Memory, M Stack or SysteM.

## Security baseline

- Do not store BotFather tokens in GitHub.
- Do not commit `.env`.
- Do not store personal conversations in public files.
- Keep the repository private.
- Log every outgoing message.
- Enable automation only after explicit permission.
- Connect third-party accounts and conversations only with owner consent.

## Repository structure

```text
README.md
.env.example
.gitignore
docs/
  gate-spec.md
  architecture.md
  telegram.md
  security.md
  modes.md
src/
  gate/
    __init__.py
    config.py
    core.py
    router.py
    models.py
    telegram_adapter.py
    memory_adapter.py
    project_adapter.py
tests/
```

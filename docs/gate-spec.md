# Gate Specification

Gate is a separate technical project.

Previous working name: `Secretary` / `Секретарь`.
Current name: `Gate` / `Гейт`.

Repository: https://github.com/markmalina/gate  
Repository status: private  
Primary Telegram bot: `@gate_m_bot`

## 1. Definition

Gate is a communication gateway between Telegram accounts, Telegram bots and Mark's internal projects:

- Alpha;
- Mayak;
- SysteM;
- M Stack;
- future agents.

Gate is not Alpha.
Gate is not Mayak.
Gate is not SysteM.

Gate is the input/output layer.

```text
Telegram → Gate → Project → Reply → Approval → Telegram → Memory
```

## 2. Responsibility boundary

Alpha thinks, remembers, highlights and helps with decisions.

Mayak runs the day: check-ins, tasks, commands and state.

SysteM / M Stack stores structured data.

Gate receives and sends messages, connects Telegram to projects and records communication facts.

Short formula:

```text
Gate is not the brain.
Gate is the entrance and exit.
```

## 3. Main function

Gate is a Telegram gateway connecting personal accounts, bot accounts and projects.

Gate must:

1. See incoming Telegram messages.
2. Understand which bot and which account received the message.
3. Determine the target project: Alpha, Mayak or another agent.
4. Pass the message to the selected project.
5. Receive a suggested reply.
6. Show the reply to Mark or another responsible user.
7. Wait for confirmation when manual or approval mode is enabled.
8. Send the reply when permitted.
9. Record the fact: to whom, what, when and next step.

## 4. Core entities

### Bot

Concrete Telegram bot.

Fields:

- `id`;
- `username`;
- `token_secret_ref`;
- `webhook_url`;
- `permissions`;
- `is_active`;
- `created_at`;
- `updated_at`.

Example: `@gate_m_bot`.

Tokens are never written to README, specs or committed files.

### Account

Concrete Telegram account.

Examples:

- Mark's personal account;
- Belarus account;
- Russian account;
- project account;
- future client account.

Fields:

- `id`;
- `owner`;
- `account_type`;
- `linked_project_id`;
- `connected_bot_ids`;
- `permissions`;
- `reply_mode`;
- `audit_log_enabled`.

### Project

Internal project or agent.

Examples:

- Alpha;
- Mayak;
- SysteM;
- M Stack;
- future agents.

Fields:

- `id`;
- `name`;
- `adapter_type`;
- `memory_target`;
- `is_active`.

### Route

Routing rule.

Route maps:

```text
bot + account + chat + event_type → project
```

Fields:

- `id`;
- `bot_id`;
- `account_id`;
- `chat_id`;
- `event_type`;
- `project_id`;
- `reply_mode_override`;
- `priority`;
- `is_active`.

### Gate Core

Shared core responsible for:

- routing;
- security;
- permissions;
- reply modes;
- audit log;
- communication fact recording.

## 5. Multi-bot support

Gate can serve multiple bots.

For now, any bot performs the same base function.

Later:

- one bot can be replaced by another;
- one bot can be tied to one project;
- another bot can be tied to another project;
- personal, project, test and backup bots can coexist.

Principles:

1. Bot does not contain business logic.
2. Bot is an adapter to Telegram.
3. Gate Core owns the logic.
4. Replacing a bot must not change the core.
5. `@gate_m_bot` is the first bot, not the only possible bot.

## 6. Multi-accounting

Multi-accounting is required.

One Gate must support:

1. Mark's personal Telegram account.
2. Belarus Telegram account.
3. Russian Telegram account.
4. Project accounts.
5. Future client accounts.

Contexts must not be mixed.

```text
Personal Mark ≠ Mayak ≠ Alpha ≠ client project
```

Every action must log:

- account;
- bot;
- chat;
- project;
- confirming user;
- sent message;
- timestamp;
- next step.

## 7. Reply modes

### manual

Gate suggests a reply. Mark copies and sends it manually.

### approval

Gate prepares a reply. Mark confirms. Gate sends.

### auto-safe

Gate replies automatically only in safe scenarios:

- greeting;
- confirmation;
- contact collection;
- simple navigation;
- basic predefined answers.

### auto-full

Gate replies automatically within a project boundary.

This requires explicit permission and complete logging.

## 8. v0 scenario

Gate v0 works like this:

1. Mark forwards a Telegram message into Alpha, ChatGPT or a future bot/interface.
2. Gate or Alpha determines sender, context, person, relationship, risks and tone.
3. Alpha or Mayak suggests a reply.
4. Mark edits or confirms.
5. The reply is sent manually or through Gate.
6. The fact is recorded in memory: who, what, when and next step.

This can work before full Telegram integration.

## 9. v1 scenario

Gate v1 works like this:

1. Telegram bot is connected.
2. Incoming messages land in Gate Inbox.
3. Gate determines `bot_id`, `account_id`, `chat_id`, `sender_id` and `project_id`.
4. Gate loads person context.
5. Gate sends the event to Alpha, Mayak or another project.
6. The project returns reply options.
7. Mark confirms.
8. Gate sends the reply.
9. Gate writes the fact to Alpha Memory, M Stack or SysteM.

## 10. First functional target

First functionality:

> suggested replies for incoming Telegram messages with Mark's manual confirmation.

Primary mode for v0:

- `manual`;
- `approval`.

## 11. Security rules

1. Do not store Telegram tokens in GitHub.
2. Do not commit `.env`.
3. Do not store BotFather token in README.
4. Store local secrets only in `.env`.
5. Store server secrets only in deployment secret storage.
6. Do not store personal conversations in public files.
7. Keep repository private.
8. Log all outgoing messages.
9. Enable automatic replies only after explicit permission.
10. Access third-party accounts only with owner consent.
11. Do not connect someone else's conversations without permission.

# Telegram Integration

Primary Telegram bot: `@gate_m_bot`.

## Bot token policy

The BotFather token must never be written into:

- chat messages;
- README;
- specs;
- committed source files;
- logs.

Use:

- `.env` locally;
- server secret storage in production.

`.env.example` may contain only variable names.

## Bot role

The Telegram bot is an adapter.

It should:

- receive updates;
- normalize incoming events;
- send events to Gate Core;
- send approved replies back to Telegram;
- never own business logic.

## Normalized Telegram event

Minimum normalized event fields:

- `event_id`;
- `bot_username`;
- `account_id`;
- `chat_id`;
- `sender_id`;
- `message_id`;
- `message_text`;
- `event_type`;
- `received_at`.

## v0 Telegram scope

For v0, Gate can operate before full webhook integration:

1. Mark forwards a Telegram message into the working interface.
2. Gate/Alpha/Mayak proposes a reply.
3. Mark approves or edits.
4. Sending is manual or approval-based.
5. Fact is recorded.

## v1 Telegram scope

For v1:

1. Configure webhook or polling.
2. Store bot metadata without token exposure.
3. Resolve account and route.
4. Send event to the target project.
5. Require approval before outgoing messages by default.
6. Log the final outgoing message.

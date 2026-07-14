# Stale approved-send recovery

This runbook covers the bounded database-only recovery for an explicitly approved
Telegram reply whose process stopped after a durable send reservation. It is an
ambiguity classifier, not a delivery worker.

## Safety contract

- The command never imports or calls the Telegram client and does not require a
  Telegram token.
- It never sends or retries a message.
- A stale `sending` attempt becomes `unknown`, because Telegram may already have
  accepted the request.
- A stale `reserved` attempt becomes `cancelled`, because provider delivery had not
  started in that state.
- `sent`, `failed`, `unknown`, and `cancelled` attempts are terminal and are not
  changed.
- Reply tasks, drafts, conversations, messages, recipients, and Business connections
  are not modified.
- Work is selected with `FOR UPDATE SKIP LOCKED`, so an active outcome transaction is
  skipped rather than delayed or overwritten.
- One invocation handles at most 100 attempts and then exits. There is no internal
  loop or scheduler.

An `unknown` attempt must never be retried automatically. Its stored draft remains
idempotently bound to that attempt. Reconciliation requires separate human evidence
and is outside this command.

## Configuration

The runtime is disabled by default and reads only these settings:

| Variable | Default | Bounds | Meaning |
|---|---:|---:|---|
| `GATE_REPLY_SEND_RECOVERY_ENABLED` | `false` | boolean | Explicit execution gate |
| `GATE_REPLY_SEND_RECOVERY_STALE_SECONDS` | `900` | `900..86400` | Minimum attempt age |
| `GATE_REPLY_SEND_RECOVERY_LIMIT` | `100` | `1..100` | Maximum rows per invocation |
| `DATABASE_URL` | empty | required when enabled | PostgreSQL connection |

Do not provide `TELEGRAM_BOT_TOKEN` to a dedicated recovery job. The normal Gate web
service remains unchanged; this command belongs in a separate one-shot or cron job.

## Run one bounded recovery

Before running, verify the deployed commit and confirm no send incident is still under
active investigation. Invoke exactly one batch:

```text
GATE_REPLY_SEND_RECOVERY_ENABLED=true \
GATE_REPLY_SEND_RECOVERY_STALE_SECONDS=900 \
GATE_REPLY_SEND_RECOVERY_LIMIT=100 \
gate-reply-send-recovery
```

The process logs only aggregate fields: whether execution was enabled, the disposition,
the recovered count, the number marked `unknown`, and the number marked `cancelled`.
It must not log attempt IDs, account/project IDs, principal identity, recipient,
connection ID, draft text, or Telegram response data.

Interpret the result as follows:

- `disabled`: no database access or mutation occurred.
- `idle`: no eligible stale attempt was found.
- `recovered`: one or more terminal classifications were committed atomically.
- a recovered count equal to the configured limit means more rows may remain; inspect
  the aggregate result before authorizing another one-shot invocation.

If any `sending` attempt becomes `unknown`, stop. Do not send the draft again and do
not switch it back to `sending`. Investigate using approved operational evidence only.

## Disable and rollback

Set `GATE_REPLY_SEND_RECOVERY_ENABLED=false` or remove the dedicated job to prevent
future invocations. Disabling the runtime does not and must not reverse already
committed `unknown` or `cancelled` classifications. A code rollback is safe because
the existing send path already treats those states as non-sendable idempotent outcomes.

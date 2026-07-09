# MAYAKI forwarding from Gate

Дата фиксации: 2026-07-09  
Статус: Gate-side forwarding client contract.

## 1. Purpose

Gate remains the transport service. MAYAKI owns product logic.

This document defines how Gate can forward normalized inbound events to MAYAKI without mixing product logic into Gate.

## 2. Runtime settings

Gate reads these optional environment variables:

```text
MAYAKI_INTAKE_URL=
MAYAKI_INTAKE_BEARER_TOKEN=
MAYAKI_INTAKE_TIMEOUT_SECONDS=5.0
```

Rules:

```text
01 MAYAKI_INTAKE_URL empty → forwarding skipped.
02 MAYAKI_INTAKE_BEARER_TOKEN is optional but required for real deployments.
03 Secrets must live only in Railway / GitHub secret storage.
04 Gate must preserve its own webhook behavior if MAYAKI is unavailable.
```

## 3. Implemented client

```text
app/services/mayaki.py
```

Exports:

```text
MayakiIntakeClient
MayakiForwardResult
```

The client accepts one normalized event and posts it to MAYAKI intake.

## 4. Event shape

Gate forwards the MAYAKI contract from the MAYAKI repository:

```json
{
  "gate_event_id": "...",
  "provider": "telegram",
  "provider_user_id": "1001",
  "channel": "telegram",
  "message_text": "Привет, Марк. Хочу заниматься Око Возрождения.",
  "occurred_at": "2026-07-09T12:00:00.000Z",
  "profile": {
    "username": "alexander_test",
    "first_name": "Александр",
    "last_name": "Иванов",
    "preferred_language": "ru"
  },
  "raw": {
    "update_id": 101
  }
}
```

## 5. Forwarding statuses

```text
skipped
sent
failed
```

`skipped` means Gate has no `MAYAKI_INTAKE_URL` configured.

`failed` must not break Telegram webhook processing.

## 6. Current implementation boundary

This PR adds:

```text
01 runtime settings
02 MAYAKI client
03 client tests
04 forwarding contract docs
```

This PR does not yet modify `/telegram/webhook`.

The next small PR should wire:

```text
/telegram/webhook
→ build normalized MAYAKI event
→ MayakiIntakeClient.forward_inbound_event
→ AuditLog: mayaki_inbound_forward_sent / failed
```

## 7. Why not wire webhook in the same PR

`app/api/routes.py` is a large route module with existing Telegram, admin and reply-task behavior.

The safe sequence is:

```text
01 introduce forwarding client and settings
02 test client in isolation
03 wire route in a small follow-up PR with focused webhook tests
```

This keeps Gate stable and avoids changing Telegram behavior without a precise test pass.

# Oko Telegram-start attribution boundary

Status: implemented, not composed, disabled by default.

## Contract

Gate recognizes only this exact Telegram deep-link payload:

```text
/start oko21_<tracking_token>
```

The application parser extracts the token plus the Telegram user id and optional username. The HTTP
adapter posts the minimum payload to Oko Control:

```http
POST /v1/gate/telegram-start
Authorization: Bearer <GATE_WEBHOOK_SECRET>
```

No message text is persisted or logged by this boundary. Transport errors expose only the stable code
`oko_attribution_request_failed`.

## Safety boundary

This change does not import the adapter into the web process or Telegram worker. It has no environment
variables, scheduler, webhook cutover, retry loop, or production activation. Composition must remain
behind an explicit default-off kill switch and must be completed only after Oko Control has PostgreSQL,
HTTPS, a strong Gate secret, readiness checks, and a rollback procedure.

## Next composition step

1. Add default-off Gate settings for the Oko Control base URL, secret, and enable flag.
2. Compose the service in canonical Telegram processing after durable receipt.
3. Define retry/idempotency behavior: Oko Control already makes `telegram_started` idempotent.
4. Add redacted counters for ignored, disabled, recorded, and failed outcomes.
5. Validate with a non-owner Instagram account before enabling production traffic.

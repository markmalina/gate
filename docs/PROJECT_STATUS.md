# Gate Project Status

Last updated: 2026-07-08

This document is the current handoff state for the next ChatGPT session, Codex task, or manual continuation.

## Current production state

Gate is deployed on Railway and the production service has pulled the latest `main` branch.

Current production domain:

```text
https://gate-production-bab1.up.railway.app
```

Confirmed production behavior:

```text
Telegram message: ping
Gate reply: Gate online
```

The first observable product behavior is working in production.

## Recently merged PRs

### PR #4 — Gate Telegram Ping v1

Merged into `main`.

Merge commit:

```text
76091668a796c142d445e1e524ed4735447a5d24
```

Implemented:

- `POST /telegram/webhook`;
- Telegram webhook secret validation using `X-Telegram-Bot-Api-Secret-Token`;
- parsing for Telegram `message`, `business_message`, and `business_connection` updates;
- persistence of incoming Telegram events into `gate_events`;
- safe automatic reply only for exact normalized `ping`;
- reply text: `Gate online`;
- no automatic reply for other text;
- `audit_logs` entries for received Telegram updates and safe replies;
- `app/services/telegram.py` with Telegram `sendMessage` client;
- tests and README documentation.

### PR #5 — ChatGPT small sprint development policy

Merged into `main`.

Merge commit:

```text
c3fcebde7d119054481f4752d22b04b26d1ab7f2
```

Recorded workflow rule:

- permission for ChatGPT to create PRs is not broad permission to perform large development instead of Codex;
- ChatGPT may implement directly only for small, bounded sprints;
- larger, risky, multi-scope, or unclear work should go to Codex or require explicit Mark confirmation;
- merge requires Mark's explicit merge command;
- production deploy requires Mark's explicit deploy command.

## Telegram production verification

Verification performed after Railway pulled the latest `main`:

- Telegram bot `getMe` succeeded for `Gate M` / `Gate_M_bot`;
- Telegram `getWebhookInfo` returned `ok: true` and the webhook URL:

```text
https://gate-production-bab1.up.railway.app/telegram/webhook
```

- initial Telegram delivery failed with:

```text
Wrong response from the webhook: 403 Forbidden
```

Root cause: webhook secret mismatch between Telegram webhook setup and Railway `TELEGRAM_WEBHOOK_SECRET`.

Resolution:

- corrected local secret usage;
- reset Telegram webhook with the same secret as Railway;
- confirmed `ping` now receives `Gate online` in Telegram.

## Current operational requirements

Railway service `gate` must have these variables configured:

```env
DATABASE_URL=<PostgreSQL URL using postgresql+asyncpg://...>
TELEGRAM_BOT_TOKEN=<real bot token in Railway only>
TELEGRAM_BOT_USERNAME=gate_m_bot
TELEGRAM_WEBHOOK_SECRET=<same secret used in Telegram setWebhook>
GATE_ENV=production
GATE_DEFAULT_MODE=manual
ACTION_BEARER_TOKEN=<secret>
ADMIN_BEARER_TOKEN=<secret>
```

Do not paste real secrets into ChatGPT or commit them to GitHub.

## Manual Telegram checks

Check bot identity:

```powershell
Invoke-RestMethod "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/getMe"
```

Check webhook:

```powershell
Invoke-RestMethod "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/getWebhookInfo" |
  ConvertTo-Json -Depth 10
```

Set webhook:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/setWebhook" `
  -Body @{
    url = "https://gate-production-bab1.up.railway.app/telegram/webhook"
    secret_token = $env:TELEGRAM_WEBHOOK_SECRET.Trim()
    drop_pending_updates = "true"
  }
```

Expected Telegram behavior:

```text
ping  -> Gate online
hello -> no automatic reply
```

## Current known limitation

There is no admin diagnostics surface yet. Debugging currently requires Telegram `getWebhookInfo`, manual synthetic webhook calls, and Railway logs.

This made the first production debugging loop slower than necessary.

## Next sprint

Next small bounded sprint:

```text
Gate Telegram Diagnostics v1
```

Proposed scope:

- `GET /admin/events/latest`;
- `GET /admin/audit/latest`;
- `GET /admin/telegram/status`;
- all admin endpoints protected by `Authorization: Bearer <ADMIN_BEARER_TOKEN>`;
- no real token or secret values returned;
- include tests for auth, response shape, and secret redaction.

Purpose:

- see recent incoming Telegram events without querying DB manually;
- see recent audit records;
- see non-secret Telegram/runtime status;
- make future production debugging fast and observable.

## Safety constraints for next sprint

Do not:

- add AI replies;
- add free-form automatic Telegram replies;
- expand auto-send behavior beyond already approved safe rules;
- expose real tokens, webhook secrets, bearer tokens, or database credentials;
- deploy without Mark's explicit deploy command;
- merge without Mark's explicit merge command.

## Current continuation instruction

Tomorrow or in a new chat, begin from this file plus `README.md` and `docs/TEAM_WORKFLOW.md`.

Recommended first action:

```text
Create a PR for Gate Telegram Diagnostics v1.
```

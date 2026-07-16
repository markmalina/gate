# Instagram delivery plan

Gate owns provider transport only. TAKE owns all media production. MAYAKI owns CRM, campaigns, funnel policy and long-term product analytics.

## Sprint status

1. ChatGPT → Reel: implemented as dormant MCP tools for file validation, exact confirmation, official Meta container publication and permalink return.
2. Multiple accounts: implemented through alias-based account registry; tokens are environment secrets and are never returned.
3. Analytics: implemented first read-only Reel insights tool; persistence and scheduled snapshots remain a MAYAKI integration task.
4. Comments: contracts and webhook work are deferred until Meta App webhook credentials exist.
5. Direct: deferred until messaging permissions/App Review exist; unknown follow state must never be treated as followed.
6. TAKE: boundary is fixed. Gate accepts only a finished video or future versioned TAKE publishing package; it never edits media.
7. MAYAKI: Gate will emit provider-neutral events after the MAYAKI ingestion endpoint and workspace mapping are supplied.
8. Automation: deliberately disabled until comments/DM webhooks, idempotency storage, approved copy and a kill switch are present.

## Required runtime variables

- `GATE_INSTAGRAM_ENABLED=true` exposes the Instagram MCP tools.
- `GATE_INSTAGRAM_DELIVERY_ENABLED=true` permits the external publish call.
- `GATE_INSTAGRAM_SIGNING_SECRET=<random secret>` signs 20-minute exact confirmation tokens.
- `GATE_INSTAGRAM_ACCOUNTS_JSON` is a secret JSON map: `{ "main": { "instagram_user_id": "...", "access_token": "..." } }`.
- `GATE_INSTAGRAM_GRAPH_VERSION` pins the reviewed Meta Graph version.

Both feature flags default to `false`. Enabling tools and enabling delivery are separate actions.

## Known production gap

The initial confirmation token is stateless. It prevents mutation of account/caption/file and expires quickly, but does not provide durable replay protection. Use one deliberate publish attempt only; any timeout is `unknown` and must be reconciled manually before another attempt. Durable publication attempts are required before unattended automation.

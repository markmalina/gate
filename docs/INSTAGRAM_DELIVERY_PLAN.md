# Instagram delivery plan

Gate owns provider transport only. TAKE owns all media production. MAYAKI owns CRM, campaigns, funnel policy and long-term product analytics.

## Sprint status

1. ChatGPT → Reel: implemented as dormant MCP tools for file validation, exact confirmation, official Meta container publication and permalink return.
2. Multiple accounts: implemented through alias-based account registry; tokens are environment secrets and are never returned.
3. Analytics: implemented first read-only Reel insights tool; persistence and scheduled snapshots remain a MAYAKI integration task.
4. Comments: signed Meta webhook normalization, public-reply transport and authenticated Gate-to-MAYAKI delivery are implemented and dormant.
5. Direct: private reply, quick replies, normal messaging and fail-closed follow-state lookup are implemented against official Meta contracts; activation still requires the real messaging permissions/App Review.
6. TAKE: boundary is fixed. Gate accepts only a finished video or future versioned TAKE publishing package; it never edits media.
7. MAYAKI: Gate emits provider-neutral comment/message events to MAYAKI's authenticated `/intake/gate` contract with deterministic idempotency keys.
8. Automation: validated provider commands and a process-local replay-protected executor are implemented, but automatic command dispatch remains deliberately disabled until durable attempts, rate limits and a kill switch are present.

## Required runtime variables

- `GATE_INSTAGRAM_ENABLED=true` exposes the Instagram MCP tools.
- `GATE_INSTAGRAM_DELIVERY_ENABLED=true` permits the external publish call.
- `GATE_INSTAGRAM_SIGNING_SECRET=<random secret>` signs 20-minute exact confirmation tokens.
- `GATE_INSTAGRAM_ACCOUNTS_JSON` is a secret JSON map: `{ "main": { "instagram_user_id": "...", "access_token": "..." } }`.
- `GATE_INSTAGRAM_GRAPH_VERSION` pins the reviewed Meta Graph version.

Both feature flags default to `false`. Enabling tools and enabling delivery are separate actions.

## Known production gap

The initial confirmation token is stateless. It prevents mutation of account/caption/file and expires quickly, but does not provide durable replay protection. Use one deliberate publish attempt only; any timeout is `unknown` and must be reconciled manually before another attempt. Durable publication attempts are required before unattended automation.


## Keyword automation webhook bridge

Gate now exposes a fail-closed Meta callback at:

```text
/webhooks/instagram
```

The GET handshake returns Meta's challenge as plain text after a constant-time verify-token check. POST requests require `X-Hub-Signature-256`, normalize supported comment/message events, and deliver them to MAYAKI's existing `/intake/gate` endpoint with:

- a bearer token;
- a fresh timestamp;
- an HMAC-SHA256 canonical signature;
- a deterministic idempotency key.

If MAYAKI delivery is not configured or fails, Gate returns `503`; the webhook is not acknowledged as successfully processed.

Additional environment variables:

```text
GATE_INSTAGRAM_WEBHOOK_ENABLED=false
GATE_INSTAGRAM_WEBHOOK_DELIVERY_ENABLED=false
GATE_INSTAGRAM_APP_SECRET=
GATE_INSTAGRAM_WEBHOOK_VERIFY_TOKEN=
GATE_INSTAGRAM_MAYAKI_INTAKE_URL=
GATE_INSTAGRAM_MAYAKI_INTAKE_BEARER_TOKEN=
GATE_INSTAGRAM_MAYAKI_INTAKE_HMAC_SECRET=
```

Verification and delivery have separate feature flags. Both default to false.

Provider command contracts are implemented for public replies, private replies, follow checks and follow prompts. A deterministic fake executor proves the workflow without external calls. The real Meta adapter now maps those commands to the official public reply, private reply, Send API and User Profile API endpoints, with process-local replay protection. It is intentionally not exposed as an unattended command endpoint: durable command attempts and the connected account permissions are production admission requirements.

## Offline setup diagnostics

`gate_instagram_diagnose_setup` is exposed even while the Instagram feature is disabled. It reports capability booleans, missing check names, configured account aliases and the safe activation order. It never returns access tokens, signing secrets, the Meta app secret, bearer tokens or HMAC secrets.

Use diagnostics before changing any flag. A capability is reported separately for ChatGPT tools, prepared publishing, external publishing, webhook verification and signed MAYAKI delivery, so connection can proceed one canary step at a time.

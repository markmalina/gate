# Gate

> **CURRENT HANDOFF — ACTIVE PRODUCTION PROJECT**  
> Verified: **2026-07-13**  
> Repository: `markmalina/gate` (private)  
> Source of truth: GitHub `main`  
> Verified application commit before this documentation update: `0543a34d961b11a807c06588a1c040befaff8321`  
> Production: Railway, automatic deployment from `main`  
> Status at handoff: ChatGPT Work MCP MVP is operational; both current Telegram Business accounts are connected and able to reply.

This README is the primary project handoff. A new owner or implementation agent must be able to continue from it without relying on chat history, an agent memory summary, or a stale local checkout.

Read next:

- [Team workflow](docs/TEAM_WORKFLOW.md) — authority, PR, CI, merge, deployment and terminology rules;
- [Full technical specification](docs/GATE_FULL_TECHNICAL_SPEC.md) — target architecture and scale requirements;
- [Operator regulations](docs/GATE_OPERATOR_REGULATIONS.md) — operational and safety rules.

If any older document conflicts with the verified current state in this README, re-check GitHub and production first, then update the documentation in the same substantive PR.

## 1. What Gate is

Gate is a cloud-first communication gateway between Mark's communication accounts and isolated project contexts. The first production transport is Telegram Business; the first user-facing control surface is a custom MCP integration in ChatGPT Work.

Gate is not a generic chatbot and is not the business logic of Alpha, Mayak, Oko or another project. Its responsibilities are deliberately narrow:

```text
Telegram communication fact
→ durable Gate record
→ correct account and project scope
→ reply task
→ bounded context
→ proposed draft
→ Mark sees account, recipient and exact text
→ explicit approval
→ one idempotent delivery attempt
→ Telegram
→ audit
```

Current product goal: let Mark see who wrote to him, distinguish unanswered inbound conversations from messages he already sent, get GPT help with a reply, approve the exact result and send it through the correct Telegram Business account.

## 2. Product and agent terminology

- **ChatGPT** is the product and user-interface name. Continue to call the interface, plugins/connectors and user-facing features ChatGPT.
- **WorkGPT**, shortened to **Work**, is the workflow agent Mark uses inside the ChatGPT Work tab. Work is the normal architect, reviewer and GitHub/Railway workflow controller.
- **Codex** is the implementation environment for larger development packages. Codex is not the source of truth and must not call a local-only commit complete.

Do not rename the ChatGPT product to WorkGPT in user-facing copy. WorkGPT identifies the working agent, not the product.

## 3. Verified production snapshot

Verified through GitHub and the live Gate M connector on 2026-07-13:

| Item | Verified state |
|---|---|
| GitHub default branch | `main` |
| Open pull requests before this handoff | none |
| Application commit | `0543a34d961b11a807c06588a1c040befaff8321` |
| Environment | `production` |
| Railway service URL | `https://gate-production-bab1.up.railway.app` |
| Bot username | `gate_m_bot` |
| Operating mode | `manual` |
| MCP authorization | OAuth 2.0 enforced |
| Workspace | `mark-personal` |
| Active account contexts | 2: `telegram_mark_ru` / RU and `telegram_mark_by` / BY |
| Telegram Business health | RU `ready`, BY `ready`, both `can_reply=true` |
| MCP tools | 5, listed below |
| Automatic/free-form sending | disabled by product rules |
| Explicit confirmed sending | enabled and exercised successfully |

Live counters are operational snapshots, not invariants. At verification they were approximately: 21 conversations, 520 messages, 105 reply-task records, 3 currently unanswered conversations, 39 answered/outbound messages and 27 saved drafts. Re-query production instead of copying these numbers into logic or tests.

Public verification endpoints:

- `GET /health` — lightweight service health;
- `GET /deployment/fingerprint` — Railway commit and non-secret deployment metadata;
- `GET /.well-known/oauth-protected-resource/mcp` — MCP protected-resource metadata;
- `POST /mcp` — MCP transport, protected per tool by OAuth.

After every merge, verify the exact new Railway commit. A healthy old release is not proof that the new release deployed.

## 4. Current ChatGPT Work MVP

The installed connector is named **Gate M**. It uses the production MCP endpoint:

```text
https://gate-production-bab1.up.railway.app/mcp
```

Active OAuth scopes:

```text
gate:status:read
gate:inbox:read
gate:drafts:write
gate:messages:send
```

Active tools:

| Tool | Scope | Behavior |
|---|---|---|
| `gate_get_status` | `gate:status:read` | Scoped counts plus redacted per-account Business connection and contact-import health |
| `gate_list_unanswered` | `gate:inbox:read` | One current unanswered item per conversation plus a separate answered/outbound list |
| `gate_get_reply_context` | `gate:inbox:read` | Bounded chronological context for one scoped reply task |
| `gate_save_reply_draft` | `gate:drafts:write` | Saves proposed text; never approves or sends |
| `gate_send_approved_reply` | `gate:messages:send` | Sends the exact saved draft only after explicit confirmation of account, recipient and final text |

Important UI behavior:

- refresh connector actions after MCP schemas/descriptors change;
- reconnect only when OAuth scopes, grants or authorization metadata change;
- an orange permission banner means the token did not receive every tool scope requested by the connector; compare ChatGPT action scopes with Auth0 API permissions and User-Delegated Access;
- do not solve a missing-scope warning by weakening Gate authorization.

## 5. Message and inbox semantics

Gate must distinguish direction, not guess from text.

- **Unanswered** means a current inbound human message exists after the last outbound reply in that conversation.
- Consecutive inbound messages are grouped into one actionable conversation; `pending_message_count` shows how many are pending.
- **Answered/outbound** means Mark sent the message. A message such as “Привет, Стефан” is answered/outbound even if it looks like a greeting that could have been received.
- Owner self-chats and service/command messages are excluded from the answered-people list.
- Every result must show the originating account explicitly as RU or BY.
- Unknown or ambiguous account routing fails closed; it must never silently fall back to RU.

Account ownership repairs were performed for legacy data. Do not reintroduce hard-coded per-contact corrections. New account resolution is workspace- and connection-driven.

## 6. Safe send contract

Sending is the highest-risk currently active path. These invariants are non-negotiable:

1. Load a reply task within the caller's workspace/account/project scope.
2. Show Mark the exact account (`RU` or `BY`), recipient and final text.
3. Receive explicit confirmation.
4. Save/use the exact draft; do not regenerate after approval.
5. Validate the expected account and expected recipient against the stored task.
6. Resolve and live-validate a Business connection within that same account.
7. Reserve a unique `SendAttempt` and commit before calling Telegram.
8. Call Telegram once.
9. Persist `sent`, `failed` or `unknown`.
10. Close the reply task only after Telegram confirms delivery.

Repeated calls for the same draft are idempotent. A timeout or crash ambiguity becomes `unknown` and is never retried automatically. Never fall back across RU/BY boundaries. Never send merely because a draft exists.

The confirmed production send to the BY account succeeded after Business connection reconciliation. This proves the path, not permission for future automatic sends.

## 7. OAuth and account authorization model

Auth0 verifies the user and issues access tokens for the Gate MCP resource. Gate verifies signature/JWKS, issuer, audience, subject and tool scope.

The durable authorization design is workspace-first:

- the token asserts the trusted workspace identity;
- Gate resolves active readable accounts owned by that workspace from its own database;
- Gate derives project access from those account records;
- explicit account/project claims remain a backward-compatible fallback, not the normal way to add each account;
- an empty or unknown workspace fails closed.

This was introduced so a new Gate account does not require manually hard-coding every account UUID into Auth0 metadata. Auth0 User-Delegated Access must still permit every requested API scope for the ChatGPT application.

Never commit or document real client secrets, bearer tokens, JWTs, session strings, webhook secrets, Telegram bot tokens or authorization codes.

## 8. Telegram Business connection lifecycle

Telegram can rotate, disable or replace Business connection identifiers. A connection that once worked can later become invalid.

Current behavior:

- new Business connection updates are resolved dynamically from the Telegram business owner identity;
- enabling a replacement retires the previous active connection for that account;
- nested rights, including `can_reply`, are stored correctly;
- connection updates are state, not ordinary chat messages;
- confirmed sends live-validate candidates with Telegram before use;
- only an enabled, reply-capable connection in the already-confirmed account may be selected;
- invalid or temporarily unverifiable connections fail closed;
- safe bounded Telegram error codes/details are recorded without tokens or message contents;
- `gate_get_status` exposes only redacted health (`ready`, `degraded`, `cannot_reply`, `inactive`, `missing`), never connection identifiers.
- contact-directory status is also aggregate-only: imported count, source and last import time per granted account; it never exposes names, phones or Telegram user IDs.

The database may retain historical connection rows. `business_connections` can therefore be larger than `active_business_connections`; this is not by itself an error.

## 9. Contact identity and names

PR #100 added account-scoped `Person` / `PersonIdentity` persistence and resolved-name snapshots. Gate now has the correct internal seam for stable names and prevents future approved-draft sends from degrading to a raw numeric recipient when a resolved identity is known.

Current limitation: the Telegram **Bot API** supplies the other party's Telegram profile/chat name, not the private label Mark saved in his personal Telegram address book. Therefore:

- MCP lists and reply context resolve imported account-scoped names at read time, including messages created before identity snapshots;
- contacts absent from the imported user-ID/name pairs can still show a Telegram profile name or numeric ID;
- Gate must not infer a saved contact name from message wording;
- Telegram Desktop JSON can provide offline user ID/name pairs for exported personal chats; phone-only address-book rows still require a separate identity source for complete matching.

Delivery status and intended continuation:

1. The protected, idempotent contact-snapshot boundary is implemented by PR #102.
2. PR #104 added the preferred offline Telegram Desktop JSON reader, source-account ownership verification and a one-shot PowerShell-compatible import path.
3. RU Desktop-export contacts have been imported; BY remains pending until Telegram permits its Desktop export.
4. `gate_get_status` reports privacy-bounded aggregate import evidence per granted account.
5. MCP unanswered, answered and reply-context reads prefer the imported account-scoped contact name without rewriting historical message facts.
   The current imported identity intentionally outranks an older message snapshot, because that snapshot may contain the pre-import Telegram profile name.
6. Keep Telegram session material outside message payloads, logs and Git history.
7. Send only sanitized `(account, Telegram user ID, saved display name, optional username, revision)` snapshots to Gate.
8. Upsert names account-scoped; the same Telegram user may have different saved names in RU and BY.
9. Backfill existing `PersonIdentity`, conversation and message linkage without rewriting communication facts.
10. Make synchronized contact names authoritative over profile names in MCP output.
11. Import and verify BY after its export becomes available, then document one-time onboarding for future accounts.

Telegram authorization is not required for the Desktop export path. It remains an optional fallback when complete phone-to-user-ID matching is required, because Desktop `contacts.list` has no Telegram user IDs. No Telegram user-session secret has been added to production.

## 10. Current code architecture

Technology:

- Python 3.12;
- FastAPI / Uvicorn;
- PostgreSQL;
- SQLAlchemy async + asyncpg;
- Alembic;
- httpx Telegram adapter;
- Auth0 OAuth/JWT/JWKS;
- GitHub Actions;
- Railway.

Current runtime flow:

```text
Telegram Bot API webhook
→ FastAPI router chain
→ raw TelegramUpdate receipt
→ normalized GateEvent / Message
→ connection and account resolution
→ Conversation
→ ReplyTask
→ MCP read/draft/send services
→ SendAttempt reservation
→ Telegram API
→ outbound Message + AuditLog
```

Important locations:

| Path | Responsibility |
|---|---|
| `app/main.py` | App composition, router registration, startup webhook reconciliation |
| `app/api/mcp_routes.py` | Current MCP protocol and tool adapter |
| `app/api/mcp_metadata_routes.py` | OAuth protected-resource and MCP metadata |
| `app/api/sprint14_routes.py` and later sprint routers | Current Telegram Business normalization and incremental behavior |
| `app/application/` | Draft/send application contracts and services |
| `app/infrastructure/reply_sends.py` | Explicit idempotent Telegram delivery adapter |
| `app/contact_sync/` | Separate one-shot read-only Telegram contact synchronizer |
| `app/core/mcp_security.py` | MCP OAuth verification and access scope |
| `app/core/config.py` | Environment-backed settings; names only, no committed values |
| `app/models/domain.py` | Current SQLAlchemy domain model |
| `alembic/versions/` | Ordered database migrations |
| `tests/` | Unit, contract and PostgreSQL integration coverage |
| `docs/TEAM_WORKFLOW.md` | Collaboration and deployment authority |

Core persisted concepts currently include `Account`, `Project`, `TelegramBusinessConnection`, `TelegramUpdate`, `GateEvent`, `Person`, `PersonIdentity`, `Conversation`, `Message`, `ReplyTask`, `ReplyDraft`, `SendAttempt` and `AuditLog`.

## 11. Known architecture debt and risks

The MVP works, but the target architecture is not complete.

1. **Sprint router chain.** Multiple incremental Telegram routers remain registered. Replace them with one canonical ingress/application pipeline only after parity tests.
2. **Webhook durability.** The target is durable receipt + transactional outbox + worker before acknowledging. Current flow still contains synchronous domain work.
3. **MCP direct ORM access.** Move MCP reads behind scoped query services/read models.
4. **Operational exact counts.** Current status performs exact counts; this does not scale to very large history.
5. **Static RU/BY assumptions.** Workspace access is dynamic, but some labels/configuration and send schema still assume RU/BY. A third account must be addable without a code change.
6. **Project isolation evolution.** The final model needs explicit workspace/project grants, dialog identity independent of project, and time-bounded context binding.
7. **Legacy routing data.** Production required collision-safe and NULL-safe repair migrations. CI against an empty database did not expose every production-data shape.
8. **Contact directory rollout.** RU Desktop-export sync and MCP read-time name cutover are complete; BY sync and unmatched phone-only identities remain operational work. MTProto authorization is fallback-only.
9. **Output schemas.** ChatGPT recommends explicit output schemas for tools; add them without breaking existing MCP clients.
10. **Observability.** Add structured redacted metrics for webhook latency, queue lag, duplicate rate, DLQ, send failures/unknown, Telegram 429 and DB pool pressure.
11. **Scale path.** The billion-message target requires partitioning, hot/cold separation, object storage for raw payload/media, read projections and measured thresholds—not a claim that one Railway database stores everything forever.

Never fix this with a big-bang rewrite. Use expand → backfill → shadow/dual path → compare → cutover → later cleanup.

## 12. Recent delivery history

Key merged milestones:

| PRs | Result |
|---|---|
| #75–#80 | Auth0/ChatGPT connector integration and OAuth setup iterations |
| #87 | Complete safe MVP send path: context, draft, explicit confirmation, idempotent send |
| #88–#93 | Fail-closed RU/BY routing repair, production-safe collision/NULL migrations, confirmed data corrections |
| #94–#95 | Dynamic workspace-based OAuth grants and normalized account ownership |
| #96 | One unanswered item per conversation with pending message count |
| #97 | Same-account active Business connection fallback for confirmed sends |
| #98 | Safe Telegram delivery diagnostics |
| #99 | Dynamic Business connection synchronization and live validation |
| #100 | Stable contact identity seam and redacted connection health |
| #102 | Protected, bounded and idempotent contact snapshot boundary |
| #104 | Verified offline Telegram Desktop export import path and RU rollout |
| #105 | Privacy-bounded per-account contact import observability |
| #106 | Account-scoped imported-name precedence across MCP reads |
| #107 | Current imported names supersede stale historical message snapshots |

Migration lesson from #88–#92: green CI on a clean database is necessary but insufficient for a data repair. Any future backfill must be tested against production-like empty conversations, duplicate natural keys, nullable legacy ownership and already-coexisting target rows.

## 13. Ordered next development plan

Do not start several tracks in one PR.

### Immediate product continuation

1. **Complete BY Desktop-export rollout.** Export after Telegram's availability delay, run the same verified one-shot import and confirm aggregate evidence in Gate status.
2. **Measure remaining unmatched identities.** After RU/BY imports, quantify profile-name/numeric fallbacks and use MTProto only if phone-only matching is materially required.
3. **Dedicated contact-sync authorization.** Replace temporary admin-bearer reuse before any permanent schedule/service is enabled.
4. **MCP output schemas.** Add explicit bounded output schemas and refresh ChatGPT actions.
5. **Account generalization acceptance test.** Add a third test account without code or Auth0 UUID edits; verify receive → inbox → draft → confirmed send.

### Target architecture continuation

6. Runtime foundation: singleton DB engine, bounded pooled HTTP clients, Unit of Work, liveness/readiness, telemetry.
7. Canonical ingress: one webhook, durable receipt, outbox, worker, retry/DLQ, safe replay and shadow comparison.
8. Dynamic account/channel model: remove remaining hard-coded RU/BY paths while preserving human-readable labels.
9. Workspace/project isolation: explicit grants, account-project bindings, dialog independent of project, time-bounded context segments.
10. Application services: inbox, routing, context, draft revisions, approvals, sends and audit behind ports.
11. Adapter cutover: Telegram and MCP use the same application layer; remove sprint routers only after characterization parity.
12. Scale hardening: projections, keyset pagination, partitions, hot/cold data, backup/restore, load tests and capacity thresholds.

The next executor should begin with item 1 when the BY export becomes available; until then continue with the next independent code-only item. Prefer the offline Desktop export path; if MTProto fallback is later required, never place session material in chat or Git.

## 14. Target architecture and invariants

The durable target separates transport identity, project context and delivery state:

```text
Workspace
├── ChannelAccount(s)
│   ├── ChannelConnection history
│   └── PersonIdentity / contact projection
├── ProjectSpace(s)
│   └── scoped ProjectContact projection
└── Dialog(s)
    ├── ContextBinding segments
    ├── Message facts
    └── ReplyTask → DraftRevision → Approval → SendAttempt
```

Required invariants:

- unknown connection/account/project is quarantined or rejected, never defaulted silently;
- account and project are separate dimensions;
- a dialog does not duplicate merely because its project context changes;
- each message has one confirmed primary project or an explicit unclassified state;
- personal/global notes are never leaked into another project's contact projection;
- inbound processing is at-least-once with idempotent effects;
- concurrent copies of one update create one durable/domain result;
- concurrent sends for one approved revision produce at most one external call;
- timeout/unknown is never blindly retried;
- audit and logs are bounded and redact message text and secrets where operationally unnecessary;
- future high-volume IDs may become time-sortable, but existing UUIDs are not destructively rewritten.

## 15. CI, PR and deployment workflow

GitHub is the source of truth. Read [TEAM_WORKFLOW](docs/TEAM_WORKFLOW.md) before acting.

Normal flow:

```text
Mark intent
→ inspect current GitHub main/open PRs/CI/production
→ one bounded branch and PR
→ review exact diff
→ GitHub Actions on exact head SHA
→ fix small clear failures in the same PR
→ auto-merge only when every Team Workflow condition is satisfied
→ Railway automatic deployment from main
→ verify exact merge commit and smoke tests
```

Required CI gates:

- `ruff check .`;
- `mypy app tests`;
- `pytest`;
- `alembic upgrade head`;
- `alembic downgrade base`;
- `docker build -t gate:ci .`.

CI reporting uses: 🟢 success, 🟡 pending, 🔴 failed, ⚪ unknown.

Eligible Work-managed PRs auto-merge under the standing authorization in Team Workflow. This authorizes only Railway's automatic deployment caused by that merge. Manual deploy, retry, rollback and production configuration changes require Mark's direct command.

## 16. Runtime configuration and secret policy

Configuration is supplied through environment variables. Safe variable names include:

```text
DATABASE_URL
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
GATE_ENV
GATE_DEFAULT_MODE
GATE_SEND_ENABLED
GATE_ACCOUNT_RU_SEND_ENABLED
GATE_ACCOUNT_BY_SEND_ENABLED
GATE_MCP_ENABLED
MCP_AUTH_MODE
MCP_AUTHORIZATION_SERVER
MCP_PUBLIC_RESOURCE_URL
MCP_OAUTH_JWKS_URL
MCP_OAUTH_ALLOWED_ALGORITHMS
MCP_OAUTH_WORKSPACE_CLAIM
MCP_OAUTH_ACCOUNT_IDS_CLAIM
MCP_OAUTH_PROJECT_IDS_CLAIM
ADMIN_BEARER_TOKEN
TELEGRAM_CONTACT_API_ID
TELEGRAM_CONTACT_API_HASH
TELEGRAM_CONTACT_PHONE
TELEGRAM_CONTACT_SESSION_STRING
TELEGRAM_CONTACT_SESSION_FILE
GATE_CONTACT_SYNC_ACCOUNT_ALIAS
GATE_CONTACT_SYNC_URL
GATE_CONTACT_SYNC_BEARER_TOKEN
RAILWAY_GIT_COMMIT_SHA
RAILWAY_SERVICE_NAME
```

Some legacy Business connection configuration names still exist in `app/core/config.py`; dynamic database/Telegram state is the intended authority.

Rules:

- commit variable names and safe defaults only;
- real values belong in Railway/GitHub/Auth0/appropriate secret storage;
- never paste secrets into issues, PR bodies, logs, screenshots, tests or chat;
- future Telegram user-session strings are high-privilege secrets and must not be stored in normal Gate tables or emitted in logs;
- do not expose raw Business connection identifiers in MCP status output.

## 17. Operational runbook

### Start of a new work session

1. Read `docs/TEAM_WORKFLOW.md` and this README.
2. Fetch current `main`, open PRs, recent merged PRs and exact CI state.
3. Query production fingerprint and Gate status.
4. Compare production commit to `main`.
5. Check connection health for every granted account.
6. Continue the first incomplete ordered next step; do not reuse an old sprint branch.

### After an MCP-only descriptor change

1. Verify deployment fingerprint.
2. Refresh Gate M actions in ChatGPT.
3. Confirm tool names/scopes/output schemas.
4. Reconnect only if authorization scopes or grants changed.

### Before any message send

1. Load fresh task/context; yesterday's context may no longer be current.
2. Confirm the correct RU/BY account.
3. Show recipient and exact final text.
4. Ask for explicit confirmation.
5. Send once through the saved draft.
6. Verify result and idempotent replay behavior; do not retry `unknown` automatically.

### If a Business connection fails

1. Read the safe Telegram error classification and connection health.
2. Wait for/process Telegram Business connection updates.
3. Reconnect the Telegram Business bot connection when Mark directly performs/authorizes that account-side action.
4. Do not copy an old connection ID into code or silently switch accounts.

### If Railway deployment fails

Inspect build/pre-deploy logs and the exact migration. Do not manually retry, force, roll back or modify production variables without Mark's direct command. Fix a small clear code issue in the active PR/next hotfix, validate CI and let the normal automatic deployment path run.

## 18. Acceptance scenarios

Before declaring the architecture mature, prove:

- RU, BY and a third test account complete receive → inbox → draft → explicit send;
- equal Telegram user/chat IDs remain isolated by account;
- unanswered grouping is conversation-level and directionally correct;
- saved Telegram contact labels override profile names per account after authorized sync;
- one project token cannot read another project's data;
- unknown connection/account/project fails closed;
- 100 concurrent copies of one update create one effect;
- 100 concurrent sends of one approved revision produce at most one Telegram call;
- `sent` closes work; `failed`/`unknown` does not;
- no secret or message text appears in operational logs unintentionally;
- production-like migration fixtures cover duplicates, empty conversations and partial legacy ownership;
- critical inbox/history queries use bounded keyset pagination and indexed plans;
- backup/restore and retention are tested, not merely documented.

## 19. Definition of done for the next executor

A task is not done because an agent says so or because a local patch exists. It is done when:

1. the intended GitHub PR exists and has the exact changes;
2. the PR head SHA is known;
3. exact-head CI is 🟢;
4. the diff, security boundary and architecture are reviewed;
5. the PR is merged only under Team Workflow authorization;
6. Railway production reports the exact merge commit;
7. relevant health, OAuth/MCP and safety smoke checks pass;
8. known limitations and the next risk are recorded.

For send behavior, also require an explicit human-confirmed test; never manufacture a production message for a smoke test.

## 20. Handoff privacy boundary

This repository handoff contains project knowledge only. It intentionally excludes:

- Mark's unrelated personal memory and other projects;
- message contents not required for a reproducible test;
- personal email addresses, OAuth subjects, Telegram numeric identities and contact lists;
- Auth0 client IDs/grant IDs when not needed by source code;
- Business connection identifiers;
- tokens, API hashes, session strings, authorization codes and all other secrets.

If operational recovery requires a sensitive value, retrieve it from the approved secret store. Do not reconstruct it from chat history or commit it to make handoff easier.

---

**Immediate continuation:** import and verify BY when Telegram enables its Desktop export, measure remaining unmatched identities, and add dedicated contact-sync authorization before any recurring schedule. Preserve explicit send confirmation and account isolation throughout.

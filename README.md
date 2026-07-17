# Gate

> **CURRENT HANDOFF — ACTIVE PRODUCTION PROJECT**  
> Verified: **2026-07-17**  
> Repository: `markmalina/Gate` (private), source of truth: GitHub `main`  
> Accepted product baseline through PR #227: `f7c100e4eee575d60cc82dd178d550a15f333229`  
> Verified Railway production commit: `f7c100e4eee5`  
> Production: Railway automatic deployment from `main`  
> Open WIP: PR #207 remains a broad dormant Instagram transport and must not merge because it changes the exact nine-tool MCP contract and still lacks shared transactional idempotency. PR #221 is being rebuilt as this current documentation snapshot.  

This README is the entry point for transferring Gate to another developer or implementation agent.
Do not rely on chat history or a local workspace.

Continue in this exact order:

1. [Team workflow](docs/TEAM_WORKFLOW.md) — authority, safety, PR/CI/merge/deploy rules.
2. [Current project handoff](docs/PROJECT_HANDOFF.md) — verified production state, delivered PRs,
   code map, environment names and the exact next development order.
3. [Alpha operating policy](docs/ALPHA_OPERATING_POLICY.md) — standing authorization,
   provider scope, $50 hard budget, and fixture → shadow → canary → live gates.
4. [Alpha operator runbook](docs/ALPHA_OPERATOR_RUNBOOK.md) — ID-free group control,
   one-shot provider activation, stop drills and rollback boundaries.
5. [Relationship CRM architecture](docs/RELATIONSHIP_CRM_ARCHITECTURE.md) — standalone relationship product, privacy spaces and extraction rule.
6. [Integrated delivery roadmap](docs/INTEGRATED_DELIVERY_ROADMAP_2026.md) — current cross-product release order.
7. [Full technical specification](docs/GATE_FULL_TECHNICAL_SPEC.md).
8. [Operator regulations](docs/GATE_OPERATOR_REGULATIONS.md).

If an older section conflicts with `docs/PROJECT_HANDOFF.md`, re-query GitHub and production, then
update both documents in the same substantive PR.

## Project transfer acceptance / Приёмка проекта

This repository is the complete non-secret handoff package. A new owner must be able to continue
from GitHub and the approved production control surfaces without chat history, agent memory or a
previous local workspace.

### What is being handed over

- Gate production source, migrations, tests, Docker build and GitHub Actions workflow;
- the protected Telegram Business human-reply path and nine-tool Gate M MCP contract;
- the delivery-blocked Alpha fixture/model release path and owner controls;
- the standalone Relationship CRM domain, migrations, billing boundary and transfer format;
- architecture decisions, operating policy, runbooks, known debt and the ordered next plan;
- environment-variable **names** and secret-handling rules, never secret values.

Operational credentials are a separate secret-store handoff. Their absence from Git is a pass
condition, not missing documentation.

### Read-only acceptance procedure

The receiving owner performs these steps in order and records evidence:

1. **Repository identity.** Confirm access to private repository `markmalina/Gate`, default branch
   `main`, the current head SHA and all open PRs. Read this README, then
   `docs/PROJECT_HANDOFF.md`, `docs/TEAM_WORKFLOW.md`,
   `docs/ALPHA_OPERATING_POLICY.md` and `docs/ALPHA_OPERATOR_RUNBOOK.md`.
2. **Exact-head CI.** Confirm the latest `main` SHA has a successful CI run. The backend job must
   show Ruff, strict mypy, pytest, Alpha rehearsals, Alembic upgrade/schema-drift/downgrade,
   disposable PostgreSQL backup/restore and both Docker builds as successful.
3. **Clean build from source.** In a disposable environment, follow §15 and run Ruff, mypy, pytest
   and `alembic upgrade head`. Current schema head is `0031_billing_ledger`. Never point this
   acceptance run at production.
4. **Deployment identity.** Read Railway deployment metadata and Gate status. The production commit
   must equal the accepted `main` merge SHA. A healthy older deployment is not acceptance.
5. **Gate M surface.** Confirm OAuth is used and exactly nine public actions are visible: five
   human-inbox actions plus Alpha list/control/preflight/shadow-review actions. Do not weaken scopes
   to make an action appear.
6. **Read-only production health.** Call only `gate_get_status`,
   `gate_alpha_list_installations` and `gate_alpha_list_shadow_reviews`. Confirm production/manual
   mode, RU and BY connection health, owner-scoped installation labels/opaque references and
   Telegram delivery blocked for model proposals.
7. **Safety state.** Confirm provider preflight remains unverified until billing is ready, the USD 50
   monthly hard ceiling remains configured, no cross-group identifiers are exposed, and private
   Alpha, Oko, CRM backfill and real voice transcription are not silently active.
8. **Documentation consistency.** Compare GitHub head, Railway fingerprint and Gate M status with
   `docs/PROJECT_HANDOFF.md`. Update snapshots in a documentation PR if they changed; never rewrite
   history to make old counters look current.

### Actions forbidden during read-only acceptance

Do not send Telegram messages, save or approve drafts, run provider preflight, approve/pause/resume
an Alpha installation, activate a private installation or Oko, change Railway variables, run a
production migration manually, redeploy, roll back or expose a secret. Each of those is a separate
authorized operational action, not project acceptance.

### Pass criteria and acceptance record

Acceptance passes only when repository, exact-head CI, disposable migration/build, Railway commit,
Gate M action surface and read-only safety state all agree. Record the result outside Git secrets
using this template:

```text
Gate handoff accepted at: <UTC timestamp>
Accepted GitHub main SHA: <full SHA>
CI run: <URL / run number> — success
Railway production SHA: <full SHA> — matches / does not match
Gate environment/mode: production / manual
RU/BY connector health: <redacted aggregate>
MCP public actions: 9 / 9
Alpha installation review: <labels and states only; no Telegram IDs>
Provider preflight verified: false (until separately authorized)
Model Telegram delivery blocked: true
Billing hard ceiling: USD 50/month
Open blockers or deviations: <list>
Accepted by: <name/role>
```

Any mismatch is a documented handoff blocker. It is not permission to mutate production while
diagnosing it.

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

Current product goal: keep Gate as safe transport while Relationship CRM owns people, personal attention, notes, follow-ups, commercial/program pipelines and deals. The legacy answered/unanswered inbox remains a compatibility surface until explicit CRM attention becomes the primary owner view.

The next product program preserves that human-controlled path while adding two separately scoped agents: an Alpha bot for explicitly approved Telegram groups and an Oko assistant for eligible RU Telegram Business dialogs. Their release sequence and safety boundaries are defined in the [architecture audit and release roadmap](docs/ARCHITECTURE_AUDIT_AND_RELEASE_ROADMAP.md).

## 2. Product and agent terminology

- **ChatGPT** is the product and user-interface name. Continue to call the interface, plugins/connectors and user-facing features ChatGPT.
- **WorkGPT**, shortened to **Work**, is the workflow agent Mark uses inside the ChatGPT Work tab. Work is the normal architect, reviewer and GitHub/Railway workflow controller.
- **Codex** is the implementation environment for larger development packages. Codex is not the source of truth and must not call a local-only commit complete.

Do not rename the ChatGPT product to WorkGPT in user-facing copy. WorkGPT identifies the working agent, not the product.

## 3. Verified production snapshot

Verified through GitHub, Railway/Gate M and exact-merge smoke checks on 2026-07-17 after PR #227:

| Item | Verified state |
|---|---|
| GitHub default branch | `main` |
| Application / Railway commit | `f7c100e4eee575d60cc82dd178d550a15f333229` / `f7c100e4eee5` |
| Environment / mode | `production` / `manual` |
| Workspace | `mark-personal` |
| Existing Business bot | `gate_m_bot` |
| Telegram Business | RU and BY both `ready`, `can_reply=true` |
| Contact import | RU 90 from Desktop export; BY deferred |
| MCP | OAuth 2.0 enforced, nine tools |
| Human sending | exact saved draft + explicit confirmation only |
| Alpha fixture | one approved test group active on the exact reviewed fixture |
| Alpha model path | staged and delivery-blocked; one synthetic preflight failed with rejected credentials |
| OpenAI budgets | Alpha USD 50/month app ceiling with USD 0.012288 reserved; Gate voice USD 1/month app ceiling; OpenAI organization spend limit USD 5 |
| Model delivery evidence | 0 model turns and 0 model delivery grants |

Snapshot counters were 41 conversations, 1100 messages, 375 reply-task rows, 17 unanswered and 32
drafts. Alpha evidence was 5 accepted canonical messages, 2 fixture Agent turns, 1 delivery grant
and 1 send attempt. Re-query production instead of treating these values as invariants.

The production control plane now discovers Alpha groups by label and opaque installation reference;
no Telegram group ID is needed. Approval, pause and resume require the separate
`gate:agents:manage` OAuth scope and an explicit confirmed action. The review room is read-only,
and provider preflight remains uninvoked while billing is unavailable.

The canonical detailed status and next work are in
[docs/PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md). After every merge, verify the exact Railway SHA;
a healthy previous release does not prove the new release deployed.

## 4. Current ChatGPT Work MVP

The installed connector is named **Gate M**. It uses the production MCP endpoint:

```text
https://gate-production-bab1.up.railway.app/mcp
```

Required OAuth scopes (the first four are active; `gate:agents:manage` needs the one-time Auth0/User-Delegated Access grant):

```text
gate:status:read
gate:inbox:read
gate:drafts:write
gate:messages:send
gate:agents:manage
```

Active tools:

| Tool | Scope | Behavior |
|---|---|---|
| `gate_get_status` | `gate:status:read` | Scoped counts plus redacted per-account Business connection and contact-import health |
| `gate_list_unanswered` | `gate:inbox:read` | One current unanswered item per conversation plus a separate answered/outbound list |
| `gate_get_reply_context` | `gate:inbox:read` | Bounded chronological context for one scoped reply task |
| `gate_save_reply_draft` | `gate:drafts:write` | Saves proposed text; never approves or sends |
| `gate_send_approved_reply` | `gate:messages:send` | Sends the exact saved draft only after explicit confirmation of account, recipient and final text |
| `gate_alpha_list_installations` | `gate:status:read` | Lists Alpha groups by label and opaque reference; never exposes Telegram IDs |
| `gate_alpha_control_installation` | `gate:agents:manage` | Confirmed approve, pause or resume with a reviewed-state fence |
| `gate_alpha_run_model_preflight` | `gate:agents:manage` | One-shot synthetic OpenAI check; no group content or Telegram delivery |
| `gate_alpha_list_shadow_reviews` | `gate:agents:manage` | Read-only bounded review of delivery-blocked model proposals |

Every tool advertises a strict JSON `outputSchema`; CI validates the descriptors and representative success/error `structuredContent` against those schemas.

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
| `app/models/domain.py` | Legacy SQLAlchemy domain model |
| `app/application/relationship_crm.py` | Standalone CRM spaces, relationships, interactions, attention and deal contracts |
| `app/application/relationship_crm_transfer.py` | Versioned deterministic CRM extraction/import boundary |
| `app/models/relationship_crm.py` | Module-owned CRM persistence, independent from reply tasks and Telegram IDs |
| `app/application/billing.py` / `app/models/billing.py` | Unified product attribution, entitlements, ledger and hard-budget reservations |
| `app/application/alpha_access_policy.py` | Default-deny group/private discovery and addressing policy |
| `alembic/versions/` | Ordered database migrations |
| `tests/` | Unit, contract and PostgreSQL integration coverage |
| `docs/TEAM_WORKFLOW.md` | Collaboration and deployment authority |
| `docs/ALPHA_OPERATOR_RUNBOOK.md` | Alpha owner controls, provider activation, stop drills and rollback |

Core persisted concepts currently include `Account`, `Project`, `TelegramBusinessConnection`, `TelegramUpdate`, `GateEvent`, `Person`, `PersonIdentity`, `Conversation`, `Message`, `ReplyTask`, `ReplyDraft`, `SendAttempt` and `AuditLog`.

## 11. Known architecture debt and risks

The MVP works, but the target architecture is not complete.

1. **Sprint router chain.** Registration is centralized and frozen by characterization tests, but the incremental handlers still delegate directly. Replace them with one canonical ingress/application pipeline only after shadow parity.
2. **Webhook durability.** The target is durable receipt + transactional outbox + worker before acknowledging. Current flow still contains synchronous domain work.
3. **MCP direct ORM access.** Move MCP reads behind scoped query services/read models.
4. **Operational exact counts.** Current status performs exact counts; this does not scale to very large history.
5. **Static RU/BY assumptions.** Workspace access is dynamic, but some labels/configuration and send schema still assume RU/BY. A third account must be addable without a code change.
6. **Project isolation evolution.** The final model needs explicit workspace/project grants, dialog identity independent of project, and time-bounded context binding.
7. **Legacy routing data.** Production required collision-safe and NULL-safe repair migrations. CI against an empty database did not expose every production-data shape.
8. **Contact directory rollout.** RU Desktop-export sync and MCP read-time name cutover are complete; BY sync and unmatched phone-only identities remain operational work. MTProto authorization is fallback-only.
9. **Schema evolution.** Explicit output schemas are enforced in CI; future response changes must remain backward-compatible or be intentionally versioned.
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
| #108 | Explicit bounded output schemas for all five MCP tools, validated against runtime payloads |
| #109 | Frozen Telegram ingress registration boundary, characterization guard and canonical cutover plan |
| #110 | Scoped MCP status application query extracted from the transport route |
| #111 | Scoped MCP inbox and reply-context queries extracted from the transport route |
| #112 | Side-effect-free canonical Telegram ingress classifier, fixture replay and redacted production shadow observations |
| #113 | Configured account/project routing context and reply-task candidate parity in redacted ingress shadow |
| #114 | Select-only identity snapshot boundary for database-backed connection, direction and reply-task parity |
| #115 | Idempotent Telegram receipt/outbox application contract and bounded retry/dead-letter policy |
| #116 | Durable Telegram receipt and transactional outbox schema with reversible migration |
| #117 | Atomic PostgreSQL receipt/outbox acceptance with concurrent idempotency coverage |
| #118 | Bounded one-item worker contract for success, retry and hard dead-letter outcomes |
| #119 | Exclusive PostgreSQL queue claims and atomic processed/retry/dead-letter transitions |
| #120 | Disabled-by-default one-shot Telegram shadow worker runtime with redacted classification |
| #121 | Redacted queue health aggregates and bounded stale-claim retry/dead-letter recovery |
| #122 | Disabled Oko Telegram-start attribution parser and fail-closed HTTP adapter |
| #123 | Deterministic queue alert severity and payload-free recovery audit records |
| #124 | Application contract for durable queue alert incidents, delivery gating and operator acknowledgement |
| #125 | Dormant Telegram queue incident schema and serialized PostgreSQL lifecycle adapter |
| #126 | Dormant authenticated HTTPS queue alert notifier with incident-key idempotency |
| #137–#153 | Canonical ownership, Agent Kernel and isolated exact-target Alpha group path |
| #154–#161 | Automatic one-group discovery, supervised worker, owner-approved canary activation, redacted evidence and packaged immutable bundle |
| #162–#166 | Dormant Responses runtime, reviewed/staged model deployment, live Privacy Mode silence and exact reversible pause drill |
| #167–#172 | Provider fault isolation, one-shot preflight, hard budget and reviewed model-readiness gates |
| #173–#174 | Offline adversarial evals, dry-run promotion plan and operational alerts |
| #175 | Gate M Alpha control plane: ID-free list, pause/resume and protected preflight |
| #176–#178 | Shadow review, product evals and explicit multi-group approval/isolation |
| #179–#184 | Incident, retention, restore, supply-chain and release-admission hardening |
| #185–#186 | Unsupported-media terminal handling and literal human Telegram sends |
| #187 | Ecosystem audit plus product, technical and integrated roadmaps |
| #188–#193 | Personal CRM contracts, Alpha context safety, channel/voice policy and durable dormant voice intake |
| #194 | End-to-end nine-tool MCP surface regression contract |
| #195–#199 | Standalone Relationship CRM architecture, domain kernel and durable personal/commercial/program tables |
| #206–#220 | Handoff boundary, scoped CRM services, private-Alpha discovery, transactional billing, Mayaki/Oko contracts and dry runs |
| #222–#227 | Gate-owned OpenAI voice transcription, ffmpeg normalization, text-only projection, fail-closed orchestration and canonical worker wiring |
| #176 | Read-only, delivery-blocked Alpha shadow review room |
| #177 | Twelve offline product-quality and natural-voice release evals |
| #178 | Explicit ID-free group approval and PostgreSQL multi-group isolation proof |

Migration lesson from #88–#92: green CI on a clean database is necessary but insufficient for a data repair. Any future backfill must be tested against production-like empty conversations, duplicate natural keys, nullable legacy ownership and already-coexisting target rows.

## 13. Ordered next development plan

The [architecture audit and release roadmap](docs/ARCHITECTURE_AUDIT_AND_RELEASE_ROADMAP.md) is the
single authority for implementation order. Do not run several tracks in one PR.

1. **Reliable transport foundation:** process-owned DB/runtime resources, honest readiness, replay
   evidence, ownership contracts and dormant durable acceptance.
2. **Canonical ingress and Agent Kernel:** worker cutover by update class plus versioned,
   shadow-only agent proposals.
3. **Alpha group bot:** one separate credential, approved installation and command/reply-only pilot.
4. **Oko ownership and handoff:** exact RU enrollment phrase, consent, inbox separation and a fixed
   reviewed first response.
5. **Controlled Oko pilot:** bounded dynamic responses, memory, delivery grants and mandatory
   handoff.
6. **Operational release:** remove unreachable legacy paths, prove isolation, restore/rollback and
   reusable multi-installation operation.

The fixed Alpha transport canary, ID-free owner control plane, shadow review room, adversarial
evals, product-quality evals and multi-group isolation proof are complete. The model runtime is
staged and delivery-blocked. Provider billing is the only external blocker before the one-shot
synthetic preflight; after preflight, model shadow activation still requires a reviewed code release.
The fixed `/alpha` behavior remains the rollback-safe production baseline. The paid OpenAI
organization is configured, but the first synthetic preflight failed with the bounded category
`credentials`. Replace the Railway `OPENAI_API_KEY` with a key from the funded project before one
new preflight; never paste the key into chat or Git.

BY Desktop-export/contact rollout, recurring contact sync and a third personal account remain
deferred. Gate-owned Telegram Business voice transcription is implemented behind
`GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED=false`; activation requires the valid key and a separate
owner-authorized Railway configuration change.

Preserve OAuth, account/project isolation, explicit confirmation for every human-managed send and
backward-compatible MCP schemas throughout.

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
TELEGRAM_ALPHA_BOT_TOKEN
TELEGRAM_ALPHA_BOT_USERNAME
TELEGRAM_ALPHA_WEBHOOK_SECRET
GATE_TELEGRAM_WORKER_ENABLED
GATE_ALPHA_CONNECTOR_ENABLED
GATE_ALPHA_SHADOW_WORKER_ENABLED
GATE_ALPHA_CANARY_ENABLED
GATE_ALPHA_DELIVERY_ENABLED
GATE_ALPHA_PRIVACY_MODE_CONFIRMED
GATE_ALPHA_MODEL_RUNTIME_ENABLED
GATE_ALPHA_MODEL_DATA_GATE_APPROVED
GATE_ALPHA_MODEL_MAX_OUTPUT_TOKENS
GATE_ALPHA_MODEL_TIMEOUT_SECONDS
OPENAI_API_KEY
OPENAI_API_BASE_URL
GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED
GATE_TELEGRAM_VOICE_TRANSCRIPTION_MODEL
GATE_TELEGRAM_VOICE_RESERVE_MICROUSD_PER_SECOND
GATE_TELEGRAM_VOICE_LANGUAGE
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

The isolated Alpha connector uses `TELEGRAM_ALPHA_BOT_TOKEN`,
`TELEGRAM_ALPHA_BOT_USERNAME` and `TELEGRAM_ALPHA_WEBHOOK_SECRET`. New groups are discovered
automatically but remain pending until the owner explicitly approves the opaque installation in
Gate M. Model provider configuration is a separate gate; the API key may exist while billing and
provider preflight remain unavailable.

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

### Before Alpha provider activation

1. Complete the one-time `gate:agents:manage` Auth0 API permission and ChatGPT User-Delegated Access grant, then reconnect Gate M.
2. Confirm billing is actually ready; status inspection must not probe the provider.
3. Follow [the Alpha operator runbook](docs/ALPHA_OPERATOR_RUNBOOK.md).
4. Invoke the protected synthetic preflight once; never retry a failed or ambiguously reserved key.
5. Keep model delivery blocked until ten clean shadow proposals and another reviewed release.

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
- backup/restore and retention are tested, not merely documented;
- two approved Alpha groups cannot exchange triggers, context, limits or usage;
- every Alpha automatic stop drill leaves promotion non-mutating with fixture rollback.

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

**Immediate continuation:** GitHub `main` and Railway production are verified at PR #227 / `f7c100e4eee5`. Replace the Railway `OPENAI_API_KEY` with a new key from the funded OpenAI project, wait for the automatic deployment, then run exactly one protected synthetic provider preflight. Do not enable Gate voice transcription until that preflight succeeds. Model Telegram delivery remains blocked; no private Alpha/Oko activation, live CRM backfill or Telegram smoke send is authorized. PR #207 remains draft until it is split into bounded persistence, webhook, provider and MCP releases that preserve the exact nine-tool contract.

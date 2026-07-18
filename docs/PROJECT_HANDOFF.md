# Gate project handoff

> Canonical continuation guide for a new product owner, developer or implementation agent.  
> Verified on 2026-07-18 after PR #244.  
> Accepted GitHub `main` / Railway production merge: `6680d9dce1cbcfd13aa62b22f757be8f22ce9c6d` (`6680d9dce1cb` in Gate status).  
> Schema head in this release: `0037_direction_parity_v2`.
> Open WIP: draft PR #207 is a dormant broad Instagram transport. Do not merge it as-is; split and redesign it before review.

## 1. Transfer outcome

This file is the single current handoff for Gate. A receiving team must be able to continue from
GitHub, CI and the protected production control surfaces without chat history, agent memory or a
previous local workspace.

Read in this order:

1. `docs/TEAM_WORKFLOW.md` — authority, secrets, CI, merge and deployment rules.
2. This document — current truth, decisions, incidents and exact continuation order.
3. `docs/DEVELOPMENT_AND_COST_REGULATIONS.md` — engineering, test and cost discipline.
4. `docs/ALPHA_OPERATING_POLICY.md` — Alpha provider, budget and delivery barriers.
5. `docs/ALPHA_OPERATOR_RUNBOOK.md` — owner control and incident boundaries.
6. `docs/GATE_FULL_TECHNICAL_SPEC.md` — long-form architecture.
7. `docs/RELATIONSHIP_CRM_ARCHITECTURE.md` — separately extractable relationship product.

GitHub `main` is the source of truth. Re-query GitHub and production before acting because counters
below are dated evidence, not invariants.

## 2. Product definition

Gate is the workspace-scoped communication and safety gateway between Telegram, ChatGPT and
versioned agents. Gate owns transport, durable facts, account/dialog scope, approval boundaries,
provider usage accounting and delivery audit. Alpha, Oko and future Mayak agents own product voice
and domain behavior through immutable instruction revisions.

The protected human path is live:

```text
Telegram Business
→ durable receipt
→ receiving-account-relative direction
→ reply task and bounded context
→ exact draft
→ Mark sees account + recipient + final text
→ explicit confirmation
→ one idempotent Telegram attempt
→ audit
```

The Alpha group path is live only in delivery-blocked model shadow:

```text
approved Telegram group
→ dedicated Alpha bot webhook
→ durable receipt and canonical message
→ explicit /alpha invocation
→ exact immutable model deployment
→ budget reservation
→ structured reply | handoff | no_reply proposal
→ owner review
→ no Telegram delivery without a separate exact grant
```

Telegram Business voice transcription belongs to Gate, not to Alpha. Gate downloads and normalizes
the voice file, calls the OpenAI transcription model, settles the Gate voice budget and persists the
transcript as a canonical text input with origin `telegram_voice_transcription`. Downstream agents
consume canonical text; they do not fetch or transcribe Telegram voice themselves.

## 3. Current verified production snapshot

Snapshot captured through Gate M after exact production commit `6680d9dce1cb` became active.

| Area | Verified state |
|---|---|
| Environment / mode | `production` / `manual` |
| Workspace | `mark-personal` |
| Business bot | `gate_m_bot` |
| Telegram Business | RU and BY both `ready`, `can_reply=true` |
| Business counts | 47 conversations, 1192 messages, 406 reply tasks, 17 unanswered, 321 answered, 32 drafts |
| Contact directory | RU: 90 imported; BY: 0, intentionally deferred |
| Gate M | OAuth enforced; exactly 9 public tools |
| Human sending | enabled only for exact saved draft + explicit confirmation |
| Alpha group installation | one approved active fixture-bound group |
| Alpha v2 deployment | one exact staged OpenAI deployment, preflight verified |
| Alpha v2 shadow | 10 model turns, 10 proposals, 0 failed, 0 delivery grants |
| Alpha promotion dry run | next stage `limited_canary_review`; no blockers |
| Alpha delivery | model Telegram delivery blocked |
| Alpha budget | USD 0.307200 reserved; USD 49.692800 remaining from the internal USD 50 monthly ceiling |
| Voice | 3 received, 2 delivered, 1 correctly dead-lettered as non-inbound, 0 retry/open/unknown |
| Voice billing | USD 0.000242 settled; USD 0.999758 remaining from the internal USD 1 monthly ceiling |
| Agent audit | 26 canonical Alpha messages, 22 total Agent turns, 1 historical fixture grant/send attempt |

The one Alpha delivery grant and send attempt are historical fixture-canary evidence. Model v2 has
zero grants and zero Telegram sends. The third-account voice dead letter is also historical expected
evidence; the connected-account-to-connected-account test succeeded.

External provider billing at the time of this handoff:

- OpenAI Platform prepaid balance was funded with USD 5;
- automatic recharge was off;
- organization spend limit was USD 5;
- GitHub Actions account budget was USD 5 with stop-usage behavior;
- a July billing example showed 503 Linux minutes at USD 0.006/minute, USD 3.02 gross. Treat this as
  a dated invoice fact, not a permanent price promise.

Internal Gate ceilings do not authorize external spend beyond provider/account limits. The lowest
active hard boundary wins.

## 4. Exact Gate M contract

Do not add a tenth public MCP tool. Extend an existing owner surface when product behavior belongs
to the same bounded workflow.

| Tool | Purpose |
|---|---|
| `gate_get_status` | redacted scoped production state |
| `gate_list_unanswered` | current human reply work |
| `gate_get_reply_context` | bounded chronological context |
| `gate_save_reply_draft` | save only; never approve/send |
| `gate_send_approved_reply` | exact draft after explicit account/recipient/text confirmation |
| `gate_alpha_list_installations` | labels and opaque references, never Telegram IDs |
| `gate_alpha_control_installation` | confirmed approve/pause/resume with stale-state fence |
| `gate_alpha_run_model_preflight` | one-shot synthetic provider verification, no group data/send |
| `gate_alpha_list_shadow_reviews` | bounded delivery-blocked model proposals for owner review |

Management/review/preflight requires `gate:agents:manage`. Human sending requires the existing send
scope and an exact explicit confirmation. Missing or ambiguous scope fails closed.

## 5. Product decisions that must survive handoff

### 5.1 Alpha identity and answer quality

- Product name: **Альфа**.
- Russian self-reference is feminine: `помощница`, `создана`, `проверила` only when actually true.
- Do not call her AlphaViaBot, a bot or `помощник` in product answers.
- Answer the current message first. Earlier context is for meaning, not for concatenating several
  previous requests unless the user explicitly requests a summary.
- Natural, calm Russian; usually 1–4 short sentences.
- No claims of internet, current-data, Telegram or external-action access when the runtime lacks it.
- One inbound invocation permits at most one generation and, after separate authority, one send
  attempt.

These rules are pinned in immutable bundle `agent_bundles/alpha_model_v2` and deployment
`alpha-model-v2-openai`.

### 5.2 Permanent principles, not temporary switches

Do not make the operator manage engineering scaffolding through Railway toggles. State is encoded by
deployed code, durable lifecycle and exact revision bindings. Operator controls remain only for real
decisions: installation approval/pause, dialog eligibility, exact delivery authority, hard budgets,
manual deploy/retry/rollback and production configuration.

### 5.3 Account and direction semantics

Direction is relative to the receiving Business account. A message sent from one connected account
to the other is inbound for the receiving account. Only the receiver's own current or historical
identity is outbound for that receiver. Never infer direction from message text and never fall back
to RU when account resolution is missing or ambiguous.

### 5.4 Delivery authority

- A model proposal cannot select workspace, connector, account, group, recipient or parse mode.
- A proposal is not a grant.
- A grant must bind exact installation, target dialog, trigger, candidate hash and expiry.
- External Telegram sending always requires the exact user-facing target and final text to be shown
  to Mark and explicitly confirmed.
- Provider timeout/crash ambiguity becomes terminal `unknown`; do not automatically resend.

### 5.5 Privacy

- Never commit message contents, Telegram IDs, phone numbers, provider IDs, keys or secrets.
- Message and transcript text may exist in scoped database columns but not logs or `repr`.
- Owner views use account labels, group labels and opaque references.
- Cross-group and cross-workspace reads fail closed and have PostgreSQL tests.

## 6. Delivered work after the previous handoff

The former handoff stopped at PR #238. The following releases are now accepted:

### PR #239 — Business voice exclusive durable ownership

- Voice updates no longer enter the legacy sprint-router chain.
- The authenticated webhook durably accepts the update and the canonical worker owns transcription,
  billing, canonical projection, retry and dead-letter evidence.
- Voice never creates delivery authority.

### PR #240 — canonical text and input-origin contract

- Canonical persistence stores ordinary Telegram text and voice transcripts through the same private
  text field.
- `input_origin` is exactly `telegram_text` or `telegram_voice_transcription`.
- Migration `0033_canonical_input_origin` is additive and reversible.

### PR #241 — Alpha review source evidence

- Existing shadow review items include bounded input origin.
- No new MCP tool was added.

### PR #242 — immutable Alpha model v2

- Added the female Alpha identity and current-message-focused response policy.
- Paused v1 as rollback target and staged one exact v2 deployment.
- Added a fresh idempotent provider preflight release key.
- Production preflight succeeded on the first attempt with Telegram delivery blocked.
- v2 evidence is deployment-scoped; old v1 proposals do not count toward v2 promotion.

### PR #243 — Business text parity barrier

- Added a read-only production observer comparing canonical and legacy facts for ordinary Business
  text: RU/BY account, receiving-account-relative direction, dialog, Business connection, exact
  in-memory text and expected reply-task cardinality.
- The observer creates no workflow writes, drafts, turns, grants or sends.
- Private text and identifiers remain absent from logs and result representations.
- Legacy bypass has not happened yet; durable content-free parity evidence is still needed before
  cutover.

### PR #244 — durable Alpha rate-limit deferral

- Root cause: the installation allows 5 turns per 60 seconds. The first five burst messages created
  proposals; later messages returned `rate_limited` and were incorrectly marked processed.
- Fix: `rate_limited` is now durable backpressure, rescheduled after 61 seconds without an attempt
  cap instead of terminal loss/dead-letter.
- Migration `0035_alpha_rate_limit_deferral` requeued only v2 `/alpha` commands that had canonical
  ingress but neither usage reservation nor Agent turn.
- Five lost test commands recovered automatically without asking the user to resend.
- Production reached 10/10 v2 proposals, 0 failures and 0 model delivery grants.

### Sprint 1 release — durable Business text parity evidence

- Ordinary Telegram Business text now upserts one workspace/account-scoped evidence row per
  canonical message with exactly `matched`, `pending` or one bounded mismatch code.
- The evidence table contains internal scope IDs, bounded state, observation count and timestamps;
  it never stores text, provider dialog IDs or Business connection IDs.
- Replay and worker restart update the same row. A conflicting canonical-message scope fails closed.
- `gate_get_status.telegram_business_text_parity` exposes scoped aggregate counts, covered versus
  required active Business accounts and an explicit `cutover_ready` barrier.
- The observer still creates no additional reply task, Agent turn, draft, grant or send. Legacy text
  routing remains active until real RU and BY observations make the barrier ready.

## 7. Production evidence and test history

### 7.1 Voice

- A voice message from an unrelated third account was rejected as `telegram_voice_not_inbound`.
- A connected-account-to-connected-account voice message was correctly treated as inbound for the
  receiver, transcribed and settled.
- Final snapshot: 3 received, 2 delivered, 1 expected dead letter, no retry/open/unknown reservation.

### 7.2 Ordinary Business text

- A connected-account text test reached the production Business path.
- PR #243 compares canonical facts with the committed legacy reply-task fact.
- This release makes parity evidence durable and exposes its bounded status summary.
- Current limitation: real RU and BY matched evidence must accumulate after deployment; do not
  bypass the legacy path until `cutover_ready=true` with no pending or mismatch rows.

### 7.3 Alpha v2 sample

Ten real group proposals are now recorded for `alpha-model-v2-openai`:

- all `status=proposed`, `outcome=reply`, `result_code=ok`;
- all `input_origin=telegram_text`;
- all `sent_to_telegram=false`;
- female identity and current-message behavior are correct;
- two duplicate summary prompts produced distinct per-message turns, proving trigger identity rather
  than content-hash deduplication;
- promotion dry run has no blockers and reports `limited_canary_review` as the next stage.

The sample is sufficient to begin exact owner review. It is not blanket permission to send.

## 8. Work on errors / lessons learned

### Error 1 — billing language was confusing

Usage, free allowance, accrued invoice amount, prepaid credit and hard spend limits were discussed as
if they were one concept. They are not. Future communication must state separately:

1. what resource was consumed;
2. whether it was included or billable;
3. whether an amount is an invoice/debt or only usage evidence;
4. whether a payment method/prepaid balance exists;
5. which hard stop prevents further spend.

### Error 2 — a healthy older deployment was treated as progress evidence

Production is accepted only when the deployment fingerprint equals the exact merge SHA. Health of an
older revision is not proof that a release deployed.

### Error 3 — test instructions were formatted ambiguously

A Telegram message containing several monospace `/alpha` lines is one Telegram message, not several
invocations. With group privacy mode, code/preformatted text may also lack a bot-command entity and
may not reach the bot as a command. When Mark must run a packet:

- provide one service message containing separate inline-monospace snippets;
- each snippet represents one actual Telegram message;
- Mark copies and sends each snippet separately;
- verification counts actual durable triggers, not visible bubbles alone.

### Error 4 — rate limiting silently lost work

Safety backpressure must delay work, not erase it. A capacity limit is not a terminal business
decision. PR #244 establishes the permanent rule: defer and retry the same durable identity without
granting extra delivery authority.

### Error 5 — v1 identity and context behavior were not product-correct

v1 answered as `AlphaViaBot — помощник` and sometimes blended prior prompts. v2 fixed the immutable
instruction bundle; old evidence was not reused to claim v2 quality.

### Error 6 — operator switches were overused conceptually

Temporary engineering controls pushed mental work onto the product owner. Future designs must use
permanent code and lifecycle principles. Add an operator control only when the operator is making a
real product, safety, cost or deployment decision.

### Error 7 — tests from the wrong account were initially interpreted as product failure

Always establish the sender, receiving connected account and account-relative direction before
diagnosing. Third-account rejection and connected-account inbound success are different expected
outcomes.

## 9. Current architecture map

| Path | Responsibility |
|---|---|
| `app/api/telegram_ingress.py` | Telegram Business authenticated durable ingress and remaining legacy boundary |
| `app/api/alpha_telegram_ingress.py` | dedicated Alpha bot webhook |
| `app/application/telegram_processing.py` | durable receipt/outbox contracts, retry and non-terminal deferral |
| `app/worker/telegram_processing.py` | canonical worker composition for Business text/voice and Alpha |
| `app/application/canonical_messaging.py` | connector-neutral dialog/message/ownership contracts |
| `app/application/telegram_canonical_projection.py` | Telegram-to-canonical projection |
| `app/infrastructure/canonical_messaging.py` | idempotent PostgreSQL canonical store |
| `app/application/telegram_business_text_parity.py` | pure canonical/legacy Business-text comparison |
| `app/infrastructure/telegram_business_text_parity.py` | read-only legacy fact observer |
| `app/application/telegram_voice_*` | voice extraction, transcription and fail-closed orchestration |
| `app/infrastructure/openai_transcription.py` | OpenAI transcription adapter |
| `app/application/agent_kernel.py` | deployment/session/turn/result contracts |
| `app/application/instruction_bundles.py` | immutable bundle loader and SHA verification |
| `app/application/alpha_shadow_worker.py` | invocation recognition and delivery-free model orchestration |
| `app/infrastructure/alpha_shadow_worker.py` | exact binding, context, budget and turn persistence |
| `app/application/alpha_canary.py` | installation and proposal/delivery boundary |
| `app/application/alpha_delivery.py` | one-use grant delivery contract |
| `app/infrastructure/alpha_delivery.py` | exact target consumption and send-attempt ledger |
| `app/infrastructure/alpha_model_status.py` | model readiness and promotion evidence |
| `app/infrastructure/alpha_shadow_reviews.py` | owner-scoped proposal review query |
| `app/api/mcp_routes.py` / `mcp_schemas.py` | exact protected nine-tool Gate M surface |
| `app/application/billing.py` / `app/models/billing.py` | provider-neutral entitlement, usage and cost ledger |
| `app/application/relationship_crm.py` | standalone Relationship CRM domain |
| `alembic/versions/0033_*`–`0035_*` | input origin, Alpha v2 and durable rate-limit recovery |

## 10. Product and technical plan — next ten sprints

Each sprint is a bounded PR train with exact-head CI and automatic Railway deployment only after the
standing merge conditions are satisfied.

### Sprint 1 — durable Business text parity evidence (implemented in this release)

**Product outcome:** the owner can see that ordinary connected-account text is safely represented in
both canonical and legacy paths before cutover.

**Technical scope:** persist content-free `matched`, `pending` and bounded mismatch-code counters;
expose the summary through `gate_get_status`; add restart/idempotency/PostgreSQL tests. Do not store
private comparison values in the evidence table.

**Acceptance:** real RU↔BY text produces matched evidence; no extra reply task, turn, draft or send.

### Sprint 2 — ordinary Business text canonical cutover

**Product outcome:** all ordinary Business text uses the durable worker without double processing.

**Technical scope:** bypass the legacy webhook chain only after parity evidence; derive the same
reply-task intent from canonical input; keep rollback to the last known-good deployment.

**Acceptance:** one update creates one canonical message and at most one human reply task across
restart/replay; legacy side effects are unreachable.

### Sprint 3 — one canonical downstream input contract

**Product outcome:** text and voice behave consistently for later agents and human work.

**Technical scope:** make canonical text plus `input_origin` the only downstream input; remove
duplicate transcript/text adapters; preserve voice billing and dead-letter evidence.

**Acceptance:** the same policy suite passes for `telegram_text` and
`telegram_voice_transcription`; no transport-specific duplicate effects.

### Sprint 4 — authoritative dialog ownership

**Product outcome:** every dialog is clearly human-owned, approved-agent-owned or no-reply.

**Technical scope:** versioned ownership fence; human outbound/handoff revokes stale agent capability;
ordered per-dialog claims; ambiguity quarantines.

**Acceptance:** concurrency tests prove one owner and stale grants cannot send after takeover.

### Sprint 5 — private Alpha proposal path

**Product outcome:** explicitly eligible RU/BY private dialogs can receive reviewed Alpha proposals
without automatic delivery.

**Technical scope:** wire the existing default-deny private policy to canonical input; no group
context leakage; no delivery grant; account/recipient/input-origin visible through opaque owner scope.

**Acceptance:** eligible designated dialog proposes; every unapproved dialog remains silent.

### Sprint 6 — exact owner review and approval

**Product outcome:** Mark can review one exact candidate and approve or reject it without IDs or a new
tool.

**Technical scope:** extend the existing Alpha review/control workflow; bind approval to turn,
candidate SHA, installation/account, recipient and expiry; preserve the nine-tool contract.

**Acceptance:** edited/stale/wrong-target proposals cannot obtain authority; rejection writes audit
only.

### Sprint 7 — one-use limited Alpha canary

**Product outcome:** one explicitly confirmed reviewed proposal can be delivered to one designated
test target.

**Technical scope:** consume one exact five-minute grant; one provider attempt; terminal unknown;
mandatory disclosure; fixture rollback retained.

**Acceptance:** show Mark group/account, exact final text and target label; send only after explicit
confirmation; verify one send and idempotent replay.

### Sprint 8 — designated text and voice dialog canaries

**Product outcome:** Alpha can assist in explicitly designated test dialogs for both typed and voice
input under the same review/delivery rules.

**Technical scope:** canonical input parity, per-dialog ordering, budget admission, human takeover,
stop conditions and bounded context.

**Acceptance:** one inbound creates at most one generation and one authorized send attempt; any
failure, budget exhaustion or ownership change stops delivery.

### Sprint 9 — legacy removal and operational proof

**Product outcome:** the system is simpler to own and failures are visible/recoverable.

**Technical scope:** remove unreachable sprint routers and obsolete flags; expose content-free queue
backlog/deferral/parity state; run provider outage, restore, rollback and unknown-send drills.

**Acceptance:** disposable restore and downgrade/upgrade pass; production remains exact-head; no
hidden compatibility writer remains.

### Sprint 10 — reusable product platform and transfer closure

**Product outcome:** a new team can operate Gate and add Oko/Mayak without chat history.

**Technical scope:** finalize module boundaries, extract reusable agent/transport contracts, update
cost dashboards/regulations, refresh architecture/README/handoff and define the next Oko release
train. Relationship CRM backfill remains a separate authorized product action.

**Acceptance:** repository-only transfer checklist passes; documentation, schema, CI and production
fingerprint agree.

## 11. Actions that still require Mark

Stop and request Mark only when one of these boundaries is reached:

1. show exact Telegram target and final text, then obtain explicit confirmation before any send;
2. approve a new Alpha installation or private-dialog eligibility decision;
3. change Railway/OpenAI/GitHub production billing or configuration;
4. manually deploy, retry, roll back or alter production variables;
5. choose a product behavior when alternatives materially change user experience or risk.

Do not ask Mark to perform repository status checks, inspect CI, copy IDs, manage temporary switches
or resend work that Gate can safely recover itself.

## 12. Development and release procedure

For every PR:

1. branch from exact current `main`;
2. keep one bounded product outcome;
3. inspect the complete diff and privacy/delivery impact;
4. require Ruff, strict mypy, pytest, Alpha rehearsals, Alembic upgrade/drift/downgrade,
   disposable PostgreSQL backup/restore and both Docker builds;
5. fix only the observed CI cause on the same PR;
6. merge only the exact green head under `docs/TEAM_WORKFLOW.md`;
7. wait for Railway to report the exact merge SHA;
8. run affected read-only production smoke checks;
9. update this handoff when state, next order or owner requirements change.

CI run #906 for PR #244 passed every required stage. Production then reported exact merge
`6680d9dce1cb` and recovered the five deferred Alpha triggers.

## 13. Local bootstrap and environment inventory

Use a disposable PostgreSQL database, never production. Owner-facing local instructions default to
Windows PowerShell.

```powershell
git clone https://github.com/markmalina/gate.git
Set-Location gate
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install ".[dev,contact-sync]"
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy app tests
& .\.venv\Scripts\python.exe -m pytest
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/gate"
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m alembic downgrade -1
& .\.venv\Scripts\python.exe -m alembic upgrade head
```

Important environment-variable names only:

- core: `DATABASE_URL`, `GATE_ENV`, `GATE_WORKSPACE_ID`, `RAILWAY_GIT_COMMIT_SHA`;
- Business: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET`;
- Alpha: `TELEGRAM_ALPHA_BOT_TOKEN`, `TELEGRAM_ALPHA_BOT_USERNAME`,
  `TELEGRAM_ALPHA_WEBHOOK_SECRET`;
- canonical/Alpha runtime: `GATE_TELEGRAM_WORKER_ENABLED`, `GATE_ALPHA_CONNECTOR_ENABLED`,
  `GATE_ALPHA_SHADOW_WORKER_ENABLED`, `GATE_ALPHA_CANARY_ENABLED`,
  `GATE_ALPHA_DELIVERY_ENABLED`, `GATE_ALPHA_PRIVACY_MODE_CONFIRMED`;
- OpenAI: `OPENAI_API_KEY`, `OPENAI_API_BASE_URL`, `GATE_ALPHA_MODEL_RUNTIME_ENABLED`,
  `GATE_ALPHA_MODEL_DATA_GATE_APPROVED`, `GATE_ALPHA_MODEL_MAX_OUTPUT_TOKENS`,
  `GATE_ALPHA_MODEL_TIMEOUT_SECONDS`;
- voice: `GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED`,
  `GATE_TELEGRAM_VOICE_TRANSCRIPTION_MODEL`, `GATE_TELEGRAM_VOICE_LANGUAGE`;
- human sending: `GATE_SEND_ENABLED`, `GATE_ACCOUNT_RU_SEND_ENABLED`,
  `GATE_ACCOUNT_BY_SEND_ENABLED`;
- MCP/Auth0: see `docs/MCP_CHATGPT_AUTH0_RUNBOOK.md`.

Never place values in Git, issue/PR bodies, logs, screenshots intended for publication or chat.

## 14. Transfer acceptance checklist

A receiving owner records:

```text
Gate handoff accepted at: <UTC timestamp>
Accepted GitHub main SHA: <full SHA>
Exact-head CI: <run URL/number> — success
Railway production SHA: <full SHA> — matches / mismatch
Schema head: <revision>
Environment/mode: production/manual
RU/BY health: <redacted aggregate>
MCP public tools: 9 / 9
Alpha v2: preflight verified; proposals <count>; failures <count>
Model Telegram delivery blocked: true
Voice: delivered/retry/dead-letter/open/unknown <aggregate>
Provider/account hard budgets: <redacted limits>
Open PRs and blockers: <list>
Accepted by: <name/role>
```

Acceptance is read-only. Do not send, approve, run preflight, alter installation state, change
variables, deploy, retry or roll back merely to complete the checklist.

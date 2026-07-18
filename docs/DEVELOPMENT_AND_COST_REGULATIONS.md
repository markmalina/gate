# Gate development, testing and cost regulations

> Applies to Gate and serves as the initial company-wide AlphaVia engineering baseline.  
> Product-specific safety rules remain stricter when they conflict with this document.

## 1. Goals

The development system must optimize four things in this order:

1. no secret or private-data exposure;
2. no incorrect external action or silent work loss;
3. fast verified delivery to production;
4. low, predictable GitHub Actions and OpenAI cost.

Cost optimization never justifies skipping the exact checks that protect production. The first
optimization is fewer unnecessary runs and calls, not weaker tests.

## 2. Work packaging

- One PR owns one bounded product outcome.
- Do not create duplicate PRs for the same task.
- Collect related file edits before opening the PR when tooling would otherwise start CI for every
  intermediate commit.
- After the PR exists, batch small fixes when safe; do not push speculative formatting churn.
- A documentation snapshot that materially changes handoff truth is a valid bounded PR.
- Unrelated schema, transport, product copy and deployment changes never share a PR.

## 3. GitHub Actions cost control

Historical July evidence showed 503 private-repository Linux minutes at USD 0.006/minute and USD
3.02 gross. Prices and included allowances can change; use GitHub Billing as the current source.

Rules:

1. Keep the account Actions budget and stop-usage boundary enabled.
2. Use workflow concurrency with cancellation of superseded runs on the same PR when a dedicated CI
   change is approved.
3. Cache immutable dependencies with a lock/config hash, never with secret-bearing paths.
4. Run cheap static checks before expensive database drills and Docker builds so failures stop early.
5. Prefer one full authoritative CI run for the final head rather than repeated manual reruns.
6. Never manually rerun a failed workflow until the observed cause has been fixed or the failure is
   proven transient.
7. A future workflow optimization may add a safe docs-only lane, but it must still validate links,
   migration-head references, secret scanning and handoff consistency. Until that PR is accepted,
   current full CI remains authoritative.
8. Record monthly minutes, failed-run minutes and the three most expensive jobs. Optimize the largest
   repeated waste first.

Recommended monthly review:

```text
Actions budget / actual / remaining
Total Linux minutes
Minutes from failed or superseded runs
Top expensive jobs
Number of PRs and workflow runs
Optimization chosen for next month
```

## 4. OpenAI cost control

OpenAI billing has three separate layers and they must never be described as one:

- prepaid/credit balance and payment method;
- organization/project spend and alert limits;
- Gate's own transactional product budgets.

Current dated baseline:

- prepaid credit funded: USD 5;
- automatic recharge: off;
- organization spend limit: USD 5;
- Alpha internal UTC-month ceiling: USD 50;
- Gate voice internal UTC-month ceiling: USD 1.

The lowest active boundary wins. The internal USD 50 Alpha ceiling does not bypass an external USD 5
organization stop.

Rules:

1. Every provider call reserves a worst-case amount before network access.
2. Exact provider usage settles the reservation; known failures release it; ambiguous outcomes remain
   visible for reconciliation.
3. Synthetic preflight uses no real group/dialog content and never grants delivery.
4. A preflight key is immutable and idempotent. Credential replacement requires a new explicit key;
   do not replay a terminal credential failure.
5. Shadow samples have a declared size and promotion condition. Do not generate open-ended test
   traffic merely to observe variation.
6. Select the smallest model that meets measured quality, not the cheapest model by assumption.
7. Store token and cost aggregates without logging prompts, transcripts or identifiers.
8. Alerts must be early enough to act; recommended thresholds are 50%, 80% and 100% of the real
   external monthly boundary.
9. Auto recharge stays off until the owner explicitly chooses continuity over a hard stop and sets a
   monthly recharge limit.

## 5. Telegram test protocol

### 5.1 Test packets for Mark

When several Telegram messages are required, prepare one service-group message containing separate
inline-monospace snippets. Every snippet is one message Mark copies and sends separately.

Never imply that pasting a multiline block sends several commands. One Telegram bubble is one
durable update and Gate supports at most one Alpha invocation per message.

### 5.2 Verification

For every test record:

- sender category: connected account, unrelated account, human, bot/service;
- receiving connector/account;
- expected receiving-account-relative direction;
- input origin: text or voice transcription;
- expected durable receipt/canonical/turn/grant/send cardinality;
- observed status and bounded error code;
- external delivery expected: yes/no.

Visible Telegram checkmarks prove client-to-Telegram delivery, not Gate processing. Gate status,
durable evidence and exact proposal review prove product processing.

### 5.3 Backpressure

Rate and budget limits are capacity controls. They must defer work or return a visible terminal
product decision; they must never silently mark recoverable work complete.

Alpha currently admits 5 turns per 60 seconds. Additional valid invocations are durably rescheduled
after 61 seconds without an attempt cap. Deferral never creates delivery authority.

## 6. Error handling

When an error occurs:

1. preserve the durable event identity;
2. determine whether the outcome is retryable, deferred, terminal failed or terminal unknown;
3. expose only a bounded safe code;
4. keep private payloads out of logs and exception text;
5. fix the observed cause, not a guessed adjacent cause;
6. add a regression test reproducing the exact failure;
7. recover already accepted user work when safe instead of asking the user to repeat it;
8. update the canonical handoff when the incident changes architecture or operating rules.

External-send ambiguity is different from internal processing failure: `unknown` Telegram delivery is
terminal and must not be automatically retried.

## 7. Switch and configuration policy

Do not add temporary environment switches to make development easier. Use code, migrations, durable
lifecycle and exact revision bindings.

A production control is allowed only when it represents a real operator decision:

- enable/disable an external connector;
- approve/pause an installation or dialog;
- authorize exact delivery;
- set a hard budget;
- manually deploy, retry or roll back.

The operator must not be asked to visit Railway to coordinate sprint stages that code can determine.

## 8. CI and release definition of done

A Gate change is complete only when:

- complete diff and privacy/delivery impact reviewed;
- Ruff green;
- strict mypy green;
- pytest green;
- Alpha release/privacy/incident rehearsals green when affected;
- Alembic upgrade and schema-drift check green;
- disposable PostgreSQL backup/restore green;
- Alembic downgrade green;
- web and contact-sync Docker builds green and unprivileged;
- exact PR head merged under `docs/TEAM_WORKFLOW.md`;
- Railway production fingerprint equals the merge SHA;
- task-specific read-only production smoke checks pass;
- canonical handoff updated when state materially changed.

Local checks or a healthy older deployment are useful evidence but never completion.

## 9. Monthly operating review

Once per month record, without secrets or private message content:

```text
GitHub Actions spend and minutes
OpenAI prepaid balance and organization limit
Alpha reserved/settled usage and failures
Voice reserved/settled/open/unknown usage
Deferred/retry/dead-letter counts by bounded code
External send attempts: sent/failed/unknown
CI failure causes and preventable wasted minutes
Documentation/production fingerprint consistency
One cost optimization and one reliability improvement for the next month
```

## 10. Transfer rule

If a product decision, safety boundary, incident lesson or continuation step exists only in chat, it
is not transferred. Write it into `docs/PROJECT_HANDOFF.md`, this regulation or the relevant runbook
in the same substantive PR.


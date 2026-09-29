# Reply sequences #716 + #718: cutover, monitoring and rollback

This runbook accompanies PR #720 and both specifications in `docs/specs/`.
It is an operational procedure, not permission to run production migrations or send messages.
All commands below require the exact authorized environment and server-side configuration.
Never put credentials, message texts, personal chat IDs or provider payload in a GitHub receipt.

## Defaults and authority

| Setting | Default | Meaning |
| --- | --- | --- |
| GATE_REPLY_SEQUENCE_WORKER_ENABLED | false | Run the sequence worker |
| GATE_REPLY_SEQUENCE_STOP_ON_INBOUND_ENABLED | false | Install durable ingress/sender authority and shared bot budgets |
| GATE_REPLY_SEQUENCE_DISPATCH_V2_ENABLED | false | Use canonical registry and bounded dispatcher |
| GATE_REPLY_SEQUENCE_PARALLEL_ENABLED | false | Permit DB parallel mode; serial still uses one slot |
| GATE_REPLY_SEQUENCE_NOTIFICATIONS_ENABLED | false | Run the separate owner notification outbox worker |
| GATE_REPLY_SEQUENCE_DB_POOL_MODE | unknown | Activation accepts only verified direct or session pooling |
| GATE_REPLY_SEQUENCE_MAX_INFLIGHT | 4 | Workspace-wide durable slots, hard configuration range 1–8 |
| GATE_REPLY_SEQUENCE_MAX_INFLIGHT_PER_ACCOUNT | 2 | Shared account occupancy limit |
| GATE_REPLY_SEQUENCE_MAX_INFLIGHT_PER_BOT | 4 | Shared bot occupancy limit |
| GATE_REPLY_SEQUENCE_POLL_SECONDS | 2 | Existing bounded polling setting |

DB control mode is disabled until explicitly changed. Generation/config mismatch denies new
admissions. Disabling a flag or mode never proves an admitted message was not sent.
The local supervisor is not the fleet limit. A sending/unknown permit is never recycled on TTL alone.
Legacy workers must be absent before v2 activation; old binaries do not know the new authority.

## Prerequisites and receipt

Record accepted source SHA, exact workspace and deployment, migration operator, existing and new
replicas/processes, last-known-good SHA, rollback trigger and owner notification principal binding.
Run full CI on the literal head: Ruff, mypy, PostgreSQL tests, migrations upgrade/drift/downgrade,
restore drill and container checks. Reviewer acceptance is separate from the author's report.

Verify PostgreSQL session semantics: direct connection or session pooling, never transaction pooling.
Each enabled process reserves the configured local cap (default four) sender guard connections plus two ingress guard
connections, independently of the ordinary transaction pool. Account for every process and deployment
replica, including overlapping rollout replicas, CLI and monitoring. Keep at least two ordinary DB
connections available for ingress/health/recovery at saturated sends. The actual sum must fit the
verified PostgreSQL connection ceiling; reduce admitted concurrency/replicas if it does not.
Do not raise the default cap of four without an explicit deployment connection-budget review.

Verify authenticated Business ingress includes business_message and business_connection updates.
No transcription/classification/LLM step is allowed ahead of durable receipt+stop. Verify provider
pending/error state and continuity over the cutover interval. Every ingress web process startup
invalidates connector continuity. A connection/rights update invalidates it immediately on receipt.
After all ingress replicas have started, record new scoped evidence before admitting sends.
Continuity evidence has an explicit expiry of at most one hour; renewal does not excuse an observed gap.
A gap/expiry with active series requires terminal stop and new user approval, not automatic resume.

## Baseline probe before 0112

The read-only CLI command below uses the configured DB URL in its own process and reads only
the live SQL session identity and `alembic_version`; it neither constructs the sequence
runtime nor requires a Telegram bot token or schema 0112. It emits an identity digest, not
the database/role/host/DSN. The operation ID is an opaque Ops journal reference.

```sh
gate-reply-sequence-baseline-probe --operation-id REVIEWED_OPAQUE_ID \
  --expected-workspace mark-personal --expected-schema 0111_reply_documents
```

A READY receipt proves only this CLI connection. It never proves the binding of web/worker
processes, the complete replica inventory, legacy drain, backup or transport. Ops must
compare an independently observed actual DB session on **each** running process with the
provider target and full schema ledger. UNKNOWN, mismatch or a partially observed process
keeps the release blocked. The CLI is suitable for the offline/synthetic P1a package and
a later protected, bounded read-only probe after a separate authorized live plan.

## Default-off deployment and import

1. Before any candidate deploy or automatic `preDeployCommand`, acquire the shared Ops
   deploy/control lock and prove the exact legacy process inventory. Disable new legacy
   admissions on **every** replica and drain/classify current attempts; verify durable
   reserved/sending = 0. A process list or a flag in the new binary is insufficient.
2. Obtain and verify the pre-migration backup of the **actual** Gate DB and an applicable
   recovery drill. Record exact identity, scope and retention. Do not use the old
   `/ops-deploy production target=gate` fast path for this migration: it has no
   freeze/drain/backup control. Prevent pending auto-deploy until these predicates hold.
3. Only then deploy the exact reviewed candidate with new flags off through the
   protected Ops sequence executor. `railway.toml` runs `alembic upgrade head` in
   `preDeployCommand` **before** the new application starts. Verify the full migration
   graph from observed baseline to candidate head (0111 → 0112 → 0113 → 0114 → 0115)
   and reconcile its result; do not
   treat a later manual Alembic call as a separate required stage. No merge/deploy
   is authorized by this runbook.
4. With the worker off and verified pool mode, run:

   ```sh
   gate-reply-sequence-control initialize --bot-resource-id REVIEWED_BOT_DIGEST
   gate-reply-sequence-control import-legacy --bot-resource-id REVIEWED_BOT_DIGEST --legacy-workers-drained
   gate-reply-sequence-control status --bot-resource-id REVIEWED_BOT_DIGEST
   ```

   Import discovers sequence identities by keyset, then loads full memberships of 2–50, independent
   of discovery page boundaries. Valid fully sent history becomes completed. Every unfinished legacy
   series becomes stopped/legacy_reapproval_required; sent, failed and unknown ledger outcomes are
   preserved. Invalid membership is quarantined in legacy metadata. Re-run is a no-op for imported
   identities. There is no notification storm for legacy cancellations and no automatic reapproval.
   A live reserved/sending attempt or non-disabled DB control aborts import.
5. Compare imported/existing/quarantined counts with the scoped baseline. Resolve quarantined
   memberships before activation. Do not edit original 0111 or earlier migrations.

## Serial activation, then parallel

Configure STOP_ON_INBOUND and DISPATCH_V2 true together, DB_POOL_MODE to the verified value,
WORKER_ENABLED true, and PARALLEL true if the reviewed plan includes parallel mode.
Restart every relevant process before installing the epoch; keep DB mode disabled.
The initial DB mode is serial even when the configuration permits parallel.
This avoids a configuration restart inside the bounded canary window.
Initialize notification routing through the already approved account permissions mechanism:
`reply_sequence_owner_subject` must equal the queue approver's authenticated subject. Account owner
Telegram identity and exactly one enabled Business connection must agree; the owner private chat
must be the same owner ID and must differ from the counterparty. Alias/last-chat fallbacks are forbidden.
Enable NOTIFICATIONS only for the authorized owner route. Queue returns ready, blocked_route or
disabled; missing delivery readiness does not undo a committed stop.

After Ops verifies the complete live inventory, install the exact initial epoch using
`epoch-arm --bot-resource-id REVIEWED_BOT_DIGEST` with protected bounded stdin JSON
as specified in [canary admission](REPLY-SEQUENCE-CANARY-ADMISSION.md). When canary
is configured, arm its immutable scope before activation. Inputs and evidence must
come from the authenticated reviewed Ops executor; a user-provided digest is not
proof of inventory. Activate with:

```sh
gate-reply-sequence-control serial --bot-resource-id REVIEWED_BOT_DIGEST --legacy-workers-drained
```

No arbitrary continuity expiry/ref is accepted. The runtime automatically renews
only the same verified healthy epoch and preserves generation. A gap is terminal;
use `stop-active`, reconcile and obtain new approvals after a newly verified epoch.
Queue fresh synthetic/staging series with the current `dialog_inbound_revision` from context.
Require `sequence_approval.ready=true` and a non-null revision. A pending/dead-letter receipt,
missing message projection, obsolete task, changed revision during the read, or an older history
page does not supply approval authority. Refresh the latest task/context after processing completes;
never substitute the revision from sequence detail or guess a counter. Queue and final admission
independently recheck projection readiness. A dead-letter receipt needs normal worker recovery;
marking it processed without the latest scoped Message projection does not authorize sending.
Observe serial delivery, stop-on-inbound, duplicate webhook, expiry and notification readback.
For production canary, obtain exact owner-only installation/recipient refs and hard send caps first.
Never use contacts or historical conversations as convenient test targets.

After the serial gate, with PARALLEL already enabled and the same verified epoch healthy,
run the same activation command with `parallel`. Canary scope, expiry and attempt count
are unchanged. A configuration change or restart requires closing the old canary and
a separately reviewed new epoch/scope; do not reset the deadline or quota.
Record mode/generation and source/deployed SHA.
Configuration changes invalidate old claims; existing accepted requests retain honest outcomes.

## Monitoring and incident response

`gate-reply-sequence-control status --bot-resource-id REVIEWED_BOT_DIGEST` is payload-free: mode/generation/import status, occupied and
quarantined permits, active series, oldest due, last admission, observed time and ingress/degradation
reason. Worker heartbeat proves loop liveness only. Compare oldest eligible work against confirmed
item progress and resource wait. `gate_get_reply_sequence` requires workspace/account/project scope;
unknown and foreign refs have identical not-found behavior. Detail counts partition all items,
not just the returned page; unknown and sending are never counted as sent. Readback includes stop
reason, revision, notification state, due/cooldown and no-auto-resume evidence.
Detail also includes `recipient_display` and paginated saved `draft_text` values. Iterate every
`next_cursor` to recover the remaining paragraphs. `draft_text_matches_approval=false` means the
saved body changed; null means no correctly bound body is available. These are current saved drafts,
not a second immutable body archive. Owner notifications use the same bounded, single-line recipient
label; unnamed dialogs get a stable opaque fallback. Incoming message bodies are not copied into them.

- Provider 429: current series stops; shared account/bot/recipient cooldown persists. Neighbors wait
  for that resource. Missing delay falls back to 60 s; unrepresentable delay blocks indefinitely.
- Provider timeout/5xx/connection loss: delivery unknown, tail cancelled; no automatic resend.
- Expired preflight without attempt: token-CAS release/reclaim only. A stale callback cannot release
  the new owner's permit. Expired sending: reconcile to unknown and quarantine when task termination
  cannot be established. A locally completed unknown send may release capacity, never its delivery fence.
- Stop during existing HTTP: ingress waits for the same advisory barrier. On its 2 s acquire timeout
  it cannot ACK durable receipt; provider retry is required. An already accepted HTTP request cannot
  be unsent. Once stop commits, the old sequence cannot start another request.
- Lost guard connection: cancel transport and preserve ambiguity. Before a terminal stop with an
  admitted attempt, hold the barrier and wait 110 ms: this exceeds the old 100 ms HTTP start window,
  which begins before admission commit. A paused former guard owner cannot resume a fresh HTTP call
  after stop commit; its adapter rejects the expired monotonic window. HTTP has a 20 s deadline and
  the dispatcher a 30 s envelope. Shutdown drains 20 s, cancels/observes 5 s; unresolved tasks retain
  ownership, disable admission, quarantine permits and terminate the process. Never restart a local pool
  whose HTTP tasks survived cancellation.
- Owner notification timeout: unknown with no replay. Known 429 retries are bounded to four attempts
  with 5/30/120 s minimum delays plus retry_after. Permanent refusal fails; unverified route blocks.
  Persisted stop/readback remains available regardless of notification delivery.

For immediate admission stop:

```sh
gate-reply-sequence-control disable --bot-resource-id REVIEWED_BOT_DIGEST
gate-reply-sequence-control status --bot-resource-id REVIEWED_BOT_DIGEST
```

For an observed ingress gap/expired evidence/connection rebind with active series:

```sh
gate-reply-sequence-control stop-active --bot-resource-id REVIEWED_BOT_DIGEST
```

This disables admission first, then terminally stops active series under their dialog barriers.
Retry the command after transient guard contention; committed stops are idempotent. Investigate and
reconcile any blocked dialog health under a separately reviewed exact scope; do not globally mark
blocked guards healthy or reuse their prior approval. New activation requires fresh continuity proof.
There is deliberately no generic 'retry unknown' or 'resume old series' command.

## Rollback

Disable DB admission first, then stop the sequence worker and drain/classify admitted requests.
Keep durable ingress, readback and reconciliation available for the latest schema. Record all sending,
unknown and quarantined counts; never clear claims or budgets to make health green. Prefer serial mode
on the same reviewed binary after resolving the cause. Reverting the binary to a legacy worker is not
safe while canonical series exist. Do not downgrade/drop 0112 on production with durable history;
CI downgrade is only a disposable-schema reversibility check. Use the registered Ops rollback to the
recorded last-known-good deployment only after its feature/schema compatibility is established.

The release receipt must distinguish code/CI acceptance, migration acceptance, runtime continuity,
owner notification delivery and bounded live-canary acceptance. No production-ready claim follows
from a green build alone.


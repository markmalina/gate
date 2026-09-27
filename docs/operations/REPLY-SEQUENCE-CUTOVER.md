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

## Default-off deployment and import

1. Deploy reviewed code with all new flags false, through the registered Ops route when authorized.
2. Set legacy sequence worker off on every replica/process. Drain and prove there are no reserved or
   sending legacy SendAttempts. Do not infer this solely from a local process list.
3. Apply `alembic upgrade head` through the approved migration path. Revision 0112 creates only
   authority tables/indexes/constraints; it does not send, activate or import payloads.
4. With worker off and verified pool mode, run:

   ```sh
   gate-reply-sequence-control initialize
   gate-reply-sequence-control import-legacy --legacy-workers-drained
   gate-reply-sequence-control status
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
WORKER_ENABLED true, PARALLEL false initially. Restart every relevant process. Keep DB mode disabled.
Initialize notification routing through the already approved account permissions mechanism:
`reply_sequence_owner_subject` must equal the queue approver's authenticated subject. Account owner
Telegram identity and exactly one enabled Business connection must agree; the owner private chat
must be the same owner ID and must differ from the counterparty. Alias/last-chat fallbacks are forbidden.
Enable NOTIFICATIONS only for the authorized owner route. Queue returns ready, blocked_route or
disabled; missing delivery readiness does not undo a committed stop.

After verifying uninterrupted ingress, activate serial with an opaque evidence reference and explicit
aware expiry (substitute the reviewed values, not the literal examples):

```sh
gate-reply-sequence-control serial --legacy-workers-drained \
  --continuity-evidence-ref REVIEWED_OPAQUE_REF --continuity-until REVIEWED_ISO8601_EXPIRY
```

The CLI records only the evidence digest in audit. It rejects activation across an unverified gap
while active series exist. Use `stop-active` first for that incident; obtain new user approvals later.
Queue fresh synthetic/staging series with the current `dialog_inbound_revision` from context.
Observe serial delivery, stop-on-inbound, duplicate webhook, expiry and notification readback.
For production canary, obtain exact owner-only installation/recipient refs and hard send caps first.
Never use contacts or historical conversations as convenient test targets.

After the serial gate, enable PARALLEL, roll all replicas, re-verify continuity after startup, and run
the same activation command with `parallel`. Record mode/generation and source/deployed SHA.
Configuration changes invalidate old claims; existing accepted requests retain honest outcomes.

## Monitoring and incident response

`gate-reply-sequence-control status` is payload-free: mode/generation/import status, occupied and
quarantined permits, active series, oldest due, last admission, observed time and ingress/degradation
reason. Worker heartbeat proves loop liveness only. Compare oldest eligible work against confirmed
item progress and resource wait. `gate_get_reply_sequence` requires workspace/account/project scope;
unknown and foreign refs have identical not-found behavior. Detail counts partition all items,
not just the returned page; unknown and sending are never counted as sent. Readback includes stop
reason, revision, notification state, due/cooldown and no-auto-resume evidence.

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
gate-reply-sequence-control disable
gate-reply-sequence-control status
```

For an observed ingress gap/expired evidence/connection rebind with active series:

```sh
gate-reply-sequence-control stop-active
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

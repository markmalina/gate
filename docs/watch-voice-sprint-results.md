# Watch Voice implementation and acceptance

Continues #736/#731 and the single implementation PR #766. The five-sprint
owner request of 25 October supersedes planning-only scope; it does not turn
historical spent Ops grants into reusable credentials or acceptance.

## Independent defect769 remediation and release facts — source only

Independent [Delta349](https://github.com/AlphaCenter/delta/issues/349) REJECTED
head4da7445 for false recovery and liveness suppression. The terminal verdict
is preserved. Fixes continue in the existing PR766, with a new independent
attempt required for the successor; prior green CI is not acceptance of it.

- A matching full deployment SHA, expected driver and recorded cursor ownership
  are required before a bounded phase can suppress stale/progress signals.
  Different drivers/revisions cannot lend their heartbeat to this deployment.
- Source-only pending/retry/downloading work now contributes to queue size/age;
  archive acknowledgement loss does not count one source twice. Unresolved
  download leases and cancelled/no-first-scan cycles remain visible.
- The existing application lifespan records an expectation before scheduling
  its existing persistent worker. Startup has180seconds grace and is explicitly
  not proof that the worker runs. Missing evidence after grace raises a fault;
  matching healthy idle cycles remain quiet. No new loop or heartbeat is added.
- Empty codes during startup or unproven Actions idle cannot close an existing
  incident. Recovery requires a fresh matching cursor-owner observation; repeated
  observations still produce one durable recovery event.
- GET `/admin/ops/watch-voice` uses the existing admin bearer before database
  access. It returns a closed, payload-free runtime fingerprint: sanitized fixed
  Railway identity fields, full process source SHA, actual loaded driver flags,
  bounded read-only current/expected Alembic heads and journal worker proof.
  It performs no writes/provider calls and creates no release authority.
- The endpoint deliberately leaves effective start/predeploy command values and
  historical predeploy execution unknown. A ready schema or source TOML is not
  evidence of that execution. It is absent on the old production revision and
  cannot bootstrap authorization merely by being deployed for observation.

Effect: remove false recovery, account for pre-archive backlog and provide
authenticated facts for the separately reviewed Gate-owned Ops preflight.
Cost: zero real STT/publications in this source slice; one metadata expectation
row per approved persistent application start and bounded read-only SQL per
explicit authenticated preflight request. Actual infrastructure cost unknown.
Validation and exact successor SHA/CI are recorded in the latest PR/769 receipt.
Rollback: restore the reviewed application while retaining additive schema and
journals; these fixes do not alter audio, model/budget, cursor, effect claims or
the existing worker count. Production migration/deploy/cutover are not performed.

## Durable continuation — 25 October 2026, source only

This section supersedes the process-local monitoring limitation in the earlier
dated Sprint2 evidence below. It does not supersede its production boundary.

- Migration0116 adds durable scoped cycle/phase/scan evidence, discovery/download
  records with CAS leases and bounded retry, incident episodes and nine nullable
  note measurements. No historical values are guessed or backfilled. Audio,
  transcripts, cursor/watermark and existing effect claims are preserved.
- Both existing drivers write cycle evidence; Actions starts its journal after
  the existing protected private binding is verified. Invalid/conflicting driver
  admission does not create a journal or reach an external effect.
- Discovery commits each admitted source revision before downloading. An oversize
  file is visible as rejected; a temporary download failure is retryable. Neither
  holds unrelated records or cursor advancement. Expired download leases recover
  safely; a saved archive after an acknowledgement loss is not downloaded again.
  Historical watermark, exact-source and workspace/account boundaries still apply.
- Protected `watch-voice:sources` pages those source states without audio/text.
  Existing `watch-voice:list` adds observed processing/provider start/finish,
  attempt counters and incomplete-timing disclosure. Legacy counters stay null.
- Existing Gate status reads SQL across API/worker processes, showing expected
  and observed driver, full SHA, bounded phase deadline, last successful scan,
  queue, sources and incident status. Fresh cursor-owner evidence from the exact
  deployment is required for running_driver_proven. Timing samples omit unknown,
  negative and incomplete values; all percentiles carry n, period and preliminary.
- Existing Calls loop observes stale/deadline/queue/source/budget signals and
  records one durable incident episode. Codes, escalation and recovery have
  at-most-once log attempts across restarts and concurrent observers. No new
  loop, schedule, credentials or outbound message mechanism. Actual push receipt
  is not claimed. Disabled monitoring host is explicit in status; production must
  retain the existing Calls loop or approve an existing-monitor integration.
  Conflicting driver flags produce an explicit fault even with an empty queue.
  Each observation is bounded to5seconds so slow diagnostics do not hold Calls.
- Speech model, endpoint, no-retry policy, timeout/part limits and shared $3
  hard cap are unchanged. No real provider or publication call was made by this
  Work. Each observed part adds bounded SQL metadata writes. At a quiet30s poll
  the journal grows by approximately2880cycles/day; actual DB storage/latency and
  infrastructure attribution remain unmeasured. History is not automatically purged.

Synthetic checks cover migration preservation/unknown legacy values, independent
readers/restarts, phase deadlines, stuck-worker recovery, incident dedup, concurrent
PostgreSQL observers, download retry/lease recovery, historical/foreign exclusion,
bad-file batch isolation, monitor failure isolation, shared speech charges/reserves,
multipart provider counters and missing-finish timing exclusion. Local and exact-head
CI results are recorded in the PR receipt for this immutable candidate.

Read-only production observation at18:40UTC: source prefixafe0c6b85193, old Actions
enabled, persistent disabled, Calls enabled,47delivered/7received. These are scoped
aggregates, not a new every-record/full-text acceptance result. Subsequent read-only
verification of four more bot-authored single-part issues matched their complete
Gate text and current Dropbox ID/revision/size/headerhash, bringing the checked
cohort to47. No duplicate source revision found in the47matching issues. Today's
completed cohort n22/preliminary: P50=2504s, P95=9786s, max=10710s, based on actual
Dropbox server_modified and issue created_at; unfinished records are excluded.
These are old Actions backlog delays, not candidate performance. The candidate is NOT
deployed; no measured latency improvement or24h/restart acceptance is claimed.

Fresh complete non-recursive Dropbox listing at18:53UTC contains65files, one
new supported audio revision beyond the initial64. Its exact protected receipt
matches current metadata: received/pending, no last error, archived at18:49:37Z
after Dropbox server_modified18:47:51Z. This unfinished record is excluded from
completed-delivery percentiles. No recording/device timestamp is inferred.

Release remains blocked on independent new-head review and the registered Gate/Ops
startup/predeploy/migration/private writer/narrow setter/one-driver prerequisites.
Old spent grants must not be rearmed. Safe rollback disables Voice through the
authorized setter and restores reviewed LKG applicationafe0c6b8519381a0007c0dcc1c145207b5d54a3f
while retaining additive schema0116, journals, originals, cursor and claims;
never downgrade live schema or blindly repeat an uncertain effect. Device trials,
poll-versus-webhook measurement and24h observation remain dependent on release.

## Sprint 1 — source implementation, live acceptance incomplete

Observation window: 25 October 2026, 17:05–17:30 UTC (20:05–20:30 Moscow).
Main: `86dbc0f429dce089bf733fc422f20f556535895b`.
Observed Production prefix: `afe0c6b85193`; constant worker disabled.

### Reconciliation actually performed

- Complete non-recursive Dropbox /Alpha/Voice listing: 64 files, has_more=false;
  current metadata/revision read for every file. 29 have server_modified on
  25 October UTC, the same business-day cohort in Moscow for this snapshot.
  No timezone or recording-finish time is inferred from a filename.
- All 40 Gate-authored initial Voice issues returned by the complete private
  Mark issue listing were joined to protected Gate receipts. Their exact
  source ID/revision/size match the current Dropbox metadata. All are bot-authored,
  single-part publications with zero comments; every published transcript equals
  the full Gate transcript, character for character. No duplicate result found
  among these 40 sources. No audio or transcript copied into this repository.
- 15 of these complete publications belong to today's cohort. Gate's scoped
  aggregate is 40 transcribed/delivered and 14 received/pending. The latest
  protected receipt independently proves one of the 14 is received/pending
  without an error. The other pending rows are NOT individually observable
  through the current deployed exact-ID/latest interface. Aggregate equality
  does not prove their individual state or absence of a hidden error.
- Ten older Dropbox files also have no matched publication among the40verified
  current source revisions. The deployed interface does not prove whether each
  was intentionally excluded by the historical watermark, superseded or not
  admitted. They remain explicit older-source reconciliation gaps; do not silently
  backfill them or infer exclusion from their names/aggregate counts.
- Completed-cohort Dropbox server_modified → full issue created_at, nearest-rank:
  n=15, P50=1311s, P95=5278s, max=5278s. Preliminary, n<30; unfinished records
  excluded, not treated as zero latency. This is the old Actions baseline,
  not improvement and not Stop-on-Watch timing. No duration-qualified SLO claim.
- Automatic scheduled run40214865308/job121233695545 reported processed=1,
  published_parts=1, delivered=1 at17:06:56Z. This corroborates actual autonomous
  old-path progress, not persistent-worker readiness.

### Implemented

`gate_get_reply_context(task_id="watch-voice:list", limit=1..50, cursor=...)`
adds an owner-scoped keyset listing to the existing protected interface. It
selects no audio blob and returns no transcript. Cards include exact source,
size/duration, available timestamps, states/reason, full-text selector, expected
and confirmed publication parts, and exact normal/recovery ledger costs.
No claim, reprocessing, provider request or publication is made by listing.

Unknown historical detection/start/attempt fields stay null. A budget reservation
is not counted as a provider request or job attempt. An uncertain reservation
keeps total cost unknown while exposing the settled component. Journal-complete
requires all parts, URL and full-confirmation time; external text verification
is explicitly separate. Live keyset paging is not labelled a frozen snapshot.

### Acceptance and release boundary

Source checks cover pagination ties, foreign workspace/account isolation,
malformed cursors, audio-free selection, partial UTF-8 publication and unknown
cost. Current source policy/skill pin is unchanged and verified. Production
has not received this inventory; Sprint1's every-source live criterion remains
OPEN pending its release. New files can arrive while reconciliation runs.

Effect: diagnosis without STT spend or replay risk. Provider cost of this Work: $0.
Existing shared speech ledger at initial observation: settled742632µUSD,
reserved166800µUSD, remaining2090568µUSD of3000000µUSD; these are ALL speech,
not Voice-only charges or the OpenAI account balance. Per-note Voice actual
cost/duration is not available from the currently deployed receipt.

Rollback: revert the new inventory selector/schema/module; existing exact-ID,
latest/file reads and old driver remain. No schema/config/original/cursor,
budget/model/claim/outbox changes in this sprint.

Next: independent source verification and release prerequisites, plus safe
monitoring/worker improvements on this same PR. Do not mark product accepted
from local tests, green CI or a successful deploy alone.

## Sprint 2 — measured status source, alert acceptance still open

Added payload-free `gate_get_status.watch_voice_metrics`, through the existing
owner scope. SQL supplies queue depth/age, latest completed publication, the
last24h nearest-rank P50/P95/max with n/preliminary/sample cap, and month-to-date
exact Voice normal/recovery settled charges and unresolved reserves. Shared
speech budget/remaining remains in the existing `telegram_voice` status;
infrastructure charges are explicitly unattributed, not zero.

Cycle/scan observations instrument BOTH existing drivers, but are labelled
current-api-process-only/not-durable and reset on restart. They do not prove a
cluster-wide active driver. Missing historical STT start/request durations have
n=0, never fabricated from archive-to-finish time. Existing root status commit
is the deployment version; expected driver is computed separately from live
configuration. No `HTTP200=worker healthy` assertion is made.

Synthetic checks cover quiet idle, Actions-versus-persistent thresholds,
queue-without-progress, unknown effects, cycle failure/recovery, safe error codes,
quantiles and exact owner Voice charge attribution. Local bounded Watch suites:
194PASS/3PostgreSQLskips; Ruff and new-module mypy PASS.

**Incomplete:** no deployed durable cycle/scan timeline or cross-process
liveness; no configured external notification adapter is proven by current
source. Existing Telegram incident classes are not wired to a runtime observer
and their single-active-stream database index must not be silently reused for
Voice. This commit does not claim deduplicated fault/recovery delivery or Sprint2
acceptance. Do not open a new notification channel to hide that gap. Continue
through the existing Gate monitoring route after a reviewed durable integration.

Effect: observable queue/completed-phase/Voice cost in existing status after
release; bounded metadata queries, zero provider cost. No model/budget/schema/
driver activation or outgoing alert added. Rollback: revert metrics module,
status/schema additions and instrumentation; original receipts remain intact.

## Sprint 3 — additional persistent-worker source fixes

The existing worker now continues immediately after useful bounded-batch
progress, yields to the event loop, and returns to the configured polling delay
when idle. It publishes each fresh transcript before starting the next STT,
instead of holding all new results until ten transcriptions finish. Fresh rows
are excluded from the second publication sweep, so a lost acknowledgement is
still reconciled in a later cycle and never blindly reposted.

Source scanning is isolated from processing already archived authorized work.
Explicit transient Dropbox OAuth/identity-transport/delta availability errors
record a safe scan error and allow archived work to continue; scope/identity
mismatch, malformed metadata and integrity errors still stop. Cursor is not
advanced past an unsuccessful page. No lease/claim/watermark/budget/model changed,
no STT parallelism added, and Actions admission/caps are unchanged.

Local final bounded Watch suites:198PASS/3explicit PostgreSQLskips. New fixtures
prove archived delivery through a Dropbox outage, no repeat provider/publication
on replay, identity-mismatch fail-closed, and loop delays0/0/30after progress/
progress/idle. Existing12-note restart/batch, watermark, lost-ACK, continuation,
quarantine, budget and recovery tests remain green. New modules/worker mypy and
all changed-code Ruff pass. PostgreSQL multi-process execution and actual restart
are not newly proven by SQLite fixtures.

Production re-observation during work: sameafe0c6b85193, Actions-only,42delivered,
12received/pending,64Dropbox files. Shared speech settled753052µUSD,
reserved166800µUSD,remaining2080148µUSD. Those2new deliveries and speech charges
belong to the unchanged production Actions flow, not this source implementation.

**Acceptance OPEN:** no controlled driver switch/restart, verified server writer,
effective startup/predeploy/config or24h observation. A per-source durable
diagnostic for metadata/download failures is still required before claiming a
problem file cannot block discovery of others. No old Actions mechanism disabled.
Effect after release: less queue and publication waiting, same STT rates/caps;
additional infrastructure cost unmeasured. Rollback restores the prior worker
source, preserving every source/claim/cursor and publication receipt.

## Sprints 4 and 5 — actual disposition

Webhook Voice wiring is not implemented/activated. Current webhook only marks
Calls pending. Durable pending generation, crash/reorder tests and same-worker
wake-up require a reviewed source slice; an in-memory-only shortcut is rejected.
Polling has not yet been released/measured, so no webhook speedup is claimed.

The single runbook now includes current source/production separation, exact
inventory/status/recovery procedures, one-driver release/rollback requirements,
device trial protocol and separate30/60/120-minute cost forecasts. Device work,
per-note actual-cost live reading, original retrieval/backup/restore acceptance
and24h observation remain outstanding. No device setting, schedule, audio,
history or retention policy was changed or deleted.

## Next bounded work and blockers

1. Complete durable/cross-process Voice cycle/start/attempt telemetry and integrate
   fault/recovery into the existing monitoring channel with incident deduplication.
   Process-local timestamps in the current source are explicitly insufficient.
2. Add durable source-problem inventory so a bad entry is diagnosed while other
   entries/archived jobs proceed without cursor loss. Extend fault fixtures.
3. Obtain independent exact-head source acceptance and fresh admissible Ops
   evidence for effective startup/predeploy/schema, private-Mark server writer,
   mode setter and one-driver overlap. The old diagnostic grants remain SPENT;
   current generic implementation instruction does not rearm them. No direct
   Railway UI/API fallback or secret provisioning performed.
4. Controlled release, exact runtime fingerprint and bounded real acceptance;
   then webhook decision and device trials, followed by at least24h observation.

This is a saved, tested partial implementation, NOT completion of all five
sprints. #736/#731 and release/monitoring/product acceptance stay OPEN.

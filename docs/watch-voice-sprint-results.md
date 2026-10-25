# Watch Voice implementation and acceptance

Continues #736/#731 and the single implementation PR #766. The five-sprint
owner request of 25 October supersedes planning-only scope; it does not turn
historical spent Ops grants into reusable credentials or acceptance.

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

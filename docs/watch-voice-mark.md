# Owner watch voice → private Mark inbox

## Current continuation — 16 October 2026

Status: source preparation; autonomous product NOT accepted. This continues
Gate #736/#731 and the existing Gate PR766. The owner explicitly instructed α
to execute through a subagent, check its work and continue without a manual
handoff. Ops375 is the bounded accepted infrastructure dependency.

Private Ops sharing was saved on 16 October under the owner's exact confirmation:
[receipt](https://github.com/markmalina/ops/issues/375#issuecomment-6215668819).
Canonical Delta review is restored. Historical access/archive blockers below
are superseded; neither restoration proves runtime readiness.

β prepared the fixed Gate-only read-only runtime observer; α reviewed it and
repeated its tests. [Delta320 SOURCE acceptance](https://github.com/AlphaCenter/delta/issues/320#issuecomment-6220355323)
covers C8103370c6a34d09eb9d839d9dd95104bb3a3a740.
[Delta321 release acceptance](https://github.com/AlphaCenter/delta/issues/321#issuecomment-6220452084)
covers release-only R1311819b4ecbba291e244c177922fd194f6b880d.
[Ops PR377](https://github.com/markmalina/ops/pull/377) merged as
e3b492dd5497a8dc0e054ba5e34b4f1ef9b24542; its complete tree equals R.
Original exact C, exact R and merged-push CI/Security passed, including
1213 Node tests, two property tests and the existing security/reproducibility
checks. The Gate lock pins the accepted immutable R, whose code and release
bytes are identical to the installed merge.

### Read-only observation caller

The candidate adds `.github/workflows/ops-runtime-observer.yml` and
`agent-ops.runtime-observer.lock.json`. It runs only from a new exact owner
label on the same Gate PR766 after that whole candidate's CI and a NEW
independent review. No Gate main merge is needed for this bootstrap.
Admission binds exact head/base, owner23397294, repository1293746267, immutable
workflow/lock/Ops pin, unedited owner request and 15-minute expiry, first
original run and complete run history. Previous failed/cancelled/unknown runs
consume that head; never rerun or create a no-change head to replenish it.

The protected job calls only the accepted immutable Ops action with the
existing gate-production credential. There is no PR checkout, dependency
installation, provider mutation, variables/SQL, STT or messages. Six fixed
Railway queries at most, zero retries, bounded responses/time, expiry before
each fetch and source/state brackets. A separate job without the protected
environment/provider credential verifies both raw artifacts and their
provenance/digests. Result remains observation-only, never a release grant.

This bootstrap assumes an owner-controlled leased branch. It does not protect
against a malicious repository writer replacing the workflow. Live use needs
the independently accepted exact Gate head, frozen branch/no conflicting
writer and successful admission by the existing environment policy. Never
weaken that policy or provision credentials to pass. The exact command and
label construction are in the pinned Ops action README; construct them only
after final review from fresh UTC, then read back the owner comment before
applying its head-bound label.

Null pending state is not zero. Null configuration remains UNVERIFIED.
Configured predeploy is not effective predeploy; the latter stays
UNKNOWN_NO_EXECUTION_PROOF. No observation result waives the separate Gate745
snapshot cutover's native, backup or schema gates.

Correction after canonical-tree verification on 16 October: Gate main86dbc and
this candidate contain the same 119 migration blobs, with the unique graph head
0115_watch_voice_post_payment, matching the observed live schema. There is no
0116 in either immutable source tree. An untracked local file from the separate
unmerged Gate745/PR747 investigation caused the earlier incorrect 0116-source
assumption. Only canonical Git objects establish the deployed source graph;
never build from that mixed local directory or infer its Alembic head is main.
The current application therefore has no 0115-to-0116 migration dependency.
Ordinary release still requires fresh source/CI/review, target/credential and
actual runtime/configuration evidence; matching migration heads alone prove
neither effective predeploy behavior nor successful deployment. Gate745's
separate cutover remains unapproved and is not activated by this work.
The Gate server writer presence preflight is still an unshipped presence-only
candidate, not proof of credentials or a suitable writer.

### Queue correctness and remaining acceptance

Actual scheduled runs38902268760/38867530352/38840830162 were created and
started at the same timestamps, separated by approximately160–312 minutes.
The failure is before run creation; its internal GitHub cause is unproven.
Cron changes or processing more work in one run alone cannot establish a
reliable trigger. Prefer the existing persistent worker only after its
existing writer is factually validated. This candidate now shares the durable
future-only protections across both drivers; activation remains off.

The source audit also found that an oldest unresolved unknown publication can
block later actionable notes, and an old note's received month can select the
wrong budget period after an outage. Their bounded correctness changes remain
in this same candidate, with synthetic unknown/fairness and month-boundary
tests; they do not enable the worker, rearm a claim, reset history or add retry.
The one-note/one-publication-part Actions cap remains until separately reviewed
driver changes. No current overspend is asserted.

The ordinary worker now requires an existing nonempty cursor and admitted
future_cutover_at before any HTTP. It never creates or advances the admission
watermark. Shared predicates exclude old Dropbox entries before download and
historical queued rows before quarantine, STT or publication. The existing
Actions admission can install the watermark only after delivered initial proof
and an existing cursor. Exact-source recovery remains separately authorized.
Synthetic tests cover missing admission, unchanged historical row journals,
12 future notes draining in batches of 10+2 after a temporary outage, and
duplicate discovery/restart without extra STT or issues. Production activation
still needs verified writer/configuration and one driver only; the forced-off
worker and existing Actions mode in railway.toml are unchanged.

α independently reviewed the queue/budget/future-only changes and ran 183 watch, MCP
surface and caller-contract tests successfully; three PostgreSQL-only checks
were explicitly skipped locally and remain required in hosted exact-head CI.
The month-boundary tests use the actual SQL budget adapter and synthetic audio;
no production provider request or publication was made by these tests.
Delta322 rejected the previous head's incorrect current-source 0116 assertion;
that terminal verdict is preserved. The corrected source and guard require a
new independent attempt, not reuse of the previous acceptance or rejected head.

Official model/request/pricing/data-control documents were rechecked on
16 October; the pinned models, endpoint, 25 MB part limit, no training/retention
terms and shared USD3 cap remain unchanged. The skill bytes match the accepted
lock. The official deprecation page lists 26 February 2027 removal for these
models; this work does not switch models or expand access.
Sources: https://developers.openai.com/api/docs/guides/speech-to-text ,
https://developers.openai.com/api/docs/guides/your-data ,
https://developers.openai.com/api/docs/pricing ,
https://developers.openai.com/api/docs/deprecations .

Gate main86dbc0f429dce089bf733fc422f20f556535895b and live/LKG
afe0c6b8519381a0007c0dcc1c145207b5d54a3f/schema0115 were unchanged at this
checkpoint. Mark-personal watch counts remain seven transcribed/delivered,
without pending states; this scoped status is not a global user census.
Both 15 October notes are already delivered; never retranscribe them.
Next: separately accepted Gate client, one bounded actual observation, safe
driver/release, then new physical notes and several-note/outage/replay tests
with exact stage timings. Ask for physical notes only when the system is ready.
Keep #736/#731 open until proven product acceptance.

## Historical continuation — 15 October 2026, 18:19 UTC

Owner renewed continuation with «Делай». The existing AlphaCenter/delta review
repository was unarchived, preserving visibility/membership. API archived=false
and new [canonical Delta316](https://github.com/AlphaCenter/delta/issues/316)
prove the review route works again. Its terminal BLOCKED verdict preserves the
unmet autonomous-product criteria; it does not accept a future implementation.

Ops private-action Access was freshly observed as Not accessible. GitHub offers
only access from ALL private repositories owned by markmalina, with no Gate-only
selector. The broader option is prepared but NOT saved, pending action-time owner
confirmation required by the browser permission-change policy.

This candidate adds read-only server_writer_configuration to the existing
owner-scoped watch status: token_present and writer_present Booleans, plus
verified=false. Neither secret values nor writer identity are returned. Foreign
scope returns null; the check makes no HTTP/provider/database calls itself and
does not enable a worker, change a credential or claim writer validity.
Use this minimal preflight after the reviewed Gate-owned release to select a
reliable autonomous driver with existing credentials. If the writer is absent,
record the exact remaining credential boundary; do not provision it silently.

Delta317 rejected the first candidate because the new field was absent from the
strict MCP output schema. The corrected candidate adds the nullable, closed
presence-only schema with verified constrained to false, and validates both
actual owner and foreign outputs against it. The original verdict remains at
https://github.com/AlphaCenter/delta/issues/317 .

Validation:161 watch/surface tests passed/3 explicit PostgreSQL skips locally; targeted
owner/foreign/redaction tests3passed; Ruff and Mypy passed. No model/endpoint,
budget, retention, retry, cursor, journal, binding, workflow pin, migration or
processing behavior changes. Hosted exact-head CI and independent source review
are required before release. The preceding checkpoint below remains historical
where it says Delta is archived.


## Historical checkpoint — 15 October 2026 10:30 UTC

Task Gate #736 continues parent #731 under the exact owner request
[15 October handoff](https://github.com/markmalina/gate/issues/736#issuecomment-6189801263).
[Receipt and accepted execution](https://github.com/markmalina/gate/issues/736#issuecomment-6189869154).
Status: blocked_release_and_formal_review; full product NOT accepted.

Two actual owner notes today have protected originals and complete transcripts,
each linked to exactly one private Mark issue. First delivery was scheduled;
second required one owner-command run. Three fresh owner-command replays ended
idle with zero effect counters; protected receipts and shared budget were unchanged.
The current watch queue is empty, but this was achieved with manual intervention.
Private source identifiers, audio and transcript text remain outside Gate git.
[Private current evidence](https://github.com/markmalina/mark/blob/337cf4a988346adc78a0ad18988254a02e85653e/data/receipts/2026-10-15-galaxy-watch-autonomy-checkpoint.json).

Observed Gate main:86dbc0f429dce089bf733fc422f20f556535895b.
Observed live/LKG:afe0c6b8519381a0007c0dcc1c145207b5d54a3f,
schema0115_watch_voice_post_payment; readiness/database/migrations OK.
No runtime code/configuration/credential change or deployment in this execution.

### Diagnosed boundary and minimal repair

- The active private Actions caller has cron2-59/5 and one note/publication part
  per authenticated run. Actual schedule events are separated by hours.
  created_at equals run_started_at; execution takes seconds. Delay is located
  before scheduled-run creation. The internal GitHub scheduler cause is unproven;
  quota/limits/outage are not asserted. Editing cron alone cannot prove recovery.
- Prefer a Gate-owned persistent queue driver, reusing an already-authorized
  existing writer only after a safe presence/identity/private-target preflight.
  Existing permanent Mark writer presence is UNKNOWN. This task does not authorize
  provisioning or changing credentials/OAuth/billing, nor a paid 24/7 Actions
  runner. If no suitable existing writer is available, record the exact blocker.
- Historical diagnosis, repaired in the 16 October candidate above: the then-current process_watch_voice path
  does not apply future_cutover_at when scanning stored notes. The repair must
  share/enforce the existing durable future-only cutover before downloading and
  selecting queued rows, preserve its timestamp/cursor/journals, bound each queue
  cycle, and retain all source/provider/publication idempotency and unknown states.
- Fixed source /Alpha/Voice, mark-personal and private markmalina/mark; no old
  backlog, foreign repository routing or Telegram. Model-access lock/policy
  remains unchanged (shared USD3/month, one provider attempt/part,60s,zero retries).
- Necessary verification: several new future notes drain without Work/manual
  commands, original/transcript/one issue each, measured stage times; restart and
  temporary unavailability preserve progress; repeated source discovery creates
  no extra STT or publication; unknown outcomes are never blindly retried.
  Ask Mark for new physical Watch notes only after the corrected system is ready.

### Exact blockers and owners

[Gate764](https://github.com/markmalina/gate/issues/764) /
[Ops375](https://github.com/markmalina/ops/issues/375): latest existing receipt
records private Ops action Access=Not accessible and unsaved sharing. The source
namespace/pin fix is merged; production route usability is not established.
Owner Mark / existing Ops375 Work must resolve its exact sharing gate.
Missing Public App private key is a separate Public blocker, not a Gate requirement.
No AQUB route or provider dashboard fallback is allowed.

AlphaCenter/delta is archived; required new review issue creation returned403.
An independent read-only audit exists at
[delta-watch-runtime-2026-10-15.md](reviews/delta-watch-runtime-2026-10-15.md),
but it is not a canonical Delta ACCEPT. Owner/review-route authority must restore
or designate the permitted canonical route; no repository is silently unarchived.

Continue this same Gate736 execution outcome/branch, without a duplicate task.
After dependencies are resolved, verify fresh lease/main/source and writer,
prepare one bounded candidate/PR, exact-head checks and independent review,
deploy only Gate-owned Ops, verify exact live revision and real acceptance.
Do not close736/731 from this checkpoint.

## Historical implementation runbook


Gate #731. Feature defaults off. Code/CI evidence is not live acceptance.

## Route

Samsung Voice Recorder → phone `Recordings/Voice Recorder` → Dropsync Upload only → Dropbox `/Alpha/Voice` → Gate original/transcript → full transcript issue and continuation comments in **private `markmalina/mark`**. Destination is fixed to the owner inbox; transcript text cannot change it. Calls/Telegram transport is unchanged. Mark root AGENTS.md requires Work to display new issues/comments when invoked; opening a screen alone runs no agent.

Gate retains original bytes, SHA-256, size, Dropbox file ID/revision/server time, Gate receipt time, transcript/model/time and GitHub receipt. Actual watch start time is unknown; filename/client timestamp is not a verified recording time. Read original with `gate_get_reply_context`, task_id `watch-voice-file:<event_id>`; receipt/text with `watch-voice:<event_id>`. Only configured workspace plus UNCLASSIFIED project may read it. No public media link.

## Exact first activation

1. Require green exact-head PostgreSQL 17 CI and the owner-only execution claim, exact installation, last-known-good revision and rollback conditions in AGENTS.md.
2. Gate `railway.toml` already sets `preDeployCommand = "alembic upgrade head"`. The registered current-main deploy uses this migration hook when effective config-as-code is active. Verify the deployed revision, readiness and migration result; a green build alone does not prove the schema. Do not use AQUB’s Node migration workflow for Gate.
3. Store a fine-grained token restricted to private `markmalina/mark`, Metadata read, Issues read/write in server `GATE_MARK_GITHUB_TOKEN` through approved secret transport. Never paste it in chat/issues/logs/code. Set `GATE_MARK_GITHUB_WRITER` to its verified owner login. Connector write permission does not prove server credential availability.
4. Reuse verified Dropbox credentials/account and pinned OpenAI configuration/shared budget. Set `GATE_WATCH_VOICE_WORKSPACE_KEY` to the verified Gate owner workspace, `GATE_WATCH_VOICE_DATA_GATE_APPROVED=true`.
5. Set both `GATE_WATCH_VOICE_FILE_ID` and `GATE_WATCH_VOICE_REVISION` to one exact owner note/revision; enable `GATE_WATCH_VOICE_ENABLED=true`. Default poll 30 seconds. Deploy only via `/ops-deploy production target=gate`, verify deployed revision. This command cannot set the feature variables or secret; its existing Gate pre-deploy hook runs Alembic.
6. Cap: one note, one issue plus required transcript comments, zero Telegram sends, one provider attempt per normalized part under shared monthly budget. Record original hash/ID, Gate event/time, full transcript, private issue ID/URL/GitHub created_at. Retrieve original through Gate and verify hash/size.
7. Replay unchanged revision three cycles: one original, one transcription attempt, one issue. Synthetic lost-ACK tests do not prove a production replay.
8. After acceptance and recorded rollout scope, clear both exact-source filters for future files. The cursor has passed excluded older files; no silent backfill. Resetting it requires bounded historical-import scope.

Stop by disabling the feature on privacy/scope mismatch, duplicate effect or unbounded provider call. Preserve receipts/originals. Downgrade drops evidence tables and is unsuitable after real intake; roll back application with feature off.

## Recovery

Read/preflight/Dropbox outage: retry later, cursor commits after durable storage. Immutable `(workspace, account, file_id, revision)` prevents source replay; changed revision is a new event.

Unknown provider effect: quarantine; no automatic retranscription. Crashed processing/sending claims become unknown after one hour. GitHub unknown/lost ACK/5xx: reconcile exact body, writer, receipt time and all pages. No receipt found never authorizes another POST. Successful reconciliation advances durable transcript part offset.

Definite GitHub refusal retains original/transcript. After fixing permissions/rate limit, use the approved runtime operation to execute `python -m scripts.watch_voice_resume <event_id>`. It resumes only a definitely rejected publication in owner scope, never unknown/delivered/non-transcribed effects, and does not retranscribe.

## Activation blocker

The registered fast-deploy lane deploys Gate current main and Gate’s existing pre-deploy hook runs Alembic; effective runtime execution still needs evidence. Gate has no registered `.agent-ops/project.yml`; the current AQUB manifest does not authorize writing Gate watch feature variables or secrets. Server private-Mark writer credential presence and identity remain unverified, rather than proven absent. Prepare a reviewed bounded presence/identity preflight and configuration route through the project-owned workflow. Keep all secret values in workflow memory and emit only safe Boolean evidence. Do not substitute provider dashboard/browser mutation.

## Device

Daily: launch watch Voice Recorder, tap Record, dictate, Stop. Owner confirmed phone/Dropbox arrival; exact launch action count/key shortcut remains unmeasured on Watch9. Dropsync depends on actual phone battery/network permissions; current uploads do not prove indefinite background work.

Watch Manager hotspot popup persists even with watch Wi-Fi off, but audio transfers after ignoring it. Owner needs hotspot on; this code does not fix the Samsung dialog and must not disable hotspot.

Owner excluded 1C from this stage on 06.10.2026. It is not an acceptance requirement or activation blocker here.

## Private Mark Actions alternative (Gate #731 successor)

The default-off `/internal/watch-voice/drain` endpoint accepts only an empty JSON
object, exact private Mark main GitHub OIDC, and a header-only repository job
token. It verifies the current main SHA, current run metadata, pinned workflow
bytes and fixed `.gate/watch-voice-binding.json` before any audio/STT effect.
No permanent Mark writer secret is required in this mode. Repository scope and
writer are independently verified; a connector credential is never substituted.

Reviewed source configuration may enable `GATE_WATCH_VOICE_MARK_OIDC_ENABLED`
and set `GATE_WATCH_VOICE_ACTIONS_WORKFLOW_SHA256` to the exact private caller
workflow SHA-256. Keep `GATE_WATCH_VOICE_ENABLED=false`. Publish current main only
through the registered Gate Ops deploy lane; preDeploy Alembic applies additive
`0113_watch_voice_drain_runs`. Roll back by disabling this endpoint and restoring
the recorded application revision; preserve originals, replay journal and cursor.
Do not downgrade tables after owner data ingestion.

Private binding schema `watch-voice-binding.v1` uses `schema`, `enabled`,
`workspace_key=mark-personal`, and `scope=initial-exact` plus exact `file_id` and
`revision`. All identifiers stay private. After initial product acceptance, a
private `scope=future-only` binding instead names `accepted_initial_event_id`.
A delivered scoped initial note and existing cursor are required; the first
future run persists a stable cutover timestamp and excludes preexisting archived
or queued rows. This transition cannot silently backfill earlier notes.

Each authenticated run is permanently consumed by run/jti hashes, considers at
most one note, and advances at most one transcript part. A long transcript
continues in later fresh runs with no retranscription. Unknown publication only
read-reconciles an exact bot-authored body; no-result is not permission to POST.
The private workflow's five-minute schedule is a mechanism, not a latency SLA.
The same pinned workflow also accepts the exact owner control comment
`/gate-watch-voice drain` on private Mark control issue #47. Its job guard
requires a newly created comment, immutable owner ID, and a real issue rather
than a pull request. Because GitHub expression string equality ignores case,
a local event-file check also requires the exact case-sensitive command before
the authentication step reads credentials or requests OIDC. Gate accepts
`issue_comment` only from that owner ID and
still verifies the private repository, main, current run, attempt and workflow
pin. Comment text, issue selectors and URLs never become endpoint parameters.
This provides a fresh bounded run when dispatch is unavailable; it adds no
automation, credentials, provider retry or publication allowance.
The scoped `gate_get_status.watch_voice_actions` reports live flags, pin presence
and journal counts; these flags never claim current credential or binding
readiness. Protected `watch-voice:` / `watch-voice-file:` reads supply actual
receipts/originals. Activation, receipt, original hash, full transcript and three
fresh replay cycles still require independent live verification.

## Owner-authorized same-note quota probe (Gate #736)

Mark explicitly requested a current quota check on the archived note on
2026-10-06 at 19:14 MSK. This overrides the earlier factual quota-restoration
prerequisite. Available Gate budget does not prove provider quota. An actual
bounded request now establishes the outcome, without payment, billing UI
mutation, key extraction/replacement or new provider/source.

The fixed private binding must contain `scope=initial-quota-probe`, the same
exact `file_id`/`revision`, existing `event_id`, verified `expected_audio_sha256`,
original `expected_error` (legacy rate_limited or typed quota_unavailable),
`provider_cause=insufficient_quota`, `owner_quota_probe=true`, and
`authorized_at=YYYY-MM-DDTHH:MM:SSZ` (the actual owner instruction time, following
the original provider attempt). Absent/false approval and invalid/future/old
approval time fail closed. Legacy `quota_restored` fields are rejected rather
than fabricated. Model, endpoint, repository and workflow are not selectors.

The pinned caller path and empty endpoint body are unchanged. Private repository,
current-main/run/workflow checks precede the protected archive read and require
exact source/hash, rejected mini-model attempt, no transcript/publication and
matched released original Gate speech reservation. The attempt stays in the
original current budget period under the existing USD3 entitlement. No Dropbox
list/redownload or second event is needed.

Additive migration `0114_watch_voice_recovery_claims` preserves original
error/model/time/reservation and records the approving private-main SHA,
`authorization_kind=owner_quota_probe`, and `authorized_at`. One permanent claim
is never rearmed. Isolated `gpt-4o-transcribe` gets exactly one normalized part
and one 60-second request with `watch-voice:<event_id>:recovery1` reservation and
actual usage settlement. Budget/media rejection, provider refusal and
unknown/crashed work consume the cap. Zero automatic retries/Telegram sends.

Protected receipt `provider_readiness` reports observations; runner receipt keys
remain unchanged to match the pinned private workflow:
`available_for_attempt`, `quota_unavailable`, `rate_limited`, `unknown`, or
`not_established`. They never claim that billing/account state was changed.
Success uses existing private Mark outbox one part per fresh run; unknown
publication only read-reconciles. Owner-scoped status exposes recovery counts.
The protected watch-voice receipt includes original released-reservation proof
and new reservation state/actual cost. Exact-head PostgreSQL CI, independent
review and registered deploy precede activation. On mismatch disable endpoint
or binding and preserve both journals; never downgrade real-use evidence.
Delivered initial receipt and cursor gate later owner-approved `future-only`;
normal future access still needs a new recording receipt/replay verification.

## Separate paid continuation (Gate #736) — 2026-10-07

Mark's paid/completion instruction at 2026-10-07T14:23:37Z authorizes ONE new
bounded attempt on the same protected original after review, exact-head CI and
registered deployment. Personal API funding does not prove the configured
server credential uses that organization. Observe actual readiness through this
attempt; never fabricate quota_restored or reset the spent recovery1 claim.

Private binding uses `scope=initial-post-payment`, the existing exact
`file_id`/`revision`/`event_id`/`expected_audio_sha256`,
`expected_error=openai_transcription_quota_unavailable`,
`owner_post_payment=true`, `provider_cause=credit_balance_exhausted`, and the
actual `authorized_at` above. No old owner_quota_probe/quota_restored fields or
new endpoint/model/repository selectors are accepted. Workflow bytes and pin,
OIDC/main/run/attempt/private target preflight and empty body remain unchanged.

Additive `0115_watch_voice_post_payment` records a separate permanent claim.
It requires recovery1 rejected by quota, its exact released reservation, the
released original reservation and current-month shared USD3 entitlement before
I/O. Fixed `:postpayment1` uses one normalized `gpt-4o-transcribe` part, 25 MB,
60 seconds, 100,000 characters and zero retries. Failure/unknown/crash consumes
this cap forever. Old source/audit/reservations stay intact; old binding is a
no-op after this successor claim. Protected receipt contains historical
`recovery` and separate `post_payment`; they cannot rewrite one another's
readiness. Private Mark outbox/reconciliation is unchanged. Preserve all
journals on rollback, without schema downgrade or silent rearming.

The code baseline is d5a9fbb8a0b5444c0734cba040e0a67bd1a7424e, but deploying
that bare revision after applying 0115 would leave its Alembic code unable to
locate the database head. Stop effects by disabling the private binding, then
use a reviewed forward application revert that retains 0115, its model and all
journals, published through the registered current-main Ops lane. Never
downgrade or erase Production schema/history.

After initial transcript/issue/display and replay acceptance, existing
future-only cutover rejects `server_modified <= future_cutover_at` before any
download and excludes stored backlog whose Dropbox timestamp is not later.
Both comparisons retain full UTC precision. A skipped delta still advances the
existing cursor; retries/restarts never move the durable cutover. A new actual
recording after that watermark remains a separate required product receipt.

# Owner watch voice → private Mark inbox

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

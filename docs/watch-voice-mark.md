# Owner watch voice → private Mark inbox

Gate #731. Feature defaults off. Code/CI evidence is not live acceptance.

## Route

Samsung Voice Recorder → phone `Recordings/Voice Recorder` → Dropsync Upload only → Dropbox `/Alpha/Voice` → Gate original/transcript → full transcript issue and continuation comments in **private `markmalina/mark`**. Destination is fixed to the owner inbox; transcript text cannot change it. Calls/Telegram transport is unchanged. Mark root AGENTS.md requires Work to display new issues/comments when invoked; opening a screen alone runs no agent.

Gate retains original bytes, SHA-256, size, Dropbox file ID/revision/server time, Gate receipt time, transcript/model/time and GitHub receipt. Actual watch start time is unknown; filename/client timestamp is not a verified recording time. Read original with `gate_get_reply_context`, task_id `watch-voice-file:<event_id>`; receipt/text with `watch-voice:<event_id>`. Only configured workspace plus UNCLASSIFIED project may read it. No public media link.

## Exact first activation

1. Require green exact-head PostgreSQL 17 CI and the owner-only execution claim, exact installation, last-known-good revision and rollback conditions in AGENTS.md.
2. Apply Alembic `0112_watch_voice_inbox` through a registered **Gate** database operation after verifying the previous schema/head. AQUB's Node migration workflow cannot migrate Gate.
3. Store a fine-grained token restricted to private `markmalina/mark`, Metadata read, Issues read/write in server `GATE_MARK_GITHUB_TOKEN` through approved secret transport. Never paste it in chat/issues/logs/code. Set `GATE_MARK_GITHUB_WRITER` to its verified owner login. Connector write permission does not prove server credential availability.
4. Reuse verified Dropbox credentials/account and pinned OpenAI configuration/shared budget. Set `GATE_WATCH_VOICE_WORKSPACE_KEY` to the verified Gate owner workspace, `GATE_WATCH_VOICE_DATA_GATE_APPROVED=true`.
5. Set both `GATE_WATCH_VOICE_FILE_ID` and `GATE_WATCH_VOICE_REVISION` to one exact owner note/revision; enable `GATE_WATCH_VOICE_ENABLED=true`. Default poll 30 seconds. Deploy only via `/ops-deploy production target=gate`, verify deployed revision. This command alone cannot set variables or migrate.
6. Cap: one note, one issue plus required transcript comments, zero Telegram sends, one provider attempt per normalized part under shared monthly budget. Record original hash/ID, Gate event/time, full transcript, private issue ID/URL/GitHub created_at. Retrieve original through Gate and verify hash/size.
7. Replay unchanged revision three cycles: one original, one transcription attempt, one issue. Synthetic lost-ACK tests do not prove a production replay.
8. After acceptance and recorded rollout scope, clear both exact-source filters for future files. The cursor has passed excluded older files; no silent backfill. Resetting it requires bounded historical-import scope.

Stop by disabling the feature on privacy/scope mismatch, duplicate effect or unbounded provider call. Preserve receipts/originals. Downgrade drops evidence tables and is unsuitable after real intake; roll back application with feature off.

## Recovery

Read/preflight/Dropbox outage: retry later, cursor commits after durable storage. Immutable `(workspace, account, file_id, revision)` prevents source replay; changed revision is a new event.

Unknown provider effect: quarantine; no automatic retranscription. Crashed processing/sending claims become unknown after one hour. GitHub unknown/lost ACK/5xx: reconcile exact body, writer, receipt time and all pages. No receipt found never authorizes another POST. Successful reconciliation advances durable transcript part offset.

Definite GitHub refusal retains original/transcript. After fixing permissions/rate limit, use the approved runtime operation to execute `python -m scripts.watch_voice_resume <event_id>`. It resumes only a definitely rejected publication in owner scope, never unknown/delivered/non-transcribed effects, and does not retranscribe.

## Activation blocker

Inspected fast-deploy lane supports deploy only. Gate has no registered `.agent-ops/project.yml`; AQUB manifest excludes `configure-variables`/`run-migrations`; its migration workflow targets AQUB. Server GitHub credential presence remains unverified. A registered scoped Gate schema/configuration/secret operation is required. Do not substitute provider dashboard/browser mutation.

## Device and 1C

Daily: launch watch Voice Recorder, tap Record, dictate, Stop. Owner confirmed phone/Dropbox arrival; exact launch action count/key shortcut remains unmeasured on Watch9. Dropsync depends on actual phone battery/network permissions; current uploads do not prove indefinite background work.

Watch Manager hotspot popup persists even with watch Wi-Fi off, but audio transfers after ignoring it. Owner needs hotspot on; this code does not fix the Samsung dialog and must not disable hotspot.

1C interface is prepared in `resources/watches/voice-gate-2026-10-05`; no actual 1C base, HTTP endpoint or service credentials available. Live transfer is not verified.

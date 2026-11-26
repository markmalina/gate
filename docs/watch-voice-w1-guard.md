# W1 archived-note fence (source candidate, not activated)

Task: https://github.com/markmalina/gate/issues/795

`GATE_WATCH_VOICE_W1_ENABLED` defaults to false. OFF keeps the existing drain,
including its discovery behavior. Release and S1 workflows remain unchanged/OFF.

When enabled, `GATE_WATCH_VOICE_W1_ADMISSION` is server-side protected JSON,
handled as a hidden SecretStr. It is not a request body, OIDC claim, header or
private Mark binding change. Exact keys (all strings; no extra/duplicate keys):

- `schema`: `watch-voice-w1-admission.v1`;
- `event_id`: canonical UUID of the existing archived WatchVoiceNote;
- `workspace_key`, `account_id`, `file_id`, `revision`, `sha256`;
- `gate_sha`: exact Gate `RAILWAY_GIT_COMMIT_SHA`, **not** private Mark workflow SHA;
- `not_before`, `expires_at`: timezone-aware ISO timestamps, inclusive/exclusive.

No real selector or admission value is included here. Configuration/pins, target
eligibility, historical budget, fleet and owner live grant require a separate
protected admission. This source change does not authorize those operations.

Existing authenticated private-Mark/current-main/run/workflow preflight and
durable run/JTI claim remain. W1 accepts only the current `future-only` binding;
initial/recovery bindings cannot bypass it. The existing cursor coordinator is
locked but never created/updated. A missing cutover is not initialized. Under
that lock the guard loads only event_id, checks its full source binding,
archived bytes/size/hash/duration and existing future-only timestamps. It never
enters the legacy selection, stale sweep, OAuth/account probe, delta or download.

Only a clean received row enters the existing committed processing/budget path;
processing/unknown/rejected, a prior attempted received row, and sending/blocked
publication stop. Transcribed pending/unknown uses the unchanged one-part outbox
and read-only lost-ACK reconciliation. Delivered is a durable no-effect on every
fresh run/JTI or restart, without an in-memory completion flag or new migration.
Missing/malformed/mismatched/expired admission cannot fall through to discovery.

The window is checked on entry and under the coordinator before processing, and
again after admitted STT before publication. In-flight admitted STT is not
cancelled at expiry: its durable result/UNKNOWN must be preserved. No new
publication starts after the post-STT expiry check fails. Operator changes to
disable W1 intentionally restore legacy semantics; expiry never disables W1.

Tests invoke the real drain, SQLAlchemy CAS/run claims, StoredVoice and outbox
against local synthetic SQLite with MockTransport and a fake STT service.
Run from the repo with dependencies constrained by constraints/ci.txt:

```sh
DATABASE_URL= python -m pytest tests/test_watch_voice_w1.py tests/test_watch_voice_actions.py tests/test_watch_voice.py
```

Fake HTTP never reaches Dropbox/GitHub/provider. SQLite and a synthetic busy-lock
case do not prove two-session PostgreSQL SKIP LOCKED races. Provider budget
service internals and live STT are unchanged and not proven by the fake service.

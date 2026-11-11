# Watch durable queue before new Dropbox delta — source checkpoint

4 November 2026, 13:29 MSK. Execution `gate-watch-queued-first-20261104T1018Z`.

Assignment: https://github.com/markmalina/gate/issues/736#issuecomment-6628238971
Rules: https://github.com/markmalina/ai/blob/main/WORK-RULES.md
Parent: `86dbc0f429dce089bf733fc422f20f556535895b`.
Quiet branch: `agent/watch-queued-first-20261104`.

## Result and boundary

Actions drain now tries one existing eligible durable note after the unchanged authenticated binding/run-claim/cutover/coordinator guards, before Dropbox refresh, account verification or delta scan. It uses the previous transcription and one-part publication path. When no eligible queued note exists, the old scan path remains, including its fail-closed source-size validation. The same helper processes a newly archived note after scanning.

There is no per-entry skip, quarantine of a new source, schema/policy/model/credential/workflow change, automatic provider retry, or new executor. Private source originals/hashes remain untouched. Unknown provider and started transcription rows remain ineligible; publication unknown retains read reconciliation. No API response fields change.

This source change does not prove today's runtime exception, installation, a new transcript, S1, or product acceptance. The observed oversized delta is a source-level starvation scenario, not a claimed server error observation.

## Verification actually executed

- Python 3.12 `py_compile` for changed Actions and pytest module: PASS.
- Supplemental stdlib harness: 17/17 PASS. It compiles the literal parent/candidate Actions drain and unchanged worker source functions from their AST. Its ORM, authorization responses, storage and provider/publication effects are synthetic fakes. It does **not** validate SQLAlchemy, PostgreSQL locking/transactions, real OIDC/network/provider/publication.
- Parent drain with already queued received note and oversized matching delta raises `watch_voice_source_size_limit` before any synthetic transcription/publication; candidate processes that same queued note without Dropbox scan.
- Candidate cases cover transcribed pending/no STT, empty queue/old scan, source-size failure preserved, guard ordering, coordinator unavailable, eligible future queue, excluded history and cutover initialization, provider unknown/started/stale claims, publication reconciliation, same-run replay and delivered restart.
- Two normal pytest regression cases (received/transcribed durable queue with invalid new delta) were added to the existing real session/worker fixture. They also assert immutable source preservation and same-run idempotency. **Not executed here**: pytest/httpx/SQLAlchemy/aiosqlite are absent. Dependency installation was automatically rejected; no retry or workaround was used. Existing integration tests were not re-run. Independent validation must retain this limitation or run them in a permitted prepared environment.

Reproduce the supplemental harness with exact parent source files and candidate source:

```sh
python docs/evidence/watch-queued-first-2026-11-04-harness.py.txt \\
  --before /path/to/parent/app/infrastructure/watch_voice_actions.py \\
  --after app/infrastructure/watch_voice_actions.py \\
  --worker /path/to/parent/app/worker/watch_voice.py
```

Intended real regression command, not executed:

```sh
pytest tests/test_watch_voice_actions.py tests/test_watch_voice.py
```

## External effects and handoff

Only the assigned quiet source branch is written. No PR, CI, deployment, Railway, STT, Mark publication or support contact was started. No credential was read into the agent environment. Hosted cost added by this source step: zero observed new runs; carried budget is not reset or recalculated here.

Independent reviewer is not the author. Controller receives exact frozen head/tree and a RELEASED claim separately in #736. No live effect is authorized by this report.


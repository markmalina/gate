# G1a baseline bootstrap contract

Repository-only baseline package for #722, based on `8e0b1672b6dc345e121cd55f5ba28540b7e18673`.
No migrations are added or changed: sole head remains `0111_reply_documents`.
`alembic upgrade head` is a no-op only after Ops independently proves the live
ledger is exactly that revision. This package is not candidate #720 or a live
transport/deployment acceptance.

## Process observation

Each actual FastAPI lifespan installs its closed observation fence **before**
starting any worker. One engine connection remains checked out per process.
It uses AUTOCOMMIT, read-only statements, a 3-second statement timeout and a
5-second heartbeat. No long transaction or application lock is retained. The
connection is invalidated on exit to avoid leaking session settings into the
application pool. Budget one reserved connection per OS process, including all
gunicorn/uvicorn workers and rollout overlap, in addition to ordinary workload.

`application_name = gate-g1a:<random boot UUID, 32 lowercase hex>` is exactly
41 ASCII bytes. Last query has this exact grammar, order and terminal sentinel:

```text
SELECT 1 /*gate_g1a_v1;boot=<32hex>;source=<40hex>;replica=<64hex>;deployment=<64hex>;workspace=<64hex>;config=<64hex>;identity=<64hex>;schema=0111_reply_documents;send=<0|1>;worker=<0|1>;inflight=<0..999999>;end=gate_g1a_v1*/
```

The full SQL is below 1024 ASCII bytes, not just the comment. The runtime
requires `track_activities=on` and `track_activity_query_size >= 1024`.
Missing privileges, truncated markers, wrong schema or changing physical
`backend_pid/backend_start` closes observation admission. No automatic GRANT,
database setting change or fallback identity is attempted. A new connection
after disconnect retains the OS process boot identity; Ops must obtain a new
fresh observation and may not carry forward a previous backend claim.

All hashes use SHA256 of UTF-8 JSON with sorted keys, ASCII escaping and compact
separators `(',', ':')`. Replica/deployment hashes use their actual loaded
`RAILWAY_REPLICA_ID` / `RAILWAY_DEPLOYMENT_ID`; workspace hashes the loaded scope.
The exact config allowlist is `_CONFIG_FIELDS` in `baseline_observation.py`.
`gate_reply_sequence_poll_seconds` is a normalized decimal **string** (e.g.
`"5"`, not JSON `5.0`); `config_evidence` is the canonical implementation.
Cross-language fixture: `contracts/baseline-observation/fixture.json`, config
SHA256 `7279cd89b398ce401c070957cdfca7b708c62149285b40ca3f56db825dd21384`.
Credentials, DSNs, private addresses, message bodies and principals are never
included or hashed. Source is the full loaded commit SHA; Ops independently
matches it and deployment/config/replicas to provider evidence, not merely the
self-reported environment. Cluster identity is read from `pg_control_system()`;
DB identity hashes `[system_identifier, current_database(), database_oid,
current_schema()]` as strings. Different DB roles and transport addresses are
allowed only when this physical cluster/database/schema identity agrees.

## Authenticated reader boundary

`read_frozen_fleet` is a fixed SQL application primitive called by the reviewed
Ops TCP/TLS executor. It adds no endpoint, service identity, secret or CLI
credential access. Its caller must use a fresh AUTOCOMMIT connection and prove:

- pinned source, deployment, workspace and relevant config;
- direct physical session or verified session pool mode;
- complete statistics visibility for every expected process (same verified
  runtime role or existing sufficient monitoring rights), no inferred grants;
- complete provider inventory and no active old/unmarked deployment/process,
  no pending changes or alternate auto-deploy path outside the resource lock;
- exactly one expectation per OS process, including multiple workers per
  replica. A load-balancer response or absence in pg_stat_activity is not proof
  an old process has stopped.

The bounded read allows at most 128 expected processes and reads at most 129
marker rows. The server limits each returned query to 1024 characters before
transfer; oversized/truncated values fail the strict ASCII grammar and terminal
sentinel. Validation requires exact marker grammar, unique boot and backend IDs,
matching application name, idle state, no transaction, DB-clock query age
between 0 and 15 seconds, and every pinned expectation exactly once. The read
uses `clock_timestamp()`, not a transaction-frozen timestamp. Missing/hidden/
extra/duplicate/stale observations are UNKNOWN and prohibit the next effect.
No raw pg_stat_activity query, DB name, role, address or provider detail is
published; only validated safe hashes, fixed predicates and aggregate counts.

## Freeze and drain

Ops applies the existing `GATE_SEND_ENABLED=false` and
`GATE_REPLY_SEQUENCE_WORKER_ENABLED=false` through its reviewed exact config +
restart operation, with journal/lock/provider fencing. This module does not
perform the configuration mutation. Every new process starts with admission
closed until its own actual DB observation succeeds. Observation failure or
age above 15 seconds closes that process's TelegramClient admission.
The standalone `gate-telegram-worker` attaches observation to its own actual
engine before running work, and tears it down afterward. ASGI iterations reuse
the one process observer only after comparing their loaded DB URL/source/scope
and relevant config; mismatch or unavailable observation blocks execution.
In Production, a standalone TelegramClient sender without an installed
observer also fails closed. In particular, a separate BECOME maintenance sender
must be inventoried and cannot be assumed to keep sending through this baseline.

Queue and worker discovery reject while the common fence is closed. Every
TelegramClient JSON write and multipart write, including delegated clients,
enters the same synchronous admission/counter section before HTTP. Only three
explicit read methods bypass write admission. Cancellation and errors release
the local counter; that alone never proves the remote outcome.

`read_frozen_fleet` requires send=false, worker=false and actual HTTP inflight=0
on every observed process; separately counts global durable reserved/sending/
unknown attempts. Reserved/sending always block. Unknown without `finished_at`
is an incomplete record and blocks. Legacy sender finalization and stale-send
recovery record terminal unknown with `finished_at`: these historical terminal
records are counted and preserved, but do not block migration/drain forever.
The receipt retains total `unknown` and distinguishes `incomplete_unknown` from
`terminal_unknown`; none are reset, deleted, retried or declared delivered.
An old process restart with counter zero cannot resolve its durable attempts.
`finished_at` alone is not a fleet stop: the separate complete frozen-process,
HTTP in-flight and provider termination evidence is still required. This uses
explicit durable lifecycle state, never elapsed age/TTL. Use the separately
reviewed recovery/classification operation for incomplete attempts and re-observe.
No history/import/recovery writes occur in this package. The receipt expressly
does not certify provider inventory or backup.

This fence covers legacy sequence entrypoints and all TelegramClient writes.
Separate AQUB webhook `method=sendMessage` responses in `app/api/aqub_telegram.py`
and the raw Alpha owner-test POST in `app/infrastructure/alpha_owner_test_runtime.py`
are other product paths. Their absence is not claimed; Ops must inventory/fence
unrelated senders when required by the exact release scope. This patch does not
modify their product behavior or grant their activation.

Do not roll back to the old binary with sender flags enabled during cutover:
it lacks these process/transport guards. Old flags plus a successful deploy do
not prove freeze. Require exact provider process termination and preserve the
closed configuration until separately accepted candidate migration/import.
Baseline publication needs its own reviewed exact SHA, authorization, rollback
and zero-message plan; no live actions are authorized by this document.

## Verification

`pytest tests/test_baseline_observation.py tests/test_baseline_observation_postgres.py`
checks strict bounded markers, negative fleet evidence, shared JSON/multipart
freeze, queue/worker admission, real in-flight cancellation accounting and an
actual PostgreSQL physical-session observation using only schema-0111 tables,
including preserved terminal unknown versus blocking incomplete/active attempts.
PostgreSQL absence is a skip, never a pass. Existing CI provides PG17. The full
migration graph must continue to have the single head `0111_reply_documents`.

PostgreSQL semantics: https://www.postgresql.org/docs/17/monitoring-stats.html

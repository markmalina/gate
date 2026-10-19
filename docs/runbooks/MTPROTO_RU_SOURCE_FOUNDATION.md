# RU MTProto: source foundation and activation handoff

Canonical execution: [Gate #696](https://github.com/AlphaCenter/gate/issues/696).
Architecture proposal: [PR #759](https://github.com/AlphaCenter/gate/pull/759).
Source baseline: `461088d68d047e4cd08c7a4f581300d591af241e`.

## What this package actually provides

This is the bounded V0/V1 **source foundation**, not completion of either sprint.
It adds executable contracts and an unbound RU worker core. Importing it does not
open a connection, modify the database or change the existing Business route.
There is no web-startup hook, new MCP tool, worker entry point, production
migration, protected login screen or Telegram mutation dispatcher in this package.

| Component | Implemented boundary | Evidence |
| --- | --- | --- |
| `command_scope.py` | Explicit RU account, OAuth owner/workspace, capability and typed-peer delegation; no workspace-account fallback | Provider-free scope tests |
| `session_persistence.py` | Fernet authenticated envelope; workspace/account/binding/owner/generation pin; serialized async CAS; poisoned session after uncertain persistence | Actual Telethon 1.44 callback, DC-change, close, cancellation and transient-clone tests |
| `command_store.py` | Encrypted payload, request-key replay/conflict, standalone intent, durable attempt before I/O, typed-peer UNKNOWN quarantine, cancellation, receipts | SQLite state-machine tests; four real PostgreSQL contention tests |
| `cloud_worker.py` | Lease before client creation, bounded startup, no update subscription, actual owner check, heartbeat, bounded disconnect, release only after confirmed disconnect | Fake-client lifecycle/DB-failure/second-owner tests |
| `capability_catalog.py` | 104 separate operations across C01-C23; scopes, candidate methods, peer types, stage, activation dependency | Actual generated constructors, Telethon 1.44.0 / layer 227 |
| Inventory scripts | 66 distinct TL constructor contracts; Telegram writer candidate call sites | Repeatable provider-free JSON; CI artifacts |

Every capability remains `planned`. A TL constructor existing does not prove
Gate implementation, Telegram permissions, deployed behavior or Work availability.
The method inventory records each of those claims separately. Forum constructors
in this pin are under `messages`, including `GetForumTopicsRequest`,
`CreateForumTopicRequest` and `EditForumTopicRequest`.

The writer inventory found 42 candidate call sites in 31 files at this baseline.
It includes abstract adapters and frozen historical paths; these are neither
42 independent effects nor proof of exhaustive coverage. Dynamic HTTP/raw RPC
and the central Bot API wrappers still require explicit review. In particular,
`universal_mayak_delivery.py` is a Business writer with its own journal; existing
Business outbound inbox logic can satisfy all open reply tasks, while the modern
reply sender records exactly its covered task IDs. V2 must reconcile these paths.

## Ownership and recovery

An expired lease never permits a replacement connection. Ownership remains
latched until the trusted worker confirms disconnect and releases its token.
The next owner receives a new UUID and monotonically increasing generation.
Database row locks and conditional updates protect ownership/revision changes.

If the process disappears without confirming disconnect, automatic acquisition
stays blocked. The separate Ops recovery operation must prove termination of the
old deployment/TCP owner before releasing ownership. Do not delete a lease row,
clear the owner from a dashboard, or use a time-to-live as that proof.

Session persistence failure is fatal even when CAS may have committed. A session
cannot retry with a stale revision or silently retain its previous credential.
The adapter is pinned to Telethon 1.44.0 because asynchronous Session hooks remain
experimental. StringSession contains authorization/DC material, not a durable
entity or updates mirror. Transient clones never receive the persistence hook.

## Queue boundary

`prepare` creates an intent independently of an incoming message or reply task.
The idempotency key is `(binding, owner, client namespace, request key)`. Reusing
it with another payload/peer/delegation fails. Two separately keyed identical
messages are independent intents, rather than content-deduplicated accidents.

`claim` commits an UNKNOWN attempt and stable per-operation `random_id` before
the caller can do provider I/O. It is **reservation, not dispatch authorization**.
Before any dispatcher is attached, V2 must revalidate the durable delegation,
OAuth client binding, revocation, expiry and exact peer/payload; then obtain the
shared Business/MTProto admission. This package's delegation DTO is not a signed
owner grant or durable grant registry. `status`/`cancel` are internal scoped-store
ports, not public authorization boundaries.

UNKNOWN blocks that exact typed peer across restart; an unrelated peer remains
eligible. Cancellation after dispatch is UNKNOWN and cannot retract an RPC.
A late confirmed acknowledgement may resolve it. No automatic retry or history
guess resolves UNKNOWN. A successful result requires a stable receipt reference.
Readiness of the connection alone cannot enable this queue's mutations.

## Staged schema

Three additive tables use isolated `PersonalRuntimeBase` metadata:
`personal_command_bindings`, `personal_command_operations`,
`personal_command_attempts`. They are intentionally absent from the active
Alembic metadata/head. Tests create them only in disposable databases/schemas.

Before activation, integrate the reviewed models/migration with the then-current
Gate Alembic head, reconcile concurrent migration PRs, test upgrade/downgrade and
backup/restore on PostgreSQL 17, and admit the exact migration through Ops.
Never run `create_all` against production or deploy this core as a web replica.
Binding setup must pin the actual Russian provider user ID through the protected
custody/login path; a Gate account label is not proof of Telegram identity.

## Reproduce provider-free evidence

Use Python 3.12 and the repository's `.[dev,personal-telegram]` dependency set.

```bash
ruff check .
mypy app
pytest tests/test_personal_command_runtime.py tests/test_personal_telegram_capability_catalog.py tests/test_personal_telegram_writer_inventory.py
python scripts/personal_telegram_contract_inventory.py --output personal-telegram-contract-inventory.json
python scripts/personal_telegram_writer_inventory.py > personal-telegram-writer-candidates.json
```

With a disposable PostgreSQL `DATABASE_URL`, run:

```bash
pytest tests/test_personal_command_runtime_postgres.py
```

The four contention scenarios are eight concurrent owner acquisitions, eight
concurrent preparations of one intent, competing session CAS revisions and
concurrent claims for one typed peer. SQLite passing is not PostgreSQL proof.
CI creates PostgreSQL 17 and runs these with the full repository suite, then
migration/restore/container checks. CI contract artifacts contain metadata only.

## Local validation before publication

- Ruff for the repository and strict Mypy for all 314 source modules passed.
- New provider-free tests: 25 passed; four PostgreSQL tests awaiting CI.
- Broader offline run before the final catalogue additions: 2,694 passed,
  181 skipped, 12 subtests passed. Four mandatory-PostgreSQL modules were excluded.
  This is not a green full-suite claim.
- Initial full-suite attempt was blocked by missing PostgreSQL and local proxy/
  dependency configuration; it is not counted as product acceptance.
- Local PostgreSQL launch was unavailable because this environment disallows
  changing the system user. No privilege escalation or production DB substitute.
- Telegram/provider calls, live messages/groups, production/config/secret/DB
  mutations and new paid resources: zero.

## Next concrete package

Close the independent Gate custody/worker/migration/drain/rollback Ops contract
described in [the activation handoff](../specs/MTPROTO_RU_RUNTIME_ACTIVATION_CONTRACT.md).
Implement its protected existing-session inspection/import and actual Work
auth/status/job/file spike. Complete V2's shared writer admission and causal echo
mapping in source before any mutation dispatcher is enabled. Only then select the
bounded RU session check; ask for login solely if the protected inspection proves
it necessary. BY is outside every MTProto/provider/live-check operation.

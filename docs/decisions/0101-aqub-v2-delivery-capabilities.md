# ADR 0101 — AQUB v2 runtime capability boundary

Status: proposed, repository-only, default off.

## Decision

Gate exposes one hidden internal acceptance route:

- `POST /internal/aqub/v2/delivery`;
- exact body is one strict `aqub.delivery-command.v2` JSON document;
- `X-AQUB-Signature` is `sha256=<lowercase hex HMAC-SHA256>` over the exact
  raw request bytes;
- the route is absent unless `GATE_AQUB_V2_DELIVERY_ENDPOINT_ENABLED=true`;
- the HMAC secret must contain at least 32 bytes;
- duplicate JSON keys, non-finite numbers, bodies over 512 KiB, extra
  properties, raw Telegram identifiers and non-text payloads fail closed.

`payload.delivery_type` is mandatory and closed to:

- `food.clarification`;
- `food.fact_reply`;
- `food.correction_reply`;
- `food.day_summary`;
- `food.omark_check`.

The same five-value closure is enforced independently by JSON Schema, the
Python parser and the database insert guard. It is not represented by a
generated PostgreSQL enum column: casts to user-defined enum types are not
immutable enough for a generated expression. The immutable canonical command
bytes plus the exact insert-guard list are the authoritative database
evidence.

Runtime SQL is limited to five `SECURITY DEFINER` operations:

1. `accept_aqub_v2_delivery`;
2. `begin_aqub_v2_dispatch`;
3. `settle_aqub_v2_delivery`;
4. `mark_aqub_v2_delivery_unknown`;
5. `reconcile_aqub_v2_delivery_unknown`.

The functions use a fixed `pg_catalog,pg_temp` search path and fully qualified
tables. The runtime role retains table `SELECT` but receives no direct DML.
Route authority is checked with `FOR SHARE` on new acceptance and rechecked
with the same lock immediately before the irreversible send marker. That lock
conflicts with non-key updates to route state, cube, generation and authority
epoch; `FOR KEY SHARE` is intentionally insufficient. A row already in
`sending`, `unknown`, `sent`, `failed` or `rejected` cannot be dispatched
again. `unknown` remains reconciliation-only.

The response is always `{ack, receipt}` for a schema-valid command. Route and
idempotency refusals are schema-valid `rejected` ACKs. Neither ACK nor receipt
contains `conversation_ref`; the conversation remains a database-only routing
predicate.

## Durable recovery query

The signed POST is also the only recovery query. AQUB re-signs and replays its
stored canonical command bytes with the same command id and idempotency key.
The existing-row lookup happens before current-route evaluation and returns a
`duplicate` ACK plus the ledger's current durable receipt:

- `receipt: null` means Gate has no terminal or unknown evidence yet; AQUB
  remains `acknowledged` and retries this query with bounded backoff;
- a `sent` or `failed` receipt is terminal evidence;
- an `unknown/reconcile_only` receipt moves AQUB into its matching unknown
  recovery path and must never cause a new command or provider resend.

This closes a crash after AQUB persists the ACK but before it persists the
receipt without adding a second endpoint or expanding secret scope. Gate does
not infer provider state. AQUB still must enumerate aged `acknowledged` rows,
replay the stored command, and durably apply any returned receipt.

Receipt correlation is enforced at the SQL boundary, not only by Python. The
0100 `BEFORE UPDATE` trigger invokes
`assert_aqub_v2_delivery_receipt(command_json, receipt_json, state)` for every
terminal or unknown transition. Both `settle_aqub_v2_delivery` and
`reconcile_aqub_v2_delivery_unknown` therefore reject a schema-valid receipt
whose command id, idempotency key, installation, cube or route generation does
not match the locked ledger row.

## Runtime reachability

`AQUBV2DeliveryExecutor` remains synthetic-rehearsal code. The application
router imports only `AQUBV2DeliverySQLStore`; it has no provider adapter and no
dispatch worker. Therefore this slice can durably accept a command only after
an explicit flag/secret/database gate, but it cannot send Telegram messages.

## Media decision

This slice is limited to text-message delivery. Caption-only ingestion is not
approved here because its current upstream receipt can retain photo provider
identifiers. Existing BECOME/Beta download adapters are bot-token- and
domain-bound and cannot be reused by reference without expanding credential
and data ownership.

Photo and caption delivery are therefore **NO-GO** in this candidate. The next
Gate schema number, 0102, is reserved for the prerequisite Consent/Data
Lifecycle Work H. Any later accepted media-reference Work G starts at 0103 or
the next unused migration after re-reading the merged chain; it must not modify
or reuse 0101.

Raw Telegram `file_id`, bot download URLs and base64 bytes must never cross the
AQUB contract. Media requires a separate Work defining an opaque `media_ref`,
route/actor authorization, digest and size, bounded retrieval,
retention/deletion, corrections and audit receipts before code is added.

## Consequences

- Migration 0101 must follow 0100; it never edits the reviewed 0100 file.
- AQUB must add the same mandatory delivery-type field and acknowledged-row
  recovery before pairing.
- Ops credential split remains a hard blocker for runtime activation.
- No route, credential, webhook, provider, deployment or migration apply is
  authorized by this ADR.

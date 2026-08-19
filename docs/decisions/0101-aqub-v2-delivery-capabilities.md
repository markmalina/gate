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

Runtime SQL is limited to five `SECURITY DEFINER` operations:

1. `accept_aqub_v2_delivery`;
2. `begin_aqub_v2_dispatch`;
3. `settle_aqub_v2_delivery`;
4. `mark_aqub_v2_delivery_unknown`;
5. `reconcile_aqub_v2_delivery_unknown`.

The functions use a fixed `pg_catalog,pg_temp` search path and fully qualified
tables. The runtime role retains table `SELECT` but receives no direct DML.
Route authority is checked under lock on new acceptance and rechecked under
lock immediately before the irreversible send marker. A row already in
`sending`, `unknown`, `sent`, `failed` or `rejected` cannot be dispatched
again. `unknown` remains reconciliation-only.

The response is always `{ack, receipt}` for a schema-valid command. Route and
idempotency refusals are schema-valid `rejected` ACKs. Neither ACK nor receipt
contains `conversation_ref`; the conversation remains a database-only routing
predicate.

## Runtime reachability

`AQUBV2DeliveryExecutor` remains synthetic-rehearsal code. The application
router imports only `AQUBV2DeliverySQLStore`; it has no provider adapter and no
dispatch worker. Therefore this slice can durably accept a command only after
an explicit flag/secret/database gate, but it cannot send Telegram messages.

## Media decision

This slice is text-only. Existing caption extraction can safely reuse a
Telegram photo caption as text, but Gate has no accepted AQUB-specific
retention/access contract for the photo bytes. Existing BECOME/Beta download
adapters are bot-token- and domain-bound and cannot be reused by reference
without expanding credential and data ownership.

Photo delivery is therefore **NO-GO** in this candidate. The next Gate schema
number is reserved: any accepted media-reference Work G must use migration
0102, not modify or reuse 0101.

Raw Telegram `file_id`, bot download URLs and base64 bytes must never cross the
AQUB contract. Media requires a separate Work defining an opaque `media_ref`,
route/actor authorization, digest and size, bounded retrieval,
retention/deletion, corrections and audit receipts before code is added.

## Consequences

- Migration 0101 must follow 0100; it never edits the reviewed 0100 file.
- AQUB must add the same mandatory delivery-type field before pairing.
- Ops credential split remains a hard blocker for runtime activation.
- No route, credential, webhook, provider, deployment or migration apply is
  authorized by this ADR.

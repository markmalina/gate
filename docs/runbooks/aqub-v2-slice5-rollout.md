# AQUB v2 Slice 5 rollout gate

Status: **STOP_PENDING_GATE_DATABASE_CREDENTIAL_CAPABILITY**.

This is a rehearsal plan, not authority to merge, deploy, apply migrations,
create secrets, seed routes, change a webhook or call Telegram.

## Exact dependency chain

1. Independently accept Gate PR #582 exact head
   `1d745030993871906edf2590a835954cfc5eec84`.
2. Independently accept this stacked PR exact head.
3. Independently accept the paired AQUB Food head that requires the same closed
   `payload.delivery_type` enum.
4. Apply Gate migration 0100 and then 0101, in that exact order, with the
   migration identity. Never run 0101 alone.
5. Apply and verify AQUB migration 022 before starting an AQUB runtime that can
   claim Food outbox rows.
6. Keep Gate acceptance disabled, all route rows held/absent, and every
   provider/worker path disabled while identities and ACLs are verified.

## Required credential split

- `GATE_MIGRATION_DATABASE_URL`: migration identity only; may own neither
  application process nor provider runtime.
- `DATABASE_URL`: application identity; must inherit only
  `gate_aqub_v2_runtime`.
- `gate_aqub_v2_owner`: `NOLOGIN`, owns tables, guards and capability functions.
- `gate_aqub_v2_runtime`: `NOLOGIN`, has schema usage, table select and execute
  on the five public capability functions; direct insert/update/delete,
  ownership, DDL and role administration must all be false.

The current single-URL Ops capability does not prove this split. Do not
substitute `SET ROLE`, owner credentials or a shared migration URL in
Production.

## Default-off configuration

The only new configuration names are:

- `GATE_AQUB_V2_DELIVERY_ENDPOINT_ENABLED=false`;
- `GATE_AQUB_V2_DELIVERY_HMAC_SECRET` (unset; minimum 32 bytes when enabled);
- `GATE_AQUB_V2_DATABASE_SCHEMA=public`.

Secret creation/injection is a separate owner gate. Never place its value in a
PR, issue, log, workflow artifact or receipt. The HMAC covers the exact raw body;
reformatted bytes require a new signature. Replays are contained by the
command idempotency key and never create a second dispatch.

## Synthetic rehearsal

With isolated synthetic schemas and no provider:

1. Prove migration `up → check → down to 0099 → up`.
2. Prove the runtime role cannot direct-write either table.
3. Prove it can execute exactly the five public capabilities.
4. Seed an opaque route held at epoch one; activate it only in the synthetic
   transaction.
5. Accept one command, replay exact bytes, then replay the key with changed
   bytes. Expect `committed`, `duplicate`, `rejected`.
6. Hold the route after acceptance and call `begin_aqub_v2_dispatch`. Expect a
   correlated rejection before any command is exposed to a provider.
7. On an active route, begin once, mark the outcome unknown, restart, and prove
   a second begin returns `not_dispatchable`.
8. Reconcile unknown with one correlated `sent` or `failed` receipt.
9. Search all HTTP bodies, database errors and test output for raw identifiers,
   body text, signature material and `conversation_ref`.

## Activation gate

No live activation is part of this slice. A later exact owner-only canary plan
must name one proven bot installation, one conversation binding, one cube,
one route generation, one consent receipt, one exact AQUB/Gate pair, hard caps
for provider calls/messages, stop conditions and a last-known-good revision.
The working bot-name hypothesis is not evidence and must not be inserted here.

## Rollback and ambiguity

- Before any provider wiring, roll back application code with the flag off;
  retained empty tables are inert.
- Downgrade 0101 only after proving the ledger is empty, then downgrade 0100.
- Never downgrade or delete an `unknown` row to make it retryable.
- After the send marker, any crash or missing provider receipt becomes
  `unknown/reconcile_only`; restart must not resend.
- Any ACL, schema, route, signature or exact-head mismatch is a fail-closed
  stop.

## Media blocker

The boundary remains text-only. Caption-as-text is allowed, but photo bytes,
raw `file_id`, download URLs and base64 are forbidden. A separate media Work
must first approve opaque reference, access, retention and deletion semantics.
Photo is **NO-GO** in this candidate; if Work G is accepted, its first Gate
migration number is reserved as 0102.

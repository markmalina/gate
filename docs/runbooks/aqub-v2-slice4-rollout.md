# AQUB v2 Slice 4 rollout gate

Status: **NO-GO**. This document is a reviewed plan, not authorization to
change a database, credential, route, feature flag, or provider.

## Required authority split

Gate currently uses one `DATABASE_URL` for both Alembic and the application.
That principal cannot simultaneously remain migration authority and be proven
unable to exercise table-owner privileges. Before any runtime wiring:

1. Provision a distinct `GATE_MIGRATION_DATABASE_URL` principal. It alone may
   create the no-login owner roles, transfer ownership, and run Alembic.
2. Keep `DATABASE_URL` as the application principal. It may inherit only
   `gate_aqub_v2_runtime`: schema usage, ledger/route `SELECT`, and exact audited
   delivery functions once those functions are reviewed. It receives no table
   `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, ownership, role-admin, or DDL.
3. `gate_aqub_v2_owner` remains `NOLOGIN`; it owns the two tables and guard
   functions. The migration principal's temporary membership is revoked at the
   end of each migration transaction. `gate_aqub_v2_runtime` is also `NOLOGIN`.
4. CI must run Alembic with the migration URL, start Gate with the runtime URL,
   and prove the runtime identity cannot direct-write while exact audited
   operations and downgrade/upgrade rehearsal still pass.

Secret values must be created and injected only through the approved production
control plane. They must never appear in a PR, workflow output, artifact, or
receipt.

## Ordered production sequence after independent acceptance

1. Keep both delivery feature flags off and route authority empty/held.
2. Provision the two Gate credentials and verify role/ownership/grant receipts
   without enabling application delivery.
3. Run Gate Alembic 0100 from the independently accepted exact commit using the
   migration credential. Verify owner, ACL, triggers, and an empty ledger.
4. AQUB refuses runtime startup when a migration is pending. Therefore run AQUB
   migration 022 from its independently accepted exact commit in the migration
   job **before** any runtime deployment or merge-triggered rollout. Verify the
   migration checksum, runtime-principal direct-write rejection, empty outbox,
   and unchanged health/404 contract.
5. Only then merge in the reviewed order, while flags remain off and no route,
   worker, provider, or canary is configured. A later reviewed slice must add
   exact runtime functions and synthetic rehearsal before any send.

Every production step requires a fresh plan/apply approval and postcondition
receipt. No step may reuse an expired plan.

## Rollback

- Before any send-capable wiring, rollback means keep flags off, remove no
  routes, and roll application code back; retained empty tables are inert.
- Gate 0100 may be downgraded only after proving both tables are empty and while
  using the migration credential. Revoke runtime grants before dropping objects.
- AQUB 022 is forward-only for this sequence. Do not drop outbox/consent evidence;
  roll application code back while retaining the migrated schema and checksum.
- If identity, ownership, ACL, checksum, or empty-table verification differs,
  stop fail-closed. Do not start a provider, seed a route, or retry blindly.

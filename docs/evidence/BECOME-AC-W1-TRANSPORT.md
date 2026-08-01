# BECOME.AC W1 — Gate transport evidence

Status: `simulated_verified`  
Execution: `bac-w1-transport-media-e4-20260801-0425`  
Frozen digest: `0303067fb38d33292a255bd6ee0dcd38ea749a5b89a1c5823fa3e8a47480f834`

## Bounded result

The Gate slice adds a local SQLite inbox/outbox and effect-claim skeleton. It
has no provider client or network path. A callback key can create at most one
effect claim; byte-exact replay is a no-op and mutated replay fails closed.
Expired `reserved` leases are recoverable after restart. An expired
`in_flight` lease becomes `unknown` reconciliation work and is never reclaimed.
Provider state is always one of
`reserved`, `in_flight`, `settled`, or `unknown`; `unknown` creates a manual
reconciliation item and blind retry is rejected.

## Verification

- `python -m unittest discover -s tests -p 'test_become_w1_transport_store.py' -v`
- duplicate callback: one inbox row, one claim, one outbox row;
- synthetic kill/restart: durable claim survives; unexpired lease is not stolen;
- reserved lease expiry: a second synthetic worker recovers the same claim;
- in-flight lease expiry: state becomes unknown; no reclaim or retry;
- unknown outcome: one reconciliation row, retry rejected;
- raw transcript, signed URL, provider identity and media bytes: rejected;
- messages, provider calls, payments, publications and external effects: `0`.

All refs and fixtures are synthetic and opaque. Gate product issues were not
read or changed. This draft does not merge, deploy or enable a capability.

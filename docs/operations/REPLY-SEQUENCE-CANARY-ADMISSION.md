# Reply sequence canary: durable attempt primitive

Migration 0113 adds `reply_canary_scopes` and `reply_canary_attempts`. The
synthetic service `reserve_canary_attempt` locks one workspace row, validates
the exact execution, principal, account/dialog/body grant or notification
destination/template, expiry and shared cap (1–6), then commits a never-refunded
attempt before external I/O. Distinct processes share the row lock and ledger.
Reusing an attempt key is denied. A crash after commit consumes capacity.
The scope trigger forbids deletion, reopening a closed scope, changing an
execution ID, cap, expiry, grants, principal or destination. Closing remains
monotonic. The unique execution ID cannot be recycled by normal writes.

This is **an implementation slice, not an armed Production canary**. The
protected Ops arming/reconciliation operation remains required. When
`GATE_REPLY_SEQUENCE_CANARY_ENABLED=true`, each `sendMessage` checks its exact
saved sequence or notification context against the outbound JSON, then commits
an attempt before HTTP. The flag is false by default. Missing/expired scope,
absent or mismatched context, cap exhaustion and unrelated manual sends are
denied before HTTP. Sequence sends bind to the persisted dispatch claim and
draft; outbox notifications bind to the saved sequence principal and verified
route. A 429 retry reenters HTTP through a new admission. The unarmed migration
creates no scope. Do not enable dispatch or this flag without a protected exact
scope, reconciliation and rollback procedure; no Production canary is authorized.
This wiring does not establish G4 release readiness: the scope has no protected
arming/reconciliation command, no owner route is bound, and the registered M
contract remains stale. These predicates remain BLOCKED under #722.

Full migration graph from the baseline in #722 is now
`0111_reply_documents → 0112_reply_sequence_control → 0113_reply_canary_budget`.
Recompute against the exact final head if another additive migration is added.

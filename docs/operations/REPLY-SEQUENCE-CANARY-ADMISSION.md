# Reply sequence canary: durable attempt primitive

Migration 0113 adds `reply_canary_scopes` and `reply_canary_attempts`. The
synthetic service `reserve_canary_attempt` locks one workspace row, validates
the exact execution, principal, account/dialog/body grant or notification
destination/template, expiry and shared cap (1–6), then commits a never-refunded
attempt before external I/O. Distinct processes share the row lock and ledger.
Reusing an attempt key is denied. A crash after commit consumes capacity.

This is **an implementation slice, not an armed Production canary**. The
protected Ops arming/reconciliation operation and Gate sender/notification HTTP
integration remain required. In particular every sequence provider call and
every notification retry must invoke the reservation before HTTP; pending
notifications must remain fenced after expiry or serial→parallel. No code path
currently calls this primitive. The table is unarmed by default; no row is
created by migration. A synthetic cap test does not establish the release G4
criterion. Do not enable dispatch or treat the presence of 0113 as permission
to send.

Full migration graph from the baseline in #722 is now
`0111_reply_documents → 0112_reply_sequence_control → 0113_reply_canary_budget`.
Recompute against the exact final head if another additive migration is added.

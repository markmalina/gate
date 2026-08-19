# ADR 0100: Isolate the default-off AQUB delivery v2 ledger

Status: proposed

Gate accepts a strict `aqub.delivery-command.v2` only in the code-owned
`SYNTHETIC_REHEARSAL` mode. It records canonical command bytes before returning
`committed`; exact replay returns `duplicate`; the same idempotency key with
different bytes returns `rejected` and never reaches a provider.

The separate opaque-only route authority table is empty by default and has no
runtime writer. Acceptance and dispatch both require an exact active
installation/conversation/cube/generation row. Dispatch locks that row, commits
`sending` before crossing the injected provider boundary, and therefore has a
deterministic effect boundary. A stale `sending` row becomes `unknown` with
`reconcile_only`; restart and duplicate acceptance do not resend it. Only an
explicit, correlated terminal receipt can reconcile it.

This slice does not import the executor from the application composition,
register an HTTP route, configure a provider, or seed route authority. It is
not deployable delivery functionality and does not authorize a canary. Future
runtime wiring and route-authority maintenance require a separate review.

# AQUB contract v2 mirror for Gate

This directory pins the neutral Gate/AQUB transport contract accepted in
`markmalina/aqub` PR #43 and merged as
`cadd5f0ff9731b1114e85e95023ddb3f5d5099ba`.

The four schemas and six synthetic positive fixtures are byte-identical mirrors of
the upstream Git blobs listed in `provenance.json`. They are copied here so Gate can
run contract parity and privacy tests without a network dependency or mutable branch
reference.

## Ownership boundary

Gate owns channel identity, opaque installation bindings, transport envelopes,
transport idempotency, delivery attempts, reconciliation and terminal transport
receipts. AQUB owns Cube context, participants, consent, programs, progress, memory,
model policy and the meaning of a response.

The mirrored contract carries only opaque installation/Cube/conversation/message
references, route generation, bounded text transport and transport facts. It must
not acquire roles, programs, progress, prompts, model policy or raw provider IDs.

## Runtime status

- runtime wiring: **absent**;
- route or binding activation: **absent**;
- configuration/environment switch: **absent**;
- database migration: **absent**;
- Telegram/provider effect: **not authorized**.

`app/application/aqub_contract_v2.py` exposes a pure parser/serializer and an
explicit synthetic-rehearsal seam. The seam defaults to disabled and cannot be
enabled by a boolean or environment string. No current router, worker, settings or
application composition imports it.

Activation, shadow traffic, persistence and cutover require later bounded work with
fresh route evidence, exact contract tests, duplicate protection, reconciliation and
separate operational authority.

## Delivery invariant

`sent` and `failed` are settled at the AQUB boundary with `do_not_retry`. `unknown`
means the provider effect may have happened; it requires `reconcile_only` and an
opaque `reconciliation_ref`. Gate must never convert `unknown` to `failed` or blindly
retry it.

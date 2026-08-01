# BECOME.AC W0 — Gate transport contract evidence

Status: `simulated_verified`  
Canonical coordination: `AlphaCenter/become#22` and draft PR `AlphaCenter/become#21`  
Execution: `bac-w0-gate-transport-001`  
Mode: `SIMULATED`

## Bounded outcome

This pack freezes Gate's W0 provider capability, delivery and Telegram Stars
payment contracts. It defines Telegram Bot, Telegram Business and Meta
Instagram capability states; message/private-reply/publication/group
operations; and effect-ledger states `reserved`, `in_flight`, `settled` and
`unknown`. Every record has an explicit `receipt_type`. Provider
`effect_state` is separate from `delivery_outcome` or `payment_outcome`.

`unknown` is terminal for automatic delivery: every contract sets
`automatic_retry: false` and requires reconciliation. The payment contract
requires a prior `ResultReceipt`, preserving free-result-before-paid.

## Contract digests

| File | SHA-256 |
| --- | --- |
| `provider-capability.schema.json` | `a8ca71fa92596bf1c2abff9dfe18dee90d901a20695a9a51e3be66f831b5a45c` |
| `delivery-receipt.schema.json` | `705bd13c50335883b346e78119dd007060ffa803cbb3dad919311cc450edb226` |
| `payment-receipt.schema.json` | `d86f98ea1f2bb9a5d71eefb31831b8b9afe5723c1d02fc468aa4f153e1720cac` |
| `gate-transport-w0.json` | `70e2ace5cefc8ba42d3ba1053e938b0ad33031fe01df4f718cdf89450b3e6b2c` |

## Verification receipt

- strict JSON parse: 4/4 files;
- synthetic positive cases: 6/6;
- declared fail-closed cases: 5/5;
- checks: simulated-only fence, result-before-payment, no blind retry,
  reconciliation on unknown, no provider receipt claim in synthetic mode,
  and rejection when a business outcome is supplied as `effect_state`;
- local command: execute the three `test_*` functions in
  `tests/test_become_w0_transport_contracts.py` (pytest will discover them in
  repository CI);
- provider calls: 0;
- deliveries/messages/payments/publications: 0;
- production/deploy/config/secret/PII operations: 0.

## Scope manifest

Changed only new W0 contract, fixture, test and evidence files. No existing Gate
runtime, migration, product issue, automation, provider adapter or deployment
was changed. Existing Gate product issues remain untouched.

## Blockers and next pointer

- IC-0 must align opaque `payload_ref`, `result_receipt_ref`, consent and
  correlation fields with the other producer packs.
- LIVE/TEST/CONTROLLED capability availability remains an owner/provider gate;
  this pack proves no provider capability.

One next pointer: coordinator compares these digests at IC-0 and either freezes
them or returns one bounded Gate schema correction.

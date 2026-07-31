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
`unknown`.

`unknown` is terminal for automatic delivery: every contract sets
`automatic_retry: false` and requires reconciliation. The payment contract
requires a prior `ResultReceipt`, preserving free-result-before-paid.

## Contract digests

| File | SHA-256 |
| --- | --- |
| `provider-capability.schema.json` | `8ff48d925e813e3c39b2117e28a59bae3ed031d7662f598c6890ace5b9415721` |
| `delivery-receipt.schema.json` | `b76b203a9fd1a0b2ab0794aadbc6833dbc01973d55dc2963d1ba6faccaea1d10` |
| `payment-receipt.schema.json` | `2a15a2db3ef89721ad3766cec13bc9909036347fe91740e9f0cc6b2a8e45ad72` |
| `gate-transport-w0.json` | `9500fcc2da1bb6102c7af82cec729464f1ae8681795468d31929f008a52f9ec2` |

## Verification receipt

- strict JSON parse: 4/4 files;
- synthetic positive cases: 6/6;
- declared fail-closed cases: 4/4;
- checks: simulated-only fence, result-before-payment, no blind retry,
  reconciliation on unknown, no provider receipt claim in synthetic mode;
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

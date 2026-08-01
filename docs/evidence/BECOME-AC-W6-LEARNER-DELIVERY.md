# BECOME.AC W6 — Gate learner-delivery evidence

Status: `simulated_verified`  
Frozen digest: `418bb1d0e49abc1239addd0bc03502c42f6c2d5524c852b912221f3763b15487`

Gate records exercise and ResultReceipt delivery claims against exact opaque
learner, signed-link digest, FakeMayaki exercise-version and session references.
One idempotency key creates at most one claim. Payloads are opaque refs only;
provider execution is mocked. Unknown state requires reconciliation and cannot
be blindly retried.

Verification:

- `python -m unittest discover -s tests -p 'test_become_w6_learner_delivery.py' -v`;
- exact signed-link/version/session delivery and deterministic receipt `f284172f6f4c232dda717c59b98cb838c5ec035ea2f1a560bce0ae44a115cd7c`;
- duplicate no-op and mutated version rejection;
- exercise and ResultReceipt delivery are independent;
- PII-shaped learner ref and extra invoice field rejected;
- unknown/no-blind-retry reconciliation;
- external effects and invoices: `0`.

Gate product issues were not read or changed. No merge, deploy, publish or send.

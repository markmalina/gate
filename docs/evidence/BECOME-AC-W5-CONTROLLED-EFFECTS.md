# BECOME.AC W5 — Gate controlled-effects evidence

Status: `simulated_verified`  
Execution: `bac-w5-transport-e4-20260801-0635`  
Frozen digest: `f47de6ca0a085ccc17c50c269d94b86d009972abaeab6ae9ef1c1d4b306d6419`

Gate verifies the exact TAKE staging receipt and a matching mode grant bound to
account, render digest, caption digest and SIMULATED mode. Instagram publication
and Telegram group invite/welcome/pin are provider-mock operations only. An
idempotency key creates at most one effect claim. Missing permission records an
explicit blocker without creating an effect.

Unknown mock-provider state requires reconciliation and cannot be retried.
States remain `reserved/in_flight/settled/unknown`; a proven no-effect receipt
may return the claim to reserved, while a proven effect settles it.

Verification:

- `python -m unittest discover -s tests -p 'test_become_w5_controlled_effects.py' -v`;
- exact staging receipt and exact grant verification;
- duplicate claim no-op and mutated replay rejection;
- denied group right → one blocker, zero effects;
- invite/welcome/pin provider mocks settle independently;
- unknown retry rejected until reconciliation;
- actual provider/publication/message effects: `0`.

Gate product issues were not read or changed. No LIVE provider, account identity,
secret, media or PII is stored. This draft does not merge, deploy, publish, send,
invite, welcome or pin.


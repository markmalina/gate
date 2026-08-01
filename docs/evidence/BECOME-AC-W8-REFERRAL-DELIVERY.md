# W8 Gate referral delivery evidence

Frozen IC-7 digest: `099f8b5bca93a3558ce9fa9b1ec4019304b126810c06b65384e3e71cd09c24e5`.

The SIMULATED store binds an active FakeRelations attribution tuple to the exact
FakeBECOME signed-link payload digest. Acceptance receipts preserve exact opaque
referrer/candidate facts but contain no provider or media identifier, including
derived provider hashes.

Status transitions are monotonic: delivered/failed are terminal, UNKNOWN is
quarantined, and reconciliation requires explicit `EFFECT_PROVEN` or
`NO_EFFECT_PROVEN` evidence. Exact replay is a no-op; mutation fails closed.
SQLite initialization is reopen-safe and retains the latest redacted receipt.

Contract digests (SHA-256): runtime
`310a71b13cc90aa8233d66f478ee8445084dce6cc64295bdad2bc2ac4719169d`;
fixture `4c681040ea00568dcbf8e5c18c5398aac2e07528edff37a785ddc59991b62a4b`.

Checks:

- `python -m unittest discover -s tests -p 'test_become_w8_referral_delivery.py' -v`
- `python -m py_compile src/become_w8/referral_delivery.py tests/test_become_w8_referral_delivery.py`
- result: 7/7 targeted tests and syntax PASS

Repository-only synthetic fixtures. Provider/send/message/media/PII/secret,
deployment and external effects: 0. Existing Gate product issues are untouched.

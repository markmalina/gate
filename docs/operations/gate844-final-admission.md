# Gate844: final account flags and shared execution expiry

Scope: [Gate844](https://github.com/markmalina/gate/issues/844), existing
[PR747](https://github.com/markmalina/gate/pull/747), following
[Delta520 REJECT](https://github.com/AlphaCenter/delta/issues/520#issuecomment-6799854291).
Rejected baseline: `3c40c55097393daef055b6f728d13a7e7ac65537`, tree
`fa2541075ba70b108e330b2c7678755d351a6de1`, main
`8d17d0b40a86c856bec1072633c721212a34a9dc`.
The implementation checks are author evidence, not independent acceptance.

## F01: account flags after the final fence

`_admit_dispatch` initially loads context models, then waits for the same
durable ingress fence as before. A committed account disable during that wait
must not be checked against the earlier ORM Account instance.

After acquiring the fence and validating dialog readiness/freshness, admission
now selects scalar `Account.is_active` and `Account.send_enabled` from SQL.
Scalar columns do not reuse an ORM identity-map value. A missing or disabled
account refuses with `account_send_disabled` before any provider write.
No account row lock is acquired before the ingress fence; the established
ingress-first lock order is preserved.

## F02: the reserved execution at the final boundary

All supported sends with `execution_id`, including plain/Markdown/Rich and
documents, must retain a live execution budget at final admission. Media asset
expiry alone does not cover the text control route.

Admission follows `ReplyMediaBudgetReservation.send_attempt_id` for this exact
attempt to its durable budget. A scalar SQL query checks workspace, principal,
execution, the existing 12-write/20-message caps, expected message count,
recorded counters and current deadline. Missing, foreign or expired authority
refuses with `media_execution_expired`. This is a read-only check: it does not
renew TTL, reserve a second slot, refund counters or recreate a reservation.
It runs before payload expiry checks so an expired execution has the same
explicit refusal for text, document and media transports.

Terminal replay still returns its existing outcome before new admission; an
expired execution never authorizes a new write or changes a terminal receipt.
The existing preflight, reservation, snapshot, payload hashes and media budget
rules remain in place. No schema, workflow, runtime flag or deployment change
is part of this correction. Both 0116 branches and join0120 are untouched.

## Reproduction and regression evidence

The unchanged seven
[reviewer probes](https://github.com/AlphaCenter/delta/issues/520#issuecomment-6799793629)
were executed against the rejected source: 3 FAIL / 4 PASS / 0 SKIP. With this
source correction the same external file gave 7 PASS / 0 SKIP. The failures
before correction were both account flags and the shared plain execution TTL.

`tests/test_reply_final_authority_postgres.py` preserves those seven scenarios:
two independent-connection flag races, initial disable control, fresh read and
draft after inbound/owner voice refusal, terminal SENT replay after a new event,
and TTL expiry after reservation. Voice is only a synthetic envelope, without
audio processing or STT.

`tests/test_reply_final_execution_postgres.py` adds real PostgreSQL final-fence
waits for valid and expired executions across plain, Markdown, Rich, document,
photo, album and native Rich media modes. It also covers missing and foreign
workspace/principal/execution budgets, missing or rebound reservation and a
shortened durable deadline. Counter/TTL preservation and terminal replay are
checked after refusal and success. The initial focused 35-case set passed
without skips; final exact-source local and CI results belong to Gate844.

Tests use disposable local PostgreSQL, independent sessions, synthetic records
and provider recorders/MockTransport. No production data, provider delivery,
model/STT call or paid resource is used by the local verification.

## Boundary and remaining work

The last scalar checks are the final authorization decision under the ingress
fence. Changes or expiry after that decision/committed admission cannot reliably
revoke a provider HTTP already admitted. SQL transactions end before provider
HTTP; this change does not promise continuous revocation or cancellation.
Events not yet durably received by Gate remain outside the freshness guarantee.

`notification_status=route_unavailable` is still the known separate
[Gate722](https://github.com/markmalina/gate/issues/722) gap: no owner notification
is implemented or sent here. Watch/Calls/STT and AQUB remain frozen.
No merge, deploy, production migration, configuration or credential mutation is
authorized by this source package. A new independent attempt3 with predecessor
Delta520 must review the new exact candidate; Delta520 stays terminal REJECT.
Neither that new review nor sprint6 is started by this correction.

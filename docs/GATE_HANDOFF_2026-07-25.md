# Gate handoff — 25 July 2026

## Purpose

This document is the durable restart point for the next Gate Work. It records
only repository-safe and redacted evidence. GitHub issues remain canonical for
execution receipts and product acceptance.

## Owner operating mode

The owner activated pre-user autonomy on 25 July 2026. The authoritative rule
is in `AGENTS.md` and `docs/work-bootstrap.md`.

While fresh evidence proves `external_users: 0`, bounded repository work,
exact-head CI, merge, Production deployment, verification and rollback proceed
without repeated owner confirmation. The mode terminates automatically on the
first external user, third-party participant or third-party payload.

Credentials, secrets, OAuth, payment/legal actions, publication, destructive
history/data changes and effects outside exact owner-only caps remain gated.

## Completed runtime recovery

Canonical Work: https://github.com/markmalina/gate/issues/277

- recovery specification:
  `docs/GATE_RUNTIME_RECOVERY_SPEC_2026-07-24.md`;
- PR #302 merged;
- accepted Production revision before the Alpha release:
  `a9ff4e01da330e8a4ee4abbc7d2f0cf20c6e90a4`;
- repository verification: 1,100 tests passed, one skipped, plus static,
  migration, PostgreSQL, Docker, Windows and secret-tree checks;
- redacted health/readiness and current-tree secret guard are deployed;
- Railway ChatGPT OAuth is optional telemetry and never a Work dependency.

## Alpha Group outcome

Canonical Work: https://github.com/markmalina/gate/issues/261

Accepted first owner-only canary:

- exact turn: `5a3fd088-fe80-45f6-a909-7486696c236b`;
- one OpenAI-backed proposal;
- one Telegram delivery;
- exactly one owner-visible message;
- exact replay without a second provider or Telegram call;
- old turns remain forbidden.

Repository outcome:

- PR #303 merged from exact reviewed head
  `a4d1d8790483f871333610fd8d70c8a0a6b3074c`;
- merge commit:
  `d7d8d6cd13ecafa258c7e103b2d571572475f8ec`;
- CI #1058 succeeded;
- 1,102 tests passed, one skipped;
- Alpha release 32/32, recovery 71/71, privacy 4/4 and admission 6/6 passed.

The composition permits only targeted `/alpha@AlphaViaBot`, an exact bot
mention, or a verified reply. It binds bounded-live delivery to the exact
ACTIVE `alpha-model-v2-openai` deployment and `alpha-model-v2` policy.
It preserves one turn/grant/send, commit-before-send, terminal unknown,
provider-free replay, active-state checks, pause/revoke barriers and disclosure.

At 2026-07-25T03:58:31Z, Production still reported `a9ff4e01da33`.
Therefore merge alone is not runtime acceptance. The next Work must use the
latest completion receipt to confirm the deployed fingerprint and redacted
runtime acceptance before claiming permanent bounded-live availability.

## Product pointers

1. Alpha Group — Gate #261. Confirm the exact Production revision, then run
   only a bounded owner-only acceptance under the pre-user policy.
2. Telegram Business universal Mayak — Gate #300 / PR #301. Implement the real
   top-level `business_message`, authoritative SQL binding/store, Production
   OpenAI composition, Business delivery adapter and two linked replies.
3. MAYAKI/LMS — MAYAKI #95 / PR #96. Connect the already tested learning loop
   to the accepted Gate personal-message path.
4. Oko — full program evidence remains incomplete until five complete exercise
   material bundles exist; this does not block one bounded Telegram learning
   loop.

## Restart checklist

1. Read `AGENTS.md`, `docs/work-bootstrap.md`, this document, Gate #277 and
   the latest receipt in Gate #261.
2. Inspect open issues, PRs, executions, leases and the single enabled Gate
   heartbeat before claiming anything.
3. Confirm no unexpired duplicate execution.
4. Verify `pre_user_mode: true`, `external_users: 0` and fresh redacted
   owner-only evidence before using standing authorization.
5. Claim one execution, one lease, one branch and one PR.
6. Run local/static checks first and only one necessary exact-head CI.
7. Merge, deploy, verify or roll back autonomously inside the proven boundary.
8. Record exact repository, deployed revision, runtime and product receipts
   separately.

## Known limitations

- A successful CI, merge or deployment does not prove runtime readiness.
- Railway connector availability does not control Gate availability.
- Personal Mayak is not yet a user-visible product.
- MAYAKI/LMS is not yet connected to Telegram.
- Alpha permanent bounded-live remains unaccepted until the new Production
  fingerprint and owner-only runtime check are recorded.

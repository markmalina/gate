# Alpha shadow release admission

Gate keeps the approved fixture canary as the production rollback target while
provider billing is unavailable. Status inspection, CI, deployment and this
admission contract never call OpenAI or Telegram.

The first model-backed step remains one explicitly confirmed, synthetic provider
preflight. A successful preflight does not activate shadow automatically. It
only makes a separately reviewed binding release admissible for the exact
staged OpenAI deployment and the exact approved installation. Telegram delivery
stays blocked throughout shadow observation.

Admission fails closed when any required release, incident, retention, restore
or supply-chain evidence is absent; when deployment or installation cardinality
is not exactly one; when rollback is unavailable; when budget is exhausted; or
when any provider ambiguity, failed model turn or model delivery grant exists.

After billing is confirmed, the operator sequence is:

1. Read production status and require the fixture canary, one staged deployment,
   one approved installation, zero model turns and zero model delivery grants.
2. Run the protected synthetic provider preflight exactly once.
3. Require a verified, non-ambiguous result and remaining monthly budget.
4. Build and review a separate binding release for delivery-disabled shadow.
5. Observe the required shadow proposal sample. Any failure retains the fixture
   binding and blocks promotion.

No environment flag or model output can bypass the reviewed release boundary.

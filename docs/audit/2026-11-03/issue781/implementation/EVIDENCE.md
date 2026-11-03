# #781 — offline implementation evidence, 2026-11-03

Scope: OFFLINE_IMPLEMENTATION_AND_REVIEWABLE_PACKAGE. Real Railway trial has NOT run.
Railway calls: 0. Production operations: 0. S2: not started. Release: disabled.
Accepted #780 is the baseline, not a repeated audit.

## Frozen candidates and checks

- Gate [45ba499a5f1852208ba8a478a606358fd0fe3bd5](https://github.com/markmalina/gate/commit/45ba499a5f1852208ba8a478a606358fd0fe3bd5), [PR766](https://github.com/markmalina/gate/pull/766).
- Ops [0c8fc49b2e9d973d5f7e58289726e54e5f6ec2e4](https://github.com/markmalina/ops/commit/0c8fc49b2e9d973d5f7e58289726e54e5f6ec2e4), [PR414](https://github.com/markmalina/ops/pull/414).
- Ops [CI41613337223](https://github.com/markmalina/ops/actions/runs/41613337223): SUCCESS, 2,576 Node tests; Vitest and build/dependency/license/action-pin checks passed. Includes the actual pinned Railway IaC SDK test without provider access.
- Ops [Security41613337957](https://github.com/markmalina/ops/actions/runs/41613337957): SUCCESS.
- Gate [CI41613168624](https://github.com/markmalina/gate/actions/runs/41613168624): SUCCESS.
- Gate [exact-head Quest41613168672](https://github.com/markmalina/gate/actions/runs/41613168672): SUCCESS.
- Gate [offline PostgreSQL/process41613168631](https://github.com/markmalina/gate/actions/runs/41613168631): SUCCESS; five local runtime unit tests, real PostgreSQL seed/stop/logical dump+restore/read-only verification, actual capped containers and two writer process trees.

The archive contains unmodified raw JSON files from GitHub artifact13220381193, ZIP digest
`sha256:cb64095c6e48041b9006e78e54a782e09eec7cdf0a9407d0ecfaf16fdcfa59e9`.
The downloaded ZIP digest and every proof sourceSha were checked before archival. Individual
raw-byte digests and Actions metadata are in provenance.json. GitHub artifact expires on
2026-11-10; these source-repository receipts remain available independently of that expiry.

All `00000000-...` IDs are explicitly synthetic. The PostgreSQL proof says
`PG_DUMP_LOGICAL_NOT_RAILWAY_VOLUME`; the container proof says `LOCAL_DOCKER_SYNTHETIC`.
The identical baseline/restored data digest is
`sha256:53355133c4e16e2f4d140ce2c3d5f9bb8b55e11b2730ef07ae0261beda527085`.
Two writer replicas each prove two child processes reaped. CPU/RAM observations are
1 vCPU / 536870912 bytes. Wrong database marker is refused before seeding.
These observations do not prove Railway instance/log semantics or native volume restore.

## Independent review and limitations

Independent review: [Delta451](https://github.com/AlphaCenter/delta/issues/451), attempt2;
predecessor [Delta450](https://github.com/AlphaCenter/delta/issues/450) remains immutable.
The reviewer independently compared 35 Ops and 10 Gate file blob hashes to the exact published
candidates: zero mismatches. The ordinary scenario suite passed 15/15, including the existing
terminal-before-backup ordering regression. The reviewer read the final verifier ordering,
checked the package, resource costs, external dependencies and disabled release.

Earlier independent findings led to implemented fixes and regression coverage: fresh fleet
before backup, verifier config/caps/SHA/volume/lock checks, PostgreSQL identity before DDL,
restore parent and patch reread, no token echo in persisted artifacts, durable acknowledged
IDs for UNKNOWN outcomes, and terminal/workflow evidence preceding dependent effects.

The attempted final broader adversarial review was rejected by an automatic tool restriction
for possible cybersecurity risk. It was not retried or bypassed. The final independent pass
was narrowed to read-only package/source/CI consistency and existing ordinary tests. Its
conclusion is functional offline readiness, not an exhaustive security verdict and not
ACCEPT_NATIVE_FIXTURE. The terminal verdict, exact scope and limitations are recorded in Delta451.

## Next action

Use PACKAGE.md as the single concrete proposal. The owner decides a separate bootstrap/access
authorization for the specified disposable resources and cost envelope. The native trial itself
requires a later fresh manifest-bound grant. No prior Railway authorization is rechecked.
Physical target IDs, fresh secret provenance and native restore emission remain explicit
external dependencies; they do not block the implemented offline controller/runtime/verifier.

The issue remains open for native proof; S1 is not DONE. Current code is published in candidate
branches only. No merge, installation, provisioning, credential changes or workflow native run occurred.

# Gate781 — S1 native disposable preparation

2026-11-03. Result: **BLOCKED_NATIVE_EXECUTION / NOT_RUN**.
This is a concrete refusal package, not an executable stop/backup/restore grant.
Gate736 stays open; S1 is not DONE. Production and S2 are outside this work.
Accepted #780 is reused; its boundary audit has not been repeated.

## Exact sources and resources

| Component | Exact state |
|---|---|
| Accepted Gate780 | Gate897b2f1b882326cdc77777f4c600d8087cfbcdb8 |
| Accepted Ops780 | Ops0079fc2f3f5048cb54fc0907364945779d89a6ca |
| Accepted Mark126 | f448bdb07efd97192e4a1b19117179d4abc3ef71; unchanged |
| Guarded fallback | Gate4827624955c9543bfae1b1377de8497956720ab9; unchanged |
| Gate781 candidate | Gate728ce4ff7f8dbbb0751ecf15776251f4506b2ce3 / tree346464d1ded84d59bceecb442e70eff8d9b88c36; PR766 unmerged |
| Ops781 candidate | Ops1a698524260ba193b3b2904c5d2c9be23c973f51 / treeaa81aa836aab4e46d902f2e0ab725763d93d8bdc; PR414 draft/unmerged |
| GitHub environment found | repo markmalina/ops ID1331657982, environment `agent-ops-disposable-backup-fixture` |
| GitHub environments absent | `gate745-isolated-fixture` in Gate and Ops; `agent-ops-disposable-backup-fixture` in Gate ID1293746267 |
| Railway project/environment | NOT_OBSERVED; no exact IDs available in authenticated evidence |
| Gate deployment / every replica | NOT_OBSERVED; not authorized |
| PG service / original volume / instance | NOT_OBSERVED; not authorized |
| Restored volume / instance / copied backups | NOT_CREATED; not authorized |
| Dedicated token provenance | NOT_PROVEN; no secret value read/requested/output |
| Native executable version | NONE RELEASED for this fixture; OFF/PENDING |

Names of GitHub environments are not evidence that a Railway project exists.
The historical [Ops run37920700155](https://github.com/markmalina/ops/actions/runs/37920700155)
failed E_FIXTURE_TOKEN_MISSING before Railway. This proves neither present-day
absence nor present-day availability of a token. Its old auth was not retried.

## Normal mechanism and prepared correction

The installed Ops disposable workflow exists at
[blob982a46d](https://github.com/markmalina/ops/blob/64d8ccd231bf2fd3b3a1a71d0634c0bf9f8fe09e/.github/workflows/prove-disposable-locked-backup.yml).
Its identity code pins `AlphaCenter/ops`, not current `markmalina/ops`; its CLI
backup proof is not Watch0119-native proof. That workflow was not changed/run.
No alternate browser, provider CLI/MCP, generic SQL executor or old grant was used.

New metadata-only PR CI checks only two fixed GitHub environment names. It has
no protected environment, provider secret, id-token or write permissions. Its
maximum is 2 GitHub list calls/run with no retries (actual1); timeout2 minutes.
It reports incomplete/denied reads as UNKNOWN. The byte-length check occurs
after response.text(); it is an output limit, not a hard streaming-memory bound.

The minimal next discovery adapter is prepared and tested **offline only** at
[Ops scripts/gate781](https://github.com/markmalina/ops/tree/1a698524260ba193b3b2904c5d2c9be23c973f51/scripts/gate781).
CLI refuses E_DISCOVERY_BINDING_UNPROVEN before event/token/network access;
there is no default network transport and no invoking workflow. Explicit
transport injection is a synthetic test seam, not a live authority boundary.
This removes the unsafe assumption that environment presence proves token scope:
`secrets.RAILWAY_TOKEN` can fall back to a same-name repo/org secret.

Offline design ceilings: GitHub4 reads per admission (8 if a future distinct
pre-credential admission step is added), Railway<=4 fixed queries, <=15s/request,
60s per admission/provider window, <=15-minute owner request expiry, <=64KiB
streamed provider JSON, <=10 services/volume instances, no retries or mutations.
No enumeration through unknown pagination, no adoption, no raw source/variables
or errors in receipt. Errors erase provider fields; a UUID-shaped token echoed
as an ID cannot survive in a receipt. Cardinality match is not safety acceptance.
These are future design ceilings, not permission to execute this version.

## Why the physical proof cannot start

This is a narrow fixture compatibility check, not the #780 boundary re-audit:

1. Fixture manifests remain UNBOUND/enabled=false, allocation policy null and
   review PENDING. The native v2 adapter deliberately refuses with
   E_FIXTURE_V2_RESOURCE_LIMITS_NOT_PROVEN before mutation: opaque limits JSON
   does not prove CPU/RAM caps.
2. [native-cutover-core](https://github.com/markmalina/ops/blob/1a698524260ba193b3b2904c5d2c9be23c973f51/agent-ops/actions/gate-snapshot-cutover/native-cutover-core.mjs)
   routes Watch0119 to watchSnapshotCutover; fixtureSnapshotCutoverV2 still uses
   legacy0116 fixedCore. Same file does not mean the same execution path.
3. [native waitStopped](https://github.com/markmalina/ops/blob/1a698524260ba193b3b2904c5d2c9be23c973f51/agent-ops/actions/gate-snapshot-cutover/gate-snapshot-cutover.mjs)
   polls deploymentStopped only. No independent per-process completion / graceful
   drain / retiring-replica witness has been produced. A provider flag alone
   cannot authorize the backup as transactionally consistent. This finding is
   an evidence gap, not a claim that Railway's stop is broken.
4. [fixture controller](https://github.com/markmalina/ops/blob/1a698524260ba193b3b2904c5d2c9be23c973f51/agent-ops/actions/gate745-fixture-controller/README.md)
   explicitly excludes provisioning, restore and deletion. No ready cleanup
   owner or same-run cleanup artifact is available.
5. Railway [backup documentation](https://docs.railway.com/volumes/backups)
   describes restore into a new volume, retaining the old one and staging a swap
   followed by deployment in the same project/environment. Cleanup therefore
   must account for both volumes and copied backups. Existing one-volume
   invariants cannot silently authorize that change. No physical restore or
   fresh PostgreSQL consistency check was performed in this task.

## Operations, limits, costs, recovery and cleanup

**Executable Railway budget for this package: 0 reads, 0 mutations.** No live
command is provided because exact resources, scope and executor are not ready.

| Action | Present limit | Required before later authorization |
|---|---:|---|
| Provision / configuration / secret changes | 0 | exact disposable ownership and typed bounded route |
| Stop processes / deployment mutations | 0 | complete exact fleet; real all-process completion proof |
| Create backup / lock / restore | 0 / 0 / 0 | source + target IDs, completed/locked readback, restore verifier |
| Driver / model / Telegram / production calls | 0 | outside this task |
| Cleanup / delete | 0 | exact created resource IDs and dedicated cleanup authority |

Future native scenario, not authorized: synthetic-only Gate+PG, exact immutable
images/SHAs, autodeploy off and no pending changes; retain committed
originals/hash/cursor/processing/sending/UNKNOWN and journals; include a live
uncommitted transaction. Stop all exact Gate processes and prove terminal
completion + empty allocation, then backup<=1, wait complete, lock<=1, exact
readback. Restore<=1 within that disposable environment, verify committed data,
rollback of uncommitted work and no rearmed claims via an independent verifier.
One terminal producer+verifier receipt must link every ID, operation, raw digest
and original run. These upper-level counts are insufficient as a live budget:
provider polling/configuration/cleanup counts and executor remain unimplemented.

Uncertain mutation outcomes must never trigger another mutation. Reconcile only
original IDs/operation within its future reviewed read budget. Preserve stopped
state and evidence; no automatic restart/downgrade. Restore must never target
production. Before any fixture allocation, prepare a cleanup route accounting
for original/restored volume instances, volumes, backups, services and project.
Queued deletion/403/null cannot be reported as authenticated physical absence.

Observed Railway incremental resource cost: no new resources or operations in
this work; no new Railway usage initiated. Existing account/resource charges
were not inspected. GitHub CI may consume included minutes or incur account-plan
charges; billing was not queried and a dollar total is not claimed. Real fixture
compute/storage/backup cost cannot be capped until CPU/RAM/storage/lifetime and
cleanup semantics are proven. No nonzero spending authorization is requested.
Rollback/cleanup now: no provider state changed, so no provider recovery or
resource deletion is needed. PR changes remain unmerged. CI artifacts expire
in3 days; raw receipts are preserved here rather than deleting their evidence.

## Evidence and verification

Original raw bytes are stored beside this report, not reconstructed JSON.

| Receipt | Source / run / artifact | Raw SHA256 | ZIP SHA256 |
|---|---|---|---|
| Gate inventory | 728ce4f /41595129632 /13211169902 | 058f423f709940571761a5239d9baab4b6edbf7214c690153dc7fcd346aa605c | e0253a0edc31b8bfab2583054addf1401cee29433f9c1bead23fa38ce2783835 |
| Ops initial | 64d8ccd /41595124458 /13211379450 | a0c80ae0ee774205209afca21523c1f7397f8d3568ed22d93a76621838677746 | 4c3663e99340e76c80aeaaa16fab7a61ff98184f266af976c094d4fef84e2dc0 |
| Ops final | 1a69852 /41596762203 /13212980185 | 02af605636812dbe99f3e56911e9db6824a8853786d79a43a4f3365a8d98a45a | a4129780a091fc10a8bde7e4a0070b736fcf8b8bfa581acaa22f021692a13637 |

Actual metadata window: 07:15:40.864–07:15:44.417 UTC; final Ops observation
07:30:38.674–07:30:39.167 UTC. Three metadata runs total3 GitHub list GETs,
zero provider calls/mutations. Workflow merge SHAs are in receipts; tested
checkout is separately pinned to exact PR head. No reruns.

Author tests: Ops51 and Gate19 new synthetic tests PASS; native fixture baseline32
PASS. Those tests do not prove native semantics. Pinned Railway schema projections
are source conformance only: commit6f1b464eb96793700a5d02d2e55ecf69037ccc35,
blob4a3f50871c4d0f48bd69128cc455e74c11ee16a4.

Gate exact-head CI41595129547, Quest41595129538 and metadata41595129632 SUCCESS.
Final Ops CI/Security and independent verdict are recorded in FINAL.md after
completion. Independent bounded review: [Delta450](https://github.com/AlphaCenter/delta/issues/450).
No ACCEPT_NATIVE_FIXTURE has been issued.

## Finite next step

Owner dependency: designate a genuinely disposable Railway project/environment
and confirm a dedicated fixture-only credential binding in the named Ops
GitHub environment, **without providing secret values**; otherwise leave blocked.
The authenticated metadata/allowed normal routes do not establish those facts.
No old production credential may be tried to discover them. An owner decision
about this binding does not authorize stop/backup/restore or installation.

After that dependency is resolved, the authorized source continuation must unify
the actual Watch0119 core, implement completion/resource-cap evidence and bounded
restore/cleanup, then present ONE exact live package with IDs/versions/all query
and mutation caps/cost/cleanup before requesting launch permission. This report
explicitly does not claim that remaining implementation is complete.

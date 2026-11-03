# Gate780 — writer boundary without invented remote settlement

Owner instruction: https://github.com/markmalina/gate/issues/780 . This is source/test preparation only. No merge, installation, live driver operation, provider observation, backup, migration, restart, deploy or S2 is authorized or performed. Final exact heads, CI and independent review are recorded in the issue and its linked final evidence. Do not interpret this source document as current production evidence.

## Selected solution

Replace the impossible full Actions-history/remote-settlement prerequisite with **two different proven boundaries**, without weakening at-most-once behavior:

1. Mark's fixed control produces `mark.watch-admission-fence/v2`, result `ADMISSION_DISABLED`, after an exact private repository/main/workflow check, at most one disable, and a fresh same-ID disabled reread. It makes no claim about historical logs, running handlers or remote completion. Old fail-closed v1 functions remain historical and are not invoked by the entrypoint.
2. Ops authenticates the fresh artifact and live disabled workflow, stops the exact observed baseline deployment/all replicas, waits for stopped state and verifies empty fixed fleet/allocation. Only then does it emit `LOCAL_WRITERS_FENCED_REMOTE_OUTCOMES_QUARANTINED` and admit a transactionally consistent completed locked backup.
3. Both candidate and compatible fallback now query the fixed drain workflow before **any** DB claim. Only `state=active`, correct path, positive workflow ID equal to the current run's workflow ID is admitted. Disabled/unknown/malformed/timeout deny. A job queued before disable, or delayed HTTP request arriving after restart, cannot bypass this fence.
4. Restore remains a separate owner-authenticated action after independent runtime acceptance; it returns only the prior enabled state. No failure, timeout, unknown effect or failed candidate automatically enables the driver.

## Why bootstrap does not require an already-installed server fence

Old baseline `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` commits run identity, then commits a per-effect `processing`/`sending` claim before sending the corresponding external request. Recovery and postpayment use their own committed note/attempt claims; they do not rely on the cursor lock. A request already past old preflight can still finish after workflow disable. Therefore disable alone is never accepted for backup: all old application instances must be stopped.

After confirmed process death, the process cannot issue a new external call. A native transactionally consistent snapshot taken afterward contains the committed pre-effect claim. A final result transaction already sent to PostgreSQL may be included atomically or omitted; in the latter case the prior claim remains `processing`, `sending` or `unknown`, never `received`/`pending` for a fresh attempt. No zero-client-connection claim or fabricated complete run history is required. Cursor commits follow awaited original commits, so a committed cursor cannot precede the originals it acknowledges.

A remote request may complete after process death and its result may not be saved locally. **Remote settlement is not proven.** This is an explicit bounded liveness/result-loss risk. Such attempts remain quarantined; an unknown publication may be read-reconciled by its exact receipt, never blindly POSTed again. No rollback restores an older database or erases claims. Full external exactly-once completion is not claimed.

The admission guard is first installed in the new candidate/fallback after the old fleet has stopped and the backup is complete. It therefore has no circular prerequisite. No preliminary application deployment, SQL trigger, migration, table rewrite, new secret or permission is required. The existing job token already has `actions:read`.

## Evidence requirements

| Requirement | Existing source evidence | Runtime predicate still required |
| --- | --- | --- |
| No new job admitted after restart | Fixed live workflow check before claim in candidate and fallback | Authenticated disabled Mark workflow, unchanged main/workflow identity |
| No old live writer | One effect only after durable claim; no delayed callback worker | Exact fleet/allocation stopped, no pending change/autodeploy, other Watch drivers OFF |
| Stable restore semantics | processing/sending/UNKNOWN never rearmed; unique run/recovery/postpayment journals | Native transactionally consistent backup/lock fixture and fixed DB/volume binding |
| Preserve originals/cursor/journals | Existing fallback PostgreSQL proof reused; new guard and interrupted-state restore tests added | Actual backup complete+locked and independently verified |
| Remote attempt completed | **Not known, not needed for writer-fenced snapshot** | Explicitly `remoteSettlementProven=false`; unknown preserved, no repeat |

## Exact fallback

Historical proven `7fd322c958cccf6828a3e8fcb00a075b2d4f1f46` remains immutable and is **not** selected by the new path because it lacks the restart admission guard.

Selected guarded successor: [`4827624955c9543bfae1b1377de8497956720ab9`](https://github.com/markmalina/gate/commit/4827624955c9543bfae1b1377de8497956720ab9), parent7fd. Relative to afe: the same four additive0116–0119 migrations plus only the workflow-admission guard in `app/infrastructure/watch_voice_actions.py` (blob `0f05c0ce04b1d4e2b473b0383fc231de59f9b94b`). No models, provider settings, transcription behavior or data rewrite. Ops now pins its new PostgreSQL restore proof `sha256:1b9e28f7100ae56acc37d3dbaf5f5b7346c9c1550425ec11a7b3fd787432d399`; an old green proof cannot admit this successor.

## Concrete installation and operation order — NOT executed

Targets remain project `972b6ab7-988a-421c-abfa-425451ad714f`, environment `a148b138-44b3-4349-9d4b-292880503055`, Gate service `099cbf8c-1d94-43fe-8714-90725bf93998`, PostgreSQL service `561d3da3-ddb5-4d3d-8fa2-c9454b0996bf`, volume `7d2de9ed-c63e-454a-811e-4f206f3dfef3`.

- Review/install the exact source-only Mark126/Ops414/Gate766 revisions named by the final issue receipt, using a separately approved installation. Release files stay OFF/PENDING in this work; existing drain YAML/binding and all existing secrets/permissions are unchanged. A release-only successor must pin the reviewed installed executor, Mark main, candidate current-main and the new guarded fallback/proof. Source preparation is not installation.
- Complete the previously outstanding native disposable backup/lock fixture and authorized fresh target/fleet/source/pending/flags/DB/volume observation. Do not repeat the already accepted auth test.
- Obtain the separate bounded S2 decision. Run fixed Mark stop on exact installed main (disable≤1, no cancellation or history-log inference). Preserve its original private artifact and priorEnabled.
- Existing Ops requester/owner digest-bound route verifies artifact and source pins, stops only the exact baseline deployment (all proven replicas), waits/observes empty fleet, rechecks Mark fence, then makes backup≤1 and lock≤1. Only complete+locked+rechecked backup admits candidate≤1. Normal budget20min, recovery28min, plan15min, admission30min.
- Candidate and guarded fallback reject queued/late jobs while disabled. Verify exact source/schema0119/flags/fleet/backup and independent receipt. Then separately restore only prior enabled state.
- Confirmed candidate failure: stop it; deploy guarded fallback482762≤1, keep schema0119 and all rows/journals, verify readiness, leave driver disabled until independent acceptance. Unknown deployment acknowledgement forbids another deploy. Backup/stop failure leaves a named safe-stop/UNKNOWN state; no automatic old unguarded app restart, DB downgrade or data restore.

Downtime starts at baseline stop and ends at verified candidate/fallback readiness. The20/28min budgets cap attempts, not guarantee restoration. The remote-result-loss risk above must be part of the separate S2 decision. If the baseline differs from afe, allocation is unknown, another writer is enabled, or the new proof fails, do not start.

## Validation scope

New tests exercise actual server admission before claim, workflow identity/state errors, cancelled handler with active transaction, durable pre-effect claims, original bytes/checksum, late remote success without a duplicate POST, new admission transport, prior-state restore, changed workflow/main, and Ops ordering. PostgreSQL CI additionally executes abrupt child-process death (`os._exit`) at the actual external-effect boundary and dump/restore of processing/sending/UNKNOWN. No real audio, STT, messages, provider or production database is involved.

Whole S1 remains pending native fixture/current protected facts and full independent acceptance. Gate736 stays open. S2 has not started.

Native acceptance must also prove that the pinned provider's stopped deployment
and empty allocation/fleet observation mean termination of ALL old application
processes, not merely stop-request acknowledgement or the beginning of graceful
shutdown. Fixed waiting time cannot substitute for this. The next native fixture
therefore covers stop-all completion semantics as well as consistent backup and
lock. Until that evidence is accepted, the release gate remains PENDING.

## Confirmed source proof

- Mark PR126: `f448bdb07efd97192e4a1b19117179d4abc3ef71`; 44 local Node tests passed, independently repeated.
- Ops PR414: `0079fc2f3f5048cb54fc0907364945779d89a6ca`; 154 local snapshot tests passed, independently repeated. Gate Watch action pins and both workflow-hash locks are updated together; Reply745 action revisions are unchanged.
- [Gate CI41590282789](https://github.com/markmalina/gate/actions/runs/41590282789) and [Quest41590282890](https://github.com/markmalina/gate/actions/runs/41590282890) succeeded on `6232e49221d5d84261434d634bce9e9d77e338df`. The corrected late-remote-completion test and final client pins are checked again at the final PR head; final run links are recorded in issue780.
- Actual fallback job125765478098: 11 passed, then a separate restored-database test passed. Disposable PostgreSQL17.11, no production actions. Artifact13209747366 ZIP SHA256 `ea36cf5c20d075a7f388a01fc7111b4a4722ae4ffd010dcf71762633f7efeb92`. The three adjacent files are exact extracted artifact bytes, independently compared with the downloaded ZIP.
- Fallback receipt SHA256 `4606b86e0f1fc31e893579cd2676535d93b5cafa1a126a45e00baaeb0753d4b0`; restore receipt SHA256 `1b9e28f7100ae56acc37d3dbaf5f5b7346c9c1550425ec11a7b3fd787432d399`; JUnit SHA256 `c20b54d793f68152ab58aa10a528474ab4132a5658c6f2a872f6cf65c6902310`.

The independent reviewer is a separate agent, not the implementation author. Its final exact-head source conclusion and final CI metadata are linked from issue780. Source acceptance does not attest any native provider behavior or authorize S2.

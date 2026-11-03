# Gate780 independent source assessment

Reviewer: separate `boundary_review` agent, 3 November 2026. The reviewer
inspected source and repository evidence, ran isolated tests, and did not
modify implementation files, call production/provider APIs, run live driver
control, merge, deploy, migrate, restore production data, process private
audio, invoke STT, or send messages.

**Decision: ACCEPT_SOURCE_BOUNDARY_METHOD.** This accepts the implemented
bounded method and its preparation package, not runtime readiness, the whole
S1, installation, or S2. **Whole S1 remains BLOCKED on native/current runtime
evidence and subsequent acceptance.** Final exact-head Gate and Ops CI are
now independently confirmed terminal SUCCESS; see the final appendix.

## Exact reviewed subjects

| Subject | Revision |
| --- | --- |
| Gate final source/client/tests, PR766 | `897b2f1b882326cdc77777f4c600d8087cfbcdb8` |
| Ops source/executor, PR414 | `0079fc2f3f5048cb54fc0907364945779d89a6ca` |
| Mark control, PR126 | `f448bdb07efd97192e4a1b19117179d4abc3ef71` |
| Guarded compatible fallback | `4827624955c9543bfae1b1377de8497956720ab9` |
| Historical old baseline | `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` |
| Earlier immutable migration-only fallback | `7fd322c958cccf6828a3e8fcb00a075b2d4f1f46` |

The reviewer independently fetched the final Gate and Ops Git trees and
verified local bytes against 13 relevant Gate blobs, 54 available snapshot
action files, and seven directly fetched Mark source/workflow/test blobs.
Manifests are `gate-reviewed-hashes.json`, `ops-reviewed-hashes.json`, and
`mark-reviewed-hashes.json`. Hash matching is distinct from claiming all
unchanged files were newly audited.

Final Gate app tree is `da554d37f4d89d336037273146b3d9fd92b6aea8`; Alembic tree
is `c3793c393b1c73dc19ce16a35063ef6aa520593d`. Both match the reviewed Ops
policy and the tested preceding Gate source. Whole-tree checks protect the
transitive dispatch/reconciliation code and migrations, not only the entry
guard. Truncation, missing/duplicate paths, type/mode or hash drift fail before
provider authentication.

## Safety reasoning and inspected implementation

The old baseline source was read at its immutable GitHub revision, including
Actions admission and claim, normal transcription/publication, recovery,
postpayment, and GitHub reconciliation. A cursor lock alone is insufficient:
recovery and postpayment bypass that coordinator. The selected method does
not rely on that lock or retained Actions history.

Before an old handler can issue a remote transcription or publication, its
effect claim is committed. After actual termination of all old processes,
no old handler can initiate another request. A transactionally consistent
backup taken after that termination contains the committed pre-effect claim.
A result transaction may be included atomically or omitted; omission leaves
`processing`, `sending`, or `unknown`, never a freshly retryable state. Cursor
commits follow awaited original commits. A remote request can still complete
after local process death; this method deliberately does not prove remote
settlement or lossless recovery of its result.

The implementation correctly separates the proofs:

- Mark emits `mark.watch-admission-fence/v2` / `ADMISSION_DISABLED` after
  fixed repository/main/workflow verification, at most one disable, and a
  same-ID disabled reread. It does not manufacture complete history or
  settled-operations evidence. The historical fail-closed v1 is not called
  by the new entrypoint.
- Ops authenticates the artifact and rereads the live disabled workflow and
  identity, including cached-artifact rereads. Only after exact old deployment
  stop, stopped observation, empty fleet and stable allocation does it emit
  `LOCAL_WRITERS_FENCED_REMOTE_OUTCOMES_QUARANTINED`. The receipt explicitly
  requires `remoteSettlementProven:false`; the independent verifier rejects
  a missing or altered boundary.
- The Gate candidate and guarded fallback both check the fixed workflow's
  live `active` state, exact path, and positive ID matching the run before
  any database claim. Delayed/queued requests arriving after restart while
  disabled cannot become writers. Unknown, malformed or failed state reads
  deny. The already-present job credential has `actions:read`.
- Processing/sending/UNKNOWN are preserved and not rearmed. Publication
  reconciliation is read-only and cannot infer permission to repeat a POST
  from absence of a search result. Recovery/postpayment permanent journals
  retain their caps. No database downgrade or old snapshot restore erases
  these claims.
- Restore is a separate owner-authenticated operation; prior disabled stays
  disabled, stale control/main/identity fail, and post-enable workflow ID is
  checked. No error path or fallback success automatically reenables the
  driver. Independent runtime acceptance before owner restore remains part
  of the installation/operation procedure.

The guarded fallback was independently read on GitHub. Relative to `afe`,
its exact diff is the same four additive migrations plus the 15-line
admission guard in `watch_voice_actions.py`, blob
`0f05c0ce04b1d4e2b473b0383fc231de59f9b94b`. Its parent is the old migration-only
artifact; that artifact is retained and is not selected by the new path.
The new guard is first installed after the old fleet stop and backup; no
preliminary server rollout, new SQL trigger, or fictitious preexisting fence
is needed. This resolves the bootstrap circularity at the source level.

## Independently executed and retrieved evidence

- Final Mark isolated suite: **44 passed** (`mark-tests.log`).
- Final Ops snapshot suite: **154 passed** (`ops-snapshot-tests.log`).
- Final Gate admission/late-completion suite locally: **8 passed, 2 skipped**
  because local PostgreSQL was absent (`gate-boundary-tests.log`). The
  publication fixture makes the synthetic remote receipt visible only after
  local task death, then verifies reconciliation without another POST.
- Final Gate client checks: **2 passed** (`gate-client-tests.log`). The shared
  workflow differs from the predecessor only in four Watch action pins; the
  reply745 pin is unchanged. Both locks bind workflow digest
  `sha256:6dcaaffb44347a33fbddaed5880e312a9e43e9165546dedce9ca10c70632c0aa`.
  The Watch profile digest also matches. The terminal verifier has no provider
  environment, Railway credential or private key.
- Guarded fallback PostgreSQL job
  [125765478098](https://github.com/markmalina/gate/actions/runs/41590282789)
  was independently retrieved as terminal **SUCCESS**. Its actual log checks
  out Gate harness `6232e49221d5d84261434d634bce9e9d77e338df` and exact fallback
  `482762...`, runs **11 passed**, including two `os._exit(73)` subprocess
  crashes with an active coordinator transaction and committed effect claim,
  then executes actual `pg_dump`/`pg_restore` and a separate **1 passed**
  restored-database test. This proves real disposable PostgreSQL behavior,
  not Railway-native backup behavior.
- Artifact `13209747366` metadata independently binds that run and harness
  SHA. ZIP SHA256 is
  `ea36cf5c20d075a7f388a01fc7111b4a4722ae4ffd010dcf71762633f7efeb92`.
  All three archived raw files match the ZIP byte-for-byte; JUnit has
  11 tests, zero failures/errors/skips. Restore receipt SHA256
  `1b9e28f7100ae56acc37d3dbaf5f5b7346c9c1550425ec11a7b3fd787432d399`
  matches the final Ops pin. It records PostgreSQL17.11, preserved restored
  processing/sending/UNKNOWN, no repeated effects, and
  `production_authorized:false`. Metadata and log excerpt are saved beside
  this review.
- Gate harness `6232...` exact-head
  [CI41590282789](https://github.com/markmalina/gate/actions/runs/41590282789)
  and [Quest41590282890](https://github.com/markmalina/gate/actions/runs/41590282890)
  were independently re-read as terminal **SUCCESS**. This is not a claim
  about the later final client/test commit's still-running CI.
- Ops `0079...`
  [CI41591294947](https://github.com/markmalina/ops/actions/runs/41591294947)
  and [Security41591294948](https://github.com/markmalina/ops/actions/runs/41591294948)
  were independently re-read as terminal **SUCCESS**. Actual test-job logs
  report **2551 Node tests and 2 Vitest tests passed**.

## Remaining gates and explicit limits

The package does not claim current runtime source, fleet, allocation,
configuration, database/volume binding, or installed control. A native
disposable fixture must prove that the pinned provider's stopped deployment
plus empty fleet means termination of every old process, not merely a stop
request or beginning of graceful shutdown. It must also establish native
transactionally consistent backup, completion, lock and readback semantics.
Elapsed time is not a substitute. These are explicit pending runtime gates.

Mark and Ops release files remain OFF/PENDING. Installation requires exact
reviewed versions and a separate bounded owner decision; subsequent S2 also
requires its own decision and current protected facts. Unknown stop/backup/
deployment outcomes never authorize another attempt or automatic restore.
The 20/28-minute budgets limit attempts and do not guarantee recovery.

Remote-result loss or delayed external completion remains possible and must
be included in the S2 decision. The method preserves non-retry safety; it
does not certify every remote effect as finished. The new issue's source
boundary/package is accepted within these limits. Gate736 and whole S1 are
not accepted as DONE, and S2 is not authorized by this review.

## Final exact-head verification appendix

The reviewer independently re-read final Gate PR766, Ops PR414 and Mark PR126:
their heads remain exactly `897b2f1b882326cdc77777f4c600d8087cfbcdb8`,
`0079fc2f3f5048cb54fc0907364945779d89a6ca`, and
`f448bdb07efd97192e4a1b19117179d4abc3ef71`. All are OPEN/UNMERGED, all
`auto_merge` values are null. Base branches remain Gate `86dbc0f429dce089bf733fc422f20f556535895b`,
Ops `d66dece8939398d5a149f9174c4b2fb4b98684f6`, and Mark
`9fc3a9402a75632d2e9a8d0f08f3697bfa82583b`. The source manifests above bind
the reviewed files; subsequent evidence archival does not change their
acceptance scope.

Final Gate [CI41591466943](https://github.com/markmalina/gate/actions/runs/41591466943)
and [Quest41591466908](https://github.com/markmalina/gate/actions/runs/41591466908)
were independently re-read via exact-commit lookup as terminal **SUCCESS**.
All three CI jobs (Windows, fallback, backend) are terminal SUCCESS. Actual
backend job125768931367 checked out exact `897b2f1...` and reports Ruff PASS,
mypy320 files PASS, **3217 passed, 2 skipped, 12 subtests passed**, additional
39+90+4+6 tests passed, and successful image builds. The mandatory synthetic
job125769507775 checked out merge `1e07094a4a63f2ec44dbf6aebf829f19c398a37d`
of `897b2f1...` into `86dbc0f...`; its actual log reports Ruff PASS, mypy320
files PASS, and **3210 passed, 9 skipped, 12 subtests passed**. Safe excerpts
are `final-backend-job-excerpt.txt` and `final-synthetic-job-excerpt.txt`.

Final fallback job125768931323 was also independently retrieved as terminal
SUCCESS. Its actual logs check out final harness `897b2f1...` and immutable
fallback `482762...`; **11 tests passed**, including the updated actual
late-remote-receipt fixture and the two abrupt PostgreSQL process-death
tests, followed by actual disposable dump/restore and **1 restored test
passed**. Final artifact13210546134 is authenticated by GitHub metadata to
run41591466943/head897b2f1. ZIP SHA256
`bbdff8dad07bd02732419ae457f38ecd71fbcfd88a555cd4278031f9a8c96999`
matches the downloaded bytes. Its two JSON files are byte-identical to the
previous accepted proof, preserving the final Ops restore digest. Final
JUnit SHA256 is
`f186feadbfe4cb6fec6eb62566bde4faafe28782f5d9f6c8ed5923947e446a79`,
with 11 tests, zero errors/failures/skips. The three archive files were
independently compared byte-for-byte to the saved final evidence. Metadata
and excerpt are `final-fallback-artifact-metadata.json` and
`final-fallback-job-excerpt.txt`.

**Final decision remains ACCEPT_SOURCE_BOUNDARY_METHOD, with exact source
and CI validation complete.** No remaining source defect was found in this
bounded review. Native stop-all completion, native backup/lock/restore
equivalence, fresh protected facts, reviewed installation and separate S2
authority remain unfulfilled runtime gates. Source acceptance does not
change those gates or claim whole-S1 completion.

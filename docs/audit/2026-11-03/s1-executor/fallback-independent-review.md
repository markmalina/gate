# Independent fallback acceptance

Reviewer: separate `fallback_review` agent, read-only review and evidence archival.
Scope: Gate S1 compatible fallback and populated disposable PostgreSQL fixture;
not complete S1, production readiness, Mark control or Ops cutover acceptance.

## Decision

**ACCEPT — source plus actual disposable PostgreSQL runtime proof.**

Reviewed test source: `ea92973c579de3f802323f9811ca32fed070118f`,
`tests/test_watch_voice_baseline_fallback_postgres.py`,
blob `0e99ef851ef4c01e3668f91320e82787c90b5db7`.
Fallback artifact: `7fd322c958cccf6828a3e8fcb00a075b2d4f1f46`,
exact baseline parent `afe0c6b8519381a0007c0dcc1c145207b5d54a3f`.
GitHub compare independently confirmed only four added migration paths0116–0119;
application/config/models are unchanged from the historical baseline.

The UUID correction matches the actual old worker's UUIDv5 formula.
Initial review identified missing replay testing after restore; the published
fixture now exercises old run321 and fresh run323 encountering UNKNOWN after
restore, with zero synthetic publication/provider effects and unchanged state.

## Actual final checks

- [CI41525798415](https://github.com/markmalina/gate/actions/runs/41525798415), original attempt: SUCCESS.
- [Fallback job125553081900](https://github.com/markmalina/gate/actions/runs/41525798415/job/125553081900): exact candidate checkout ea92973 and exact fallback checkout7fd322c; PostgreSQL17.11; populated0115→0119 old readiness/Actions 1passed5.73s; pg_dump/pg_restore successful; restored readiness/replay/UNKNOWN 1passed1.30s.
- [Backend125553081679](https://github.com/markmalina/gate/actions/runs/41525798415/job/125553081679): Ruff PASS, mypy320files PASS;3204passed/2skipped/12subtests; additional39+90+4+6 tests PASS; migration, schema drift, disposable restore and both container checks PASS.
- [Quest41525798407](https://github.com/markmalina/gate/actions/runs/41525798407): SUCCESS. Quest job125553082186 passed40+192+30 tests and mypy320files.
- [Mandatory synthetic merge125554635999](https://github.com/markmalina/gate/actions/runs/41525798407/job/125554635999): SUCCESS, merge cfa1e479 of ea92973 into86dbc0f; Ruff/mypy PASS,3197passed/9skipped/12subtests.
- Windows125553081975 SUCCESS, native DPAPI1passed/7deselected and preflight3passed.

The reviewer fetched actual job logs, not just run summaries. No rerun,
production request, source mutation, model call or publication was performed
by the reviewer. Production facts and S2 permission are not inferred.

## Downloaded evidence

[Artifact13180056569](https://github.com/markmalina/gate/actions/runs/41525798415/artifacts/13180056569)
is bound by GitHub metadata to exact ea92973, PR766 branch and run41525798415.
Downloaded ZIP1528bytes SHA256
`84b73892fa7e7f704a6c28d2aef6849581a732da4a4dfd57e772d457f8ac37f3`
matched provider metadata and job log. All three raw files were read and archived:

| File | SHA256 |
| --- | --- |
| ea929-watch-baseline-fallback.json | fd6961af53f0eac01c30946bc44ba58702aef0a1cf412705755f13aecf7ed344 |
| ea929-watch-baseline-fallback-restore.json | 5826b13f2b27586680579a0b42d4d431ce37097ecbdb0a4a18dc1219c421e42a |
| ea929-watch-baseline.xml | 2501445462c72f5c930f81a14ab54660e8ad69012496d22a673526ecb46a401e |
| fallback-ea929-fixture.log | fb396090de0786f82bfc9fdd019f62dbac520b6f79d5727284cb600b2c69ac58 |
| fallback-ea929-backend.log | 49d70ad767a51136149ddb7111d49632af5fcf8bbf7131fe179294b0a536868e |
| fallback-ea929-quest.log | bae181b2c24ea7c58b559adbfddcb7350a18b06339d6a669407e5b0ab5dc7eb7 |
| fallback-ea929-synthetic-merge.log | d5948823db4bedd64d13337ef0a8143dcc55f6b4a90ae680598e4e4f2d85de70 |
| fallback-ea929-windows.log | 48e93bc1c58e49dd7e5a63fba42a50675bf0e464efd05185e1ead41d257a4536 |

The two JSON receipts have resultACCEPT, scopeDISPOSABLE_SYNTHETIC_ONLY,
exact artifact/baseline/schema, external_provider_calls0 and
production_authorizedfalse. Restore receipt asserts actual restored replay
and UNKNOWN preservation. JUnit has1test,0failures,0errors,0skips.
Archived logs preserve GitHub masking; known GitHub/OpenAI credential and
private-key prefix scans found no match. Local PostgreSQL credentials and
test-only synthetic values are not production secrets.

## Retained failed precursor

3329b95 had a genuinely successful fallback run41525079316/job125550611402
and artifact13179686461, but backend125550610971 failed RuffI001. This is
retained as history, not accepted fullCI. ea92973 changed only import order
and subsequently passed the exact final checks above. Original dd823a5 was
source-only; it is not described as the executed accepted SHA.

## Limits

The fallback is verified against synthetic populated databases and stubbed
external HTTP. It has not been installed, merged into main or deployed.
This proves old-app compatibility, readiness, Actions, originals/hash,
cursor/watermark, old run journal, UNKNOWN preservation and replay after
restore within the stated fixture. It does not prove current production DB,
fleet, pending changes, backup capability, driver quiescence or Ops recovery.

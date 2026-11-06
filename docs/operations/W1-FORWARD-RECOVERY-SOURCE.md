# W1 forward-recovery source candidate (not deployed)

Task: https://github.com/markmalina/gate/issues/800
Source parent: e319328af87ccb5444e6ea505697250d5661d57f.
Required parent tree: e17b705e67747f52a937d9f2574b157600c0db9d.
Application/build LKG: afe0c6b8519381a0007c0dcc1c145207b5d54a3f.

This is R(final-PR-tree), not R(actual-merged-main). No actual merge SHA,
recovery CI or installed recovery is claimed. PR784 is stale source material;
its parent and expired grant are not authority for this candidate.

The bounded manifest restores these seven files byte-for-byte from LKG:
Dockerfile; app/application/personal_telegram_status.py;
app/infrastructure/watch_voice_actions.py; app/personal_telegram/auth_check.py;
app/personal_telegram/local_runtime.py; pyproject.toml;
tests/test_watch_voice_actions.py.

It removes three cloud-only files absent from LKG:
app/personal_telegram/cloud_check_cli.py; app/personal_telegram/cloud_session.py;
tests/test_personal_telegram_cloud_session.py.

Restoring the legacy Watch actions drain removes queued-first and the W1 fence.
The two W1 Settings fields, app/infrastructure/watch_voice_w1.py and its tests
are removed together. The prior W1 guard document remains historical source
evidence, not a claim that this recovery contains that guard. This candidate
does not undo all of PR797: the public-compatible release workflow/action,
source/receipt verifier and tests/ops, pins and all four S1 OFF gates are kept
byte-identical to the final W1 parent. It does not restore private Ops uses.

No models, migrations, schema, original bytes, journals, processing/budget claims,
reservations, UNKNOWN state, outbox or provider policy/endpoint/model/retry is
changed. No data cleanup, reset, retry, STT, publication or send is authorized.
Existing UNKNOWN requires reconciliation; a recovery source is not its reset.

## Required future admission order (not performed here)

1. Accept this exact recovery source/tree. Before mechanical actual-parent rebind,
   prove actual merged main tree equals e17b705e67747f52a937d9f2574b157600c0db9d.
   A different tree means STOP and a new bounded comparison, not blind apply.
2. Obtain fresh protected facts for exact fleet/full revision/schema, credentials
   and target boundaries, autoDeploy/quiescence and historical UNKNOWN. Both
   GATE_WATCH_VOICE_MARK_OIDC_ENABLED=false and GATE_WATCH_VOICE_ENABLED=false
   are mandatory, with no in-flight Watch work. W1 alone cannot disable legacy
   drain because this recovery has no W1 fence. Hold both drivers OFF throughout.
3. Rebind the same accepted tree/delta as a forward child of the actual accepted
   main only after the equality check; review exact parent/head and run the
   necessary exact-head CI under its own publication/CI admission. No future
   commit, run or result exists merely because this patch is reproducible.
4. Use only the registered owner-gated Gate-local recovery release, with a new
   exact owner grant and approved source=executor=workflow SHA. Do not replay an
   expired PR784 grant, issue a blind second deployment, or reset UNKNOWN.
5. Independently verify deployed full fingerprint/health/fleet/schema while both
   Watch drivers stay OFF. Record observed recovery or STOP; do not enable a
   driver or perform the selected W1 processing as an automatic continuation.

The scoped Gate M prefix/status observation is not full fleet, schema, autoDeploy,
selected-note or grant evidence. Those facts remain future protected checks.

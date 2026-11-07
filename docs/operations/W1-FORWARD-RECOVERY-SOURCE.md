# W1 forward-recovery source candidate (not deployed)

Task: https://github.com/markmalina/gate/issues/836
Accepted historical slice: PR808, head 657b7581f4e7bde5638b93acb7607b702d106082.
Proposed release parent (PR835): 44531dc06f1a0b3438f70b83de110dffa525b3b1.
Required release tree: ff461b59716da761a1af55bf3a79f514d55f3965.
Application/build LKG: afe0c6b8519381a0007c0dcc1c145207b5d54a3f.

This is R(proposed-release-head), not R(actual-merged-main). No actual merge SHA,
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
byte-identical to PR835. All 22 accepted maintenance paths/blobs/modes, both
literal OFF workflow guards, HOSTED_SOURCE_GUARD=OFF, pins and the legacy public
Gate deploy prefix are retained unchanged. It does not restore private Ops uses.

Recovery startup forces both Watch drivers to literal false, overriding inherited
true. The isolated actual-shell regression covers unset/empty/false/true OIDC,
ordinary Watch, PORT/argv, other exports and subprocess failure propagation.
The PR835 forward release retains its accepted explicit-true semantics unchanged.

No models, migrations, schema, original bytes, journals, processing/budget claims,
reservations, UNKNOWN state, outbox or provider policy/endpoint/model/retry is
changed. No data cleanup, reset, retry, STT, publication or send is authorized.
Existing UNKNOWN requires reconciliation; a recovery source is not its reset.

## Required future admission order (not performed here)

1. Accept this exact recovery source/tree and the exact PR835 forward source.
   A separately admitted repository-only integration may ordinary-merge only
   exact reviewed PR835. Read back resulting actual main: its tree must equal
   ff461b59716da761a1af55bf3a79f514d55f3965 before any actual-parent rebind.
   A different tree means STOP and a new bounded comparison, not blind apply.
2. Obtain fresh protected facts for exact fleet/full revision/schema, credentials
   and target boundaries, autoDeploy/quiescence and historical UNKNOWN. Both
   GATE_WATCH_VOICE_MARK_OIDC_ENABLED=false and GATE_WATCH_VOICE_ENABLED=false
   are mandatory, with no in-flight Watch work. W1 alone cannot disable legacy
   drain because this recovery has no W1 fence. Hold both drivers OFF throughout.
3. Only under a separate exact current grant, mechanically publish the SAME
   accepted recovery tree with sole parent equal to actual merged main after
   the equality check; review exact parent/head and run the necessary new-head
   CI under its own publication/CI admission. No future
   commit, run or result exists merely because this patch is reproducible.
4. Use only the registered owner-gated Gate-local recovery release, with a new
   exact owner grant and approved source=executor=workflow SHA. Do not replay an
   expired PR784 grant, issue a blind second deployment, or reset UNKNOWN.
5. Independently verify deployed full fingerprint/health/fleet/schema while both
   Watch drivers stay OFF. Record observed recovery or STOP; do not enable a
   driver or perform the selected W1 processing as an automatic continuation.

The scoped Gate M prefix/status observation is not full fleet, schema, autoDeploy,
selected-note or grant evidence. Those facts remain future protected checks. Matching source pair is not live
readiness: runtime/fleet/fullSHA/schema/settings/autoDeploy/pending/writer-inflight,
ledger ACL, selected-note and historical-budget facts remain UNVERIFIED.
The #832 owner packet/activation diff remains UNAPPLIED; no automatic rearm,
state-branch seed/ACL, activation, quiescence, deployment, data or STT is granted.
HOLD_NO_AUTORESTORE, S1 NOT_ACCEPTED and AQUB frozen remain in force.

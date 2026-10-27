# Watch Voice early Inbox card — source stage of #770

Execution: `watch-voice-autonomous-20261015T1023Z`, existing #736 / PR766.

`GATE_WATCH_VOICE_EARLY_INBOX_ENABLED` defaults to false. This source change
does not enable a driver, deploy, run migrations, access real audio or create
real Mark issues. Previous diagnostic/preflight acceptances do not cover it.

When explicitly enabled by a separately reviewed release, durable discovery
creates a pending metadata-card journal before downloading/probing audio or
calling STT. Both drivers use this same journal. New cards are published before
batch archival. The private Inbox issue records the original Dropbox identity
and protected `watch-voice-file:<event_id>` selector, available once Gate has
archived the original. It creates no public shared link. Missing STT credentials
leave the original queued with an explicit error and do not prevent intake.

The journal freezes the exact body/hash and writer before a single POST claim.
Sending/unknown outcomes permit read reconciliation only; absence of a search
result never permits another POST. A fenced receipt binds an archived note to
the same issue before STT. Full transcript parts are comments in that issue,
with wire markers1..N; the existing `next_part` counts text parts0..N.
Legacy publications retain their original marker/part semantics. Publication
writer snapshots allow reconciliation after a serial driver change; enabling
two drivers simultaneously remains forbidden.

Metadata receipt time is separate from first/full text publication time.
Scoped operational status exposes card states, safe error counts and measured
detection-to-card aggregate latency. Metadata issue text is a frozen historical
snapshot; later full-text comments contain transcription times. Automatic issue
status/error refresh, real Work claim/submit/accepted-result coordination and
private Mark readback acceptance remain subsequent stages. The proposed120s
Work waiting threshold is not approved, configured or enabled.

Migration `0117_watch_voice_early_inbox` follows the unchanged Watch
`0116_watch_voice_observability`, adds card journal columns and a nullable text
publication writer, and leaves original bytes, transcripts and cursor untouched.
Historical rows default to legacy, without backfill. Downgrade refuses to drop
active card/publication receipts. It is not an automatic live rollback route.

PR747 has its separate unchanged `0116_reply_dialog_versions`. Integration must
preserve both0116 IDs/bytes, join the latest Watch0117 and reply0116 with one
merge revision, update reply migration ceiling/helper/tests and common API and
Railway configuration together, then use one serial reviewed release. This PR
does not mutate PR747 or execute any deployment/config/migration operation.

Acceptance still requires an exact new commit and CI, independent source review,
then separately admitted runtime checks: one early real private Mark card,
complete text in that same card, measured delays and restart/duplicate recovery.
Synthetic tests are implementation evidence only.

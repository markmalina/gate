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

## Protected current status for Work

`gate_get_reply_context(task_id="watch-voice-status:<event_id>")` observes the
current source and note journals in one scoped SQL statement. It accepts one
exact UUID, including before a Note exists. It keeps the existing owner
workspace and Unclassified-project authorization; it cannot choose a foreign
Dropbox account or fall back to another recording. A missing source is an
explicit scoped not-found error. Legacy archived notes without a discovery
row remain readable. Conflicting source/revision/size or issue receipts fail
closed instead of combining evidence from different recordings/cards.

The `watch-voice-status.v1` response separates source/download, early-card,
original, transcription, text publication, and their timestamps/errors. The
legacy Note has one shared error for STT and text delivery; `note_last_error`
preserves that distinction rather than guessing the phase. The response
includes complete stored-text UTF-8 length, character count, SHA-256 and exact
wire part count, without returning text. Part completeness requires the durable
delivered state, issue receipt, timestamp and all expected text parts. This is
journal evidence; `external_content_verified=false` does not claim fresh
GitHub readback or accepted Work text. Initial metadata-card receipts are not
counted as transcript parts. The frozen issue body remains an initial snapshot;
this selector provides current status on demand rather than editing that body.

This read temporarily suppresses session autoflush and leaves pending, dirty
and deleted ORM objects and transaction ownership with the caller, on success
and on conflict. It never flushes, commits or rolls back that session. It
performs no external calls, claims, retries, downloads or writes. It
does not select original audio bytes or the frozen issue body. It reads stored
text internally to compute the exact UTF-8 digest and parts. Original byte
length is checked in SQL; matching length and the stored hash do not prove
integrity. `archived_unverified` supplies the existing protected file selector,
whose retrieval separately verifies the original hash/size. Before archive or
on a size mismatch that selector is absent. No public URL is produced.

`coordination.work_claim_submit=not_implemented`, `accepted_result=null`,
`retry_authorized=false` and `instruction_execution=not_observed` are explicit.
This source slice establishes the read contract; it does not implement Work
claim/STT/submit/accepted CAS, live issue refresh, a waiting threshold, or runtime
acceptance. Those remain the next bounded stages in #770. Existing full-text,
file, list and sources selectors retain their contracts.


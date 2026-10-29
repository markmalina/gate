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
status/error refresh, a public Work claim/submit tool and actual Work STT
remain subsequent stages. Internal result coordination is described below;
its source tests do not establish live private Mark acceptance. The proposed120s
Work waiting threshold is not approved, configured or enabled.

Migration `0117_watch_voice_early_inbox` follows the unchanged Watch
`0116_watch_voice_observability`, adds card journal columns and a nullable text
publication writer, and leaves original bytes, transcripts and cursor untouched.
Historical rows default to legacy, without backfill. Downgrade refuses to drop
active card/publication receipts. It is not an automatic live rollback route.

PR747 has its separate unchanged `0116_reply_dialog_versions`. Integration must
preserve both0116 IDs/bytes, join the latest Watch0118 and reply0116 with one
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

The `watch-voice-status.v2` response separates source/download, early-card,
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

`coordination.work_claim_submit=internal_only_not_exposed` distinguishes the
internal service from an available public Work tool. Status includes the result
phase, epoch, expiry/start/prepared/saved times and safe error. `accepted_result`
is present only after recorded full readback/CAS and consistent current Note
identity/text/parts. Its timestamp proves past acceptance; status performs no
fresh GitHub check. `retry_authorized=false` and instruction execution remains
unobserved. V2 replaces the unreleased V1 source contract under the same selector;
existing full-text, file, list and sources selectors retain their contracts.

## Internal Work result journal — source stage

`GATE_WATCH_VOICE_WORK_RESERVE_ENABLED=false` gates new internal claim/start/submit
commands. It does not expose a new MCP write tool, execute STT, authorize a
provider, create a schedule or deploy a driver. Commands bind the configured
owner/account, exact source UUID/revision/audio SHA and saved early issue. A
future-only claim also requires the existing cursor cutover. Claim verifies
archived bytes; it admits only an unprocessed Note with zero Gate attempts and
no existing transcript or publication part. Active/unknown Gate work is BUSY,
not cancelled or retried. In-flight takeover is outside this initial slice.

The existing Note `received → processing` CAS is the common dispatch barrier.
Gate first locks and re-reads the result journal, then claims processing; Work
claims that same Note before committing its epoch. Only one path can win.
Gate honors existing claimed/started/unknown/prepared/saved/accepted fences
even if new Work admission is disabled. Unstarted expiry releases the Note;
started expiry quarantines it as unknown, without authorizing another STT.
Lease lifetime defaults to1800s (range60..1800s), below the existing1h stale
processing quarantine. It is engineering fencing time, not the unapproved120s
waiting threshold or authority for an external provider call.

`claim_work`, `start_work` and `submit_work` are internal application services,
not HTTP/MCP endpoints. Their trusted adapter must supply the authenticated
actor and a cryptographically random opaque nonce. The DB stores actor/token
hashes, immutable epoch identities and current phase. Repeated nonce/start/
same-text submission is idempotent; stale actor/token/epoch or changed source
fails closed. Text remains exact UTF-8 (up to100000characters/400000bytes).
Only the first start CAS reports `start_recorded_now=true`; replay reports false
and must not be treated as a fresh processing dispatch. Neither response grants
provider authority or proves an external call. A new claim also reconciles
expired prior leases, so unstarted recovery does not require a running driver.
Prepared text and its digest become immutable and use the same early issue's
normal outbox. Work provenance is separate from model identity; external method,
model and cost are unverified, and no Gate provider usage is fabricated.
Work timestamps mean the moment Gate received/prepared the submitted text;
external recognition time remains unconfirmed. Issue text, full-text receipts
and V2 status identify that time basis explicitly.

Every acknowledged text-part advance atomically records its writer, exact body
digest, issue target and receipt time. A stale/tampered selected payload is
rejected before publication I/O. Lost ACK is reconciled by the existing exact
body/writer rule; absence never authorizes repost. Serial driver/writer switches
retain each part's original writer. Metadata is not a text part.

`prepared` means durable candidate, `saved` means complete outbox receipts,
and `accepted` requires full same-issue exact readback followed by a fresh
Note lock/epoch/digest CAS. Missing, forged, duplicate or changed receipts keep
acceptance pending without starting STT. Persistent receipt recovery is bounded
to10notes/cycle and rotates by last attempt. Actions includes receipt recovery
in its ordinary one-note selection and still publishes at most one text part
per fresh authorized run. It never adds a second processing path or schedule.

Additive `0118_watch_voice_work_results` follows unchanged0117. It adds three
empty journals and `transcript_origin=gate` for existing Notes, preserves old
media/text/cursor and refuses downgrade with any journal/provenance to lose.
No existing source is admitted or backfilled. Schema installation requires a
separate reviewed release with the latest integration/profile; old preflight
acceptance and spent grants do not cover this source.


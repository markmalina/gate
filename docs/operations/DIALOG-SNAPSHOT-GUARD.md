# Reply context freshness — integrated source for #745

Sprint 2 integrates the existing PR747 with released media main
`8d17d0b40a86c856bec1072633c721212a34a9dc`. This is source preparation,
not production installation or acceptance of sprints 3–8.

## Read, prepare, preview, approve

1. Read `gate_get_reply_context` without a historical cursor. Review messages
   **and** `dialog_events`. Require `context_ready=true` and a non-null
   `context_snapshot`. The snapshot binds workspace, account, project,
   conversation, connection/chat revisions and projected message facts.
2. Pass the exact token as `expected_context_snapshot` to text, document or
   media preparation. Photo, album, rich_photo, rich_slideshow and rich_collage
   all preserve it in the immutable draft/package/preview and package digest.
3. Show the exact normalized payload, image bytes, captions, recipient and
   hashes for approval. Send/queue cannot replace the saved token. Approval
   is not granted by a snapshot, cursor, preview or refusal response.
4. A stale/missing/foreign token refuses new delivery. Re-read and review the
   changed context, create a **new** draft/staging key, then obtain the required
   exact approval. Never refresh an approved draft/package in place.

`context_changed`, `context_snapshot_required` and `context_unavailable` return
`sent=false` and scoped `current_context`; without inbox-read permission, or if
safe lookup fails, context is null. Media errors use the same contract.

## Bounded readset and continuation

Historical messages may exceed the ordinary history page; that fact alone is
not a permanent veto. All **unprojected** accepted raw events must fit the
current reviewed raw page with complete text. Missing raw events or incomplete
raw text produce `raw_readset_incomplete` / `raw_text_truncated`, no usable token.
Explicit durable ingress-gap markers produce `known_ingress_gap`. Hidden project
scope and changes during assembly also refuse readiness.

`dialog_events_next_cursor` reads older raw events; an event's
`text_next_cursor` reads its next 4096-character chunk. Pass either in
`dialog_events_cursor`. Cursors are bound to the task and current dialog
fingerprint and are re-scoped on every query. They are read selectors, **not
signed approval capabilities**. Invalid, foreign or changed-state cursors deny.
Continuation/historical pages never grant a sendable snapshot, even after the
last chunk. Inspect them as needed, allow normal projection to catch up, then
perform a fresh no-cursor read. An unavailable or stopped projection remains
explicitly unavailable, rather than falsely ready. This conservative behavior
avoids introducing a second read-approval state machine in this sprint.

The prepare/reservation/final-admission consumers independently enforce raw
readset completeness and known-gap refusal. Extracting a fingerprint from a
cursor, or computing it, cannot bypass those checks. A cursor is not evidence
that its holder reviewed any page.

The known-gap interface consumes the latest account/chat-scoped AuditLog action
`reply_dialog_ingress_gap` or explicit `reply_dialog_ingress_restored`; a read
never clears it. Actual ingress-gap producers/continuity observation are a
sprint 3 integration gate. Worker heartbeat is not substituted for continuity.
Voice envelopes invalidate the revision without transcription or model calls.

## Replay and media preservation

A staging key is immutable, including the original context token, bytes, captions
and deadline. Reusing it with another token refuses. An unattempted stale package
cannot be prepared again as current. Preview only returns the saved package and
never changes its approval.

Completed sent/failed/unknown attempts return their original receipt before a
new freshness check and never reserve budget or send again. Historical media
packages without a dialog token remain replayable with their original binding;
unsent legacy drafts need a new review/preparation. No snapshot backfill or
UNKNOWN cleanup is performed.

The integrated sender retains media normalization, exact order/cardinality,
private bytes, package hashes, execution caps, provider-start classification,
partial/ambiguous acknowledgements, and durable receipts. Existing media-in-series
restrictions and text/document formatting hashes remain intact.

## Admission and remaining sequence work

Raw Business receipt acceptance atomically bumps the connection/chat revision
before asynchronous projection/STT. Reservation and final admission use the same
revision rows. An already committed event before final admission blocks the old
reply. Another Work's reservation invalidates a shared old review.

No SQL transaction spans provider HTTP. Events not yet delivered to Gate, or
accepted after final admission/while HTTP is in flight, cannot retroactively
cancel an already started send or album. Full cross-process/adapter race
acceptance belongs to sprints 3/5, not this source integration alone.

Existing own sequence progress uses confirmed `reply_sequence_snapshot_advanced`
receipts. It still conservatively stops on an own webhook echo during HTTP.
Sprint 4 must implement exact own-echo correlation and immediate durable tail
stop/status/notification, without importing the entire PR720 G2/parallel stack.
The four obsolete pending-task/age vetoes stay removed: an already reviewed
pending sibling is not a new event. New same-text/new-ID events still invalidate.
Legacy direct admin send routes refuse and direct the operator to guarded MCP;
media/snapshot edit routes cannot relabel saved approval.

## Migration, readiness and release boundary

Preserve both authored revisions unchanged:

- `0116_reply_media_packages` from `0115_watch_voice_post_payment`;
- `0116_reply_dialog_versions` from that same parent.

The no-DDL `0120_reply_media_dialog_join` joins those two branches into one head.
The fixed `python -m app.worker.reply_cutover` helper admits only the released
media baseline (delivery OFF) or exact joined head (idempotent activation).
It refuses unknown/multiple schema heads, unavailable DB and reserved/sending
attempts, upgrades only to the exact joined head and checks again. UNKNOWN
receipts and all media/Watch/queue data are preserved.

`GET /admin/ops/reply-cutover` reports redacted schema/status counts and the
process flag; it proves neither physical DB identity nor remote fleet drain.
Historical generic fixture workers, fixture HTTP operations and packaging are
excluded from this product candidate; their immutable source remains at old
PR747 SHA `b975475c1c6c81035e38d9bd13a40d777933e83c`.

Do not merge this migration before a fresh protected `autoDeploy=false` proof
and accepted exact Gate-owned cutover contract. Main's ordinary Gate-local deploy
uses an ephemeral workflow token, but does not prove backup/drain/cutover.
The snapshot workflow remains QUARANTINED. Old Ops pins, private grants and old
metadata observations are not fresh release authority; AQUB remains frozen.

Sprints 5–8 still require the exact-head independent verdict, populated schema
and restore evidence, accepted isolated native rehearsal, physical target/DB/fleet
identity, admission OFF/all-old-writer stop, completed locked bound backup,
exact-source guarded deploy with delivery OFF, verification, same-SHA enable and
independent readback, then connected MCP/canary/product acceptance.
Old media-only binary is not assumed compatible with the joined schema:
recovery first closes delivery and uses a tested same-schema rollback/fix-forward.
Production downgrade/restore and blind retry of UNKNOWN are not default recovery.

Current plan: <https://github.com/markmalina/gate/issues/745#issuecomment-6789115422>.
Regulation: <https://github.com/markmalina/ai/blob/main/WORK-RULES.md>.

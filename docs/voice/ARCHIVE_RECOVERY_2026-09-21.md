# Owner-authorized archive remainder recovery

Execution: `gate-archive-recovery-20260921T094500Z`, issue #685.
Owner's current request: restore all missing audio transcriptions, including
large files and old recordings. Long-audio stage #690 is deployed and accepted.

## Evidence and exact scope

Four retained voice archives lack transcripts: August 1/2 transport-unknown and
reservation-replay failures, and two September 18 response-unknown failures.
The exact IDs, owner account and project are pinned in migration 0106 and the
runtime claim. Their original audio availability was verified through the
owner-only feed. This is one new explicitly authorized attempt per source,
not an automatic retry of an unknown provider operation.

The whole call inventory introduced in #690 also exposed five incomplete rows
outside the recent-20 window. Two are apparent copies of the restored July long
recording. Three are apparent copies of one 515,801-byte response-unknown call.
Migration 0106 grants one attempt for the oldest exact short-call archive.
Four exact duplicate rows may reuse a stored successful transcript only when
workspace, project, source account, SHA-256, byte size and complete audio bytes
match. A mismatch leaves the row untouched and visible in diagnostics.

## Bounds and effects

- Existing server-side mini transcription model, endpoint and $1 monthly cap.
- At most five new source attempts, at most 20 parts per source, zero automatic retries.
- Voice grants are consumed and committed before provider I/O. A crash or
  uncertain response cannot repeat the grant. Call recovery uses its stable
  isolated v14 reservation, also protected against replay.
- Original unknown budget reservations are preserved. New successful work is
  settled separately; duplicate transcript reuse makes no provider request.
- Voice recovery only writes the archive transcript and local summary. It
  does not requeue receipts, outbox events, Alpha turns or Telegram messages.
- Whole-archive owner diagnostics count complete and missing transcripts and
  return bounded metadata for missing rows, without audio or transcript text.

## Verification and rollback

Regression tests cover consumption before provider I/O, no repeat on the next
poll, no message requeue, exact PostgreSQL grant scope and preservation of
existing/foreign transcripts on upgrade and downgrade.

Baseline / rollback: `c659385fba589f29e22d45818a685a1af2bc2a5e`.
Revert code to that deployed revision to stop the new voice-grant consumer.
Downgrade 0106 withdraws only unused grants; it does not erase recovered text,
change historical ledger outcomes or undo successful duplicate reuse.

Production acceptance is pending deployment and owner-feed verification. A
remaining unknown billing entry is not equivalent to a missing transcript and
must not be rewritten just to make a status counter green.

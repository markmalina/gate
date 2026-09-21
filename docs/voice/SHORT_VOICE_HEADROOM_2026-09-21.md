# Minimum request headroom and the last archived voice

Execution: `gate-short-voice-headroom-20260921T102400Z`, issue #685.
Baseline / rollback: `da18ccac0d730a058e74cd98defdb1b9b75b5d27`.

The full production archive after #692 has 195 call rows with zero requiring
attention (192 transcripts, three byte-identical no-recognized-speech rows),
and 328 voice archives with one remaining source (324 transcripts, three known
no-speech results, one unresolved 1-second / 7,110-byte voice).

The existing duration-only estimate reserves just 100 micro-USD for a one-second
request. If returned usage exceeds that estimate, SQL settlement marks the
reservation unknown and raises. The worker then loses the returned result and
reports a generic failure. The observed final source failure is consistent with
this path; a narrowly scoped read-only count of its exact prior reservation's
overflow error is added to confirm the production cause, without exposing keys.

## Changes and bounds

- Reserve at least 5,000 micro-USD per provider part, within the unchanged
  USD 1 monthly cap. This is temporary headroom; settle only actual usage.
- Keep budget denial before provider I/O. Never increase the monthly limit.
- Return a sanitized explicit unknown result for settlement overflow, avoiding
  a generic worker failure and preserving the at-most-once decision.
- Migration 0108 authorizes only the one exact remaining owner voice, with
  fixed account, project, size and duration and no existing transcript/summary.
- Consume the generation-5 grant before I/O; use a dated distinct reservation.
  One source attempt maximum, no automatic retry, no receipt/outbox requeue,
  no Alpha routing/delivery. Other completed/no-speech records are untouched.
- Preserve all prior unknown reservation evidence, including any overflow.

Regression tests cover minimum headroom vs actual settlement, denial under the
hard cap, explicit overflow outcome, consumed grants, exact PostgreSQL scope
and downgrade preservation. Production acceptance requires zero sources
requiring attention across the complete scoped call and voice archives.

Rollback withdraws unused 0108 authorization and restores the baseline. It does
not erase recovered text, known no-speech results or historical budget records.

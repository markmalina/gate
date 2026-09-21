# Valid empty transcription results and final source classification

Execution: `gate-no-speech-recovery-20260921T100300Z`, parent issue #685.
Baseline / rollback: `28145e8724d7fa6a7fc2713cf3a03e70736ff88a`.

## Defect

The adapter rejected an empty text string before validating returned usage.
Consequently it treated a valid no-recognized-speech result as an unknown
provider outcome. The same behavior discarded an otherwise useful long
recording whenever one of its parts returned no words.

An empty response is now accepted only with a valid usage object and consistent
nonnegative token counts. Malformed JSON, absent/non-string text, unknown usage
or transport errors remain unknown. A valid zero-token empty result is settled
at zero cost. Nonempty text with zero tokens remains invalid.

Empty parts contribute their usage and leave other part text intact. If all
parts are empty, no transcript is invented and no Alpha routing is permitted.
The result is a terminal `telegram_voice_no_speech` reason with the existing
rejected status. The owner feed shows an explicit no-recognized-speech outcome;
recovered sources also have a local explanatory summary. Diagnostics count
these separately from actual transcripts and from sources requiring attention.

## Exact recovery bounds

The owner's current instruction authorizes completion of the missing audio
archive. Production verification of #691 leaves four voice sources (1, 1, 27,
2 seconds) and one distinct short-call source with two byte-identical copies.
The exact IDs, sizes and owner scopes are pinned in migration 0107.

- One fresh generation-4 grant per exact voice and one v15 call grant.
- At most five source attempts, at most 20 parts each, zero automatic retries.
- Dated isolated voice keys avoid collisions with older recovery generations.
- Existing mini model, endpoint and USD 1 monthly cap remain unchanged.
- All old unknown reservations remain unknown; only the new returned usage is settled.
- Byte-identical copies may reuse either a transcript or an explicit no-speech
  outcome, within the same owner/project/source-account scope.
- No receipt/outbox requeue, messages, publications or Alpha delivery.

Tests cover empty middle parts, all-empty results, known zero usage, missing
usage, service settlement without text/routing, stable v15 restart keys, exact
PostgreSQL grants and byte-identical no-speech reuse. Production acceptance
requires every source either to have text or a known no-speech result; an
unknown response cannot be relabeled merely to clear a diagnostic counter.

Rollback: stop intake before reverting to the baseline and withdrawing unused
0107 grants. Completed transcripts, no-speech results, original audio and
historical budget evidence remain durable.

# Gate Voice

> Canonical handoff for the standalone `audio → Gate` project.
> Snapshot captured on 2026-07-29. Runtime facts must be re-queried at the start of every new Work.

## Product boundary

Gate Voice is a technical ingestion and transcription subsystem. It must:

- durably archive original mobile-phone call recordings delivered through Dropsync/Dropbox `/Alpha/Calls`;
- durably archive Telegram Business voice notes from both configured owner accounts (RU and BY);
- transcribe every supported inbound and outbound Telegram voice note, including the owner's conversation with Vyacheslav (Slava);
- store the full Russian transcript and a concise Russian summary beside every successful audio archive;
- expose owner-only, privacy-safe status and transcript evidence;
- operate automatically and idempotently for historical recovery and all new audio.

Explicitly out of scope: Lenta, Alpha, Personal Mayak, reply initiation, Telegram message delivery, and any independent product behavior. No Telegram send is authorized by this Work.

## Owner authorization and privacy boundary

On 2026-07-29 the owner explicitly requested that Gate process both directions of his configured Telegram voice archive, including existing voice notes exchanged with Vyacheslav, and all configured phone-call recordings. This authorizes the bounded transcription work required by this project. It does not authorize sending messages, publishing transcripts, exposing counterparties, or placing raw audio/transcript content in GitHub.

Repository receipts must contain only redacted counts, safe error classes, revisions, and acceptance outcomes. A recovery execution must set an exact provider cap, obey the existing budget admission, avoid blind replay after unknown provider outcomes, and stop on any scope or identity ambiguity.

## Fresh production snapshot

Observed through the owner-only Gate M read surface on 2026-07-29:

| Signal | Value | Interpretation |
| --- | --- | --- |
| Production commit | `7f866cad3ddd` | Current deployed fingerprint at handoff time |
| Telegram voice state | `reconciliation_required` | Product is not ready for closeout |
| Transcription model | `gpt-4o-mini-transcribe` | API key and entitlement are active |
| Voice receipts | 82 received / 30 processing / 41 delivered / 11 dead letter | Processing backlog is growing and must be diagnosed |
| Provider accounting | 71 settled / 3 unknown / 0 open | Many provider calls completed; blind replay would be unsafe |
| Dead-letter classes | 9 `telegram_voice_not_inbound`; 2 `openai_transcription_transport_unknown` | Historical direction policy and unknown outcomes need exact reconciliation |
| Budget | $0.155178 settled / $0.104300 reserved / $0.740522 remaining | Existing hard monthly limit remains active |
| Latest phone selector | `phone_call_transcribed` | A recent call transcript is retrievable |
| Latest Telegram selectors | RU and BY returned `telegram_voice_archive_empty` | Owner observability is incomplete or incorrectly scoped |

The previous snapshot in this Work had 78 received and 26 processing. The 2026-07-29 snapshot has 82 received and 30 processing while delivered remains 41. This is strong evidence that new Telegram voices are entering Gate but not reaching a terminal receipt.

## What is already implemented

Historical work established the main architecture:

- Dropbox call discovery, durable archive, backfill, transcription, and stored summaries;
- Telegram Business voice archive for both directions;
- provider budget reservations, settlement, unknown-outcome handling, and safe error classes;
- recovery of three historical Telegram voice archives;
- normalization/compression and chunking path for long phone calls;
- atomic phone-call processing claim to prevent competing workers from overwriting a real provider result;
- tests that state outbound Telegram Business voices should be archived and transcribed;
- release discipline: `PR → full exact-head CI → merge → automatic Railway production deploy`.

Relevant merged PR sequence: `#356`, `#357`, `#358`, `#359`, `#360`, `#362`, `#367`, and `#369`. Inspect their exact diffs and receipts rather than reconstructing behavior from this summary.

Source review before this handoff showed that current main intends to transcribe outbound voices in `app/worker/telegram_processing.py`, and a regression test named `test_outbound_business_voice_is_archived_and_transcribed` exists. Therefore the current problem is not safely described as a missing feature alone: source intent and production behavior disagree.

## Unfinished work and known gaps

### P0 — Telegram terminal-state failure

Thirty voice receipts are in `processing`, with zero open provider reservations and no increase in delivered receipts. Determine the exact failure after provider settlement: archive/transcript persistence, summary persistence, outbox finalization, retry bookkeeping, or worker crash. Use Railway logs and privacy-safe database/status evidence. Do not infer the cause only from aggregate counters.

Recovery rules:

1. Enumerate the exact stuck archive/receipt rows without exposing audio, transcript, Telegram IDs, or counterparty data in GitHub.
2. If transcript or summary already exists, finalize/reconcile the receipt without another provider call.
3. Retry the provider only for an exact row proven to lack a terminal provider result and only under a recorded bounded reservation key.
4. Preserve unknown outcomes until safely reconciled; never convert them into ordinary retries.
5. Add a regression test reproducing the confirmed production failure.

### P0 — outbound Telegram voice acceptance

Prove both directions on the actual Telegram Business update shapes used by RU and BY accounts. Audit at least `message`, `business_message`, edited variants, direction detection, media download, archive identity, and deduplication. The nine historical `telegram_voice_not_inbound` dead letters conflict with the required product behavior and must be classified as stale historical policy, safe reconciliation candidates, or a live defect.

Acceptance requires one new owner-sent voice and one new inbound voice to reach durable archive, full transcript, summary, and terminal delivered state automatically. No Telegram reply or delivery is part of the test.

### P0 — phone-call closeout

Re-query the historical long Irina call and confirm that its single original archive has one combined transcript and summary after normalization/chunking. Then run one fresh end-to-end smoke:

`phone call → Samsung recording → Dropsync → Dropbox /Alpha/Calls → Gate archive → transcript → summary`

The smoke must not involve manual file copying. Record only redacted status evidence in GitHub.

### P1 — summaries

Audit every successful phone-call and Telegram voice archive for non-null summary state. Confirm that normal Telegram worker processing, not only Dropbox backfill, generates the summary idempotently. Backfill only exact rows missing a summary; never discard a good transcript when summary generation fails.

### P1 — owner-only observability

Implement or repair an owner-only audio day/status selector that reports:

- counts by source, account, direction, and terminal state;
- transcript and summary completeness;
- safe error classes and reconciliation requirements;
- no raw transcript, Telegram identifier, counterparty identifier, or private payload in aggregate status.

The current `voice:latest-transcript:ru` and `voice:latest-transcript:by` selectors returned `telegram_voice_archive_empty` despite non-zero archive telemetry. Fix selector scoping or document the correct canonical selector and prove it on production.

### P1 — complete media/update coverage

Confirm the exact supported product set. The minimum required set is Telegram voice notes in both directions. Audit whether Telegram audio-file messages, forwarded voices, edited Business messages, and duplicate webhook deliveries are intentionally supported or explicitly rejected with a safe reason. Add coverage for every type declared supported.

## Definition of Done

The Voice project may be closed only when all of the following are true:

1. Every new phone recording in `/Alpha/Calls` is archived, transcribed, summarized, and terminal without manual copying.
2. Every new inbound and outbound Telegram voice note on both configured accounts is archived, transcribed, summarized, and terminal.
3. The 30 processing receipts, 3 unknown provider outcomes, and 11 dead letters from the handoff snapshot are reconciled by exact safe outcome; no blind replay.
4. The historical long call has a verified transcript and summary.
5. Owner-only status proves per-day completeness and safe errors without exposing private content.
6. Tests cover inbound/outbound update shapes, idempotency, duplicate workers/webhooks, provider unknown outcomes, persisted-transcript recovery, summary failure, long-call chunking, and selectors.
7. Full exact-head CI is green on the exact PR head.
8. The PR is merged, Railway deploys automatically, production fingerprint matches the merge, and runtime acceptance passes.
9. A terminal redacted receipt is posted to Gate issue `#354`, this document is updated if facts changed, and issue `#354` is closed.

## Required new-Work sequence

1. Read repository `AGENTS.md`, `docs/work-bootstrap.md`, this file, `docs/voice/NEW_WORK_PROMPT.md`, Gate issue `#354`, its latest receipts, open PRs, and current `main`.
2. List visible automations. Ensure exactly one enabled `Gate Work heartbeat`; do not create a duplicate.
3. Re-query Gate M and Railway. Treat every number and SHA in this snapshot as stale until confirmed.
4. If no unexpired claim exists, supersede the expired execution with one new bounded execution, one lease, one branch, and one PR. Record exact provider caps, no Telegram sends, rollback revision, and stop conditions.
5. Diagnose the 30 stuck receipts before editing code.
6. Implement the smallest complete repair and recovery path, with regression tests.
7. Run relevant local checks, then one necessary full exact-head CI.
8. On green exact head, merge and allow the automatic Railway deploy.
9. Verify the exact production revision and real inbound/outbound Telegram plus fresh phone acceptance.
10. Post terminal redacted receipts, update this handoff, and close issue `#354` only when every Definition-of-Done item is proven.

## Canonical pointers

- Work issue: https://github.com/markmalina/gate/issues/354
- Bootstrap: `AGENTS.md` and `docs/work-bootstrap.md`
- New Work prompt: `docs/voice/NEW_WORK_PROMPT.md`
- Production fingerprint at handoff: `7f866cad3ddd`

## Handoff state

```yaml
status: ready_for_new_work
project: gate-voice
canonical_issue: gate#354
handoff_date: 2026-07-29
production_at_handoff: 7f866cad3ddd
first_action: diagnose Telegram voice receipts stuck in processing
telegram_sends: 0
closeout_requires_real_runtime_evidence: true
```

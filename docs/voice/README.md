# Gate Voice

> Canonical business-workspace handoff for the standalone `audio → Gate` project.
> Personal-workspace execution closed on 2026-07-30. Re-query runtime facts before any future work.

## Owner closeout decision

On 2026-07-30 the owner accepted the single known user-facing untranscribed long-call case as a documented exception. Leave it unchanged. Do not open an OpenAI Support request and do not make another provider retry for that archive.

Investigate again only if another supported phone recording or Telegram Business voice note fails to reach a terminal transcript. Any future Gate Voice work belongs in the business workspace, not the personal workspace. Reuse Gate issue [#354](https://github.com/markmalina/gate/issues/354); do not reconstruct the project from a personal chat.

## Product boundary

Gate Voice is the ingestion and transcription subsystem for:

- Samsung call recordings delivered through Dropsync to Dropbox `/Alpha/Calls`;
- inbound and outbound Telegram Business voice notes on the configured RU and BY owner accounts;
- durable original archives, full Russian transcripts, concise Russian summaries, and owner-only safe status.

Lenta, Alpha, Personal Mayak, reply initiation, Telegram message delivery, and a general communications browser are outside this project. Gate Voice never authorizes a Telegram send.

## Accepted production state

Observed on 2026-07-30 after the final Voice release:

| Signal | Accepted value |
| --- | --- |
| Production commit | `e05f65e06e4d` |
| Final implementation PR | [#401](https://github.com/markmalina/gate/pull/401), merged |
| Exact-head CI | run `#1350`, full success |
| Railway deployment | `85baeaa8-98de-446d-8905-1a857a9fb889`, success |
| Telegram voice | 84 received / 84 delivered / 0 processing / 0 dead letter |
| Provider accounting | 126 settled / 0 open / 5 historical unknown |
| Ordinary transcription model | `gpt-4o-mini-transcribe` |
| Telegram sends by this execution | 0 |

A new voice was observed traversing the normal production path, and the Telegram voice queue is terminal and empty. These counters prove the observed configured archive, not an unconditional guarantee about every future Telegram update type.

## Implemented behavior

- durable Dropbox phone-call discovery, archive, transcription, and stored summary flow;
- durable Telegram Business voice archive for inbound and outbound owner-account voice notes;
- idempotent queue processing, persisted-transcript recovery, stale-claim recovery, and safe terminal reconciliation;
- bounded provider budget reservation, settlement, and unknown-outcome protection;
- long-call normalization and chunking;
- owner-only privacy-safe status and transcript selectors;
- privacy-safe OpenAI error telemetry containing only HTTP status, model, request ID, and allowlisted structured error fields;
- release discipline: exact-head CI, merge, automatic Railway deployment, production fingerprint, runtime acceptance.

The complete implementation and migration history is in merged PRs linked from issue #354. The latest code is already on `main`.

## Accepted residual exception

One preserved long-call archive has no transcript or summary. Its first bounded part was deterministically rejected by OpenAI with HTTP 400, `invalid_request_error`, `invalid_value`, `param=file`. The original archive is retained.

Before closeout, the project tested valid locally decodable MP3 and PCM WAV representations at multiple bounded durations with both supported transcription models. The final telemetry release produced exactly one diagnostic request and no open reservation. More format/model retries are not evidence-based and are prohibited by the owner closeout decision.

The provider ledger also retains five historical `unknown` accounting outcomes. They are preserved for reconciliation and are not an active Telegram queue backlog. Never replay an unknown provider outcome blindly.

## Owner access and interpretation

Gate exposes owner-only status and transcript evidence through Gate M selectors. The status surface proves archive/transcript/summary state and safe error classes without exposing private content in GitHub.

A single browsable interface containing every communication is not part of Gate Voice and must not be inferred from the successful ingestion counters. That belongs to a separate Gate product surface.

## Reactivation trigger

Do nothing while new supported audio remains terminal.

If another supported audio item fails:

1. Open the work from the business workspace.
2. Re-read `AGENTS.md`, `docs/work-bootstrap.md`, this file, and issue #354 receipts.
3. Re-query Gate M, Railway, current `main`, open PRs, and exact provider accounting.
4. Reopen issue #354 and claim one bounded execution only after proving the new failure is distinct.
5. Do not retry the accepted long-call exception or any unknown provider outcome.
6. Use the normal `PR → full exact-head CI → merge → automatic Railway deploy → runtime acceptance` sequence.

## Canonical pointers

- Issue and full redacted execution receipts: https://github.com/markmalina/gate/issues/354
- Final implementation release: https://github.com/markmalina/gate/pull/401
- Bootstrap: `AGENTS.md` and `docs/work-bootstrap.md`
- Reactivation prompt: `docs/voice/NEW_WORK_PROMPT.md`

## Handoff state

```yaml
status: accepted_with_known_exception
project: gate-voice
workspace_owner: business
personal_workspace_work: closed
canonical_issue: gate#354
production_revision: e05f65e06e4d
telegram_voice: 84_received / 84_delivered / 0_processing / 0_dead_letter
known_untranscribed_case: retained / no_further_retry
future_trigger: another_supported_audio_failure
telegram_sends: 0
```

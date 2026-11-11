# Gate745 sprint 3: durable ingress and final send admission

Author implementation evidence, 11 November 2026. This is not independent acceptance,
installation or product acceptance. Same #745, branch `agent/745-dialog-snapshot`, PR747.
Parent: `4c726a82014398ae33f58dcf28c373e4c672b437`, tree
`7a989fb311c1bb93067f6ff3c20554050518a34c`. Main baseline:
`8d17d0b40a86c856bec1072633c721212a34a9dc`. Exact published commit/tree and
original exact-head CI results are recorded in the sprint receipt in #745.

Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md
Plan: https://github.com/markmalina/gate/issues/745#issuecomment-6789115422
S2 receipt: https://github.com/markmalina/gate/issues/745#issuecomment-6791742807
S3 claim: https://github.com/markmalina/gate/issues/745#issuecomment-6792409574

## Contract and serialization

Actual route: authenticated webhook → `AcceptTelegramReceiptService` →
`SqlTelegramReceiptStore.create_if_absent` → receipt + dialog revision + outbox in
one transaction → asynchronous canonical/legacy projection and optional STT.
No content classification, projection, STT or model is needed to invalidate a reply.

Read/prepare uses the immutable scoped S2 token. `SqlTelegramReplySender.send` runs
preflight → live rights **read** → reservation (including exact payload/package,
budget and original token) → `_admit_dispatch` → a single provider write → receipt.
Rights lookup is not a send and is not counted as a synthetic provider write.

Final admission acquires these PostgreSQL UPSERT row fences before reading current
snapshot/readiness: connector-wide unavailable fence; sorted connection unavailable
fences; sorted connection/chat revision rows. Ingress increments the matching row in
the receipt transaction. A gap producer uses the same unavailable row. A committed
event before final admission changes the token; a pending producer holding the row
blocks admission until commit, then the new version/gap is read under READ COMMITTED.
Refusal before HTTP finalizes the reservation as failed when the journal is writable.
No database transaction crosses provider HTTP. Missing version data yields
`context_unavailable/authoritative_version_unavailable`; no raw database error is
returned as a successful check.

The unavailable rows reuse `reply_dialog_versions`: reserved connection
`gate:business-ingress`, chat `*` for connector-wide loss of trust; actual connection
and chat `*` for connection-scoped loss. Nonzero values are sticky. Zero-value fence
rows are not included in the dialog fingerprint. An unrelated dialog event does not
change this dialog's token. The short global admission fence serializes admission
transactions; it does not make unrelated dialog events invalidate the token.

## Ingress and gap path matrix

All test names below are in `tests/test_reply_final_admission_postgres.py` unless
another file is named. Results refer to executed local synthetic cases; CI evidence
for the published head remains a separate #745 receipt.

| Event / actual producer | Revision / gap producer | Final consumer | Test / local result |
| --- | --- | --- | --- |
| Business incoming, manual outgoing, text/voice/photo/file: `durable_telegram_webhook` → actual SQL receipt store | New `(bot_key, update_id)` receipt increments connection/chat, atomically before projection | Shared sender `require_dialog_snapshot(lock=True)` | `test_dialog_snapshots_postgres::test_raw_receipt_stops_stale_draft_without_waiting_for_projection`, both directions × four content types: PASS |
| Legacy supported Business `message`, and `edited_message` carrying connection id | Same atomic receipt/revision; same scoped raw-event reader | Same final consumer | `test_legacy_business_envelope_is_invalidated_before_async_projection`, both classes: PASS |
| `edited_business_message`, `deleted_business_messages` | Same atomic revision independent of projection support | Same final consumer | `test_dialog_snapshots_postgres::test_edit_or_delete_after_review_also_refuses`: PASS |
| Another Work's approved send | Existing reservation increments the same connection/chat before provider dispatch | Another draft's preflight/reservation/final consumer | `test_dialog_snapshots_postgres::test_two_works_with_one_review_cannot_both_send`: PASS |
| Identical text, new event id; exact event replay | New update id increments; exact duplicate returns original receipt without increment | Same final consumer | `test_same_text_new_event_changes_version_but_duplicate_and_other_connection_do_not`: PASS |
| Another account/connection or another chat | Only its own version row; connection-specific gap only affects mapped account | Scoped connection set | Same test plus `test_dialog_snapshots_postgres::test_duplicate_receipt_is_stable_and_other_chat_does_not_block`: PASS |
| Authenticated invalid JSON/non-object/invalid update id | Actual ingress error path → independent SQL `mark_unavailable` → durable connector fence and bounded audit reason | Prepare/read and final admission | `test_authenticated_invalid_envelope_marks_gap_but_invalid_auth_does_not`: PASS; bad auth does not produce a marker |
| Observed receipt persistence exception | Actual authenticated catch → independent durable gap transaction if database permits it; webhook remains 503 | Same gap consumer | `test_webhook_durable_acceptance_failure_records_gap_if_database_can_write`: PASS |
| Mutated replay / missing original integrity evidence | Receipt store rolls back; Business connector gap in independent transaction; original receipt/outbox preserved | Same gap consumer | `test_observed_failures_are_durable_sticky_and_block_prepare_send[mutated_replay]`: PASS |
| Business event cannot be attributed to a connection/chat | Same receipt transaction marks connector gap; no content is guessed | Same gap consumer | `test_observed_failures_are_durable_sticky_and_block_prepare_send[unscoped_event]`: PASS |
| Accepted `business_connection.is_enabled=false` | Same receipt transaction marks that connection unavailable; missing id uses connector fence | Same gap consumer | `test_observed_failures_are_durable_sticky_and_block_prepare_send[connection_disabled]`: PASS, later enabled/ordinary success does not restore |
| Another bot's corrupt receipt replay | Its own store rejection; no Business fence | Business sender remains usable | `test_other_connector_corrupt_replay_does_not_poison_business_admission`: PASS |
| Version SQL/table unavailable | Explicit safe refusal; no invented version | Preflight/final sender | `test_real_postgres_authoritative_version_unavailable_returns_refusal` and injected version-read failure: PASS |

No update-id sequence continuity is assumed: webhooks can arrive concurrently/out
of order and provider update classes can be filtered. A heartbeat/connector-ready
state proves no history continuity. Projection/STT delay or failure by itself is
not a missing-ingress gap when the complete raw receipt remains available. The
existing S2 raw-readset completeness/scope checks still apply.

## Supported send path matrix

| Entry / producer | Reservation and final consumer | Provider write | Test / local result |
| --- | --- | --- | --- |
| `gate_save_reply_draft` → `gate_send_approved_reply`, plain, Markdown, follow-up | `SqlReplyDraftWriter` retains original token; common sender `_reserve` → `_admit_dispatch` | `send_plain_message` / `send_formatted_message` | `test_every_mode_rechecks_after_reservation_before_provider`, event and gap: PASS |
| Rich text, approved immutable blocks/hash | Same sender, preserved Rich scope/hash/receipts | Single `send_rich_message` | Same late-event/gap matrix + `test_reply_rich_send.py`: PASS |
| `gate_prepare_reply_document` → `gate_send_approved_document`, including original office/image formats | Same sender; file metadata/hash/caption/expiry preserved; final expiry recheck | Single `send_business_document` | Late-event/gap matrix + document tests in `test_reply_sends_postgres.py`: PASS |
| Photo package through media prepare/preview/approval | Same sender; package binding/budget/expiry retained | Single `send_business_photo` | Late-event/gap matrix + `test_reply_media_transport.py`: PASS |
| Album package | Same sender, no splitting or per-item fallback | Single native `send_business_album` | Same matrix / media native wire and receipt tests: PASS |
| Rich photo, slideshow, collage | Same sender, saved native blocks and local attachment mapping | Single `send_business_rich_media` | All three modes × late event/gap, native media regressions: PASS |
| Queued sequence item: `gate_queue_approved_reply_sequence` → `run_reply_sequence_once` | Worker creates scoped principal and invokes `SendApprovedReplyService(SqlTelegramReplySender)` | Same shared sender for each due item | `test_actual_sequence_worker_item_uses_final_admission`, event/gap: PASS; existing sequence/pending-task tests preserved |
| Legacy `/send_` and `/approve_` draft commands, external draft forms | Existing S2 deny paths cannot grant snapshot approval | No Business write | Existing media legacy/formatting regression tests: PASS |
| Legacy generic auto/admin Business reply via `TelegramClient.send_message` | No immutable approved draft or send journal: explicit `context_snapshot_required` before HTTP | No Business write; ordinary non-Business bot console unaffected | `test_legacy_generic_business_transport_cannot_start_http`: PASS |
| Delayed invocation/restarted worker/repeated terminal request | Common preflight returns original sent/failed/unknown attempt and saved media receipt | No retry/fallback | Text/document/Rich/media replay and timeout/crash tests: PASS |
| `reply_send_recovery`, document/media cleanup workers | Recovery marks stale sending unknown; cleanup only prunes eligible bytes; neither sends | None | Existing recovery/media storage regressions; no new scheduler or retry |

Actual MCP JSON-RPC/ASGI read/prepare/media/send error contracts are covered by
`test_reply_snapshot_integration.py`. Refusal is `sent=false`, `context_changed`
or `context_unavailable` with bounded `reason`; current context is included only
when the principal already has the read scope. Both ordinary and media error
schemas advertise the optional reason. No snapshot is silently refreshed.

## PostgreSQL ordering and defect reproduction

`test_independent_process_commit_precedes_final_admission_and_zero_stale_calls`
uses a separate Python process (`tests/support/dialog_ingress_process.py`), distinct
PostgreSQL backend PIDs and an explicit stdin commit barrier. The child persists a
voice event or gap but pauses before COMMIT. Final admission waits on the actual
row lock, confirmed via `pg_stat_activity`. Releasing child COMMIT yields
context_changed/context_unavailable and zero synthetic send writes. Async tasks
are joined and children reaped before dropping the test schema. Existing two-session
ingress/reservation tests additionally cover the earlier reservation boundary.

The literal S2 parent was checked separately on synthetic PG with a delayed
`message` envelope containing voice. Before projection its old reviewed draft
sent once (`S2_PARENT_SYNTHETIC_STALE_PROVIDER_WRITES=1`). The candidate's
`test_legacy_business_envelope_is_invalidated_before_async_projection` rejects the
same case with zero writes. This is a demonstrated before/after defect, not a
claim derived from mocks of the revision or admission logic.

Local environment: PostgreSQL17.11, UTF8, disposable cluster/schema, independent
connections/processes, no external DNS/connect including Python children. Providers
are recording adapters or `httpx.MockTransport`; no real Telegram/model/STT call.
The first local cluster was incorrectly SQL_ASCII and a detached process did not
survive its isolated command namespace; those runs are not PASS evidence. Corrected
tests run the UTF8 cluster and tests in the same command environment. Narrowed old
SQL fixtures also required the same canonical/relationship model registration used
by the full suite; missing-table-registration failures are not behavior verdicts.

Local exact counts, commands, attempts and final-head CI are recorded in #745.
Ruff, strict Mypy, diff check and sensitive-data scan are required before publishing.
The two authored 0116 migrations and no-DDL0120 join are unchanged; migration
round-trip/schema/populated metadata tests are retained. S2 pending-task behavior,
immutable staging replay, photo/album budgets and terminal receipts are retained.

## Limits and remaining work

The guarantee starts when Gate durably accepts the event, and orders it against
final admission. An event not delivered to Gate is not observable. An event that
commits after final admission cannot retroactively cancel an admitted/in-flight
HTTP or a native album. No cancellation of a started request is promised.

If the entire database is unavailable, admission fails; a webhook cannot persist
its gap in that unavailable database, returns non-2xx and logs only the error type.
There is no cross-process durable outage record in that case and no promise to
detect events never durably accepted. A later heartbeat is never treated as repair.
Existing committed sticky gaps remain blocking across restart/reconnect. There is
no implemented automatic restoration: a reviewed authoritative reconciliation and
explicit repair procedure is required before releasing such a fence. S3 adds no
gap-clearing endpoint or production mutation. Conservative refusal is intentional.

S4: own-echo correlation, immediate stop of the unsent series tail, visible status
and no automatic resume; no new scheduler is implemented here. S5: independent
whole-candidate source review/races/migration verdict. S6: accepted joined-schema
cutover/rehearsal/recovery contract and fresh protected autoDeploy=false before
merge. S7: separately authorized controlled production cutover with exact fleet,
database, backup and runtime evidence. S8: connected catalogue and bounded live
product acceptance. No merge/deploy/production migration/config/credential changes,
real sends/models/STT, Watch/Calls/AQUB activation; #745 remains open.

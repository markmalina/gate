# R3 — PostgreSQL concurrency и матрица доказательств

27.10.2026 МСК. Существующие #745/PR747/execution gate-745-snapshot-20261008T1458Z.
Base249c5abd32f61e912a6f8fed5912b0b6d9e0ebb2; R1/R2 receipts — в issue745.
Это авторский source/test packet; independent SOURCE, installed runtime и product acceptance не заявляются.

## Новое доказательство

Существующий test_ingress_commit_racing_dispatch_serializes_on_the_actual_version_row проверяет разные PostgreSQL connections и настоящий row lock, но обе coroutine исполняются в одном Python OSпроцессе.

tests/test_dialog_snapshot_processes_postgres.py добавляет2case (inbound и owneroutbound) с multiprocessing spawn:
1. Parent ingress создаёт настоящий durable receipt и удерживает транзакцию revision до commit.
2. Sender в отдельном OSпроцессе создаёт собственный engine/pool/session; передаётся только имя изолированной schema и synthetic seed.
3. Соединение sender закреплено, поэтому preflight rollback не меняет наблюдаемый PostgreSQL backend PID.
4. Parent доказывает по pg_stat_activity, что sender ждёт PostgreSQL lock и ещё не dispatch.
5. Parent commits ingress; child получает context_changed, mock dispatches0; durable SendAttempt0.
6. Process/pipe/task cleanup ограничен тайм-аутами и завершается до удаления test schema.

Одинаковый текст другого события не заменяет идентичность/revision. FakeTelegram не делает networkcalls. Реальные пользовательские данные/DSN/exception payload не передаются через process protocol. Схема допускается только reply_send_test_<32hex>; процесс создаёт engine самостоятельно, не наследует live connection через fork.

Локально Ruff/compile PASS; обаPGcase SKIP безDATABASE_URL. Обязателен realPG в оригинальном exact-head hosted CI, авторские ожидания не считаются прохождением. Первичная2process проверка child/spawn/lock подтверждается только после такого CI.

## Трассировка A01–A12

Все ссылки относятся к исходникам именно этого source tree. Фактические terminalrun records/counts см. в последней R3receipt; historicalCI не переносится на новые runtime changes.

| Критерий | Конкретное source/test evidence | Что остаётся вне этого доказательства |
| --- | --- | --- |
| A01 неизменныйдиалог | test_dialog_snapshots_postgres.test_unchanged_snapshot_sends_once_and_replays_after_dialog_changes; reviewed_pending_tasks single/follow-up replay | Live receipt и установленный клиент |
| A02 inbound | test_raw_receipt_stops_stale_draft_without_waiting_for_projection, owner=False, text/voice/photo/document; newprocess inboundcase | Наблюдение Telegram вне fake fixture |
| A03 outbound | Тот же owner=True; test_new_same_text_event_still_blocks_reviewed_pending_work; newprocess ownercase | Реальный ручной owner/другой Work canary |
| A04 eventidentity | rawreceipt matrix, test_edit_or_delete_after_review_also_refuses, same-text/new-event test; pure snapshot/key cases | Ограничения Telegram delivery/observability описать в live packet |
| A05 races | test_change_during_connection_probe_is_rechecked_before_reservation; test_change_after_reservation_before_dispatch_is_failed_without_http; actualversionrow race; R3spawn tests | Уже начатый HTTP нельзя гарантированно отменить |
| A06 scope | test_duplicate_receipt_is_stable_and_other_chat_does_not_block; test_other_project_change_invalidates_without_exposing_its_content; sequence foreignaccount/project | Global census/реальные установки не проверены |
| A07 nobypass | legacydraft noauto-backfill, snapshotlessdraft refusal, save reviewed snapshot check, staleOffice/image dispatch0 | Необъявленные новые endpoints требуют той же source review |
| A08 visible refusal | R2MCP22cases: scoped sentfalse/current_context, no-read/null, rollback, context-read failure, truncationflag | Установка server catalogue в реальное подключение |
| A09 newcycle | R1same-text refusal→freshread→newdraft→send; R2realMCP refusal→new exactplain drafts/hashes→confirmedqueue→mockdelivery | Новое liveapproval/recipient/textcap |
| A10 competition/replay | twoWorksoneReview, twoDrafts/twoTasks single delivery, timeoutunknown no retry, cancelledPOST durableSENDING, outcome flushfailure fence, sequence restart | Независимый итоговый source verdict |
| A11 documents/media | staleXLSX/PNG guard, originalOffice/image exactreplay, documenttimeoutunknown, MIME/hash/caption/formatting contracts | Real Office/image delivery в согласованном canary |
| A12 evidence | Exact commits/trees; original literal/syntheticPG CI; schema/backuprestore; этот packet | Native fixture, guardedProduction, connectedMCP, independentreview и owneracceptance — OPEN |

Итог: source/CI покрывают техническую часть A01–A11. A12 и runtime/product части остальных строк открыты. Эта матрица не закрывает issue и не создаёт livegrant.

## Идемпотентность и unknown

test_reply_sends_postgres:
- test_timeout_is_unknown_and_exact_retry_replays_without_provider_io;
- test_document_timeout_is_unknown_and_does_not_retry;
- test_cancelled_post_leaves_committed_sending_fence_for_exact_retry;
- test_outcome_flush_failure_rolls_back_around_committed_sending_fence;
- test_reservation_replay_does_not_wait_on_outcome_attempt_lock.

Нельзя объявлять unknown успехом/отказом до I/O или автоматически повторять. Исторические attempts и exactpayload bindings сохраняются. R3не меняет runtime, только усиливает проверку process boundary.

## Series, formatting и recovery

R1regression file19cases: reviewed older/newer taskorigin, NEEDS_REPLY/REPLIED, single/follow-up, series2/50, exactorder, replay и новая ревизия.
test_reply_sends_postgres:50-item restart/order, fiftiethbinding mismatch atomicity, providerI/O без удержания sendertransaction.
test_dialog_snapshots_postgres:rawevent во время первогоHTTP останавливает unsenttail.
R2test module22cases:единыйerror/permission/recoverycontract, immutableolddrafts, sameworkspace/account/project filtering, rawhas-moreflag.
Existing formatting/media regression сохранены: payloadhash/entities/captions не меняются при доставке.

## Миграции, schema и backup

test_reply_cutover и test_reply_cutover_postgres покрывают fixedmigration0116, activeattemptabort, unknownschema/multipleheads, sendsdisabled baseline и journalpreservation.
LiteralCI дополнительно запускает upgrade/schema-drift/backuprestore/downgrade; mandatorysyntheticmerge проверяет собственную schema convergence и populated restore.

Эти syntheticDB backups не являются Production backup. R4/R5/R7 требуют настоящих targetidentity/fleet/configuration/backup/restore/cost/cutover receipts на конкретной установленной ревизии.

## Минимальный независимый review packet

Для предусмотренной отдельной проверки R6:
- exact candidate commit/tree и единственный PR747;
- base/currentmain и literal/synthetic runrecords, attempt1, actualjobsteps;
- changeset R1fourvetoes/R2errorcontract/R3process tests;
- current immutable snapshot/coveredtasks/attemptledger/dispatchboundary contract;
- actual source/installed Ops pins и release gates отдельно;
- весь A01–A12 ledger со статусами выше и явным отсутствием native/Production/live acceptance.

Автор не выдаёт собственный matrix или GitHubCI за independent ACCEPT. Новое внешнее review оформляется проектным механизмом на точном итоговом source, без имитации другого Work.

## Следующий этап и безопасная граница

После R3sourcechecks — R4:доказать действительный private Ops/Delta metadata transport и installed Gate-owned operation contract. Сначала перечитать свежие existingOps375/381 и supporting758/760/762; не переносить historicalbody/grant/pin.
Нельзя отменять guards или применять обычный shortdeploy ради обхода missingcutover.
Credential/provider/resource/native/Production/live действия требуют конкретных готовых пакетов; source/test scope их не разрешает. Старые4/12drafts не используются.

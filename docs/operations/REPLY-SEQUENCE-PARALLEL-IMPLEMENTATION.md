# #716 + #718: реализация и проверка

Дата: 27.09.2026. Автор: α Gate. Единый PR: #720.
База: `8e0b1672b6dc345e121cd55f5ba28540b7e18673` (включает #715).
ТЗ: `docs/specs/ISSUE-716-PARALLEL-REPLY-SEQUENCES.md` и
`docs/specs/ISSUE-718-STOP-ON-INBOUND.md` (источник второго — PR #719).

Команда Марка «Да, хорошо. делай так.» разрешила prerequisite #718 и завершение
#716 в одном execution `gate-716-718-implementation-20260927T1940Z`.
Прежний blocker `blocked_on_718_baseline` снят. Этот документ заменяет partial receipt.

## Реализовано

- Канонические header/items, inbound revisions/signals и durable owner notification outbox.
- Атомарные receipt/outbox/stop; дедупликация provider identity, проверка mutated replay.
- Session advisory barrier для queue, ingress и sender до завершения HTTP; отдельные guard pools.
- Общий реестр permits, поколения control, account/bot/recipient budgets и monotonic 429 cooldown.
- Ограниченный supervisor, keyset discovery/recovery, отдельные sessions, отправка без batch barrier.
- Финальная проверка подтверждения и revision в transaction SendAttempt; запрет direct-send обхода.
- Recovery без blind resend: terminal ledger projections, unknown quarantine, bounded shutdown.
- Default-off composition, общий bot budget для текста/документов/notification и отдельных workers.
- Scoped MCP readback, detail pagination, агрегаты и явные состояния уведомлений.
- Alembic 0112 и явный legacy importer: старые незавершённые серии требуют нового подтверждения.

## Проверки и привязка к ТЗ

Ruff и mypy app проходят. Локальные unit/contract/lifecycle проверки выполняются без
Telegram и моделей. Полный CI запускается на literal PR head с PostgreSQL 17.
Первый прогон `93552e15` дал 2429 passed / 7 failed: все 23 новых PG-теста прошли;
падения были фиксациями прежнего migration head и перечня MCP tools. Они обновлены.
Второй прогон `5b51dded` дал 2460 passed / 1 failed: тестовая фикстура второго
получателя меняла chat_id, но не sender_id исходного сообщения. Исправление сохраняет
production-проверку точного получателя. Полная окончательная квитанция — в PR #720
и execution comments #716/#718; промежуточные прогоны не являются exact-head acceptance.

| Критерии #716 | Синтетическое доказательство |
| --- | --- |
| T01–T03, T09, T18 | `test_same_account_distinct_dialogs_overlap_and_account_cap_two`; `test_slow_series_and_another_account_overlap_with_exact_routing`; `test_same_recipient_other_account_is_not_stopped_or_exposed` |
| T04–T06 | `test_two_fifty_item_series_preserve_full_order_without_real_sleep`; `test_actual_completion_sixty_second_boundary_and_no_catchup` |
| T07, T10, T54 | Existing `test_reply_sequences*.py`, `test_reply_sends_postgres.py`, MCP contracts plus scoped detail test |
| T08, T12, T17 | Concurrent queue/claim tests: four competing queue calls, eight claim contenders, six sequences sharing four SQL permits |
| T11 | Current/tail text mutation and canonical-membership-with-lost-JSON tests; exact digest/binding check before reservation commit |
| T13–T16, T35–T39, T43, T55 | Stale token, projection-crash, sending-expiry, corrupt-membership, late-confirmed-outcome and quarantine tests |
| T19, T28, T40 | Supervisor deterministic event barriers and bounded shutdown tests; durable shutdown quarantine test |
| T20, T46, T47, T52 | Config/generation/disabled/continuity tests; operator cutover gate; CLI and heartbeat degradation reason |
| T21–T25 | SQL shared budget/restart cooldown tests; pure cooldown/429 parser tests; real HTTP MockTransport start-window check |
| T26–T34, T59–T60 | Failure/unknown isolation, incoming media without downstream processing, ingress-vs-HTTP barrier, probe race, owner outbox and stop/readback tests |
| T41, T44 | 10,000 terminal + 1,000 blocked candidates: EXPLAIN indexed active scan, 100-row pages, keyset continuation, eligible series dispatch |
| T42, T48–T51 | Complete 50-item legacy import, repeated import, terminal old approvals; old worker ignores managed_v2. Cutover explicitly checks all live ledger attempts before import |
| T45, T56 | Shared-account overlap plus simultaneous six-series stop/dispatch stress with transport-start assertions |
| T53 | Scoped uniform not-found, counts independent of detail page, schema validation, owner-only notification route |
| T57–T58 | Existing full PostgreSQL regression, Alembic upgrade/check/downgrade and disposable restore/container gates in CI |

Файлы новых integration tests: `test_reply_sequence_dispatch_postgres.py` и
`test_reply_sequence_acceptance_postgres.py`. Provider — синтетический adapter;
сетевые пересечения проверяются Event barriers, а интервал 60 секунд — управляемым
DB-clock seam без минутных sleep. PostgreSQL locks, уникальность, transactions и
rollback реальные. HTTP boundary отдельно проверяется через httpx MockTransport.
Ни один skipped-тест не засчитывается как passed.

Матрица указывает тестовые группы, а не подменяет внешние release-доказательства.
Latency p95 и суммарный connection budget измеряются на выбранном deployment rehearsal;
CI indexed-query check не является production SLA или доказательством ingress continuity.
Независимая приёмка δ не выполнена автором за самого себя.

## Эксплуатация и оставшиеся release gates

Полная инструкция: [REPLY-SEQUENCE-CUTOVER.md](REPLY-SEQUENCE-CUTOVER.md).
Все новые flags false; DB control mode disabled. Активация требует подтверждённых
pool mode/budget, отсутствия legacy workers, непрерывности ingress и owner route.
Перезапуск ingress инвалидирует continuity; при разрыве старые active approvals
нельзя возобновить командой serial/parallel — сначала terminal stop, затем новое согласие.

В этом execution production_changes=0, telegram_sends=0, model_calls=0.
Не подтверждены фактические Production migration, deployed fingerprint, owner notification
delivery и bounded live canary. Они имеют отдельный exact target/authority и release receipt.
CI/код не объявляют эти gates пройденными автоматически.

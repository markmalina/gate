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

## Проверки — текущий checkpoint

Ruff и mypy app проходят. Локальный набор из 123 тестов проходит, включая MCP
contracts, supervisor, cooldown, retry_after, legacy sequence validation и database lifespan.
Добавлен PostgreSQL acceptance suite с синтетическим provider и barrier tests.
В локальной среде нет пригодного PostgreSQL server; пропуски не считаются успехом.
Фактическая интеграционная проверка и migrations/drift выполняются на PG17 в CI.
До завершения exact-head CI это implementation checkpoint, не acceptance.

## Эксплуатационные ограничения

Все новые flags false; DB control mode disabled. Код не включает feature сам.
Реальные сообщения, чтение Production payload, миграция Production и deploy в этом
execution не выполняются. Активация требует подтверждённых direct/session pooling,
connection budget всех replicas, drain legacy workers, непрерывности ingress и owner route.
Полная release-инструкция и матрица acceptance уточняются по результатам CI.

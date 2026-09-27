# #716: реализация независимой части и граница интеграции

Дата: 27.09.2026. Автор: α Gate. PR: #720.
База кода: `8e0b1672b6dc345e121cd55f5ba28540b7e18673` (включает #715).
ТЗ: `docs/specs/ISSUE-716-PARALLEL-REPLY-SEQUENCES.md`.

**Состояние: partial implementation; blocked_on_718_baseline.**
Это не реализация всей #716, не repository acceptance и не разрешение активации.
Команда Марка на реализацию #716 получена. Отдельное поручение #718 на момент
проверки остаётся specification-only; PR #719 содержит только документ.

## Что добавлено

| Файл | Реализованное поведение |
| --- | --- |
| `app/worker/reply_sequence_supervisor.py` | Ограниченный набор задач 1–8, одна локальная задача на sequence UUID, ленивое создание только при свободной ёмкости, FIRST_COMPLETED, сбор ошибок без provider payload, закрытие admission и bounded shutdown |
| `app/application/reply_sequence_cooldown.py` | Чистый расчёт deadline: malformed → 60 секунд, запрет сокращения уже действующего cooldown, overflow → indefinite block, только aware timestamps |
| `app/services/telegram.py` | Совместимое optional поле `TelegramApiError.retry_after_seconds`; parser принимает только целые >=0, отклоняет bool/string/float/negative, сохраняет большие значения без усечения; повторных запросов не добавлено |
| `tests/test_reply_sequence_supervisor.py` | Барьеры пересечения задач, продолжение после быстрой/ошибочной задачи, cap без тысячи ожидающих tasks, дедупликация, shutdown/неподтверждённая отмена, ошибки параметров |
| `tests/test_reply_sequence_cooldown.py` | Точные delays, fallback, монотонность, integer/timestamp overflow, indefinite block, clock validation |
| `tests/test_telegram_retry_after.py` | HTTP MockTransport: text и multipart, malformed/huge delay, один запрос, совместимый старый конструктор ошибки |

Supervisor и cooldown helper **не подключены** к legacy worker, `main`, sender
или settings. Автономного фонового запуска нет. Parser расширяет тип ошибки,
но существующие consumers не начинают retries или cooldown автоматически.
Настройки, модели БД, миграции и ingress не изменены.

## Контракт подключения после #718

1. Runtime controller читает общую DB generation и config; только после всех
   разрешающих gates создаёт supervisor. Локальный cap не заменяет global SQL permits.
2. Discovery выдаёт immutable DTO после закрытия SQL transaction; keyset scan и
   account fairness принадлежат repository adapter. Не создавать task на каждый
   rate-blocked candidate. Вызывать `collect_finished` до заполнения ёмкости.
3. `try_start(sequence_id, factory)` вызывает factory только если есть ёмкость.
   Каждая coroutine открывает собственные sessions. Coroutine сама выполняет
   durable claim/admission/finalization. Возврат `finished` означает лишь нормальное
   завершение coroutine, **не** Telegram `sent`.
4. При `failed`/`cancelled` controller запускает безопасное DB reconciliation,
   не resend. Исключения не публикуются в health/log как исходный provider текст.
5. Ожидать `wait_for_completion` с poll deadline; медленная A не задерживает сбор
   B и запуск C. При пустом pool controller обязан ждать poll/event сам, чтобы
   не создавать busy loop. Единственный event loop владеет supervisor.
6. Shutdown defaults: 20 секунд drain + 5 секунд cancellation observation.
   `unresolved` сохраняет ownership/reference в supervisor. Controller обязан
   quarantine durable permits, закрыть admission и завершить process; нельзя
   объявлять отсутствие HTTP или освобождать permit по истечению TTL. Даже
   `cancelled` требует проверки SendAttempt. Повторное admission после shutdown
   этим экземпляром запрещено.
7. Cooldown считать с DB time под account/bot budget locks; сохранить deadline
   и indefinite marker в той же согласованной transaction. Ошибка 429 останавливает
   текущую серию без automatic retry; соседи ожидают ресурс. Pure helper не доказывает
   persistence, межрепличную монотонность или соблюдение лимита реальным transport.

## Единственный владелец будущих полей и методов

| Контракт | Владелец | Состояние |
| --- | --- | --- |
| `reply_connector_admission`, `reply_dialog_guards`, `reply_inbound_signals` | #718 | отсутствуют в принятой базе |
| canonical `reply_sequences`, `reply_sequence_items`, notification outbox | #718 | отсутствуют в принятой базе |
| `expected_inbound_revision`, stop/readback, ingress receipt + stop commit | #718 | отсутствуют в принятой базе |
| внешний session advisory dialog guard, transport-start boundary | #718; #716 использует тот же guard | отсутствует |
| sequence cursor/claim token/approval digest, item approved hash | #716 extensions к #718 | не реализованы |
| `reply_dispatch_permits`, `reply_transport_budgets`, DB control generation | #716 | не реализованы |
| supervisor coroutine ownership и bounded drain | #716 | модуль реализован, runtime не подключён |
| parser retry_after и pure cooldown | #716 | реализованы; durable rate adapter отсутствует |
| лимит 2–50 | #715 | принят в базе; здесь не дублируется |

Второй registry/state machine не создаётся. После #718 фиксируется её принятый SHA,
модели/миграция сопоставляются с таблицей, и продолжается тот же PR #720.

## Проверенный inventory transport call sites

Статическое чтение main и code search по `send_plain_message`,
`send_business_document`, `send_message`, `TelegramClient`:

| Группа | Пути | Решение для будущего budget |
| --- | --- | --- |
| Reply text + document | `app/infrastructure/reply_sends.py` | Общий bot/account cooldown; slots только для sequence; не менять durable idempotency |
| Gate main client | `app/main.py`, `app/api/routes.py` | `settings.telegram_bot_token`; same-bot потребители обязаны разделять budget |
| Legacy internal/admin sends | `app/api/sprint9_routes.py`, `sprint14_routes.py`, `sprint14b_routes.py`, `sprint15b_routes.py`, `sprint16_raw_routes.py`, `sprint16d_clean_inbox_routes.py`, `sprint17_send_draft_routes.py`, `sprint18b_draft_ux_routes.py` | Проверить фактическое route wiring; наличие метода в файле не доказывает активный Production путь |
| Canonical delivery | `app/infrastructure/alpha_delivery.py`, `become_media_prompt.py`, `universal_mayak_delivery.py`, `aqub_live.py` | Одинаковая реальная bot identity должна давать один resource key независимо от instance клиента |
| Отдельные client factories | `app/api/alpha_telegram_ingress.py`, `aqub_telegram.py`, `app/worker/telegram_processing.py`, `become_pilot_maintenance.py` | Не использовать process identity или объект AsyncClient как bot budget key |
| AQUB/Beta clients | `app/main.py`, `app/api/beta_telegram_ingress.py`, `app/worker/aqub_food_media.py`, `beta_space.py` | Конфигурационно отдельные credentials; реальные bot identity в этой работе не читались и равенство не утверждается |
| Telethon | `app/personal_telegram/*`, `app/contact_sync/telethon_reader.py` | Другой protocol/client; не смешивать с Telegram Bot API budget |

Это code inventory, не аудит runtime конфигурации и не полный acceptance T25.
Токены, реальные chat IDs и переписка не запрашивались.

## Lock order для следующего этапа

Неизменяемый outer порядок: завершить discovery transaction → получить отдельное
physical session advisory lock #718 → начать короткую admission transaction.
Existing attempt: attempt → conversation/context → sequence → items → budgets
(стабильная сортировка) → slot. New attempt: conversation/context, затем те же
sequence/items/budgets/slot; не ждать existing attempt lock, удерживая conversation.
Commit admission до HTTP; advisory guard удержать через HTTP и outcome.
Recovery текущей базы блокирует только attempts; комбинированную проекцию
не добавлять в обратном порядке. PG races должны доказать порядок, код pure
supervisor этого не доказывает.

## Фактические проверки

Python 3.12.14; зависимости по `constraints/ci.txt` и `constraints/runtime.txt`:
pytest 9.1.1, anyio 4.14.2, httpx 0.28.1, ruff 0.15.21, mypy 2.3.0.
78 passed: supervisor 16, cooldown 16, новый parser 27, существующий client 19.
Ruff для шести изменённых Python-файлов: PASS.
Strict mypy для двух новых модулей: PASS (`--follow-imports=skip`).

```bash
python -m pytest tests/test_reply_sequence_supervisor.py tests/test_reply_sequence_cooldown.py tests/test_telegram_retry_after.py tests/test_telegram_client.py
ruff check app/worker/reply_sequence_supervisor.py app/application/reply_sequence_cooldown.py app/services/telegram.py tests/test_reply_sequence_supervisor.py tests/test_reply_sequence_cooldown.py tests/test_telegram_retry_after.py
mypy --follow-imports=skip app/worker/reply_sequence_supervisor.py app/application/reply_sequence_cooldown.py
```

Исходники для локальных targeted checks взяты из main и сверены Git blob SHA;
это целевой snapshot, не полный checkout. Полный `pytest`, `mypy app`, PostgreSQL,
migration/drift, sender regressions и Production не проверены.
Draft PR CI может пропускать backend; его зелёный статус не заменяет эти проверки.

| ТЗ | Доказательства текущей части | Итог полной приёмки |
| --- | --- | --- |
| T03, T19, T28 | Unit barriers, cap/lazy factory, failure isolation | частично; durable dispatcher отсутствует |
| T22–T23 | Parser и pure monotonic/overflow arithmetic | частично; SQL stop/cooldown отсутствуют |
| T40, T55 | Local drain/cancel/unresolved ownership | частично; durable quarantine/recovery отсутствуют |
| T58 | 19 существующих Telegram client tests | частично; sender/PG regression не выполнялась |
| T01–T02, T04–T18, T20–T21, T24–T27, T29–T39, T41–T54, T56–T57, T59–T60 | нет полной интеграционной реализации | blocked/not run |

Ни один из 60 end-to-end критериев ТЗ не объявляется принятым этой квитанцией.

## Что осталось до завершения #716

1. Получить принятую реализацию #718 либо прямую команду Марка расширить этот Work
   на её prerequisite реализацию. Разделы 15–16 ТЗ прямо исключают автоматическое
   принятие #718; её текущая owner scope запрещает разработку до команды.
2. На этой базе реализовать SQL extensions/permits/budgets/control, атомарный queue,
   claim/admission/finalization, sender guard и общий same-bot limiter.
3. Подключить supervisor, flags, health, reconciliation, legacy import и shutdown.
4. Выполнить все T01–T60 на реальном disposable PG, migration/drift/performance,
   exact-head CI и независимую приёмку. Не обходить dependency копией таблиц.
5. После scoped release authority — runbook ТЗ, deploy/schema/cutover/canary.

До этого PR остаётся draft, issue #716 открыта. Merge/deploy/activation и реальные
отправки не выполнялись. Следующий owner decision: prerequisite #718 в том же
поручении или отдельная принятая реализация #718 перед продолжением интеграции.

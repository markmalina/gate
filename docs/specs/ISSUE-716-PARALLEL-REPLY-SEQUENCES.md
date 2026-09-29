# Gate #716 — полное техническое задание: независимые параллельные серии

**Версия 1.0 · 27.09.2026 · Только проектирование. Реализация не начата.**

Интеграционная редакция: учтено опубликованное ТЗ #718 на a05f13658b25ae953c6c852baa7d0ea54eb95703.

| Поле | Значение |
| --- | --- |
| Каноническая задача | https://github.com/AlphaCenter/gate/issues/716 |
| Автор требования / продуктовая приёмка | Марк Малиновский |
| Владелец технического результата | α Gate #716 |
| Исследованный main | 9630135f5ac8098c3bf9f63fc6a691cb96f2a40e |
| Ветка документа | agent/716-parallel-sequences-spec |
| Текущий результат | ТЗ для разработчика, тестировщика и ответственного за выпуск |
| Разрешено сейчас | Документ, проверка полноты документа, запись в репозиторий |
| Не разрешено сейчас | Код, тестовый код, миграции, конфигурация, merge, deploy, реальные отправки |
| Следующий шаг | Отдельная команда Марка на реализацию; затем выполнение разделов 16–20 |

Документ самодостаточен для передачи исполнителю. Указанные далее таблицы, поля, настройки, тесты и алгоритмы — **проект будущей реализации**, если прямо не названы существующими. Это не отчёт о выполненных тестах и не доказательство работоспособности Production.

## 1. Результат для пользователя

Владелец может подтвердить несколько независимых серий разным адресатам. Пока сообщение одной серии ожидает ответ Telegram, сообщение другой серии может находиться в отправке. Каждая серия сохраняет собственные порядок, паузы, подтверждённый аккаунт и адресата.

Пример с синтетическими адресатами A и B:

- A1 и B1 становятся готовы одновременно.
- Worker допускает обе отправки с учётом общего бюджета; B1 не ждёт завершения медленного A1.
- A2 разрешается только после подтверждённого A1 и минимум 60 секунд.
- B2 рассчитывается относительно B1, а не A1.
- Ошибка/unknown/остановка A отменяет хвост A. Серия B не отменяется.
- Общий flood control может задержать обе серии, использующие один ограниченный Telegram-ресурс. Такая задержка отображается как ожидание лимита, а не остановка соседней серии.

Параллельность означает возможность пересечения интервалов сетевых запросов. Она не означает одновременный старт до миллисекунды, обход rate limit или отправку всех сообщений пачкой.

## 2. Границы и связанные работы

### 2.1 Входит в #716

1. Ограниченный асинхронный dispatcher без барьера «дождаться самого медленного элемента всего batch».
2. Долговечный поиск активных серий, не зависящий от первых 500 исторических draft rows.
3. Защита от двух worker/реплик, повторного claim и устаревшего исполнителя.
4. Глобальные и account/bot ограничения, сохраняемые в PostgreSQL.
5. Независимые SQLAlchemy sessions и immutable данные каждой отправки.
6. Повторная проверка контекста перед отправкой, интеграция с остановкой #718.
7. Recovery после рестарта и незавершённой фиксации результата.
8. Диагностика, синтетические проверки, совместимость, миграция, выпуск и откат.

### 2.2 Разделение владельцев

| Задача | Владеет | Связь с #716 |
| --- | --- | --- |
| #715 | Размер 2–50; application/SQL validation; input/output MCP schemas | #716 использует общие MIN/MAX, не копирует лимит и не переписывает #715 |
| #716 | Dispatch, распределённый admission, fairness, rate control, recovery | Этот документ |
| #718 | Любое новое входящее от адресата; durable stop; уведомление Марку; scoped readback | Обязательная интеграция перед включением параллельности в Production |
| #696 | Personal Telegram/MTProto | Новый транспорт и автоматический fallback здесь не вводятся |
| #700 | Файлы/документы | Текстовая серия не начинает принимать документы |

Правильная ссылка на увеличение размера: https://github.com/AlphaCenter/gate/issues/715. Ссылка #undefined в исходной issue #716 ошибочна.

Известный документ #715:
https://github.com/AlphaCenter/gate/blob/d589c71d0ab305520fde94177c2fdb7d464caad7/docs/specs/ISSUE-715-REPLY-SEQUENCE-LIMIT-50.md

На момент чтения #715 имеет отдельное implementation execution; #718 — отдельное specification execution. Это не подтверждает их готовность или принятие.

**Совместный контракт закреплён по опубликованному ТЗ #718:**
https://github.com/AlphaCenter/gate/blob/a05f13658b25ae953c6c852baa7d0ea54eb95703/docs/specs/ISSUE-718-STOP-ON-INBOUND.md

#718 владеет reply_dialog_guards, reply_connector_admission, reply_inbound_signals, canonical reply_sequences/items и notification outbox. #716 расширяет те же sequence/items диспетчерскими полями и добавляет permits/budgets. Второй реестр состояния не создаётся. Реализацию общих файлов выполнять последовательно, после принятой базовой схемы #718; это техническая зависимость, а не новое поручение соседнему Work.

Приняты без изменения: session advisory dialog lock; send deadline 20 секунд; lock acquisition timeout 2 секунды; обязательный expected_inbound_revision; terminal sequence states active/completed/stopped; legacy_reapproval_required. Согласование двух опубликованных проектов здесь выполнено на уровне документа; принятие #716 исполнителем #718 не заявляется.

### 2.3 Не входит

Массовая рассылка, генерация текста, интеллектуальный разбор ответа, автоматическое продолжение после «да», произвольные интервалы, новые аккаунты, оплата ускоренных рассылок, обход Business window, самостоятельное возобновление остановленных серий, UI-конструктор и отправка реальным контактам для нагрузочного тестирования.

## 3. Что установлено чтением текущего кода

Все перечисленные наблюдения относятся к main 9630135f5ac8098c3bf9f63fc6a691cb96f2a40e.

| Путь / элемент | Наблюдение | Следствие для проекта |
| --- | --- | --- |
| app/worker/reply_sequences.py / run_reply_sequence_once | for item in due с последовательным await sender | Сетевая отправка одной серии задерживает другие |
| _discover_sequence_rows | LIMIT 500 по всем draft rows с JSON metadata, включая историю | История вытесняет новые серии; batch способен разрезать одну серию |
| _due_items | Группировка/сортировка; предыдущая попытка должна быть sent с finished_at | Сохранить правило паузы, добавить полноту membership |
| _sequence_superseded | Отдельный запрос до sender | Сам по себе не устраняет гонку с новым входящим |
| _record_sequence_outcome | Блокирует все ReplyDraft данного аккаунта | Сузить запись до конкретной серии/позиции; убрать межсерийные блокировки |
| app/infrastructure/reply_sequences.py | Queue metadata в ReplyDraft.prompt_context; один commit вызывающего слоя | Нужен индексируемый реестр очереди и атомарная dual-write при переходе |
| app/infrastructure/reply_sends.py | Существующий conversation fence; reservation commit до provider; finalization отдельно | Переиспользовать sender; не вызывать Telegram напрямую из scheduler |
| app/models/domain.py / SendAttempt | UNIQUE(reply_draft_id); reserved/sending/sent/failed/unknown/cancelled | Не заводить второй независимый журнал отправки |
| app/infrastructure/reply_send_recovery.py | Stale sending превращается в unknown; reserved в cancelled | Expiry не даёт права повторной отправки; нужен sync состояния серии |
| app/services/telegram.py | Общий process-owned httpx client; max_connections=10; фазовые timeout | Client можно разделять, session — нельзя; нужен также общий deadline операции |
| TelegramApiError / parser | error_code/description/http_status; retry_after не извлекается | Добавить структурное безопасное поле без копирования raw response |
| app/main.py | Worker стартует по существующему флагу; cancellation на shutdown | Добавить bounded supervisor/drain; не менять enabled самовольно |
| app/core/config.py | Worker default false; poll default 5 sec, диапазон 1–30 | Сохранить default-off; новые gates также off |

Текущая main не проверялась как deployed revision. Реальные очереди, содержимое переписки, контакты и production rows не читались.

## 4. Инварианты — обязательны для всех реализаций

| ID | Инвариант |
| --- | --- |
| I01 | Один confirmed ordered list принадлежит одному workspace/account/project/task/conversation/recipient |
| I02 | Позиции полные, уникальные, 0..N-1; N берётся из контракта #715 |
| I03 | В одной серии максимум один элемент допущен к внешней отправке |
| I04 | Следующий элемент допускается только после sent предыдущего и finished_at + 60 sec |
| I05 | Queued/claimed/rate_wait не означают sent |
| I06 | Один draft имеет максимум один SendAttempt; replay не создаёт новую внешнюю попытку |
| I07 | Unknown не повторяется, не обходится новым transport/account/draft clone |
| I08 | Ошибка серии меняет только её элементы; общий cooldown не отменяет соседние серии |
| I09 | Admission caps действуют на весь deployment, а не отдельно на каждый Python process |
| I10 | Авторизация sending и stop используют общий recipient fence и generation |
| I11 | Истёкшая lease разрешает новый claim только при доказанном отсутствии sending attempt |
| I12 | Завершение устаревшего claim не продвигает серию и не снимает более свежую остановку |
| I13 | Нет shared mutable principal, recipient, command, ORM object или AsyncSession между задачами |
| I14 | Provider I/O не выполняется внутри SQL-транзакции |
| I15 | Нельзя включить v2 параллельность при одновременно работающем legacy dispatcher |
| I16 | Пауза/откат/отключение не очищают unknown, историю sent и подтверждение |
| I17 | Нет фонового авто-продолжения после остановки; новая серия требует нового точного подтверждения |
| I18 | Нет обещания exactly-once Telegram-доставки или прочтения сообщения |

## 5. Архитектура

Выбрано: PostgreSQL как единственный долговечный источник очереди и admission; асинхронный supervisor выполняет ограниченное число независимых задач. Redis/Celery/Kafka и новый внешний сервис не требуются.

~~~mermaid
flowchart TD
  Q["Подтверждение серии"] --> R["Реестр серий и элементов"]
  R --> D["Поиск due и admission"]
  D --> A["Задача адресата A"]
  D --> B["Задача адресата B"]
  A --> S["Существующий sender"]
  B --> S
  S --> T["Telegram"]
  S --> J["SendAttempt и результат"]
  J --> R
  I["Входящее: #718"] --> F["Recipient fence и stop"]
  F --> R
  F --> S
~~~

Слои:

- Application: команды, immutable DTO, политика допуска и классификация результатов.
- Infrastructure: SQL registry/claim/budget/reconciliation, sender integration.
- Worker: supervisor, ограниченный fan-out, wake-up/poll, shutdown.
- Provider adapter: timeout и безопасный Telegram error mapping; не знает продуктовый смысл серии.
- MCP/readback: статус и счётчики под существующей авторизацией; не подменяет scheduler.

Один task обслуживает одну попытку одного draft. Task не живёт 49 минут и не спит минуту между сообщениями. Следующий элемент получает новый durable due-time.

## 6. Модель данных

### 6.1 Канонический реестр reply_sequences

Одна строка на sequence_id; текстов сообщений в таблице нет.

| Поле | Тип / назначение |
| --- | --- |
| id | UUID PK; сохраняется исходный sequence_id |
| workspace_ref, account_id, project_id | Canonical #718; workspace_id в application DTO отображается однозначно в workspace_ref; account/project FK |
| task_id, conversation_id, trigger_message_id | FK на исходный подтверждённый контекст |
| recipient_chat_id | Точный внутренний адресат, не имя; не включать в общие логи |
| bot_resource_id | Стабильный неприватный ключ зарегистрированного bot/connector; не токен |
| expected_account, principal_subject | RU/BY и субъект подтверждения |
| item_count, interval_seconds | Canonical #718: 2..MAX_SEQUENCE_DRAFTS; interval=60; далее size/N означает item_count, не вторую колонку |
| state | active/completed/stopped — canonical #718; unknown/integrity передаются stop_reason и item state |
| next_position | Следующая незавершённая позиция; 0..size |
| next_due_at | UTC timestamptz, nullable только для terminal/blocked |
| dispatch_generation | bigint >=0; увеличивается при новом claim/отмене |
| guard_id, queued_revision, version, stop_reason, stopped_at, stop_receipt_ref | Canonical #718; guard.inbound_revision — версия входящего контекста; version — версия header; далее stop-generation означает эту пару, не отдельную competing authority |
| claim_token, claim_owner, claim_expires_at | Nullable; только preflight claim |
| admitted_attempt_id | Nullable FK SendAttempt; источник истины об отправке остаётся SendAttempt |
| approval_digest, approved_at | Привязка к точному порядку, текстам и binding |
| created_at, updated_at, last_admitted_at | Служебные timestamps; DB UTC |

Сохранить canonical #718 поля/constraints и добавить только отсутствующие dispatcher-поля; inbound revision не копировать в независимый счётчик. Workspace/recipient/address хранятся через guard_id, лишнее дублирование разрешено только как проверяемый immutable snapshot.

Дополнительно UNIQUE(guard_id) WHERE state=active защищает выбранную политику одной активной серии адресату. В stopped-серии незавершённый sending/unknown по-прежнему блокирует новые отправки через существующий conversation/recipient fence. При переносе старых нескольких active они сначала становятся stopped по #718, только затем создаётся partial unique index.

Индексы: partial (next_due_at,id) WHERE state=active; (workspace_id,account_id,recipient_chat_id,state); FK/task indexes. Не индексировать JSON для полного сканирования истории вместо реестра.

### 6.2 reply_sequence_items

| Поле | Контракт |
| --- | --- |
| sequence_id, position | Составной PK; position>=0 |
| draft_id | FK ReplyDraft, UNIQUE: один draft не входит в две серии |
| approved_text_sha256 | Хэш исходного UTF-8 текста; не normalise/trim после подтверждения |
| scheduled_at | Canonical #718: queue_time + position×60 sec; initial_scheduled_at в формулах означает это поле |
| state | scheduled/reserved/sending/sent/failed/unknown/cancelled — canonical #718; слова pending/claimed/dispatching далее обозначают соответственно scheduled/reserved/sending, не новые DB enum |
| attempt_id | Nullable FK SendAttempt; unique при наличии |
| finished_at, safe_error_code | Canonical #718: UTC / безопасный код; completed_at/reason_code в описании — смысловые обозначения |

Размер и непрерывность всего списка проверяются транзакционно при создании/import и в reconciliation. CHECK отдельной строки не доказывает полноту серии.

approval_digest = SHA-256 канонического JSON UTF-8 без пробелов с фиксированным порядком ключей: schema_version=gate.reply-sequence-approval.v1, workspace_ref, account_id, project_id, guard_id, queued_revision, task_id, interval_seconds и упорядоченный список (position,draft_id,approved_text_sha256). UUID — lower-case canonical string; целые — JSON integers; UTF-8 без Unicode normalization. Проверять digest заново по каждому фактически отправляемому immutable binding; текст никогда не логировать. Digest дополняет explicit approval и не создаёт полномочий сам по себе.

Тексты остаются в ReplyDraft. Любое изменение текста, scope, адресата или порядка после approval блокирует отправку: approval_changed. Не отправлять «последнюю редакцию» под старым подтверждением.

### 6.3 reply_dispatch_permits — распределённые слоты

Постоянный ограниченный набор slot rows на deployment. PK slot_id; поля state=free/preflight/dispatching/quarantined, owner_token, sequence_id, draft_id, account_id, bot_resource_id, generation, claim_expires_at, attempt_id, updated_at.

Выбор свободного слота — под row lock/SKIP LOCKED. Одновременно с назначением проверяются account/bot caps под budget locks. Нельзя реализовать общий лимит через SELECT COUNT без блокировки координирующей строки.

- preflight lease можно вернуть в free после CAS и доказательства отсутствия sending attempt.
- dispatching не освобождается просто по истечению времени.
- unknown delivery останавливает серию. Permit можно освободить после доказанного завершения локального coroutine/сетевой операции; unknown о доставке при этом сохраняется.
- При потере процесса/DB-связи, когда завершение операции не доказано, slot=quarantined. Автоматическое снятие такого карантина только по подтверждённому завершению старой worker instance и сверке attempt, не по TTL.
- Поздний ответ может уточнить transport evidence по исходному attempt; он не возобновляет остановленную серию.

Это ограничивает число новых допусков даже при рестартах. Нельзя обещать контроль над временем обработки уже принятого HTTP-запроса внутри Telegram.

### 6.4 reply_transport_budgets

Строки для deployment, bot_resource_id, account_id и recipient key. Поля: resource_kind/resource_key PK, next_start_at, blocked_until, inflight_limit, configured_gap_ms, config_generation, updated_at.

Одна отдельная control row содержит dispatch_mode=disabled/serial/parallel, activation_generation и admission limits. Environment flags разрешают возможность; DB mode и generation — общий фактический режим для всех реплик. Любой deny сильнее allow.

Ключ account — внутренний account_id, а не общая строка RU/BY. Ключ recipient включает workspace/account/chat. Bot bucket нужен отдельно: два Business-аккаунта могут использовать один bot resource.

### 6.5 Истина и транзакции

- SendAttempt — единственный журнал внешней попытки.
- reply_sequences/items — очередь и политика продолжения.
- permits/budgets — ресурс допуска.
- JSON prompt_context — совместимая проекция на переходный период, не второй независимый state machine.
- Все mutation paths сериализуются через общие repository methods. Прямые UPDATE из MCP/worker запрещены архитектурным тестом или код-ревью.

Номер Alembic migration выделить по фактическому единственному head после #715/#718. Исторические migrations не редактировать. Добавить upgrade, schema-drift, downgrade на disposable DB; production downgrade таблиц с историей не выполнять.

## 7. Публичный контракт и совместимость

Существующий gate_queue_approved_reply_sequence сохраняет имя, scope, ordered-list контракт и output #715. В интегрированном выпуске действует обязательное поле expected_inbound_revision из #718: старый клиент без него получает inbound_revision_required; stale revision — recipient_context_changed. Это предусмотренное #718 fail-closed изменение API, не обратная совместимость старого input. Параллельность не задаётся пользователем в каждом запросе и не требует новых OAuth grants.

Queue transaction после включения v2:

1. Вызвать общую валидацию #715, проверить все draft bindings и expected_inbound_revision из #718. После probe/получения dialog guard сверить queued_revision с current inbound_revision; не подставлять свежий номер под старое подтверждение.
2. Под recipient/conversation fence проверить отсутствие другой active/claimed/dispatching серии тому же account+chat. Новый запрос отклоняется sequence_already_active. Не перемешивать две серии одному человеку.
3. Создать registry + все items + approval digest + одну audit-запись; проекцию JSON записать в той же транзакции.
4. Commit; вернуть полный список позиций, queued=true/sent=false.
5. Wake-up — только ускорение: потеря сигнала не теряет durable queue.

Серия другому адресату создаётся независимо. Одинаковый chat_id в RU и BY — разные привязки; ни одна не переводится на другой аккаунт автоматически.

Повтор после потерянного ответа queue не создаёт вторую серию. Сохранить существующий отказ already_queued; не объявлять queue endpoint идемпотентным успешным API, пока отдельный request-id контракт не введён.

Status/readback интегрируется с #718. Нужные данные: sequence_ref, state, total и взаимоисключающие counts scheduled/reserved/sending/sent/failed/unknown/cancelled, next_position, next_due_at, reason_code, rate_wait_until, admitted/inflight flag, observed_at. Агрегат pending=scheduled+reserved можно показывать отдельно, но не прибавлять повторно к сумме. Свободный текст получателя и provider payload в агрегированные health/metrics не включать. Счётчики state должны однозначно классифицировать каждый item и суммироваться до size.

## 8. Scheduler: поиск, fairness и отсутствие барьера

### 8.1 Поиск кандидатов

1. Выбрать только active registry rows с next_due_at<=DB now и без чужого live claim.
2. Упорядочить по next_due_at, затем last_admitted_at NULLS FIRST, затем id.
3. Использовать короткий SELECT FOR UPDATE SKIP LOCKED; загрузить полный membership только выбранных серий.
4. Размер candidate page=100; за tick просматривать до 1000 кандидатов, keyset pagination внутри tick и continuation cursor между ticks. Не возвращаться постоянно к первым 100 blocked rows.
5. Если account/bot/resource недоступен, перенести next eligibility до ближайшего known gate time, не держать permit. Если слот занят без известного срока, release claim и проверить позже; rotate scan cursor, не голодать другим accounts.
6. Исторические terminal rows не участвуют в active discovery.

Claim каждой серии — атомарный CAS: state=active, ожидаемые next_position/generation, нет live claim, нет stop. Один победитель получает claim_token/generation. Локальный set активных task — оптимизация, не распределённая защита.

### 8.2 Supervisor

- Поддерживать bounded set running tasks; local ceiling=global configured cap.
- На завершение любого task сразу освобождать его локальное место и запускать eligible работу.
- Не использовать gather всего найденного batch как условие следующего discovery.
- Ошибка task сериализуется в outcome только этой серии; непойманная ошибка не отменяет соседние задачи.
- При shutdown запретить новые claims, ожидать ограниченный drain, затем отменить оставшиеся с фиксацией unknown/needs_reconciliation по фактическому send phase.
- Нельзя держать свободный SQL transaction, semaphore slot или DB permit во время 60-секундной межсообщенческой паузы.
- Не создавать 1000 asyncio tasks, которые просто ждут semaphore; число живых dispatch tasks ограничено cap.

### 8.3 Due-time

Для позиции i>0:

next_due_at = max(initial_scheduled_at[i], previous SendAttempt.finished_at + interval_seconds, resource_ready_at).

На каждом новом admission проверять формулу по DB, а не только по старому discovery snapshot. Для sent обязательно наличие достоверного finished_at. Sent без finished_at => stopped/integrity_invalid.

Интервал между сообщениями не сокращается после задержки, перезапуска или смены количества реплик. Начальные 49 минут для 50 сообщений — нижняя оценка при идеальных условиях; не SLA.

## 9. Допуск, sender и блокировки

### 9.1 Граница preflight / отправки

Preflight claim не даёт права отправлять. Он позволяет прочитать данные и выполнить существующую ограниченную проверку Business connection.

После probe выполняется **новая короткая transaction admission**:

1. Проверить актуальные config/activation generation.
2. Проверить claim_token, lease, sequence state/next_position, stop generation, точный approval digest и scope.
3. Проверить complete prior sent chain и due-time.
4. Проверить sender conversation fence, personal transport fence, текущую task и поддерживаемый transport.
5. Атомарно получить свободный permit и account/bot/rate admission.
6. Создать существующий SendAttempt с SENDING; связать item/permit с attempt_id; зафиксировать state dispatching и generation.
7. Commit.
8. Только этот победитель вызывает существующий Telegram sender. При неуспешном commit вызова нет.

Этапы 5–6 реализуются внутри sender reservation transaction, а не независимыми commits worker и sender. Иначе возможны потерянный slot, отправка без claim или расхождение stop.

### 9.2 Единый порядок блокировок

Использовать обязательный внешний PostgreSQL session advisory dialog lock из #718. Preflight/probe — до этого lock. Никто не запрашивает advisory lock, уже удерживая row lock. Порядок внутри guard:

1. При изменении существующего SendAttempt: attempt row, затем conversation/context, как в нынешнем outcome path.
2. Для нового reservation без attempt: conversation, затем context/drafts; чужую attempt row под conversation lock не ждать, active fence читать без её захвата.
3. Sequence header, затем items в порядке position/PK.
4. Budget rows в стабильном порядке (resource_kind,resource_key).
5. Permit slot.

Ingress/queue под тем же advisory gate используют conversation/context, если они нужны, затем header/items; они не блокируют SendAttempt после context. Recovery, который изменяет только attempt, не удерживает attempt lock при последующем получении dialog gate: сначала закончить recovery-транзакцию, затем отдельно guarded projection reconciliation. Recovery, меняющий вместе attempt/projection, получает outer gate до транзакции и следует attempt-first.

Discovery — отдельная короткая транзакция, полностью завершённая до sender/guard. Account/bot budget update не запрашивает dialog lock, удерживая budget lock. Так нет обратного пути slot/budget → dialog/context.

Session advisory connection удерживается до окончания HTTP/outcome и освобождается/invalidate в finally; SQL-транзакции во время HTTP нет. Transaction-pooling mode недопустим. Acquire timeout=2 секунды, общий text provider deadline=20 секунд — параметры #718.

Для cap=4 нужен отдельный guard connection pool с 4 sender connections на процесс и 2 дополнительными ingress connections, плюс обычный transaction pool: минимум 2 свободных connections для ingress/health/recovery при насыщенных sends. Реальный суммарный budget равен сумме всех процессов и резервов; перед активацией он должен помещаться в подтверждённый PostgreSQL connection limit. В случае нескольких реплик не умножать пул молча: конфигурация deployment фиксирует budgets и ceiling. При невозможности обеспечить резерв уменьшить global cap, а не отнимать соединения у ingress.

На preflight acquire guard не держать DB permit. Не более local cap задач могут ждать guard; timeout => deferred/no-send, соседние адресаты доступны. Для ingress timeout применяется 503 без частичного receipt/ACK по #718.

Deadlock retry допустим только для SQL-транзакции без внешнего эффекта, максимум 2 повторения. После отправки сеть не повторяется. До merge проверить все mutation paths по этой таблице, в том числе новые paths #718.

### 9.3 Сеансы и ошибки

Каждый dispatch task имеет отдельную AsyncSession. В другие tasks передаются только UUID/строки/числа/immutable DTO. Общий httpx.AsyncClient допустим; закрывается только при завершении lifespan.

Не считать Retryable: account mismatch, recipient mismatch, scope loss, superseded task, approval_changed, failed/unknown. Эти результаты останавливают данную серию.

Служебные состояния claim_lost, not_due, capacity_wait, rate_wait до SendAttempt — deferred/no-send. Они не должны превращаться в failed и отменять серию. Явно отделить их от ReplySendError, который нынешний worker трактует как terminal stop.

Если другая реплика уже создала SendAttempt, сверить binding и reconcile; не записывать stopped только потому, что «кто-то уже отправляет». Idempotent replay sent не прибавляет второй раз счётчик и не сдвигает next_position повторно.

## 10. Остановка #718 и честная граница внешней доставки

#716 не анализирует смысл входящего. Любое принятое #718 событие того же account/chat останавливает pending остаток; другие адресаты продолжают.

Общий интерфейс обязан предоставлять:

- recipient generation, зафиксированную при approval;
- stop_sequence(reason, source_ref) с CAS и terminal stop;
- проверку generation/stop в sender reservation;
- readback terminal reason и pending/inflight distinction;
- outbox уведомления владельцу, который принадлежит #718.

Порядок гонки:

| Кто победил в общей сериализации | Результат |
| --- | --- |
| Inbound/stop зафиксирован до admission | SendAttempt не создаётся; provider calls=0 |
| Preflight идёт, затем stop | Финальный admission видит stop; отправки нет |
| Sending уже допущен до stop | Этот один item может завершиться; хвост отменяется; статус честно показывает in-flight |
| Provider уже получил запрос | Отозвать его гарантированно невозможно; не обещать отсутствие поздней доставки |
| Stop, затем поздний success текущего attempt | Записать sent текущего item; series остаётся stopped; новый item не запускается |

**Критически:** claim, admission commit и фактический старт HTTP — разные события. По опубликованному #718 выбран общий session advisory lock, удерживаемый от окончательного admission-check до HTTP и фиксации outcome. Ingress берёт тот же lock; если он занят, ждёт не более 2 секунд и возвращает 503 без нового receipt/ACK. После committed stop новых HTTP-start этой серии нет; ранее начатый Telegram-запрос может завершиться позже.

Это не обещание мгновенно прекратить запрос в момент, когда человек нажал «Отправить»: update ещё должен быть принят, а текущий guarded send может задержать приём. Text HTTP ограничен общим deadline 20 секунд. Потеря guard connection запрещает новый HTTP; уже начатый запрос локально отменяется с unknown. Нельзя отпускать guard и оставлять detached task продолжать отправку.

Никакого альтернативного early receipt commit/отдельного stop-request пути #716 не вводит: receipt, revision, stop, отмена и notification outbox атомарны по #718. Таблица гонок выше читается относительно этой сериализации. Пока ingress не committed, нельзя сообщать владельцу «остановлено». Direct send старого draft серии проходит тот же guard и stop-check.

До совместных race-tests нельзя объявить Production готовым. DB direct/session-pooling и непрерывность ingress — фактические предпосылки активации, а не предположения.

## 11. Rate limit и ресурсные пределы

### 11.1 Начальные значения

Это **консервативные проектные параметры Gate**, не обещанные Telegram квоты.

| Параметр | Начальное значение | Разрешённый диапазон / смысл |
| --- | --- | --- |
| parallel_enabled | false | Отдельное включение после приёмки |
| v2_enabled | false | Legacy по умолчанию до согласованного cutover |
| global_inflight | 4 | 1..8; fleet-wide для sequence dispatch; у иных send paths общий rate/cooldown, а не обещание того же sequence concurrency cap |
| account_inflight | 2 | 1..global; позволяет две серии одного аккаунта |
| bot_inflight | 4 | 1..global; RU/BY могут делить bot |
| account_min_start_gap | 1000 ms | >=1000; между admission новых sequence sends |
| bot_min_start_gap | 250 ms | >=250; бюджет sequence-потока |
| recipient_min_start_gap | 1000 ms | Дополнительный transport guard; series всё равно 60 sec |
| preflight_lease | 60 sec | Длиннее общего preflight deadline |
| preflight_total_deadline | 30 sec | Включая все connection probes |
| text_send_total_deadline | 20 sec | Дополняет фазовые httpx timeouts |
| shutdown_drain | 25 sec | Затем reconciliation; не новое отправление |
| candidate_page / scan_cap | 100 / 1000 | На tick; keyset continuation обязателен |

Конкретные новые env-настройки: GATE_REPLY_SEQUENCE_DISPATCH_V2_ENABLED=false, GATE_REPLY_SEQUENCE_PARALLEL_ENABLED=false, GATE_REPLY_SEQUENCE_MAX_INFLIGHT=4, GATE_REPLY_SEQUENCE_MAX_INFLIGHT_PER_ACCOUNT=2, GATE_REPLY_SEQUENCE_MAX_INFLIGHT_PER_BOT=4. Существующие GATE_REPLY_SEQUENCE_WORKER_ENABLED и GATE_REPLY_SEQUENCE_POLL_SECONDS сохраняются. Gap/lease/deadline/page значения первой версии — именованные кодовые константы, а не произвольные дополнительные env; их значения приведены в таблице. DB control по умолчанию disabled; env true без разрешённого control generation не включает отправку.

Проверять согласованность конфигурации при startup; нулевые/отрицательные/несогласованные значения => fail closed v2, явный health reason. Разные config_generation на репликах запрещают admission у отставшей реплики.

При parallel=false и v2=true используется тот же алгоритм с global cap=1; semantic safety не меняется. При existing worker_enabled=false не запускать dispatcher вовсе.

### 11.2 Алгоритм rate admission

В короткой reservation transaction блокируются нужные budgets в стабильном порядке. Теперь=DB clock. Если now меньше max(next_start_at,blocked_until), вернуть rate_wait без SendAttempt/permit и с точным next_due_at.

Если допущено, установить next_start_at=now+gap. Не резервировать пачку будущих стартов и не спать с permit до будущего окна. Rate debit после commit не возвращать «назад», даже если до HTTP получен stop: безопаснее недоиспользовать бюджет. Grant не является разрешением отложенно отправить пачку: под dialog guard перед самим HTTP подтвердить актуальность start window (не более 100 ms после admission). Если окно потеряно из-за остановки процесса/event loop, доказанно не начатую операцию завершить cancelled с safe_error_code=dispatch_start_window_expired и остановить её серию; не делать поздний burst и не переигрывать SendAttempt. Это консервативный отказ; точная миллисекундная частота внутри Telegram не гарантируется.

Общий bot budget должен учитывать другие отправляющие пути того же bot credential: ручные approved replies, документы и #718 notification. Перед включением составить inventory call sites. Для общего transport gate они проходят тот же bot/recipient admission либо используют документированный зарезервированный бюджет с общим cooldown. Нельзя назвать лимит fleet-wide, если часть отправителей его обходит.

Не вводить скрытый повтор существующего ручного вызова при rate_wait: безопасный no-send ответ или его существующий durable путь. Другие боты/инсталляции не получают чужой cooldown.

### 11.3 Telegram 429

Добавить TelegramApiError.retry_after_seconds: nullable integer; bool/negative/нецелое не принимается. Число переносится в безопасный результат, raw body/URL/token не сохраняется.

- Достоверный 429 текущего send — failed, серия останавливается по существующей политике «ошибка останавливает».
- Retry_after задаёт общий blocked_until для bot_resource и account; если scope лимита неизвестен, консервативно блокировать соответствующий bot, не всю систему.
- Другие серии не отменяются; переходят в rate_wait.
- Cooldown монотонный: max(старый, response_received_at+retry_after).
- При 429 без пригодного retry_after — внутренний fallback cooldown 60 sec и operational warning.
- Большой retry_after не обрезать вниз; при overflow/непредставимом timestamp блокировать ресурс для ручного разбора.
- После cooldown продолжаются только untouched активные серии. Ошибочная серия автоматически не возобновляется.
- Сетевой timeout/5xx/разрыв => unknown, не 429 и не безопасный retry.
- Paid broadcasts и allow_paid_broadcast не включать.

Telegram публикует рекомендации по частоте в чате и broadcast и возвращает retry_after при flood control. Эти сведения не устанавливают гарантированную квоту конкретного Business-аккаунта. Ссылки и дата проверки — в разделе 22.

## 12. Finalization, recovery и отмена процесса

Finalization по attempt_id и binding в одной короткой транзакции обновляет существующий SendAttempt, item и queue projection. Продвижение cursor — CAS ожидаемой позиции/attempt/generation; повтор finalization — no-op для прогресса.

| Событие | Действие |
| --- | --- |
| sent с доказанным provider message id | item=sent; если series active, next_position+1 и due; последний => completed |
| failed | item=failed; pending хвост cancelled; series stopped |
| unknown | item=unknown; pending хвост cancelled; series stopped, stop_reason=send_unknown |
| stopped/superseded до sending | Текущий несостоявшийся item и хвост cancelled; attempts не создавать |
| Ошибка DB до admission commit | Отправки нет; безопасное повторение claim после rollback |
| Crash после admission, до/во время HTTP | Sending не переигрывать; stale recovery => unknown |
| Crash после provider success, до DB commit | Unknown до доказанного reconciliation; не выводить sent из предположения |
| Crash после SendAttempt commit, до queue projection | Reconciler восстанавливает projection по attempt без provider calls |
| Истёк preflight claim без attempt | CAS освобождение, generation++; новый worker может проверить заново |
| Неожиданное исключение до sending | Безопасный infrastructure defer с ограниченным backoff; не infinite tight loop |
| Cancellation после sending | Не ловить как простой no-send; сохранить ambiguity и durable recovery pointer |

Reconciler работает bounded batches, идемпотентно и независимо от process-local task set. Не переписывает terminal stop на active/completed; может только уточнить состояние фактически выполнявшегося item.

Если все permits quarantined, новые sends блокируются, health=degraded с точной причиной; очередь не удаляется. Безопасность сильнее обещания безусловной живучести при потере всех worker.

## 13. Миграция существующих JSON-серий

Нужен управляемый переход, а не автоматический запуск старой истории.

1. Сначала additive schema deploy при v2=false/parallel=false. Legacy behavior сохраняется.
2. Отдельным разрешённым действием остановить admission legacy worker; drain или признать unresolved attempts unknown. Подтвердить отсутствие старых worker instances с правом dispatch.
3. Приостановить queue creation на короткое окно cutover; вернуть явный retry-later без частичной записи.
4. Import complete groups по sequence_id, а не первые 500 drafts. Использовать keyset pagination полных групп.
5. Проверить unique IDs/positions/size, общий binding/interval, соответствие SendAttempt, approval metadata и отсутствие нового inbound/stop.
6. Невалидные/неполные/ambiguous группы импортировать stopped с reason integrity_invalid/send_unknown; никаких отправок.
7. Все незавершённые legacy серии импортировать stopped reason=legacy_reapproval_required по #718. В частности, текущие тексты без approval digest нельзя выдать за доказанно старое подтверждение. Продолжение только через новое подтверждение и новые drafts; миграция не генерирует ложные recipient_replied notifications. Терминальная история сохраняется.
8. В той же транзакции пометить legacy JSON pending state=managed_v2, чтобы старый _due_items не мог их отправить. Это защитная совместимость, не разрешение держать старые реплики живыми.
9. Import повторяем: тот же sequence_id+состав => no-op; другой digest/binding => conflict, без перезаписи.
10. Сверить counts/digests; активировать DB mode serial только после проверки. Возобновить queue creation по v2.
11. Mixed legacy/v2 senders во время rolling deploy запрещены. Новые v2 rows всегда имеют legacy projection, запрещающую старый dispatch.
12. Parallel activation — отдельный шаг после serial и race acceptance.

Импорт не читает/выгружает payload в Git. Receipt содержит только counts, безопасные digest и причины. Нельзя автоматически пересоздать отменённый хвост новыми draft IDs для обхода старого unknown.

## 14. Наблюдаемость и эксплуатационные сигналы

Обязательные безопасные метрики:

- active/pending/stopped series и stopped по safe reason send_unknown/integrity_invalid;
- dispatcher running tasks и permit counts по state;
- oldest_due_age_seconds;
- admission_deferred по reason;
- sent/failed/unknown outcomes, duplicate claims prevented;
- bot/account cooldown active counts, rate_wait duration;
- reconcile lag, quarantined slots, claim expiration;
- deployed revision, DB activation_generation, worker mode.

В метриках не использовать chat_id, текст, ФИО, sequence UUID как label: это и приватность, и высокая cardinality. Scoped audit может хранить внутренние references по существующей политике; health общедоступные поля — только агрегаты.

Алерты для первой версии:

- любое новое unknown/quarantined — немедленный operational incident;
- oldest due >60 sec без cooldown/capacity reason при работающем worker — warning;
- reconcile lag >120 sec — warning;
- mismatch generation, duplicate actual provider send или wrong recipient/account — stop activation и incident.

Worker heartbeat success без фактического прогресса не доказывает здоровую очередь. Status должен показывать отдельно «loop жив» и «eligible очередь продвигается».

## 15. План файлов и владение

| Файл / группа | Будущая работа |
| --- | --- |
| app/application/reply_sequences.py | Сохранить #715; общие DTO/errors/approval invariants |
| app/application/reply_sequence_dispatch.py — новый | Admission results, command/token/generation contracts |
| app/infrastructure/reply_sequences.py | Атомарная запись registry/items/projection; один active recipient |
| app/infrastructure/reply_sequence_dispatch.py — новый | Claim, due scan, permits, budgets, CAS/reconcile |
| app/models/reply_sequences.py — новый или canonical #718 | Registry/items/budgets/permits; импорт модели в metadata |
| alembic/versions/<next>_reply_sequence_dispatch.py | Additive schema/index/constraints; согласованный migration head |
| app/worker/reply_sequences.py | Supervisor вместо serial batch, точная outcome запись |
| app/infrastructure/reply_sends.py | Общая reservation transaction, stop/claim/budget guards, без bypass sender |
| app/infrastructure/reply_send_recovery.py | Sync unresolved attempts с queue/permits; не retry |
| app/services/telegram.py | Структурный retry_after; общий transport rate gate при согласованном inventory |
| app/core/config.py, app/main.py | Default-off gates, startup validation, supervisor drain |
| app/application/mcp_status.py и MCP schemas | Совместный с #718 readback; scope и backward-compatible поля |
| tests/test_reply_sequences*.py | Существующая регрессия #715 |
| tests/test_reply_sequence_dispatch*.py — новые | Deterministic concurrency, PG claims, recovery, fairness |
| tests/test_reply_sends_postgres.py | Sender races, isolation, lock order, idempotency |
| tests/test_main_* и worker health | Disabled startup, shutdown, bounded resources |
| README.md; docs/PROJECT_HANDOFF.md | Пользовательское поведение и актуальный указатель |
| docs/operations/REPLY-SEQUENCE-PARALLEL-ROLLOUT.md — новый | Runbook реализации и redacted acceptance receipt |

Предполагаемые новые имена можно адаптировать к уже принятому #718, но в implementation PR обязательна таблица «поле/метод → единственный владелец». Ни #715, ни #718 не принимаются в работу автоматически этим документом. Retention новых записей следует существующей scoped policy и #718; этот scope не разрешает удаление истории или raw payload.

## 16. Порядок разработки после отдельной команды

1. Re-query main/#715/#716/#718/PR/leases и pinned Ops route. Записать actual base SHA; продолжить эту ветку/PR или согласованно rebase после dependencies.
2. Реализовать после принятой базовой схемы #718: использовать опубликованный mapping разделов 2/6/9/10, обновив его только при изменении #718. Общие файлы не менять одновременно в двух implementation executions.
3. Утвердить технический lock-order и call-site inventory. Это работа исполнителя α/β, не просьба Марку выбрать SQL детали.
4. Добавить schema/repositories и synthetic migration/import tests с v2 off.
5. Реализовать atomic queue/admission и sender guards; сначала cap=1.
6. Реализовать bounded supervisor, slots/budgets, per-account fairness; затем cap>1.
7. Реализовать 429 cooldown, recovery/late outcome/shutdown.
8. Подключить #718 stop/readback и выполнить общие races.
9. Обновить operational docs, выполнить targeted local checks, затем один необходимый literal-head CI.
10. Тестировщик проверяет exact head независимо, выдаёт доказательный ACCEPT/REJECT по матрице. При REJECT разработчик исправляет, reviewer проверяет новую ревизию.
11. Только после отдельной актуальной release authority — staging/rehearsal, Production deploy, cutover, bounded canary.
12. Закрыть #716 лишь после product/runtime receipt, либо честно остановиться на repository_ready с указанной deployment gate.

Разбивка — этапы одного результата; не плодить issue/PR на каждый пункт без необходимости. Нельзя подменить полный результат «добавлен asyncio.gather».

## 17. Среда и методика тестирования

Python 3.12, PostgreSQL 17, существующие pinned dependencies/constraints. Отдельная одноразовая DB/schema. Production DSN и реальные payload запрещены для тестов.

- Fake Telegram с asyncio.Event/barriers: записывает binding, начало/конец, correlation/attempt reference; удерживает A по сигналу.
- Virtual clock для due/cooldown, без реальных минут sleep. Для SQL времени injectable clock должен соответствовать одному DB timeline.
- Настоящие независимые PostgreSQL connections/sessions для competing workers. In-memory fake не доказывает SKIP LOCKED, unique constraint и deadlock safety.
- В тесте concurrent overlap зафиксировать started(A)<finished(B) и started(B)<finished(A); одни только два asyncio task этого не доказывают.
- Связать fake response message IDs только с синтетикой.
- Тесты recovery используют новый service/session/process context и состояние только из DB.
- Timeout tests должны иметь внешний watchdog, чтобы зависание было fail, а не вечным CI.
- PostgreSQL skip не считается PASS; отчёт отдельно перечисляет выполненные, skipped и blocked проверки.

План команд после появления файлов:

~~~bash
ruff check app tests
mypy app
pytest -q tests/test_reply_sequences.py tests/test_reply_sequences_postgres.py tests/test_reply_sequence_dispatch.py tests/test_reply_sequence_dispatch_postgres.py tests/test_reply_sends_postgres.py tests/test_reply_send_recovery.py tests/test_reply_send_recovery_postgres.py tests/test_mcp_surface_consistency.py
alembic upgrade head
alembic check
~~~

Новые имена тестовых файлов — план. После targeted checks выполнить штатные обязательные gates проекта для exact head. Не менять CI ради обхода failures. Draft skipped backend не означает зелёную проверку реализации.

## 18. Матрица приёмки для тестировщика

Каждая строка требует test name, exact commit, результат и артефакт/лог. «Посмотрел код» не заменяет тест гонки.

| ID | Сценарий | Ожидаемое доказательство |
| --- | --- | --- |
| T01 | Две серии одного account, разные chats; обе due | Два send запроса in-flight одновременно при свободных caps и rate admission |
| T02 | Серии RU/BY, разные accounts | Пересечение сетевых интервалов; строгая привязка каждого запроса |
| T03 | A заблокирована fake provider, B свободна | B завершает send, пока A удерживается; discovery продолжает работать |
| T04 | A/B по 50 элементов | Полный порядок 0..49 каждой, ровно 100 правильных вызовов без реального ожидания |
| T05 | Предыдущий sent+59/+60 sec | До 60 provider calls=0; после 60 eligible |
| T06 | Worker долго отсутствовал | Нет catch-up пачки; следующая пауза от actual finished_at |
| T07 | Плохой последний draft среди 50 | Нет partial queue/approval/audit; сохранены проверки #715 |
| T08 | Same recipient, две active series | Второе queue отклонено атомарно, включая competing requests |
| T09 | Одинаковый chat_id в разных accounts | Никакого смешивания binding; независимая остановка |
| T10 | Неверный project/workspace/principal/expected account | No-send; metadata чужого scope не раскрываются |
| T11 | Редакция текста после queue | approval_changed; provider calls=0 для изменённого item |
| T12 | Две реплики claim одного due | Один победитель; один SendAttempt; максимум один provider call |
| T13 | Повтор finalization / replay sent | Один переход cursor, один counted success |
| T14 | Stale claim без attempt | Новый claim разрешён только после CAS; старый token не отправляет |
| T15 | Lease истекла при sending | Вторая реплика не повторяет запрос |
| T16 | Поздний stale callback | Не продвигает новый claim и не снимает stop |
| T17 | Global cap 4, три worker процесса | Максимум 4 admitted/inflight slots суммарно, не 12 |
| T18 | Account cap 2 / bot cap 4 | Ограничения соблюдаются совместно для обоих RU/BY |
| T19 | Свободен один слот, много кандидатов | Не создаётся неограниченный task fan-out |
| T20 | Все permits заняты/карантин | No new send, health показывает точную причину |
| T21 | Account/bot gap; искусственная задержка после admission | Оба gap соблюдены на разных репликах; истёкшее start window даёт доказанный no-send, а не поздний burst |
| T22 | 429 с retry_after | Текущая серия stopped; ресурс cooldown сохранён; соседняя серия не cancelled |
| T23 | 429 malformed/огромный retry_after | Безопасный fallback либо blocked resource; нет overflow/раннего продолжения |
| T24 | Restart внутри cooldown | Ограничение сохраняется из DB, не сбрасывается |
| T25 | Ручная отправка/документ/notification того же bot | Общий bot budget/cooldown не обходится |
| T26 | Failed в A, B активна | Хвост A cancelled; B продолжает по своему расписанию |
| T27 | Unknown в A, B активна | A не retry; B другого chat продолжает при доступном permit |
| T28 | Неожиданная ошибка одного task | Соседние task не отменяются; supervisor не падает целиком |
| T29 | Inbound до claim | A не отправляет; B не остановлена |
| T30 | Inbound во время preflight | Финальный admission A отклонён |
| T31 | Inbound одновременно с admission | Детерминированные оба порядка; соблюдён общий fence #718 |
| T32 | Inbound при уже начатом HTTP | Хвост остановлен; in-flight исход записан честно, без обещания unsend |
| T33 | Late success после stop | item sent, series остаётся stopped; следующего send нет |
| T34 | Voice/document/прочее inbound из #718 | Остановка до транскрипции/долгой обработки; общая интеграция |
| T35 | Crash до admission commit | Ни отправки, ни частичной reservation |
| T36 | Crash после commit до HTTP | Recovery unknown/no blind resend; permit согласован |
| T37 | Успех provider, crash до DB outcome | Не выдаётся выдуманный sent; нет повторной отправки |
| T38 | SendAttempt sent, projection отсутствует | Reconcile чинит cursor ровно один раз без provider calls |
| T39 | SendAttempt failed/unknown, хвост ещё pending | Reconcile останавливает/отменяет хвост и делает статус видимым |
| T40 | Shutdown с preflight и sending | Admission закрыт; bounded drain; ambiguity сохраняется |
| T41 | >500 исторических rows + новая due серия | Новая серия обнаружена и отправлена; история не вытесняет |
| T42 | Series items пересекают страницу import | Загружается полный список; нет пропуска позиций |
| T43 | Неполный membership / duplicate positions / missing finished_at | stopped/integrity_invalid, no-send |
| T44 | 1000 rate-blocked кандидатов, затем eligible другой account | Keyset continuation обнаруживает eligible; нет вечного starvation |
| T45 | Две большие конкурентные серии / finalizer / queue | Watchdog не фиксирует deadlock; locks scoped, не весь account |
| T46 | Config mismatch / уменьшение caps | Старые config generations не допускают новые sends; existing не убиваются как no-send |
| T47 | Worker off / v2 off / parallel off | Нужные gates соблюдены; нет непрошеного нового dispatcher |
| T48 | Legacy import повторно | Idempotent no-op; history/unknown не потеряны |
| T49 | Legacy active без approval digest | Заблокирована до нового подтверждения; не autoplay |
| T50 | Старый worker видит managed_v2 JSON | Не отправляет v2 pending rows |
| T51 | Cutover при live legacy attempt | Процедура останавливается; нет mixed dispatch |
| T52 | Rollback mode / disabled | Новых admission нет; accepted/sent/unknown состояние сохранено |
| T53 | Public/scoped health | Нет токенов, chat IDs, текстов и неверного cross-scope readback |
| T54 | Queue response/description #715 | Полный 2–50 контракт сохраняется после #716 |
| T55 | Slot release при unknown, task завершён / task неизвестен | Первый освобождает concurrency только, второй quarantine; delivery fence остаётся |
| T56 | Approval, stop и dispatch в одном concurrency stress | Нет неправильного recipient/account и sends после запрещающей generation |
| T57 | Migration upgrade/downgrade/drift на disposable DB | Целостная схема; unique/index/FK реально действуют |
| T58 | Общая регрессия sender, documents, personal fence, recovery | Не нарушены существующие защиты и false default |
| T59 | Уведомление stop/readback #718 при параллельных сериях | Точный affected sequence/count; дедупликация уведомления проверена #718 |
| T60 | Буквальная граница HTTP start / terminal stop commit | Совместный transport-start gate соблюдён, либо выпуск отклонён; preflight-check недостаточен |

Производительность — на синтетической базе: 10 000 terminal series и 200 active, 4 permits. EXPLAIN ANALYZE должен подтверждать indexed active scan без полного прохода terminal history; за один tick не более 1000 candidate rows и не более 4 live dispatch tasks. На зафиксированной CI/rehearsal среде 95% eligibility-to-admission <= 2 poll intervals + 1 sec, когда нет rate/capacity/DB contention. Измеряется конкретная среда; это не обещание публичного SLA.

## 19. Выпуск на Production

**Этот раздел — будущий runbook, не разрешение выполнить его сейчас.**

### 19.1 Release prerequisites

- Все T01–T60 и зависимости #715/#718 имеют exact-head доказательства.
- Нет непроверенной миграционной развилки, конфликтующего активного исполнения или незакрытого критического REJECT.
- Известны exact deployed revision, target installation/workspace, worker replica inventory, текущие flags и last-known-good.
- Зафиксирована отдельная authority на schema apply, cutover, activation и ограниченные внешние сообщения. Разрешение разработки само по себе не делает эти действия разрешёнными.
- Подтверждено, что queued реальные серии не начнут отправляться при техническом включении.
- Сохранён redacted baseline и rollback conditions.

### 19.2 Этапы выпуска

| Этап | Действие | Условие перехода |
| --- | --- | --- |
| R0 | Exact-head CI + независимая проверка | Все обязательные проверки passed, не skipped |
| R1 | Одноразовый PG rehearsal/migration/import/recovery | Повторяемый receipt без реальных данных |
| R2 | Deploy кода default-off через зарегистрированный Ops | Receipt deployed SHA совпадает с утверждённым; deploy не доказывает migration |
| R3 | Отдельное применение additive migration зарегистрированным DB/Ops действием | Schema fingerprint и compatibility verified |
| R4 | Остановка legacy admission, drain, миграция по разделу 13 | Legacy senders отсутствуют; counts согласованы |
| R5 | v2 serial, только выбранный owner test scope | Readback/queue/recovery/stop работают |
| R6 | parallel с caps 4/2/4 в ограниченном scope | Synthetic concurrency proof + bounded live receipt |
| R7 | Включение для утверждённого рабочего scope | No unknown/duplicates/wrong route, #718 stop и notification приняты |

Текущий AGENTS задаёт короткий маршрут /ops-deploy production target=gate через зарегистрированный AQUB Ops requester. Не заменять его Railway UI/login, старым auto-deploy описанием или новым workflow. Перед выпуском перечитать актуальную route policy. Deploy receipt не заменяет отдельное подтверждение migrations/config/runtime.

### 19.3 Bounded live canary

Предлагаемый лимит для отдельного будущего разрешения: две серии по два синтетических текста в двух точных owner-controlled тестовых диалогах, всего **4 sequence messages**, без реальных контрагентов. Если нужны notification #718 и дополнительный inbound сценарий, они отдельно названы в разрешении с отдельным cap; по умолчанию эти сообщения не включены.

Один сценарий — разные адресаты одного account; RU/BY cross-account полностью доказывается offline, live только если отдельные точные targets разрешены. Никакого автоматического увеличения canary ради «поймать перекрытие»: быстрые live HTTP могут не пересечься; concurrency уже доказывается barrier-тестом.

Проверить exact account/chat через закрытый scoped канал, actual delivery records, паузы, timestamps, отсутствие незапрошенного хвоста. В Git — только redacted refs/counts/SHA. Если нет двух разрешённых targets, live gate остаётся blocked; это не основание отправлять контактам.

Наблюдение: до окончания выбранных серий и минимум двух дополнительных poll cycles. Unknown, wrong route, duplicate send, нарушение паузы или неожиданная серия — немедленный stop admission, incident и rollback decision. Не повторять canary с unknown.

## 20. Откат и аварийная остановка

1. Отключить новый admission в общем DB control generation; все реплики проверяют его перед reservation.
2. Уже допущенные отправки drain в пределах deadline; неизвестный исход сохранить.
3. Parallel → serial допустим без очистки очереди; новые caps не отменяют выполняющиеся attempts. При критическом routing/idempotency дефекте — disabled, не serial.
4. Reconcile durable attempts/permits; не освобождать карантин, пока старый process способен продолжать отправку.
5. Вернуть код только на совместимую проверенную revision. Старый legacy binary нельзя включать отправителем поверх v2 очереди.
6. Additive tables и журнал не удалять production downgrade. При необходимости rollback только приложения, worker disabled и дальнейший forward fix.
7. Не replay unknown и не создавать новые drafts для обхода.
8. Receipt: причина, время, exact before/after SHA, mode/generation, admitted/sent/unknown counts, следующий владелец шага.

Release reviewer должен отдельно проверить rollback rehearsal; «есть git revert» недостаточно.

## 21. Передача, доказательства и критерий готовности

### Пакет разработчику

Этот документ + #716 + актуальные AGENTS/WORK-PARENT/bootstrap + pinned #715/#718 спецификации + current base/head. Начать с интеграционного mapping, затем этапы 16. Не требуются материалы из личной переписки.

### Пакет тестировщику

Exact head, diff allowlist, миграция, команды/среда, test result по T01–T60, barrier timelines, competing-worker traces, DB state assertions, privacy scan, known limitations. Независимый reviewer не исправляет код без отдельного поручения; возвращает ACCEPT/REJECT и доказательства по действующему lifecycle.

### Пакет ответственному за выпуск

Exact accepted head, deploy/migration/config authority, target и expiry, last-known-good, replica inventory, baseline counts, canary cap, rollback plan и stop conditions. Выпуск не выполняется на основании одного слова «CI green».

### Уровни готовности

| Состояние | Что доказано |
| --- | --- |
| specification_ready | ТЗ сохранено, общие риски и checks определены |
| implementation_ready | Код есть, но проверки ещё не завершены |
| repository_accepted | Тесты/ревью exact head passed, без runtime claim |
| deployed_disabled | Код/схема выложены, dispatch activation ещё нет |
| runtime_accepted | Точный canary и operational checks пройдены |
| product_accepted | Марк принял результат; #716 можно закрывать |

Финальная квитанция реализации: base/head/PR; actual deployed revision; migration fingerprint; конфигурация без секретов; таблица T01–T60; runtime evidence; остаточные ограничения; acceptance; один next pointer. Отдельно указывать что не проверено.

**Для текущей стадии:** достаточно записать этот документ, проверить его чтением обратно из GitHub, docs-only diff и ссылку в #716; освободить lease. Разработка, отправки и Production остаются not_started. Следующий ход — Марк.

## 22. Источники и дата проверки

### Код и требования

- #716: https://github.com/AlphaCenter/gate/issues/716
- #715: https://github.com/AlphaCenter/gate/issues/715
- #718: https://github.com/AlphaCenter/gate/issues/718
- Закреплённое ТЗ #718: https://github.com/AlphaCenter/gate/blob/a05f13658b25ae953c6c852baa7d0ea54eb95703/docs/specs/ISSUE-718-STOP-ON-INBOUND.md
- Worker: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/worker/reply_sequences.py
- Queue: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/reply_sequences.py
- Sender: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/reply_sends.py
- Recovery: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/reply_send_recovery.py
- Telegram client: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/services/telegram.py
- Lifespan: https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/main.py

### Первичные внешние источники

Проверены 27.09.2026:

- Telegram Bots FAQ, flood/broadcast guidance: https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this
- Telegram Bot API, ResponseParameters.retry_after: https://core.telegram.org/bots/api#responseparameters
- Telegram Bot API, BusinessBotRights и sendMessage: https://core.telegram.org/bots/api#businessbotrights и https://core.telegram.org/bots/api#sendmessage

Выбранные caps, SQL-модель, lease, fairness и rollout — проектные решения Gate, а не цитаты или гарантии Telegram.

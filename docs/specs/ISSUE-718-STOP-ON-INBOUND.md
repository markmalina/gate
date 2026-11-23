# Gate #718 — Остановка серии при входящем сообщении и уведомление Марка

**Стадия: полное проектирование. Реализация не начата.**  
Дата: 27 сентября 2026 года. Владелец требований: Марк. Автор проекта: α Gate.  
Каноническая задача: https://github.com/AlphaCenter/gate/issues/718  
Исследованная база: `9630135f5ac8098c3bf9f63fc6a691cb96f2a40e`.  
Execution: `gate-718-spec-20260927-a1`. Ветка: `agent/gate-718-stop-on-inbound-spec`.

Этот документ задаёт будущее поведение и способ реализации. Слова «должен», таблицы состояний и тесты ниже — требования, а не отчёт о работающей функции. Ни runtime, ни миграции, ни настройки, ни переписка на стадии проектирования не меняются. Перед разработкой нужна отдельная команда Марка; issue остаётся открытой.

## 1. Поручение и пользовательский результат

Марк заранее утверждает длинный текст, разделённый на последовательные сообщения. Пока Gate отправляет эту серию, получатель может написать об ином предмете. Марку нужно отреагировать на человека, поэтому Gate прекращает дальнейшую отправку и сообщает Марку об остановке.

Принятые требования:

1. Любое **новое входящее сообщение получателя** в том же личном Telegram-диалоге и исходном RU/BY-аккаунте останавливает все ещё активные серии этого диалога.
2. Текст, голос, фотография, видео, видеосообщение, документ, стикер, GIF, контакт, геопозиция и другие полученные как новое пользовательское сообщение типы равноправны. Подпись и распознанный текст не обязательны.
3. «Да», «угу», «👍» отдельным сообщением, вопрос, ответ на старое сообщение и сообщение совершенно о другой теме одинаково останавливают серию.
4. Никаких LLM, классификации намерения, семантических исключений или ожидания транскрипции.
5. Уже подтверждённые отправки и их доказательства сохраняются. Остаток отменяется, тексты остаются доступны владельцу.
6. Остановка окончательная для данного `sequence_id`. Нет продолжения по таймеру, после прочтения, ответа Марка, перезапуска, восстановления соединения или смены worker.
7. Продолжение возможно только новой отправкой после показа точного оставшегося текста и нового явного подтверждения Марка: новая серия для двух и более абзацев, обычный single-send нового draft для одного. Старое подтверждение не переносится.
8. Марк получает уведомление, а состояние доступно через Gate read tools. Собеседнику техническое уведомление не отправляется.
9. Другие адресаты, другой RU/BY-аккаунт, одиночные отправки и независимые очереди не отменяются.

**Проектные допущения, не дополнительные решения Марка:** v1 охватывает личные Business-диалоги существующей reply-sequence функции; уведомление приходит в проверенный личный канал владельца у Gate-бота и дополнительно видно в Gate M. Реакция на существующее сообщение, read receipt и typing не считаются новым сообщением. Такие допущения явно отражаются в справке.

### Пример результата (все данные синтетические)

> Серия остановлена: собеседник прислал сообщение.  
> Аккаунт: RU. Получатель: Тестовый собеседник.  
> Отправлено: 3 из 10. Отменено: 7.  
> Остаток сохранён. Автоматически продолжаться не будет.

Если одна отправка уже выполнялась и её исход не подтверждён:

> Дальнейшая отправка остановлена. Отправлено: 3 из 10; отменено: 6; результат 1 отправки неизвестен. Повторно она не отправляется.

Нельзя писать «все оставшиеся сообщения точно не доставлены», если существует `sending` или `unknown`.

## 2. Границы и связанные работы

| Работа | Отношение к #718 |
| --- | --- |
| #715 / PR #717 | Отдельный лимит 2–50. На 18:31 UTC уже появился implementation claim; #718 работает с текущим допустимым размером и числовой лимит не меняет |
| #716 | Отдельная параллельность адресатов. #718 даёт общий ключ сериализации и независимую отмену; сетевую параллельность сам не включает |
| #696 | Отдельный personal/MTProto runtime. #718 не включает transport fallback и не заявляет получение событий из неподключённого транспорта |
| #700 | Документы как одиночные отправки остаются отдельной функцией; серия текстовых черновиков не превращается в очередь файлов |
| Ручная кнопка stop, resume, умное продолжение | Не входят. Можно спроектировать отдельно после v1 |
| Группы, каналы, межаккаунтное объединение человека | Не входят. Имя/username/person_id не являются ключом отмены |

Новый реальный маршрут доставки, бизнес-права, миграции живой базы, Production-активация и отправки не разрешены этим документом. Разработку и дальнейшие внешние действия разделять по фактической команде Марка и актуальным AGENTS.

Обнаруженные открытые поручения других пространств не принимаются этим Work. Текущий приоритет — прямое поручение #718. Исторические PROJECT_STATUS/PROJECT_HANDOFF не являются свежим доказательством Production.

## 3. Что подтверждено чтением кода

Все ссылки ниже относятся к одной исследованной базе, а не к установленному Production.

| Источник | Наблюдение и следствие |
| --- | --- |
| [application/reply_sequences.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/application/reply_sequences.py) | Точное подтверждение, scope, RU/BY, адресат, 2–20 черновиков, интервал ровно 60 секунд |
| [infrastructure/reply_sequences.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/reply_sequences.py) | Серия хранится в JSON `ReplyDraft.prompt_context.reply_sequence`; отдельного authoritative header нет |
| [worker/reply_sequences.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/worker/reply_sequences.py) | Discovery берёт первые 500 строк, включая историю; проверка superseded и отправка выполняются в разных сессиях |
| Тот же worker | `_sequence_superseded` ищет более новую `NEEDS_REPLY` task того же conversation/account/project; это не любое входящее событие |
| Тот же worker | `_record_sequence_outcome` блокирует drafts всего аккаунта, затем фильтрует JSON; состояние следующего шага не защищено общим ingress/send барьером |
| [infrastructure/reply_sends.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/reply_sends.py) | Есть preflight, probe, короткая reservation-транзакция с Conversation lock, durable SENDING, внешний HTTP без DB-транзакции, затем outcome |
| [api/telegram_ingress.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/api/telegram_ingress.py) | Аутентифицированный update сначала принимается durable ingress; text/voice обрабатываются позже worker |
| [infrastructure/telegram_processing.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/telegram_processing.py) | Receipt + outbox атомарны, UNIQUE(bot_key, update_id), проверяется изменённый replay; внутренний commit уже существует |
| [application/telegram_ingress_identity.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/application/telegram_ingress_identity.py) | Можно определить account/direction по Business connection и owner identity без семантики; `reply_task_intent` для нетекстового сообщения может быть NONE |
| [infrastructure/telegram_ingress_identity.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/telegram_ingress_identity.py) | Reader возвращает DEFAULT_PROJECT_ID; нельзя использовать это как единственный фильтр остановки всех серий account/chat |
| [models/domain.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/models/domain.py) | Business connection содержит account_id, telegram_user_id, user_chat_id; это возможная основа проверки owner-notification route, но доступность личной доставки не доказана |
| [services/telegram.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/services/telegram.py) | `send_plain_message` допускает отправку ботом без business_connection_id; transport имеет фазовые таймауты, не общий deadline операции |
| [telegram_queue_alert_notifier.py](https://github.com/AlphaCenter/gate/blob/9630135f5ac8098c3bf9f63fc6a691cb96f2a40e/app/infrastructure/telegram_queue_alert_notifier.py) | HTTP notifier предназначен для redacted queue incidents; не считать его готовым каналом личных уведомлений |

**Вывод:** проверки новой ReplyTask в цикле недостаточно. Нужны ранний stop при durable ingress, один общий барьер с sender, постоянное состояние серии и надёжное уведомление. Увеличение polling frequency или ещё один SELECT перед HTTP не закрывает гонку.

Тесты приложения и PostgreSQL на стадии ТЗ не запускались. Статическое чтение кода не доказывает текущую эксплуатационную работоспособность.

## 4. Точное определение события и области остановки

### 4.1 Ключ

Внутренний ключ диалога: `(workspace_ref, account_id, provider_chat_id)`.

- account_id выводится из авторитетной Business connection; RU/BY — отображаемая метка, не ключ БД.
- provider_chat_id берётся из `message.chat.id`, а не из имени или guessed sender ID.
- workspace_ref выводится из доверенной конфигурации/владения account, не из payload webhook.
- project_id хранится у серии и ограничивает чтение, но **не сужает** transport stop: один получатель может иметь несколько conversation/task/project записей.
- Изменение business_connection_id того же account не обходит stop.
- Остановка всех активных серий ключа обязательна, даже если будущая версия разрешит несколько серий. Нельзя остановить только найденную первой.
- Другая workspace/account/chat комбинация не затрагивается.

### 4.2 Таблица событий

| Событие | Решение |
| --- | --- |
| Новое приватное человеческое inbound-сообщение, корректная account/chat binding | Stop всех активных серий ключа |
| Нет текста/подписи, неизвестный media subtype внутри валидного нового message | Stop по envelope; загрузка/разбор media не нужны |
| Сообщение отправлено владельцем вручную или самим Gate | Не stop: authoritative OUTBOUND/echo |
| Duplicate того же update или второго envelope того же provider message | Не увеличивать revision, не повторять stop/notification |
| Тот же identity с несовместимым payload | Существующая mutated-replay ошибка; не принимать как новое |
| Редактирование/удаление старого сообщения, reaction, read/typing, service event | Не новое сообщение для v1; не stop |
| Невалидный webhook secret, чужой connector, group/channel | Не выполнять #718 stop |
| Невозможно уверенно установить account/direction | Не угадывать и не отменять чужие серии; guard admission блокирует потенциально затронутую отправку, см. §8 |
| Повтор processing/transcription старого receipt | Не stop второй раз; исходное решение принадлежит ingress |
| Входящее после полного завершения серии | Обновить context revision, но completed не менять и уведомление об остановке не создавать |

«Новое» означает впервые принятое аутентифицированное provider-сообщение. При дубликате с новым update_id применяется дополнительная уникальность provider message identity. Время Telegram и локальное время сохраняются отдельно; сравнение часов не является защитой от гонки.

Исторический receipt, уже существовавший до активации guard, не становится новым от replay/backfill. Впервые доставленное с задержкой и ранее неизвестное сообщение консервативно останавливает активную серию, даже если Telegram timestamp раньше queue time. Мы предпочитаем лишнюю остановку продолжению без проверки; timestamp сам по себе не позволяет отличить delayed delivery от неверных часов. Это явно тестируется.

Поддержку реакции как stop, MTProto ingress и групп не добавлять скрытно. Если транспорт не обеспечивает непрерывное наблюдение inbound, он не может отправлять защищённую серию с обещанием этой гарантии.

## 5. Инварианты и граница гарантии

Обязательные инварианты:

- I1. После commit `recipient_replied` старая серия больше не допускает новых provider-вызовов.
- I2. Inbound receipt, увеличение dialog revision, terminal stop, отмена ещё не начатых items, audit и owner-notification outbox фиксируются одной DB-транзакцией.
- I3. Отдельное событие не зависит от ReplyTask, транскрипции, контентного worker и состояния `needs_reply/replied`.
- I4. В одном dialog одновременно может выполняться не более одной sequence delivery; для разных dialog locks независимы.
- I5. Stopped никогда не становится active/completed от запоздалого outcome, replay, queue retry или recovery.
- I6. `sent` и `unknown` не переписываются в `cancelled`; без доказательства отправки не увеличивается sent_count.
- I7. Старая sequence authority не позволяет прямому MCP send отменённого draft обойти stop.
- I8. Notification создаётся один раз логически; её неудача не откатывает stop и не возобновляет серию.
- I9. Readback ограничен теми же principal/workspace/account/project grants; неизвестный ID не раскрывает чужую серию.
- I10. Любая отправка остаётся под существующими правами Business, точным account/recipient, send-attempt uniqueness, интервалом и unknown/no-retry политикой.

### Необратимая отправка

Нельзя обещать, что на экране собеседника ни одно сообщение не появится после его ответа: update может прийти в Gate позже, а Telegram может завершить уже начатый запрос после нашего stop.

Доказуемая граница при исправном DB/worker контуре: **после зафиксированного stop Gate не начинает новую отправку этой серии**. Запрос, начатый до stop, считается in-flight и получает честный outcome. Не удаляем отправленное и не вызываем cancel HTTP как доказательство недоставки.

Issue #718 п.4 читается в этой технически реализуемой границе: запрет нового provider-call после committed stop, а не запрет позднего появления ранее отправленного сообщения у Telegram. Приёмка обязана различать provider-call start, reservation и provider outcome.

При потере DB-соединения между получением разрешения и сетевым эффектом нельзя дать математическую exactly-once/fencing гарантию со стороны Telegram: Bot API не проверяет нашу DB epoch. Проект требует fail-closed отмены локальной отправки, quarantine неизвестного outcome и тестов отказа, но не объявляет абсолютную распределённую атомарность. Если владельцу впоследствии понадобится более строгая внешняя гарантия, это отдельное изменение транспортного протокола, не скрытая оговорка в тесте.

## 6. Выбранная модель хранения

Выбор: добавить узкие таблицы управления сериями; оставить ReplyDraft источником текста, SendAttempt источником доказательства доставки. Разрозненный JSON недостаточен как authority для terminal stop и outbox. Не копировать тексты или media в новые таблицы.

Имена ниже — план; окончательный номер Alembic revision выбирается от актуального head перед разработкой.

| Таблица | Основные поля и ограничения |
| --- | --- |
| `reply_connector_admission` | UNIQUE(workspace_ref, connector_key); state=healthy/blocked/reconciling; safe_reason; version; observed_at; последнее подтверждение continuity; не содержит payload |
| `reply_dialog_guards` | UUID id; workspace_ref/account_id/provider_chat_id UNIQUE; inbound_revision bigint default 0; last_inbound_receipt_ref; health_state; guard_version; timestamps |
| `reply_inbound_signals` | receipt_ref FK; dialog_guard_id FK; connector_key/provider_message_id; UNIQUE(dialog_guard_id, connector_key, provider_message_id); revision; kind; accepted_at; только минимальные идентификаторы |
| `reply_sequences` | UUID id (сохраняет существующий sequence_id); guard_id, task/conversation/account/project/workspace, principal_subject; state; stop_reason; stopped_at; stop_receipt_ref; queued_revision; item_count; interval_seconds; version |
| `reply_sequence_items` | sequence_id + position UNIQUE; draft_id UNIQUE для принятого membership; scheduled_at; state; attempt_id nullable; finished_at; safe_error_code; binding immutable |
| `reply_sequence_notifications` | UUID event_id; sequence_id; kind; UNIQUE(sequence_id, kind); owner_route_ref; delivery_state; attempts; next_attempt_at; provider_message_ref; safe_error_code; created/finished_at |

Событие `kind=recipient_replied` логически одно на серию. Дополнительное `delivery_reconciled` допустимо максимум один раз, только при существенном изменении неизвестного исхода; это не повтор stop. Текст уведомления строится по актуальному snapshot на момент отправки; sent/unknown/cancelled counts не замораживаются преждевременно.

Ограничения:

- FK и CHECK для допустимых states, position>=0, item_count>=2, interval_seconds=60.
- Максимум берётся из единого application contract текущей версии (#715 может изменить его отдельно).
- Partial index активных sequences по guard_id; pending items по sequence_id/position; notification due index.
- Нет единственного LIMIT 500 по историческим drafts. Discovery выбирает ограниченную страницу активных headers, затем полный состав нужных серий с keyset pagination.
- Counts выводятся из items/SendAttempt согласованным snapshot; не поддерживать четыре несвязанных счётчика.
- Один immutable membership draft предотвращает незаметную перестановку и повторное использование подтверждения. Для новой серии создаются новые drafts со ссылкой на исходные, а не снимаются запреты со старых.
- Body входящего, transcript, имя, телефон и username не попадают в audit/метрики/issue. Сырой chat ID остаётся только в защищённом transport storage.
- Удаление/retention новых данных следует существующей scoped политике; этот проект не разрешает очистку истории.

### Состояния

| Объект | Переходы |
| --- | --- |
| Sequence | active → completed; active → stopped; terminal не возобновляется |
| Stop reason | recipient_replied; send_failed; send_unknown; context_superseded; guard_unavailable; legacy_reapproval_required |
| Item до HTTP | scheduled → reserved → sending → sent/failed/unknown; scheduled/reserved без начатого HTTP → cancelled |
| Item с начатым HTTP | sending → sent/failed/unknown; никогда автоматически cancelled |
| Notification | pending → sending → sent/failed/unknown; blocked_route до наличия проверенного маршрута; bounded retry только доказанно недоставленных |

Существующий SendAttempt enum не нужно расширять фиктивным SENT для отмены. Для reserved/no-provider случая item хранит cancelled, а durable attempt, если создан, получает явно доказанный pre-provider failure code `sequence_stopped_before_provider`. Outcome, send audit и recovery должны различать этот случай и транспортный unknown.

## 7. Атомарность ingress и отправки

### 7.1 Общий барьер

Выбран **межпроцессный PostgreSQL session advisory lock на dialog key**, удерживаемый на выделенном физическом DB-соединении от последнего admission-check до завершения сетевой попытки и сохранения outcome. HTTP по-прежнему выполняется **без открытой SQL-транзакции**.

Почему не обычный in-memory lock: API и worker могут быть разными процессами/репликами. Почему не только row lock при reservation: после commit reservation остаётся окно до HTTP.

Требования к реализации барьера:

1. Стабильный lock key через фиксированный cryptographic digest от версионированного dialog key; не Python hash(). Коллизия может лишь лишний раз сериализовать, никогда разрешить чужой доступ.
2. Выделенное соединение сохраняется до unlock; не возвращать его в pool с удержанным session lock. В finally выполнить unlock, при сомнении invalidation/close.
3. Получение/освобождение lock не оставляет открытую транзакцию во время HTTP. Автоматический SQLAlchemy autobegin явно завершить.
4. Transaction pooling несовместим с этим дизайном. До активации доказать direct/session-pooling режим; при отсутствии доказательства admission закрыт. Не менять инфраструктуру молча.
5. Порядок: dialog advisory → текущий conversation/reservation lock → существующий порядок attempt/context → короткая запись projection. Все пути sequence send, queue и ingress используют тот же outer lock. Recovery, меняющий sequence projection, также проходит guard.
6. Ни один участник не берёт dialog lock, уже удерживая draft/attempt/conversation row lock. Для пакетных ключей нужен стабильный порядок; v1 обрабатывает один ключ за раз.
7. Не удерживать барьер во время 60-секундной паузы, ожидания due, transcription, media download, connection probe или уведомления.
8. Общий deadline **20 секунд** для одной текстовой provider-попытки (в дополнение к фазовым HTTP timeout); DB lock acquisition timeout **2 секунды**. Это проектные эксплуатационные значения; изменение требует обновления тестов/документа.
9. Ingress при занятом барьере/DB недоступности возвращает 503 без ACK и без частичного нового receipt. Следующая попытка provider retry обрабатывается идемпотентно; provider retry latency не входит в обещание «мгновенно».
10. Потеря lock connection запрещает новый HTTP; уже начавшийся HTTP отменяется локально и считается unknown до доказанного исхода. Ни один detached/background task не может продолжить send после выхода из guard.
11. Dispatcher не получает новый send permit при неполном/unhealthy ingress guard. Наличие route mapping ещё не доказывает доступность потока входящих.

Такой выбор сохраняет существующую гарантию «provider outside transaction», но удерживает DB connection во время сетевой попытки. Это осознанная цена: ограниченная конкуренция и pool budget должны оставлять соединения ingress; до активации проверить на синтетической нагрузке. #716 обязан использовать этот барьер, не заменять его локальным semaphore.

### 7.2 Ingress-транзакция

1. Существующий secret/envelope validation.
2. Из envelope получить private chat/message identity; SELECT-only identity reader определяет account/owner/inbound независимо от text_present.
3. До изменения данных получить dialog lock. Повторно проверить binding внутри защищённой части.
4. В **одной** транзакции: insert receipt + processing outbox; при свежем человеческом message insert inbound_signal; revision++; найти все active sequences guard; поставить stopped/recipient_replied; отменить все items, не имеющие начатого provider вызова; создать audit + notification outbox для каждой реально остановленной серии.
5. Commit, unlock, только затем ACK. При ошибке rollback всей транзакции.
6. Запустить обычный существующий processing путь; его ошибка/задержка не отменяет stop.
7. Receipt exact replay возвращает существующую квитанцию. Новый update для того же message не увеличивает revision и не отменяет новую уже отдельно подтверждённую серию.

Адаптировать `SqlTelegramReceiptStore.create_if_absent`, а не делать отдельный stop после его существующего commit. Сервисный seam должен позволять атомарное расширение только BUSINESS connector; Alpha/Beta/Become/AQUB ingress не получает побочных отмен.

### 7.3 Queue и новое подтверждение

Добавить `dialog_inbound_revision` в scoped reply context. В queue command/input schema добавить обязательный `expected_inbound_revision` (integer >=0, boolean запрещён), возвращаемый из контекста, который Work показал Марку перед подтверждением.

- Подтверждение по-прежнему относится ко всем точным drafts/account/recipient и их порядку.
- Под dialog lock queue сравнивает revision. При несовпадении: `recipient_context_changed`, ноль постановок/отправок, запросить новый просмотр и подтверждение.
- Drafts должны относиться к актуальной задаче/контексту; cancelled старой серии не становятся queueable.
- Если revision поменялась после показа, нельзя молча подставить свежий номер и использовать старое «да».
- Старый клиент без поля получает `inbound_revision_required`; это намеренное fail-closed изменение очереди, не скрытая совместимость.
- Replay одинаковых drafts не создаёт вторую серию. Существующий запрет повторного membership сохраняется.
- Queue не выполняет фактический send в той же операции; commit очереди не означает доставку.

### 7.4 Dispatcher и sender

1. Discovery headers даёт только кандидата, не разрешение.
2. Connection preflight/probe выполняется без route lock и без отправки сообщения.
3. Sender получает dialog lock, заново читает header/items/context revision/permissions и due.
4. Если stopped, guard unhealthy, предыдущий item не sent или интервал не выдержан — ноль HTTP. Ошибка не должна выглядеть как доставка.
5. Существующая короткая reservation-транзакция с дополнительным sequence/version binding; commit SENDING до внешнего эффекта.
6. Перед HTTP проверить, что текущий процесс всё ещё владеет guard; вызвать provider ровно один раз под тем же outer lock.
7. Сохранить outcome существующим sender, затем conditional sequence projection. Если процесс упал между этими commit, следующий reconciliation до discovery синхронизирует terminal SendAttempt и items/header: failed/unknown обязательно переводят весь остаток в cancelled, а не только молча блокируют его; sent восстанавливает счётчик/позицию. Когда последняя отправка подтверждена — completed; при failed/unknown — stopped и отмена дальнейших.
8. Освободить guard. Никакая 60-секундная задержка не происходит внутри него.

Проверка в sender обязательна и для прямого `gate_send_approved_reply` draft, уже состоящего в серии. Отдельно нажать send старого cancelled draft нельзя; ответ `sequence_stopped`. Это не запрещает новую независимо подтверждённую одиночную реплику на новое сообщение.

### 7.5 Таблица гонок

| Порядок | Обязательный исход |
| --- | --- |
| Inbound commit → discovery/claim | Provider calls=0 для остатка |
| Discovery → inbound commit → sender admission | Старый snapshot отклонён; calls=0 |
| Queue и inbound конкурентны | Общий guard: queue до inbound останавливается; inbound до queue делает старый revision невалидным |
| Sender владеет guard, inbound приходит до HTTP | Ingress ждёт/возвращает 503; stop ещё не committed; допустим не более одного текущего запроса, остаток затем остановлен |
| HTTP уже начат → inbound | Завершить/unknown текущую попытку, затем stop; честный учёт границы |
| Stop → запоздалый success/recovery | Можно уточнить outcome конкретного item; terminal header не оживает |
| Два worker одного item/диалога | Один provider-call, второй видит reservation/outcome/stop |
| Разные адресаты или RU/BY | Независимые locks и состояния; не account-wide блокировка |
| Последний item завершён до inbound | Completed остаётся completed, ложного stop notification нет |
| Новая серия после нового подтверждения → duplicate старого inbound | Старый signal не останавливает новую серию |

Условие «cancel до claim» слабее требуемого: проверять нужно также окно между claim и HTTP. Тесты управляют этими границами barriers/events, а не случайными sleep.

## 8. Неопределённая идентичность и отказ входящего контура

Нельзя одновременно принять неразрешимый webhook как безвредный и продолжить серию с обещанием stop-on-any-message.

- Если известны account/chat, но direction unresolved: durable guard health=blocked для этого ключа; остановить активную серию с **guard_unavailable**, не приписывая собеседнику ответ; уведомить владельца о технической остановке.
- Если connection mapping неизвестен и нельзя ограничить затронутый account: зафиксировать connector-level unhealthy admission для новых sequence sends, без ложной отмены всех чужих серий. Это временная защитная блокировка, а не `recipient_replied`; после восстановления существующие активные серии требуют сверки backlog до отправки.
- Невалидный/неаутентифицированный запрос не получает права блокировать очереди.
- Не использовать DEFAULT_PROJECT_ID из identity reader как доказательство scope.
- При восстановлении обработать принятые события идемпотентно; остановленные серии не возобновляются автоматически. Для временно блокированных проверить revision, backlog и continuity evidence.
- Потеря webhook подписки/прав/соединения закрывает sequence admission. Polling статуса не способен гарантировать обнаружение самого первого пропущенного события; покрытие транспортом и наблюдаемость — обязательное ограничение runtime claim.
- Для будущего MTProto нужен такой же authoritative normalized signal и общий guard. Пока интеграции нет, нельзя заявлять, что #718 защищает поток, который Gate не видит.

## 9. Уведомление владельцу

Одного поля в `gate_get_status` недостаточно: владелец должен получить сообщение без ручного запроса статуса.

### Маршрут

Использовать существующий Gate Business bot как обычного бота, отправляющего **в личный owner chat**, без business_connection_id. Получатель — владелец серии, а не её counterparty.

Перед активацией resolver обязан доказать:

- owner principal серии соответствует владельцу account/workspace;
- активная Business connection и Account.telegram_user_id не противоречат друг другу;
- user_chat_id принадлежит проверенному личному каналу владельца; не выбирать первый из конфликтующих records;
- маршрут не совпадает с counterparty и не является группой;
- канал доступен в рамках явно разрешённой owner notification функции.

Нельзя брать ID из аргумента LLM, имени «Марк», последнего входящего чата, test-group, Beta-бота или произвольного настроечного fallback. В Git нет реальных значений.

**Не подтверждено на стадии ТЗ:** есть ли сейчас пригодный owner route и возможность доставки ботом. Если нет, v1 сохраняет stop и notification со статусом blocked_route; Gate M сообщает «серия остановлена; личное уведомление не доставлено». Полная продуктовая приёмка уведомлений заблокирована до точной привязки канала. Подключать/проверять его реальной отправкой на стадии ТЗ не требуется и не разрешено.

### Доставка

- Notification outbox записывается вместе со stop; notifier не вызывается внутри ingress-транзакции или dialog lock.
- Worker claim notification через короткую DB-транзакцию; сохраняет sending до внешнего эффекта.
- Один логический event_id + UNIQUE(sequence_id, kind), CAS/row locking против двух notifier workers.
- Успех только при ok + подтверждённом provider message ID; статус sent означает доставку Telegram, **не прочтение Марком**.
- До provider call: временные локальные ошибки можно повторить с backoff 5/30/120 секунд, максимум 3 повторных попытки после первой.
- Явный 429 — учитывать retry_after и тот же лимит; явный постоянный отказ — failed/blocked_route.
- Timeout, обрыв после отправки, 5xx с возможным внешним эффектом, падение между send и записью success — unknown, без слепого автоматического resend.
- Restart sending notification → unknown; не reset pending.
- Сбой уведомления никогда не продолжает series и не задерживает ingest.
- В статусе всегда есть notification_state и доступ к сохранённому событию; это резервный readback, а не ложное доказательство push delivery.
- Логически один stop event не равен гарантии exactly-once отображения Telegram. Не обещать одновременно безусловную доставку и отсутствие дублей при неопределённом сетевом исходе.

### Содержание

Account label, безопасное отображаемое имя из доступного owner контекста, короткий series ref, время МСК, подтверждённый sent_count/total, cancelled_count, unknown/in-flight count, причина, фраза «автоматически не продолжится».

Входящий текст, transcript, фото, ссылки с чувствительными ID и старые абзацы в уведомление не копируются. Марк смотрит исходную переписку/разрешённый контекст отдельно. В Git примеры только синтетические.

## 10. Gate M и read contracts

| Поверхность | Изменение |
| --- | --- |
| `gate_queue_approved_reply_sequence` input | `expected_inbound_revision`; описание автоматической остановки; прежние confirmation/account/recipient/order обязательны |
| Queue output | sequence_id, state=active, stop_policy=any_inbound_message, queued_revision, ordered items, sent=false; notification readiness отдельным полем |
| `gate_get_reply_context` | dialog_inbound_revision и scoped sequence summaries, включая остановленные серии того же диалога, даже если task сменился |
| `gate_get_status` | агрегаты active/stopped/notification pending/failed/unknown + ограниченный список последних доступных stop events |
| `gate_list_unanswered` | additive summary остановок для подходящих task; не создавать фиктивные needs_reply tasks ради уведомления |
| Новый read-only `gate_get_reply_sequence` | UUID sequence_id, optional cursor/limit для items; полное состояние и remaining drafts в допустимом scope |

Новый detail tool нужен для надёжного восстановления конкретной серии, когда исходная task больше не unanswered. Scope и grants ровно существующего context (точное имя брать из актуального MCP registry при реализации); никаких новых write полномочий. Адресат и draft text раскрываются только владельцу с явными grants.

Минимальный detail response:

- sequence_id, task_id, state, stop_reason, stopped_at, stopped_by_receipt_ref (opaque);
- account_label, recipient display в рамках доступного контекста;
- total, sent, cancelled, failed, unknown, sending; сумма категорий равна total;
- items: position, draft_id, item_state, safe_error_code, confirmed_at; body включать по существующей разрешённой read policy;
- notification: state, safe_error_code, delivered_at nullable;
- `can_auto_resume=false`; `requires_new_approval=true` для stopped;
- revision/version, page cursor без произвольного SQL или раскрытия чужих ключей.

Обновить обе input/output schemas и tool descriptions, response render, error allowlist, тесты surface consistency. Если есть strict additionalProperties=false, новые поля явно объявить. Не выдавать cancelled как sent, не терять unknown при пагинации.

Основные новые safe errors: inbound_revision_required, recipient_context_changed, sequence_stopped, sequence_guard_unavailable, sequence_migration_incomplete. Отсутствующий или чужой sequence ID даёт одинаковый scoped not_found.

## 11. Старые серии, миграция и откат

### 11.1 Почему нужна миграция

Новый header необходим для authoritative terminal state и атомарного outbox. Двойной независимый authority JSON+tables запрещён: таблицы становятся источником решения, старый JSON — временная совместимая projection.

План:

1. Additive schema migration без удаления старых полей/истории.
2. Отдельная идемпотентная проверяемая backfill-операция из существующего JSON; не смешивать её с произвольным replay входящих payload.
3. Проверять единый sequence_id, account/project/workspace/recipient, полный набор уникальных contiguous positions, size, interval, task и корректные SendAttempt bindings.
4. Исторические полностью подтверждённые серии → completed; failed/unknown → stopped с сохранением outcome.
5. Любая существующая незавершённая серия → stopped/legacy_reapproval_required; pending отменяются, sent/unknown сохраняются. Старое подтверждение не трактуется как согласие на новую notification policy.
6. Неполные/несогласованные metadata → quarantine, ноль sends; отчёт только о счётчиках/opaque refs.
7. Backfill legacy-stop не генерирует ложное «собеседник ответил» и не запускает массовые уведомления. Статус доступен владельцу, эксплуатационный отчёт отдельно.
8. Новый writer пишет tables и совместимую JSON terminal projection в одной транзакции на переходный период; новый dispatcher читает **только** tables.
9. Старый worker не должен одновременно работать с новым. Startup version/admission проверка + остановка старых replicas перед активацией; rolling mixed-version с активной отправкой запрещён.
10. При cutover незавершённый sender сначала bounded drain, оставшийся SENDING → unknown без resend. Операцию с реальными рядами проводить только в отдельно разрешённом runtime этапе.

Все исторические сигналы до cutover baseline помечены уже обработанными или неeligible для stop; backfill не запускает новые domain tasks/уведомления. Подсчёты до/после и повторный backfill доказывают отсутствие дублей. Не переносить реальные payload в тесты.

### 11.2 Откат

- Emergency stop: отключить **sequence dispatch**, сохранив ingress/stop/readback. Не «лечить» ошибку выключением stop guard при включённой отправке.
- Не откатывать автоматически на старый worker, способный продолжить JSON scheduled items.
- При rollback старой версии все sequence workers остаются выключенными, terminal JSON projection и таблицы сохраняются.
- Schema downgrade с активными/unknown данными запрещён; migration downgrade тестируется только на одноразовой базе. Production rollback не равен `alembic downgrade`.
- Не удалять SendAttempt, outbox или stop reason, не requeue unknown.
- Live rollback revision и точная команда устанавливаются по свежей будущей эксплуатации, не выдумываются из старых handoff.

## 12. План файлов и последовательность реализации

Ни один из перечисленных runtime-файлов на стадии ТЗ не меняется.

| Область | План изменения |
| --- | --- |
| app/models/domain.py и новый Alembic revision | Guard/signal/header/item/notification таблицы, индексы, ограничения |
| app/application/reply_sequences.py | Revision-bound confirmation, состояния/контракты; лимит из текущей константы |
| app/infrastructure/reply_sequences.py | Queue под guard, immutable membership, persistence/read adapters |
| Новые application/infrastructure reply_sequence_stop.py | Чистые stop rules, транзакционный stop без media/LLM |
| Новый infrastructure/reply_sequence_guard.py | Session lock lifecycle, exact connection ownership, timeout/fail-closed |
| app/infrastructure/telegram_processing.py | Receipt/outbox/signal/stop/notification в одном commit |
| app/api/telegram_ingress.py; ingress identity adapters | Envelope binding и admission на раннем этапе; не менять семантику чужих connector |
| app/infrastructure/reply_sends.py | Sequence admission в фактическом sender, guard через HTTP/outcome, direct-send bypass deny |
| app/worker/reply_sequences.py | Header discovery, targeted records, terminal-safe projection; убрать account-wide update |
| app/infrastructure/reply_send_recovery.py | Projection reconciliation через guard, unknown не повторяется |
| Новые application/infrastructure/worker reply_sequence_notifications.py | Owner route resolver, durable notification delivery/recovery |
| app/application/mcp_status.py; mcp_inbox.py | Scoped summaries/context revision/detail |
| app/api/mcp_routes.py; mcp_schemas.py | Input/output contracts и detail read tool |
| app/core/config.py; app/main.py и фактический lifespan seam; worker_health | Default-off activation notifier/guard capability; wiring и наблюдаемость |
| Tests, README, PROJECT_HANDOFF | Синтетические сценарии, актуальная справка, ограничения и receipts |

Порядок после команды Марка:

1. Свежие issue/PR/AGENTS/main и lease; продолжить эту ветку/PR, сверить #715/#716. Не создавать дубль.
2. Согласовать кодовые пересечения с текущим #715 без изменения его scope: его лимит/валидатор/schema остаются отдельным результатом; после появления нового main rebase документа и адаптация tests к новому writer обязательны. Затем уточнить только фактические repo/runtime constraints: DB pool mode, маршрут owner notifications, действующий send recovery. Не читать личную переписку для дизайна.
3. Ввести schema и чистые transitions + migration tests на одноразовой PostgreSQL.
4. Реализовать guard, atomic receipt stop и queue revision check; доказать гонки.
5. Встроить guard в реальный sender и все обходные точки; гарантии существующего sender сохранить.
6. Добавить discovery/readback, затем durable notifications и owner route validation.
7. Отработать legacy/backfill/restart/rollback на синтетических данных.
8. Полный self-review scope, targeted проверки, один необходимый exact-head CI. Не выполнять независимую δ роль от имени автора.
9. Квитанция code-ready с точным head и матрицей; runtime/notification route остаются отдельными доказательствами.
10. Только при разрешённом эксплуатационном этапе: pinned Ops route, migration/cutover по отдельному плану, bounded owner-only проверка, критерии rollback и отчёт. Этот текст не является такой командой.

План содержит одну сквозную функцию, хотя меняет несколько слоёв. Не выпускать частичный этап «остановка есть» без настоящего sender fence или «уведомление есть» с одним только readback.

## 13. Матрица приёмки

Все тесты используют синтетические аккаунты, адресатов, drafts и fake provider. Никаких реальных Telegram messages, media, production dumps или LLM.

| ID | Сценарий | Проверяемый результат |
| --- | --- | --- |
| A01 | 3 из 10 sent, новое текстовое inbound | stopped/recipient_replied; 3 sent, 7 cancelled; 0 новых HTTP |
| A02 | Голос при остановленной/ошибающейся транскрипции | Stop до transcription; ноль зависимостей от LLM |
| A03 | Фото/video/document/sticker/GIF/contact/location/unknown subtype | Одинаковый stop по envelope без text/caption |
| A04 | «Да», «угу», emoji, reply на старое, другая тема | Все stop, нет семантического ветвления |
| A05 | Inbound до первого due | 0 sent, весь остаток cancelled |
| A06 | Отправка последнего item конкурентно с inbound | Outcome соответствует порядку guard; нет ложного stop для completed |
| A07 | Несколько активных серий account/chat | Останавливаются все, включая другие task/conversation/project |
| A08 | Другой chat/account/workspace | Состояния и отправки соседних серий сохранены |
| A09 | Исходящее owner/echo, service, typing/read/reaction/edit/delete | Не recipient_replied stop |
| A10 | Invalid secret/malformed/foreign connector | Никакой отмены/уведомления #718 |
| A11 | Duplicate update и новый update для того же message | Одна revision, один stop event, одна notification row |
| A12 | Изменённый replay | Ошибка без побочной новой revision/notification |
| A13 | Receipt/signal/stop/outbox commit failure | Полный rollback и 503; retry единожды завершает всё |
| A14 | Inbound commit до sender admission | Fake provider call_count=0 |
| A15 | Stale discovery snapshot после stop | Нет send даже при устаревшем due item |
| A16 | Разрыв reservation→HTTP | Ingress/guard даёт согласованный порядок; после committed stop новых calls нет |
| A17 | HTTP начат до inbound | Не более одной in-flight; честные sent/failed/unknown; остаток cancelled |
| A18 | Два worker одного draft/диалога | Максимум один provider-call, reservation уникальна |
| A19 | Worker crash до reservation / после reservation / после HTTP до outcome | Без effect либо unknown; restart не повторяет отправку |
| A20 | Поздний outcome/recovery после stop | Меняется только доказанный item outcome, header остаётся stopped |
| A21 | Lock connection loss/cancel/timeout | Fail-closed, unknown где эффект возможен; lock не утекает в pool |
| A22 | HTTP под guard | Нет открытой SQL-транзакции; outer connection удерживается корректно |
| A23 | Direct MCP send старого cancelled draft | sequence_stopped; ноль provider calls |
| A24 | Старое подтверждение/старый revision | Ноль queue mutations; нужна новая явная approval |
| A25 | Новая серия из новых drafts после approval | Новая identity/authority; duplicate старого inbound не останавливает |
| A26 | >500 исторических rows, новая активная серия | Полная discovery/cancel без усечения по истории |
| A27 | Notification worker crash и конкуренция | Один логический event, no blind resend sending/unknown |
| A28 | Owner route absent/conflicting/counterparty/group | blocked_route; ноль неверных отправок; stop сохранён |
| A29 | Notification ok/429/403/5xx/timeout | Подтверждённый sent или корректный retry/failed/unknown; series не оживает |
| A30 | Scope mismatch detail/status/context | Нет чужих данных; unknown ID не раскрывает существование |
| A31 | Смена ReplyTask / already answered task | Stop всё равно виден через context/detail, не теряется из unanswered |
| A32 | Legacy valid/completed/active/unknown/corrupt; backfill twice | История сохранена, active требуют reapproval, нет новых sends/notification storm |
| A33 | Mixed-version startup и rollback | Старый/new dispatcher не отправляют совместно; downgrade не оживляет |
| A34 | DB outage / unresolved identity / broken ingress | Guard admission closed; ноль необоснованных sends/чужих cancellations |
| A35 | Пауза 60 секунд и нет входящих | Порядок/интервал сохранены; нет catch-up пачки после задержки |
| A36 | Входящее с задержкой / historical replay после cutover | Неизвестное новое stop; уже известное/history не stop повторно |
| A37 | Contract schemas и descriptions | Revision/input/output/read tool согласованы, count sum=total |
| A38 | Privacy/logs/metrics | Нет message body, media, raw chat IDs и секретов |
| A39 | Межпроцессная PostgreSQL гонка | API и worker используют отдельные connections/process contexts; in-memory lock не считается доказательством |
| A40 | Provider deadline/lock contention | Bounded завершение, ingress 503 без частичного commit, отсутствие зависших locks |

План команд: targeted pytest по reply_sequences, reply_sends, recovery, telegram durable ingress/business text/voice, MCP schemas, новым stop/notification/migration tests; ruff и mypy по актуальным repository gates. PostgreSQL тесты обязательны и должны реально выполниться; skipped из-за отсутствия DATABASE_URL не является успехом. DATABASE_URL только одноразовой тестовой среды.

Тесты гонок используют явные barriers на discovery, reservation commit, до provider-call, после provider-call и до outcome commit. Проверять порядок событий через monotonically ordered test trace, а не сравнивать wall-clock timestamps. Property-проверка: terminal stop + произвольная последовательность replay/recovery/ticks не порождает нового send старой серии.

## 14. Наблюдаемость и критерии эксплуатационной приёмки

Метрики без high-cardinality person IDs:

- sequence_stops_total{reason}; cancelled_items_total;
- inbound_accept_to_stop_ms (только для принятого события);
- notification_delivery_delay_seconds и notification outcomes;
- guard_lock_wait_seconds, guard_unavailable_total;
- active_sequences, unresolved_attempts, legacy_quarantine_count;
- denied_sequence_send_after_stop_total; любое фактическое нарушение I1 — incident и dispatch off.

Проектные целевые задержки при исправной среде: stop в той же ingress transaction без ожидания scheduler tick; от успешного приёма update до commit p95 <=1 секунда без конкурирующего in-flight; первая попытка owner notification <=5 секунд после stop. Это цели для нагрузочной проверки, не SLA Telegram. In-flight, network outage, 429 и provider retry отдельно отражаются как задержки/ограничения.

Будущая runtime приёмка требует всех доказательств:

1. Exact deployed revision + применённая schema + guard-capability/version.
2. Старые sequence workers остановлены, исторические активные серии безопасно обработаны.
3. Проверенный маршрут уведомления владельцу; одних code tests недостаточно.
4. Отдельно разрешённый bounded canary только на собственных тестовых аккаунтах владельца: короткая серия, входящее между items, подтверждённое отсутствие оставшихся sends, подтверждённый notification ID.
5. Реальная история/контакты не выгружаются в Git; receipt содержит только redacted counts и hashes.
6. Неизвестный результат canary, ошибочный получатель, нарушение isolation или I1 немедленно закрывает dispatch.
7. Runtime receipt не подменяется успешным CI или merge; прочтение Марком не заявляется по delivered notification.

При работе по #715 обнаружен новый implementation claim: https://github.com/AlphaCenter/gate/issues/715#issuecomment-5858583739. Это сообщение другого Work о начале работы, не проверка завершённой реализации или Production.

На стадии ТЗ эти действия **не выполнялись**. Наличие конфигурационного поля user_chat_id в модели не является доказательством действующего канала.

## 15. Решения, ограничения и следующий ход

Проектные решения завершены:

- stop по факту нового ingress message, без AI и без ожидания ReplyTask;
- terminal stop всех активных серий точного account/chat;
- normalized tables как authority, JSON только переходная projection;
- общий межпроцессный guard через send/outcome при HTTP вне транзакции;
- revision-bound новое подтверждение и запрет обхода прямым send;
- durable owner notification + scoped status/detail;
- безопасный cutover старых серий без автоматического продолжения;
- 40 предметных критериев приёмки и явная граница in-flight/unknown.

Непроверенные эксплуатационные предпосылки: actual DB session-pooling/direct mode, owner notification route, ingress continuity, установленная Production revision. Они не препятствуют завершению ТЗ и работе над кодом после команды, но препятствуют заявлению полной runtime готовности без проверок.

Для следующего Work: открыть #718 и этот документ на commit из итоговой квитанции, прочитать текущие AGENTS/WORK-PARENT/bootstrap, проверить ветку/PR/lease и новую команду Марка. Точный исходный контекст и критерии доступны без истории чата.

**Следующий ход — Марк: отдельная команда на реализацию. До неё: specification_ready, implementation_not_started, no_merge, no_deploy, no_messages.**

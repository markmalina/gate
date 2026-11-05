# Gate: текущий контекст и спецификация рабочих маршрутов

**Обязательная точка входа нового Gate Work.** Программа [#787](https://github.com/markmalina/gate/issues/787), общий регламент [AI WORK-RULES](https://github.com/markmalina/ai/blob/main/WORK-RULES.md), [план спринтов](GATE-RECOVERY-PLAN.md), [метрики и расходы](GATE-METRICS.md). Подготовка этой редакции ведётся в [#767 / PR768](https://github.com/markmalina/gate/issues/767); наличие документа в ветке ещё не означает его принятие или установку функций.

Срез собран **5 ноября 2026, 13:34–13:44 МСК** из исходников, GitHub runs и scoped Gate M. Это датированное наблюдение. Перед эффектом перечитать соответствующий предмет; не обновлять дату без нового чтения.

## Зачем и где границы

Gate — основной транспортный инструмент Марка: принимает внешние события, сохраняет оригиналы и состояние, выполняет разрешённую расшифровку, выдаёт результат и отправляет только актуально одобренные ответы. Gate не становится вторым планировщиком личной жизни, CRM или интеллектом. Смысл записей и последующие поручения обрабатывает владелец/Work; входящий текст не расширяет полномочия.

Текущая программа охватывает Watch, телефонные Calls, доступ к оригиналу/тексту через ChatGPT и запрет устаревших ответов. Telegram Business, существующие каналы и архивы сохраняются. MTProto, новые пользователи/каналы, AQUB, S2 и общий инфраструктурный рефакторинг не являются неявным продолжением программы. AQUB остаётся frozen.

| Предмет | Подтверждённый уровень | Следствие |
| --- | --- | --- |
| Source main до этого документационного изменения | 95914ab4075275cc17936e8541fde52bdccc737d | PR782 и pin Ops02ef в main; новый docs merge изменит main, но не production |
| Production | afe0c6b8519381a0007c0dcc1c145207b5d54a3f | Старая установленная версия; обновление не подтверждено |
| Схема | 0115_watch_voice_post_payment | PR766 migrations0116–0119 не установлены |
| Watch | Actions ON, ordinary worker OFF; 67 delivered / 3 pending | Received1 / unknown1 / rejected1; агрегат не доказывает доставку выбранного текста |
| Watch-кандидат | PR766 OPEN, head45ba499a5f1852208ba8a478a606358fd0fe3bd5 | Early card/journal/Work coordination есть только в source |
| Reply-кандидат | PR747 OPEN, headb975475c1c6c81035e38d9bd13a40d777933e83c | Полный snapshot guard не установлен |
| Последний выпуск | run41907078570: UNKNOWN / BAD_USER_INPUT / UNCLASSIFIED, deploymentId null | Исход внешнего эффекта UNCONFIRMED; не повторять вслепую |
| Подключённый ChatGPT | Gate context/original selectors доступны; standalone speech/transcribe tool не найден | Возможность прочитать файл не доказывает возможность распознать его в данной среде |

Основные доказательства: [анализ 5 ноября](https://github.com/markmalina/gate/issues/736#issuecomment-6655106321), [terminal выпуска](https://github.com/markmalina/gate/issues/736#issuecomment-6641384983), [независимая Delta506](https://github.com/AlphaCenter/delta/issues/506#issuecomment-6641401287), [текущий source-only diagnostic разбор](https://github.com/markmalina/gate/issues/736#issuecomment-6641600050).

## 1. Watch: сейчас и требуемое поведение

**Вход:** запись Galaxy Watch → Samsung Recorder → телефон/Dropsync → Dropbox /Alpha/Voice. Сам факт записи на часах не означает завершённую загрузку в Dropbox. Идентичность источника — workspace/account + file_id + revision; оригинал хранится с хешем и размером. Private identifiers и содержимое остаются в private Mark/Gate protected storage.

**Сейчас:** private Mark workflow запускается по schedule/разрешённому push/точной owner command. Gate проверяет private repo, actor, OIDC, workflow pin и current main; читает private binding; получает Dropbox delta; затем выбирает разрешённую сохранённую note, выполняет STT и не более одной publication part за run. В production карточка с текстом возникает после STT, ранней metadata-card нет.

Cron заявлен каждые5мин, но 13 фактических scheduled runs 02–05Nov дали 12 интервалов с P50 6ч58м36.5с и max10ч18м58с. Это интервалы запусков, не end-to-end latency записи. Последний [run42007811853](https://github.com/markmalina/mark/actions/runs/42007811853) SUCCESS, но considered0 / published_parts0 / delivered0 / result deferred.

**Цель:** подписанный Dropbox webhook быстро фиксирует durable pending/wake; один worker обнаруживает конкретную source revision и создаёт одну private Mark issue до STT. HTTP webhook не выполняет длительную обработку. Карточка явно различает metadata_received, original_ready, transcription_running, transcript_ready, delivered, rejected и unknown. Защищённая ссылка на оригинал не становится публичной ссылкой. Полный текст и последующие части дополняют ту же issue; независимое чтение всех частей доказывает полноту.

**Идемпотентность и сбои:** повторный webhook/delta/restart не создаёт новую логическую запись. Claim перед provider I/O предотвращает двойную STT; UNKNOWN блокирует повтор до reconciliation. Publication имеет собственные receipts, IDs/digests/order и lost-ACK reconciliation; первая часть не равна полной доставке. Recovery polling страхует потерянное событие, но не является основным путём запуска.

**Что переиспользовать:** [PR766](https://github.com/markmalina/gate/pull/766) содержит early card, durable source/cycle journal, состояния оригинала, lease/claim и Work submit. Он не добавляет Watch wake в действующий Dropbox webhook. Его полный состав с миграциями и helpers нельзя объявить минимальным отдельным спринтом; выделение coherent slice требует сохранить реальные зависимости схемы.

**Первый малый результат:** полный текст одной выбранной сохранённой note. Гипотеза обхода через LKG initial-exact проверена и **не готова к применению**: режим пропускает чужие entries, но продвигает общий WatchVoiceCursor; ещё не архивированные чужие записи могут потерять видимость после возврата future-only. Даже снимок без чужих entries не устраняет гонку; direct initial-exact остаётся NOT_READY. Предпочтителен queued-first из main через исправленный выпуск с сохранением текущего future-only: сохранённая eligible note обрабатывается под coordinator до Dropbox OAuth/listdelta, commit+return не меняет общий cursor. При пустой очереди прежний scan остаётся отдельным дефектом. Дополнительные условия:
- private binding сейчас future-only; её изменение на main само запускает workflow по push, кроме существующего schedule;
- normal STT использует received_at и исторический budget period записи; текущий месяц не доказывает его остаток;
- budget denial может дать rejected, exception — unknown до STT; автоматическое возвращение в received недопустимо;
- старый UNKNOWN выпуска сохраняется; scoped snapshot не является полной историей провайдера;
- после полной доставки exact binding можно оставить ограниченной той же note; восстановление active future-only вновь расширяет область обработки и должно быть отдельным явным шагом.

Источник механизма: [LKG Watch Actions](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/app/infrastructure/watch_voice_actions.py), [worker](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/app/worker/watch_voice.py).

## 2. Calls: текущий контракт и длинные разговоры

**Вход:** /Alpha/Calls → HMAC-checked Dropbox webhook → durable inbox pending → worker delta scan → оригинал в PhoneCallRecording → STT → transcript_text и deterministic summary → protected Gate M/ChatGPT. Публикации в Mark в этом контуре нет.

Default poll interval30s — значение исходников, не доказанное runtime значение и не SLA. Сначала worker продолжает незавершённые записи/summaries, затем читает delta; STT сейчас выполняется последовательно внутри ingest, задерживая следующие entries и cursor. Global webhook_received_at нельзя приписать конкретному файлу.

У выбранного длинного звонка identity подтверждена по private metadata и source revision hash: оригинал сохранён, текста нет, reason=dropbox_call_duration_exceeded. Dropbox server_modified→Gate received составило14.715s (n1); это время intake, не расшифровки. В исходниках два совместных предела: 60min на Calls и максимум20 нормализованных частей по180s. Поднять только одну константу недостаточно. Оригинал ограничен100MiB, provider part25,000,000bytes, общий текст100000chars. Для120min потребуется минимум40 частей с согласованным policy/normalizer/output/cost contract. Фактическую длительность выбранного оригинала нужно подтвердить до выбора нового предела; сообщение владельца «почти два часа» не заменяет media metadata.

**Цель:** один выбранный длинный разговор даёт полный текст в защищённом архиве и читается через Gate M; затем плохой файл перестаёт блокировать остальные, discovery отделяется от длительной STT, появляются per-note timestamps и видимый terminal outcome.

**Решение о доставке открыто:** создавать ли отдельную раннюю Mark issue для каждого звонка. До ответа сохраняется существующий канал Calls → protected archive/Gate M. Вопрос не блокирует исправление длинной расшифровки и измерение задержек.

Идентичность call revision и provider reservation сохраняются; UNKNOWN не переигрывается. Ошибки duration/size/decoder/budget/quota разделяются. Oversized сейчас способен остановить advance cursor страницы; его изоляция — самостоятельный функциональный спринт.

Источники: [intake contract](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/docs/dropbox-call-intake.md), [Calls worker](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/app/worker/dropbox_calls.py), [normalizer](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/app/infrastructure/telegram_voice.py).

## 3. ChatGPT и OpenAI Platform — два явных способа работы

Серверный путь использует существующий OpenAI key и policy-pinned transcription adapter. Текущая нормальная модель gpt-4o-mini-transcribe; автоматических retries0. Ключ не передаётся Work и не сохраняется в Git. Политика и model-access.lock проверяются перед изменением model route.

Work-путь должен отдельно доказать: защищённое получение оригинала → реальная доступная speech capability → claim перед обработкой → один accepted transcript → публикация в ту же issue. На текущем наборе connector tools standalone STT не подтверждена. Не обещать «ChatGPT распознает по ссылке» только потому, что есть download. PR766 claim/start/submit adapter ещё не экспонирован текущим подключённым инструментом.

Оба обработчика разделяют идентичность записи и durable claim. Gate-first, Work-first и simultaneous claims должны давать один accepted текст без повторной оплаты. После начатого неизвестного provider effect второй обработчик не получает разрешение «попробовать другой путь».

Provider balance/tier/rate limits не прочитаны: Platform требует входа. Internal Gate remaining$3 текущего месяца — не OpenAI credit balance. Исторический quota_unavailable — не доказательство сегодняшнего остатка. [Official pricing](https://developers.openai.com/api/docs/pricing?latest-pricing-expanded=true), прочитанный05Nov: оценка mini-transcribe$0.003/min;120min≈$0.36 только STT compute, без Actions/Railway и без обещания фактического счёта. [Deprecations](https://developers.openai.com/api/docs/deprecations): mini-transcribe объявлена deprecated26Aug2026, shutdown26Feb2027. Это будущая плановая миграция модели, не причина сегодняшнего duration rejection и не разрешение менять модель сейчас.

## 4. Ответы: актуальность прочитанного контекста

**Требование:** если после подготовки/одобрения ответа Gate принял новое значимое событие этого диалога, отправка останавливается и Work получает новый контекст. Owner-outgoing, edit/delete и изменение сообщения тоже должны инвалидировать соответствующий snapshot; другой диалог не должен давать ложный отказ.

**Сейчас:** LKG sender сверяет снимок, взятый при начале send, с last_inbound_at при reservation. Это не immutable revision, которую Work читал при подготовке. Подключённые draft/document схемы не содержат expected_context_snapshot; обязательной второй проверки перед dispatch нет.

**Готовый source:** [PR747](https://github.com/markmalina/gate/pull/747) содержит context_snapshot, durable raw-ingress revisions, общий lock, check при reservation и final admission, отказ context_changed/sent=false, остановку неотправленного хвоста sequence и проверки старых drafts/endpoints. Миграция и MCP client activation не установлены.

**Граница гарантии:** защищаются события, durably принятые Gate до final admission. Сообщение, которое Telegram ещё не доставил Gate, либо событие после начала внешнего HTTP send нельзя задним числом отменить. Нельзя обещать абсолютное предотвращение любого сообщения во время уже начатого запроса.

Источники: [LKG sender](https://github.com/markmalina/gate/blob/afe0c6b8519381a0007c0dcc1c145207b5d54a3f/app/infrastructure/reply_sends.py), [guard contract](https://github.com/markmalina/gate/blob/b975475c1c6c81035e38d9bd13a40d777933e83c/docs/operations/DIALOG-SNAPSHOT-GUARD.md), [race matrix](https://github.com/markmalina/gate/blob/b975475c1c6c81035e38d9bd13a40d777933e83c/docs/operations/gate745-incident-20261026/R3-MATRIX.md).

## 5. Хранение, безопасность и выпуск

Оригиналы, тексты, file IDs/revisions, private links, transcripts и персональные данные остаются в разрешённом private пространстве. В общей программе — safe aggregates, hashes без приватного соответствия и ссылки на защищённые доказательства. Обработка чужих сообщений/разговора требует scope, уже указанного владельцем; его поручение не разрешает внешнюю рассылку содержимого.

Retention не меняется этим планом. Новый Work читает действующую project policy и schema; отдельно описывает сохранение/удаление оригинала, ledger, transcript и publication receipts в затронутом scope. Не вводить «очистку» путём удаления UNKNOWN, originals, outbox или истории claims. Для длинных звонков повторное использование сохранённого оригинала предпочтительнее re-download.

Выпуск только через зарегистрированный Gate #751/current-main/pinned Ops. Внешний результат UNKNOWN41907078570 не исправлен source analysis. Исполнитель сохранял лишь HTTP200/BAD_USER_INPUT/UNCLASSIFIED; raw error/traceId не восстановимы из имеющегося артефакта. GitHub access к current main не доказывает Railway integration access к этому commit. Если выбранному спринту нужен deploy, сначала конкретная достаточная безопасная диагностика, затем проверенный выпуск/health/recovery; не новый универсальный S1/fixture/support проект по умолчанию.

PR766 и PR747 имеют пересекающуюся историю migrations0116–0119/reply0116. Интегратор строит новую join revision от фактического main и проверяет upgrade от реально установленной0115; исторические миграции/вердикты не переписываются. Required recovery определяется конкретной схемой и эффектом спринта, а не косметическим закрытием #781.

## 6. Как новый Work продолжает без истории чата

1. Прочитать этот документ, AI WORK-RULES, AGENTS, WORK-PARENT и bootstrap.
2. Открыть [#787](https://github.com/markmalina/gate/issues/787), активный спринт и его последний содержательный receipt. Исторический аудит читать лишь по необходимой ссылке.
3. Сверить свежие main/production/claims/jobs/последние effects для своей операции. Здесь указан последний срез, не вечная авторизация.
4. Продолжить один активный functional sprint по [плану](GATE-RECOVERY-PLAN.md). Документационный этап P0 отдельно от продуктовой приёмки.
5. До работы записать результат/baseline/target/scope/actor; в той же issue сохранять материальные ошибки, решения, проверки и фактическое было→стало.
6. При передаче обновить current pointer и stale-факты, которые реально изменились; не создавать heartbeat-only коммиты. Каждый видимый пульс — свежая МСК отметка и реальные действия.

Существующие требования и история: #731/#736/#770 (Watch), #745 (freshness), #767 (spec), #781 (отдельная infrastructure). Массового закрытия нет; superseded допускается после переноса каждого невыполненного критерия с двусторонними ссылками. Старые audit snapshots: [19Oct](audit/2026-10-19/README.md), [25Oct](audit/2026-10-25/voice-and-project-audit.md), [1Nov](audit/2026-11-01/README.md).

# Gate — утверждённая программа аудио и защиты переписки
Дата: 07.11.2026. Владелец утвердил план в текущем Work и поручил сохранить его в Git.
Регламент: https://github.com/markmalina/ai/blob/main/WORK-RULES.md
Единственный управляющий указатель: https://github.com/markmalina/gate/issues/787
Ближайшая техническая задача: https://github.com/markmalina/gate/issues/828

## Роли и смысл программы
α Gate — контроллер: планирует, оформляет ограниченные задания, проверяет фактический source/CI/runtime/product результат и принимает передачи. α не подменяет Work-исполнителя реализацией. Марк запросил копируемое задание для отдельного Work. Подготовка задания, запуск, claim и принятие — разные состояния.
Уже существующая автоматизация «Продвигать Gate» этим решением не отключена и не продублирована. Перед любым новым исполнением проверить её текущий записанный run и реальные issue/claim/branch/PR/CI. Запись in_progress не доказывает живой writer, истёкший lease не доказывает его остановку. Не изображать связь между чатами.

Два конечных результата:
1. Аудио: выбранная сохранённая запись даёт полный доступный текст; затем этот путь работает автоматически, быстро и без повторной STT.
2. Переписка: ответ, подготовленный по старому состоянию диалога, не отправляется после изменения контекста.
Caller, ledger, миграции, выпуск и CI нужны для этих результатов. Source, зелёный CI, merge, installation и счётчик delivered не заменяют продуктовую приёмку.

## Доказанная база и границы свежести
Проверка GitHub 07.11.2026:
- Gate PUBLIC; main e7b3e32998706f7ef19053331466ea6c63105beb, tree e17b705e67747f52a937d9f2574b157600c0db9d.
- PR827 OPEN/unmerged: head5b730132b12196d7d28fbf1d0439770ddc09b639; tree39eb78632873432c5a59482ab843c3e40e6b78c1; sole parent6dca2490c2d579fd105ce4c037f8f0a4614b8871.
- Приёмка coordinator: https://github.com/markmalina/gate/issues/826#issuecomment-6703537955 . Реальные компоненты проверены на mock HTTP; это не production.
- CI42347915031 attempt1 SUCCESS: https://github.com/markmalina/gate/actions/runs/42347915031 . Node157 PASS; Python2986 PASS/1 SKIP; PostgreSQL17.11/Docker/Windows приняты в receipt. Новых CI этим документом не запускать.
- Main содержит ограниченный W1-путь сохранённой note до scan, но его работа в production не доказана.
- Startup PR813 c7d7f71013368cd2dbd14adb674cc119b72c7a33 и private Mark PR132 остаются unmerged кандидатами. Mark merge WAITING_OWNER: https://github.com/markmalina/mark/issues/131 .
- Recovery PR808:657b7581f4e7bde5638b93acb7607b702d106082/tree0b271d944ab5ad9d79d4826947a5f9e6d433fde1. Он сохраняет literalOIDCtrue; env=false не доказывает безопасный recovery.
- Последний известный runtime AFE0/schema0115/OIDC ON/ordinary Watch OFF — датированное наблюдение #809, не свежая production-проверка. Selected-note metadata, исторический бюджет, полный writer/inflight proof и полный текст не подтверждены.
- Full WATCH_QUIESCE NOT_READY; full Gate downtime NOT_GRANTED. Maintenance receipt stopped-fleet не равен двум runtime flags=false и не доказывает завершение уже отправленного внешнего запроса.
- Полная защита переписки — source PR747 b975475c1c6c81035e38d9bd13a40d777933e83c, установка не подтверждена.
- Watch/early-card candidate PR76645ba499a5f1852208ba8a478a606358fd0fe3bd5 — OPEN/unmerged. Текущий Dropbox webhook будит Calls, а не Watch.
- Подключённый каталог Work не предоставляет standalone STT и Watch claim/start/submit. Получение original не является распознаванием.
Все volatile факты перечитать перед исполнением; этот файл не заменяет свежий next pointer.

## W1 — первый продуктовый спринт
Цель: выбранная сохранённая Galaxy Watch → Samsung Recorder/Dropsync → Dropbox → Gate запись даёт 1 полный текст в 1 private Mark issue.
Критерий: completeness100%, duplicateSTT0, без новой загрузки original и без переключения общего cursor ради обхода.
Около шести технических циклов — рабочая оценка, не гарантированный срок или фиксированное число запусков.

| Цикл | Для чего | Действия | Приёмка |
|---|---|---|---|
|1. #828, default-OFF caller|Проверяемое управление maintenance|Подключить принятый coordinator, сохранить literal OFF, offline entry/preflight/packing tests, собрать точный owner installation/activation packet. Реальное artifact происхождение подтверждать после upload по metadata+original bytes+digest|Один independently reviewed unmerged candidate + packet; либо один воспроизводимый interface gap и малый следующий шаг. Никакой фактической активации|
|2. Согласованные release/recovery|Исключить случайное включение обработки при startup/recovery|Собрать совместимые W1/startup813/maintenance source; подготовить recovery с безопасным OFF вместо текущего literalOIDCtrue|Exact heads/trees/parents/deltas, OFF, проверки, commands/caps/readback/STOP/recovery|
|3. Установка принятого source|Сделать подготовленные механизмы доступными|После exact owner gates необходимые Gate/Mark main merges и readback. Ledger branch/seed/ACL/protection — явно отдельные operational prerequisites, без скрытого bootstrap|Actual mergedSHA/installed workflow/source проверены чтением; caller не активирован этим merge|
|4. Прекращение конкурирующей обработки|Исключить двойную STT и потерянные эффекты|Mark caller pause + полная очередь; отдельно допущенный full Gate maintenance при необходимости; полный writer/inflight proof и reconciliation без reset UNKNOWN|Проверено отсутствие конкурирующей обработки; stopped deployment/disabled caller/latest100/cumulative claims не подменяют полный proof|
|5. Runtime и выбранная запись|Запустить правильную note на правильной версии|Согласованный release с безопасным OFF; exact SHA/schema/fleet/flags. Проверить note identity/revision/hash/size/duration/attempt history, исторический budget по received_at и readiness provider/key-project association|Доказанный пакет одного разрешённого W1-запуска с real limits и readback; UNVERIFIED не заполняются догадкой|
|6. Полный текст|Первый продуктовый результат|Одна разрешённая STT существующей выбранной note, полный текст и все части в одну private Mark issue. Повторная публикация продолжается из сохранённого текста|0→1 full text;100% ordered parts/readback;0 duplicateSTT; отсутствие нового scan/download и другой выбранной записи|

Старый LKG initial-exact не является коротким путём: он может продвинуть общий cursor через чужие entries. Сохранять future-only. Старый UNKNOWN не переигрывать и не очищать.
После W1 оставшиеся source/installation/real-data gates не наследуются автоматически.

## Порядок следующих продуктовых спринтов — УТВЕРЖДЁН
После W1 защита переписки R1/R2 поднята перед расширенным Watch, Calls и Work fallback. Это явное изменение прежнего общего порядка полной интеграции PR766→PR747; оно не утверждает уже выполненную интеграцию или совместимость. Не делать полный PR766 универсальным prerequisite W1.

| Спринт | Пользовательская цель | Что делаем | Критерий |
|---|---|---|---|
|R1/R2, #745|Устаревший ответ не отправляется|Минимальный согласованный guard из PR747 на фактическом main; immutable Work-read snapshot→draft→approval→final admission; обновлённый реально подключённый MCP-каталог. Независимо проверенный Gate-owned cutover/backup/fixture/recovery, sends disabled при миграции, замена всех старых ingress/send writers. Простая stop/deploy операция не заменяет guard cutover|A01–A12, реальные cross-process PostgreSQL races. Stale provider attempts0; после exact live grant — неизменный контроль и отказ stale text/document/sequence. Никакого обхода старым draft/client/endpoint|
|W2, #770|Карточка до STT и доступный original|Выделить минимальный согласованный Watch app/schema пакет из PR766, одна private issue до распознавания, metadata_received отдельно от original_ready; защищённый original. Согласовать schema graph с уже установленным reply guard|Карточка видна при медленной/недоступной STT; original реально читается; дубликатов0|
|W3, #770/#736|Быстрый Watch без ожидания редкого cron|Подписанный Dropbox webhook + durable Watch wake; независимый discovery во время долгой STT; отдельные Call/Watch cursors, один driver/claim и recovery polling|Пилот3/3; затем n≥20, предлагаемый Dropbox→card P95≤60s/max≤120s с breaches/unavailable intervals. Это targets, не достигнутый SLA|
|W4, #736|Автоматический полный текст в той же карточке|original_ready→claim→STT→ordered publication, журналы и recovery/lostACK; no duplicate providers/issues|Completeness100%, duplicateSTT/issue0. Очередь/STT/delivery измеряются отдельно; предлагаемый original_ready→start P95≤30s на n≥20|
|C1, #788|Полный текст выбранного длинного звонка|Проверить actual media duration; согласовать Call-specific duration/parts/output/model policy и budget. Существующие guards60min и20×180s меняются согласованно, Watch/Telegram не расширять попутно. Target≥120min и actual selected duration. Targeted recovery только доказанного pre-STT rejection с attempts0; existing original reused|1 full protected Gate M transcript, ordered parts/gaps/truncation/duplicate dispatch0; readback полного текста. Mark issue для каждого Calls не предполагается|
|W5, #770|Реальный Work fallback без двойной STT|Доказать существующую speech capability; подключить защищённые claim/start/submit и общий журнал. Gate-first/Work-first/simultaneous/late-result проверки|1 реальный Work transcript принят в ту же issue; Gate STT после accepted Work result0. При отсутствии capability — один точный gap, не ложный transcript и не остановка остальных целей|
|Q1, bounded Watch/Calls stages|Плохой файл не блокирует следующий|Отдельными ограниченными циклами oversized/unreadable/malformed, понятный terminal reason и сохранение cursor/original/UNKNOWN|Отказ плохого + успешная обработка следующего допустимого; lost originals/duplicateSTT/stuckcursor0 в проверенных случаях|
|Q2, Calls|Длительная STT не задерживает discovery новых звонков|Durable queue, отделить metadata discovery от обработки, ограниченная concurrency/leases. Watch-разделение уже вW3, не повторять|Новая metadata появляется во время предыдущей STT; восстановление после restart100% на серии; предлагаемый webhook→queuedP95≤30s|
|S, эксплуатация|Устойчивая работа двух маршрутов|24–72ч наблюдений, controlled restart/recovery, понятные failures/next action, latency/usage/cost с полными знаменателями|Eligible/pending/rejected/UNKNOWN видны, silentloss/duplicateSTT/issue/send/stale sends0 в наблюдённой выборке; proposed terminalcoverage≥99% оценивается с unfinished и n|

### Обязательная интеграция схемы
Watch source:0116_watch_voice_observability→0117_watch_voice_early_inbox→0118_watch_voice_work_results→0119_watch_voice_comment_ids.
Reply source:0116_reply_dialog_versions отдельно от0115.
Сохранить исторические migration bytes; добавить согласованную forward/join revision на фактическом main с новой свободной identity, получить один Alembic head. Нынешний reply_cutover helper принимает только0115/0116reply; он не готов к Watch0119 или будущему joined head. Обновить helper/readiness/fixture/cutover/recovery и проверить real PG upgrade от наблюдаемой production schema и нужных установленных heads, backup/restore и UNKNOWN-ledger preservation. Не заявлять text-clean merge семантической совместимостью. Зафиксированные общие пути CI/health/MCP/config/тестов требуют содержательной интеграции.
Источники:
- https://github.com/markmalina/gate/issues/770#issuecomment-6559081641
- https://github.com/markmalina/gate/issues/745#issuecomment-6562923852
- https://github.com/markmalina/gate/blob/b975475c1c6c81035e38d9bd13a40d777933e83c/app/infrastructure/reply_cutover.py
- https://github.com/markmalina/gate/blob/b975475c1c6c81035e38d9bd13a40d777933e83c/docs/operations/DIALOG-SNAPSHOT-GUARD.md

### Точная гарантия переписки
Событие, durably принятое Gate до final send admission, блокирует старый ответ. Новые входящие/исходящие, edit/delete, media без STT, одинаковый текст новых сообщений и другой Work учитываются в том же scope. Дубликат webhook и чужой диалог не дают ложного invalidation.
Уже начатый Telegram HTTP send и событие, ещё не доставленное Telegram в Gate, нельзя отменить задним числом. На context_changed — sent=false, доступный свежий контекст, новый reviewed draft/approval, без автоматического обновления snapshot у старого ответа или слепой повторной отправки.

## Ритм, доступ и расходы
- Один ограниченный technical outcome/цикл≤45min; implementation≤25min/с однимCI≤35min; у #828≤6paths и его более строгие STOP.
- Один writer/claim/branch/isolated checkout; independent reviewer перед публикацией, когда требуется задачей. В каждое назначение/continuation/fix/review включать прямую ссылку https://github.com/markmalina/ai/blob/main/WORK-RULES.md .
- α проверяет actual code/sole parent/tree/paths/bytes/tests/logs по масштабу; возвращает один конкретный defect. Не перезапускать зелёный/активный CI; first hostedFAIL у #828→saveSTOP/no rerun.
- До реализации читать применимые AGENTS/WORK-PARENT/bootstrap/current/recovery/active issue/latest receipts. Исторические аудиты — только для конкретного вопроса. Каждый цикл связывать с пользовательским критерием, а не новым общим аудитом.
- Существенные findings/errors/decisions/freeze/CI/cost/terminal в issue исполнителя; принятые receipts/план/next pointer в#787. No heartbeat-only writes.
- Текущий Gate PUBLIC CI ubuntu-latest/windows-latest использует standard runners: compute minutes free. Новую платную оценку за эти минуты не начислять; историческую estimate$3.800 не удалять и не называть invoice. Usage/invoice неизвестны там, где не прочитаны. Private caller billing привязан к caller; STT/Railway учитывать отдельно по реальным операциям.
- Official billing checked07.11: https://docs.github.com/en/actions/concepts/billing-and-usage ; larger runners не становятся бесплатными от публичности.
- n8n использовать, если конкретный маршрут разрешён/доступен; иначе GitHub connector; запись проверять чтением. Для текущего сохранения narrow n8n search вернул Internal error; использован разрешённый GitHub fallback.
- Новых автоматизаций/контроллеров/повторных writers не создавать.

## Protected gates / сохраняемые STOP
Утверждение плана не является exact live grant. Main merge Gate/Mark, live disable/restart/deploy/config, fullGate downtime, credentials, ledger initialization/ACL, реальная DB/data/audio/STT, сообщения/платежи требуют действующего точного допуска с subject/revision/action/target/expiry/caps/readback/STOP/recovery.
Не придумывать current UUID/fleet/ACL/remaining/expiry/grant или происхождение artifact. Перед защищённым шагом готовится проверяемый конкретный пакет; только после этого запрос владельцу.
Grants803/806/807 consumed; PR805 lock expired, no rearm. Старый UNKNOWN сохраняется/no replay. S1 NOT_ACCEPTED/AQUB frozen. Внешнюю переписку/Telegram не отправлять.
Источник текущего fixed maintenance contract: https://github.com/markmalina/gate/issues/815#issuecomment-6699598571 .
В частности, recovery HOLD_NO_AUTORESTORE: не restart/deployAFE0 или currentR с literalOIDCtrue ради восстановления health.

## Следующий Work
Готовый bounded текст: [WORK828-HANDOFF.md](WORK828-HANDOFF.md).
Ожидается запуск Work-исполнителя Марком либо уже существующее реальное исполнение автоматизации; подготовка этой передачи не создаёт claim#828.
Сначала свежая проверка конкурирующего исполнения. Current issue и принятый#787nextpointer заменяют этот датированный baseline.

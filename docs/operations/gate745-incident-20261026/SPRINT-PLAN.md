# Gate #745 / PR #747 — план исправления и завершения по спринтам

Версия плана: 26.10.2026. Основание: прямое решение Марка «Да, утверждаю и исправляй», запрос полного плана и движения по спринтам.
Основной issue: [#745](https://github.com/markmalina/gate/issues/745), единственный guard PR: [#747](https://github.com/markmalina/gate/pull/747), execution `gate-745-snapshot-20261008T1458Z`.
План обозначен **R1–R8**, чтобы не переименовывать прежние инфраструктурные S1–S7 из [исходного ТЗ](../../specs/ISSUE-745-SPRINT-SPEC.md).

## Цель и граница завершения

Gate разрешает согласованный ответ по точно прочитанному состоянию диалога. Исторические pending tasks не создают ложного отказа. Новое принятое событие любой стороны инвалидирует старый ответ **до начала внешнего запроса**; Work получает причину/изменения и может пройти новый цикл. Работают одиночные ответы, follow-up, документы/изображения и серии2–50; однократность/unknown/audit сохранены.

Готовность кода, PostgreSQL, независимого source review, инфраструктуры, установленного runtime, каталога MCP и пользовательского результата — разные состояния. #745 закрывается после матрицы A01–A12 и пользовательской приёмки, а не после merge/CI/deploy.

## Разрешённый объём и порядок

Уже утверждены source-исправления и проверки A+B из диагностики: Gate runtime/MCP, synthetic/PG/race/regression, работа в прежнем PR. Для обычных обратимых source-поправок R1–R3 и source/doc preparation не требуется повторять общее «можно исправлять?».

Credential/secret/OAuth, новые ресурсы/расходы, native операции, Production cutover и живые сообщения не входят в это разрешение. До такого действия готовится конкретный проверяемый пакет subject/revision/target/effects/caps/expiry/rollback. Раньше готового пакета разрешение не запрашивается. Значения credentials не нужны в чате/issue. Старые4/12drafts никогда не используются для приёмки.

Работать по очереди; независимую source/doc подготовку Ops можно делать через уже существующий outcome после подтверждённой передачи, без второго исполнителя того же этапа. После каждого спринта: commit, проверки/пропуски, остаток, next pointer. Нельзя объявлять тихую фоновую работу между Work.

## Сводка

| Спринт | Проверяемый результат | Условие выхода |
| --- | --- | --- |
| R1 | Исправлены ложные task-superseded во всём пути draft→queue→sender→worker | Новые regression tests и точный source commit; status испытаний указан честно |
| R2 | Завершён контракт Work↔Gate для чтения, draft, queue/send и отказа | Полный API roundtrip с mandatory snapshot; повторное чтение и новые drafts дают рабочий путь |
| R3 | Проверены гонки, идемпотентность и совместимость | Необходимые PostgreSQL/regression/миграционные checks exact head; новых дыр/необъяснённых skips нет |
| R4 | Восстановлен действительный Gate-owned Ops маршрут | Допущенный metadata transport, установленный reviewed executor и согласованные pins; protected readonly proof |
| R5 | Стенд подготовлен и проверен без влияния на Production | Supported topology/caps/cost/isolation/lifecycle; отдельный native fixture и проверяемые recovery/cleanup receipts |
| R6 | Собран и проверен полный кандидат к выпуску | Актуальный main интегрирован; все Gate/Ops/client revisions совпадают с доказательствами; независимый source verdict |
| R7 | Безопасно установлен новый Production runtime | Verified backup/fleet/schema, guarded cutover0116, точный SHA, health и проверенный rollback |
| R8 | Защита работает через реально подключённый Gate M | Каталог/roundtrip/live canary, A01–A12 и отдельная пользовательская приёмка |

## R1 — исправить фактические ложные запреты

### Работа

1. Убрать безусловный sibling NEEDS_REPLY veto в queue после обязательной snapshot validation.
2. Убрать такой же sibling veto в save follow_up после mandatory reviewed snapshot validation.
3. В single/follow-up sender сохранить stable locked covered_task_ids и preflight/reservation/admission checks, но убрать правило «любой pending coverage запрещает REPLIED follow-up».
4. Убрать из sequence worker предварительный timestamp-вызов `_sequence_superseded`: задача, созданная позже исходной, может быть уже прочитана и согласована. Общий sender проверяет актуальный immutable snapshot и durable sequence progress. Missing/deleted task по-прежнему отказывает в sender и останавливает хвост.
5. Не массово закрывать pending tasks для снятия запрета; не обновлять snapshot у старого draft; не менять текст, адресата, payload hash или sent/failed/unknown.
6. Сохранить прежний интервал60 секунд и размер2–50. Произвольные10–80 секунд — отдельное улучшение, не часть данного исправления.

### Проверки и выход

Новый integration file покрывает19сценариев: pending task старше/новее task-origin, но прочитана до approval; NEEDS_REPLY/REPLIED; single/follow-up и replay; серии2/50 с точным порядком; новое inbound/outbound с тем же текстом блокирует draft/queue/send и затем допускает новый read→draft→send; отсутствие snapshot отказывает. Существующие sender/worker/concurrency/formatting tests не удаляются и не ослабляются.

На подготовке R1: Ruff изменённых путей PASS; scoped strict Mypy четырёх runtime modules PASS; unit subset36PASS, новые19PGSKIP без локального DATABASE_URL. Локальный bundled PostgreSQL нельзя запустить непривилегированным пользователем из этой execution среды; PG проверки обязаны пройти в existing hosted CI. Это не claim «19 пройдены».

Найден четвёртый дефект worker сверх первоначальной диагностики трёх source seams. R1 не меняет миграцию/пины/workflows/provider/client deployment.

## R2 — закончить контракт Work и Gate

### Работа

- Инвентаризировать точные server route/input/output/descriptor и реально подключённые MCP schemas для context, draft, document, single, series.
- Обязательный opaque `context_snapshot` выдаётся только для полного/достоверного readset в разрешённом workspace/account/conversation; `context_ready=false` не выдаёт разрешение.
- Draft/document binding хранит именно прочитанный snapshot и payload; approval не заменяется скрытым refresh.
- Все ошибки freshness возвращают sent=false, реальную причину, scoped current_context и bounded delta обеих сторон. Усечение/неполная проекция/недоступный scope обозначаются явно, имеется дочитывание.
- Проверить полный цикл отказ→новое чтение→новый точный draft→approval→send/queue без бесконечных task_superseded.
- Согласовать progressive snapshot только для собственных подтверждённых items той же серии; посторонняя отправка прерывает серию.
- Документы/фото/formatting hashes/captions/entities используют тот же контракт. Не расширять каталог raw-provider обходами.

### Выход

Contract tests проверяют реальные handlers, а не только enum/schema. Server source готов; соответствующий published connected catalogue отдельно проверяется в R8, до этого не называется обновлённым у пользователя.

## R3 — доказать корректность и отсутствие регрессий

### Работа

- Actual PostgreSQL ingress↔admission race в разных sessions/processes, включая новое событие после queue/approval/reservation и до provider HTTP.
- Durable raw receipt меняет revision до projection/STT. Text/same text/new ID/voice/photo/file, owner/other Work inbound/outbound, supported edits/deletes, чужой scope и ingress gap.
- Два Work с одним snapshot; конкурирующие drafts/tasks/queues; crash/restart; sent/failed replay; terminal unknown без автоматического повторения; caps до provider calls.
- Series2/50: порядок, интервалы от подтверждённой доставки, остановка хвоста после нового события, own progress, отсутствие auto-resume.
- Legacy client/draft без snapshot, несовпавшие payload/account/recipient, старые retry/direct file/media routes — fail closed.
- 0116 upgrade/downgrade, migration graph/schema drift, synthetic backup/restore и совместимость XLSX/PNG/PDF/ZIP, markdown/plain/rich.
- Один необходимый оригинальный exact-head CI после bounded source-пакета, без rerun успешных/pending проверок. Если runtime меняется после этого — новые доказательства для нового SHA.

### Выход

Literal head и mandatory synthetic-merge checks подтверждены отдельно. Список PG skips и их причины явный; ни важный race, ни failing integration не заменены mock. При failure исправить причину в том же PR. Новая exact independent source attempt выполняется по действующему проектному механизму; авторский тест не независимый verdict.

## R4 — восстановить действительный маршрут Ops

Соответствие прежнему ТЗ: S1 и часть S3. Не переписывать уже существующий accepted C4 с нуля.

### Работа

- Перечитать existing Ops375/PR381 и supporting Gate758/760/762, actual main/installed release, owners/immutable IDs и смешанные namespace.
- Для двух отдельных потребителей A/O доказать существующий narrowly scoped private Ops/Delta metadata transport. Наличие secret — UNKNOWN, пока нет redacted metadata receipt; connector access не Actions credential.
- Если требуется новый binding/credential, сначала конкретный способ/issuer/readset/permissions/lifetime и точное решение владельца. Старый постоянный GitHub App source key для ordinary short deploy не возвращать в требования.
- Установить reviewed независимый Gate-owned executor, исправить current identity/owner/artifact/readsets и согласовать lock/pin/digests только с реально принятой ревизией.
- Провести разрешённый protected readonly запуск с exact target/source identity, distinct requester/approver, actual autoDeploy/readiness evidence. Default-off и deny unknown сохраняются.
- AQUB frozen: ни старые workflows/grants, ни secrets не используются. Short deploy не подменяет snapshot cutover.

### Выход

Есть конкретный работающий transport и redacted runtime metadata receipt; source ACCEPT/installed executor/release enabled/fixture accepted остаются разными фактами. Если blocker не решён — назвать его, вернуться к безопасной source/doc части, не просить владельца самому разбирать общий backlog.

## R5 — физический стенд и восстановление

Соответствие: прежние S2/S3/S5; часть source R6 может быть подготовлена до physical run.

### Работа

- Подтвердить по supported capabilities topology, effective CPU/RAM/fleet caps, initial volume до first PostgreSQL, isolation/private network и отсутствие inherited real secrets/data.
- Закрыть аутентифицированные DB/runtime identity, preparation, durable one-use counters/deadlines, original run lineage.
- Составить конкретные лимиты расходов, времени/операций/ресурсов, emergency stop, cleanup и surviving observer. REQUESTED/QUEUED/null/403 не выдавать за terminal deletion.
- До allocation — accepted preparation/native/cleanup scope и grant; source-код удаления/restore не писать вопреки действующим отдельным ограничениям.
- Изолированный disposable PostgreSQL/Gate fixture, migration0116, verified backup+restore, success/fault recovery/restart/stop.
- Сохранить результаты cleanup/billing/terminal observation с честным NOT_PROVEN, если физический факт не доказан.

### Выход

Native fixture/recovery принят по реальным receipts. Production/личные сообщения/LLM не используются как стенд. Недоказанный cost/identity/cleanup блокирует allocation, а не оправдывает обход.

## R6 — целостный кандидат к выпуску

Соответствие: S4 и повторная интеграционная проверка после изменений.

### Работа

- Интегрировать актуальный main в прежний PR747; сохранить независимые changes и frozen boundaries.
- Свести Gate runtime/MCP/migrations, installed Ops contract и supporting P/C0/H2 exact revisions.
- Выполнить необходимые exact-head unit/PG/migration/formatting/container checks и bounded staged/native rehearsal.
- Получить независимый immutable SOURCE verdict с точными commit/tree/pins; старые ACCEPT не переносить.
- Подготовить concrete release packet: source target/environment, fleet, schema, configuration fingerprint, autoDeploy, bound backup, LKG, stop и rollback.

### Выход

Нет неподтверждённых зависимостей и несовпадающих source/installed fingerprints. Техническая SOURCE готовность отделена от будущего Production и live результата.

## R7 — безопасный Production cutover

Соответствие: S6. Выполнять только после конкретного утверждённого release packet.

### Работа

- Fresh actual Production full SHA/deployment/schema/config/fleet, actual autoDeploy=false, отсутствие pending drift.
- Общий operation lock и полная остановка старых writers/overlapping replicas; ни одна старая версия не пишет при новом schema protocol.
- Проверенный bound backup, пригодный restore и версия отката. Наблюдённый short SHA afe0c6b85193 не объявлять автоматически готовым LKG.
- Guarded sends=false → migration0116 → exact-source deployment → schema/health/fingerprint verify → sameSHA sends=true → independent verify.
- STOP при identity/source/fleet/backup/health/unknown mismatch. Откат по заранее проверенному пути с закрытой доставкой и согласованной schema, без слепого retry.

### Выход

Точный установленный SHA, schema, health и release/rollback receipts. Успех deploy без runtime verification не закрывает спринт.

## R8 — реально подключённый Gate M и приёмка

Соответствие: S7 и A01–A12.

### Работа

- После выпуска проверить реально обнаруживаемые инструменты, входы/выходы snapshot, read→draft→approval→send и current_context на отказе.
- Подготовить один конкретный Mark-controlled Business dialog, синтетические final texts/files/hashes, точную installation/account/recipient и доступные provider/message caps.
- Старое предложение ≤15мин/≤6delivery dispatch/modelSTT0/bounded reads — проект лимитов, не существующий grant. Live допускается только по конкретному готовому пакету.
- Доказать unchanged send+replay один раз, новое inbound/outbound до admission→0dispatch, восстановление новым циклом, stale docs и остановку хвоста серии.
- Редкие гонки/voice/edit/delete проверять синтетически без реальных STT расходов. Для дополнительных live effects сначала подготовить отдельный точный сценарий.
- Свести A01–A12 с evidence/ограничениями; независимая техническая и отдельная пользовательская приёмка. При неудаче не закрывать #745.

### Выход

Марк может выполнить сценарий через подключённый Gate M; проверенные receipts показывают реальный результат. Только после этого задача завершена.

## Риски и приоритет

1. Простое удаление sibling veto **без** mandatory snapshot было бы ослаблением защиты. R1 выполняется только в snapshot-bound PR747, никогда отдельным переносом кода на старый Production.
2. Task timestamps не заменяют dialog revision; задержанная projection и одинаковый текст особенно важны.
3. Наш гарантируемый рубеж — durable event, известное Gate до разрешения начать provider HTTP. Уже начатый Telegram запрос и ещё не доставленное Gate событие не находятся под гарантией отмены.
4. Нельзя объявлять green CI достаточной заменой installed control/fixture/connected client.
5. R4/R5 — реальные инфраструктурные зависимости; общий календарный срок завершения #745 пока не подтверждён.

## Текущий next pointer

R1 source-пакет подготовлен в существующем PR; exact remote commit/CI и завершение фиксируются в issue receipt после публикации.
После принятого R1 — R2: contract audit и законченный Work↔Gate recovery roundtrip. Новый общий запрос разрешения на уже утверждённую source-работу не требуется.

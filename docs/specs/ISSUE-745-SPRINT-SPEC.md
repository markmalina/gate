# Gate #745 — подробное ТЗ разработки и выпуска по спринтам

Версия: 1.0, 19 октября 2026 года. Автор постановки — Марк Малиновский; подготовка и техническое проектирование — α Gate.

Статус документа: **подготовленное исполнительное ТЗ для рассмотрения и дальнейшей работы**. Поручение подготовить ТЗ получено; разрешения на перечисленные в нём будущие внешние действия этим документом не выдаются. Непринятые изменения lifecycle, лимитов и условий приёмки явно помечены как предложения.

Каноническая задача: [markmalina/gate #745](https://github.com/markmalina/gate/issues/745). Единственный guard PR: [#747](https://github.com/markmalina/gate/pull/747). Продолжать execution `gate-745-snapshot-20261008T1458Z`, ветку `agent/745-dialog-snapshot`. Новую реализацию, issue, execution, ветку или guard PR не создавать. Документ не принимает полномочия Ops или независимого δ.

## 1. Результат для пользователя

Work прочитал диалог, подготовил ответ и получил согласие на точный текст или файл. Пока ответ готовился, собеседник, Марк вручную либо другой Work изменил тот же диалог. Gate должен остановить старый ответ **до допуска внешней отправки**, вернуть понятный отказ и актуальный доступный контекст. После этого Work заново читает, готовит и согласовывает ответ.

Gate не оценивает смысл нового сообщения, не переписывает ответ, не заменяет его снимок свежим и не возобновляет старую отправку автоматически. Даже «угу», повтор того же текста, непрочитанное голосовое или файл меняют состояние.

Защита распространяется на текст, форматированный ответ, оригинальные документы и изображения в поддерживаемом document-пути, последовательности, отложенную отправку, повторы и старые интерфейсы. Отдельный будущий photo/album sender не считается защищённым до включения в ту же границу допуска.

**Граница гарантии:** событие, durably принятое Gate до окончательного send admission. Нельзя обещать знание ещё не доставленного Telegram update или отмену внешнего HTTP-запроса после admission. Гарантия не означает, что переписка не изменится между admission и появлением ответа в Telegram.

**Закрытие задачи:** защита включена в реальном Gate; подключённый MCP-каталог соответствует серверу; пройдены A01–A12 и разрешённая пользовательская проверка. Source ACCEPT, зелёный CI, merge и deploy по отдельности задачу не закрывают.

## 2. Найденные материалы и приоритет источников

Готовая основа найдена: [план R1–R7 от 11.10](https://github.com/markmalina/gate/issues/745#issuecomment-6110542018), guard runbook и отдельные bootstrap/control/fixture документы. Единого актуального файла с подробными спринтами, исследовательскими задачами, выходами и полной трассировкой до A01–A12 не найдено. Настоящий документ сводит этот материал и уточняет порядок работ по состоянию на 19.10.

Приоритет: прямое действующее решение Марка → действующие инструкции проектов → требования #745 → exact source/контракты → аутентифицированные наблюдения runtime. Старое описание или квитанция доказывают только свой предмет и время. При конфликте этой проектной версии ТЗ с действующим разрешением эффект запрещён до разрешения конфликта.

| Источник | Назначение и предел |
|---|---|
| [#745](https://github.com/markmalina/gate/issues/745) | Требования и A01–A12; заголовок содержит исторический срез, новые факты находятся в комментариях |
| [Свежий receipt 19.10](https://github.com/markmalina/gate/issues/745#issuecomment-6261141908) | Совместимость, открытые барьеры, текущая область проверки |
| [Передача/принятие 19.10](https://github.com/markmalina/gate/issues/745#issuecomment-6261020040) | Преемственность исполнения; не разрешение внешних операций |
| [DIALOG-SNAPSHOT-GUARD.md](../operations/DIALOG-SNAPSHOT-GUARD.md) | Пользовательский и транспортный контракт, миграция и граница admission |
| [GATE745-FIXTURE-BOOTSTRAP.md](../operations/GATE745-FIXTURE-BOOTSTRAP.md) | H2, идентичность БД, синтетическая рецептура и ограничения |
| [GATE745-OPS-CONTROL.md](../operations/GATE745-OPS-CONTROL.md) | Route/pins и исторические release facts; не текущий grant |
| [Ops controller README](https://github.com/markmalina/ops/blob/a58d84798d0471792d391987c27a3cd2f6e7bb03/agent-ops/actions/gate745-fixture-controller/README.md) | Реализованные seams; часть вводного статуса отстаёт от policy.json, проверять код |
| [v3](https://github.com/markmalina/gate/issues/745#issuecomment-6113002426) и [v7](https://github.com/markmalina/gate/issues/745#issuecomment-6124068885) | Подробный предложенный preparation/cleanup граф. **Не утверждены как эффект или lifecycle** |
| [Delta315 SOURCE ACCEPT](https://github.com/AlphaCenter/delta/issues/315#issuecomment-6124103323) | Только точный head/tree 827e5f9/9e11fd5; не стенд, Production или клиент |
| [Ops375 dependency packet](https://github.com/markmalina/ops/issues/375#issuecomment-6261157669) | Подготовленная зависимость caller/limits. Получение и принятие Ops не подтверждены |

Этот файл уточняет план исполнения; он не меняет автоматически owner body digest, A01–A12, accepted schemas, effect grants, профиль Production или прежние независимые verdicts.

## 3. Проверенный исходный срез

Наблюдения репозиториев освежены 19.10.2026, 09:40–09:44 МСК. Это срез для проектирования, не обещание неизменности к будущему запуску.

| Предмет | Подтверждённое состояние |
|---|---|
| Gate main | `86dbc0f429dce089bf733fc422f20f556535895b`, tree `7b5ddc0e6317fc475e2256953a69a07b109463dc` |
| PR747 | OPEN, не merged; head `827e5f94d34f833d9a5acd6f244444eec223e77e`, tree `9e11fd5263e95618135f7002080e38e2bdbce05a` |
| Ops main | `a58d84798d0471792d391987c27a3cd2f6e7bb03` |
| Gate/Ops repository identity | Gate ID `1293746267`, Ops ID `1331657982`; current owner `markmalina`, ID `23397294`; оба private |
| Main schema source | 119 migration files, head 0115; 0116 есть только в неслитом кандидате #747 |
| Guard source | Snapshot, ingress revision, draft binding, общий sender admission, sequence progression, legacy refusal реализованы |
| Исторический CI exact head 827e5f9 | Original runs 38246677141 / 38246677152 / 38246677069 SUCCESS; backend 3257 PASS / 1 SKIP / 12 subtests, реальные PG/migration/backup/container проверки |
| Свежая локальная совместимость | Synthetic tree `9fa092872d08500a2f86b67dcac872faba478d45`: 464 PASS / 90 PostgreSQL SKIP / 0 FAIL; это не новый independent ACCEPT |
| Production, scoped metadata 19.10 | `afe0c6b85193`, send_enabled=true; не полная инвентаризация процессов, не доказательство 0116 |
| Подключённый MCP 19.10 | В инструментах чтения/подготовки нет требуемых snapshot-полей; каталог ещё не обновлён |
| Identity/preparation | Два pure source checker; authenticated collector, reservation adapter и preparation route отсутствуют |
| Native fixture | Не принят; manifest UNBOUND; limits barrier сохраняется; cleanup artifact не выпускается |
| Cutover route | Старые namespace/owner bindings; текущий personal Gate caller получает E_CALLER до provider access |
| Release policy | Production mutation disabled, native fixture PENDING, allocationPolicy=null |

Дополнительные уже существующие source PR обнаружены и перечитаны: Gate #758 (H2, head `ac0e2dacefb258cff26095b1fdf58b85ee1c9169`), #760 (C0, `7ec459467b606ac67ec2110289e9018fa322b8fa`), #762 (P, `e28b1db0776d0dd1f2a20b2247d6179938e4627f`). Все OPEN/не merged на момент среза. Это прежние вспомогательные этапы, не новые guard PR. Наличие review не доказывает установку на main. Их не возобновлять конкурентно; перед любой интеграцией проверить владельца, lease и exact source. Ограничение Марка на новые issue/branch/PR сохраняется.

## 4. Исследование: выводы и технические последствия

### R01. Серверная сериализация нужна в БД

PostgreSQL row locks сериализуют конфликтующих писателей между процессами; блокировки освобождаются на границе транзакции. Process-local mutex и дополнительный HTTP-read истории этого не заменяют. Использовать существующие revision rows и короткие транзакции; не удерживать SQL-транзакцию во время provider HTTP. Проверять гонки реальными отдельными соединениями, а не только одним event loop. Источник: [PostgreSQL explicit locking](https://www.postgresql.org/docs/17/explicit-locking.html).

### R02. Полнота принятого ingress важнее готовности проекции

Revision должен коммититься с deduplicated receipt до транскрипции и проекции. ACK до durable commit запрещён. Один update_id не должен давать второй bump; порядок ID не заменяет scope и revision. Telegram ограничивает хранение непринятых updates и повторяет неуспешные webhooks конечным образом, поэтому downtime cutover не означает автоматически доказанную непрерывность. Источник: [Telegram Bot API: getting updates и setWebhook](https://core.telegram.org/bots/api#getting-updates). До выпуска нужны измеренный интервал остановки, durable queue/readback и честная классификация неопределённого ingress.

### R03. Перенос repository сломал несколько связанных проверок

Нельзя выполнить глобальную замену строки AlphaCenter. В cutover caller/profile, source loader, OIDC subject, C0/P workflows, artifact repository и owner-id проверках различаются repository identity, owner identity, actor identity, App identity и исторический review repository. Стабильный Gate repository ID сохранился, владелец изменился на ID `23397294`; старый org owner ID `310893851` не подходит current P. Исторические Delta-ссылки автоматически не переносить. GitHub позволяет связывать OIDC с repository_id, repository_owner_id и reusable workflow; это независимые утверждения: [OIDC reference](https://docs.github.com/en/actions/reference/security/oidc).

### R04. Найден отдельный owner-body mismatch

Текущее тело #745 имеет SHA256 `da37bc133eab3aa0bd21bc3ab700490c9f9c82e9c1b04b50ec0b4f3b150721ae`; snapshot profile требует `d8ae2e0e680c3876dd9baf872e5972e868e3b9b83aa7d2b449633f8935922d61`. `assertOwnerAuthorizationBody()` сравнивает именно байты тела. Это дополнительный SOURCE admission blocker после исправления caller.

Нельзя просто переписать expected digest и объявить тем самым новый effect grant. В S1 определить канонический неизменяемый owner authorization record, его действующий scope и происхождение. Mutable status держать отдельно от immutable authorization. Смена record/schema требует отдельной source-проверки; runtime-полномочия не расширяются.

### R05. Текущие resource-limit getters не дают готовности

В Ops `gate745-fixture-controller/native.mjs` функция `requireResourceLimits()` после чтения двух services безусловно отказывает с `E_FIXTURE_V2_RESOURCE_LIMITS_NOT_PROVEN`. Opaque JSON scalar не является доказанным сопоставлением CPU/RAM/units. Документация подтверждает per-replica limits, но не их фактическую настройку в выбранном target: [Railway cost control](https://docs.railway.com/pricing/cost-control). Нужно найти и закрепить поддерживаемый exact setter/readback и измерить полную physical fleet, включая overlap.

### R06. Начальная ёмкость тома ≤1 GiB пока не доказана

[Volume reference](https://docs.railway.com/volumes/reference) описывает defaults по plan, отсутствие downsizing и billing по занятому месту. Из этого нельзя вывести наличие initial-size setter или отсутствие такого setter во всех API. Нельзя сначала создать default 5/50 GB и назвать это выполнением ограничения capacity ≤1024 MiB. Used bytes и capacity — разные величины. Сначала доказать поддерживаемый маршрут и единицы либо вынести конкретное изменение ограничения на решение владельца.

### R07. Workspace hard limit не изолирует бюджет стенда

Общий compute hard limit может остановить соседние workloads. В этом проекте не менять его ради теста. US$5 из старого предложения — не доказанный отдельный provider hard cap. Cost model включает preparation, build/predeploy/rollout overlap, running compute, volume/backup retention, egress и observer/artifact расходы. Если поддерживаемый механизм не даёт требуемого ограничения, честный STOP до allocation; не обещать «точно не больше $5».

### R08. Predeploy — отдельный исполняющий процесс

[Railway predeploy](https://docs.railway.com/deployments/pre-deploy-command) выполняется отдельно от app; app volume там не смонтирован, а лимит времени необходимо установить явно. Доступ к удалённой PostgreSQL не зависит от app volume mount. В inventory включать build/predeploy и все процессы, способные писать в БД. Requested start/predeploy settings и HTTP health не доказывают effective command конкретного deployment.

### R09. Backup требует отдельного доказательства

[Railway backups](https://docs.railway.com/volumes/backups) описывает manual backup size limit и отдельный storage billing. Маленькая синтетическая рецептура не ограничивает PG/WAL и не доказывает backup eligibility. Для каждого backup нужны bound volume-instance, workflow completion, точный backup ID и locked state. Restore/wipe не являются разрешённым cleanup или recovery #745.

### R10. Удаление не помещается в прежнее обещание T+120

[Volume reference](https://docs.railway.com/volumes/reference) и [API changelog 01.05.2026](https://railway.com/changelog/2026-05-01-undoable-deletes) описывают 48-часовую soft-delete очередь. Это поддерживает необходимость раздельных состояний REQUESTED и DELETED, но не доказывает фактическое terminal состояние, права текущего token, cascade или billing выбранных объектов. В API guide встречается краткое «permanently delete»; не принимать его за синхронный purge вопреки более подробному описанию. После потери видимости 403/null нельзя объявить очистку.

### R11. v7 — предложение, а не активный протокол

v7 предлагает остановить compute/запросить удаление не позже исходного write deadline, максимум ARM+120 минут, и отдельно наблюдать до раннего read expiry, максимум ARM+72 часа. Наблюдение до 72 часов не обещает purge за 72 часа. Текущие строгие readers требуют старую same-run cleanup lineage; новый supplemental terminal receipt не примут без согласованного обновления schema/producer/verifier. Не выдавать молча новый receipt за старый четвёртый artifact.

### R12. Стенд должен проверять тот же cutover

Существующий `native-cutover-core.mjs` уже разделяет общий алгоритм и точные Production/fixture wrappers. Сохранить его: отдельный «упрощённый успешный тест» не доказывает Production release. Local/CI PostgreSQL полезны для races/migration/journal, но не заменяют provider-native fleet/config/backup/cleanup. Не добавлять второй generic deploy, SQL, GraphQL или shell executor.

## 5. Нормативные требования к поведению

| ID | Требование |
|---|---|
| FR01 | Снимок выдаётся Gate и связан с workspace/account/conversation, доступным project scope, connection set, durable revision и состоянием видимой истории |
| FR02 | Только непротиворечивое текущее чтение без historical cursor и с полным разрешённым scope выдаёт `context_ready=true` и usable snapshot |
| FR03 | Историческая страница, скрытый project, неполная история или изменение во время сборки не дают sendable token; частный payload не раскрывается |
| FR04 | Draft сохраняет именно `expected_context_snapshot` из прочитанного контекста; send API не может заменить его свежим |
| FR05 | Точное согласование связывает draft/payload/recipient/formatting и snapshot; изменение любого утверждённого элемента требует нового согласования |
| FR06 | Новое событие любой стороны, включая поддерживаемые edit/delete/media/voice, инвалидирует старый снимок без смысловой фильтрации |
| FR07 | Durable receipt и bump атомарны. Duplicate webhook, откат транзакции и другой dialog/account не создают ложного изменения нужного диалога |
| FR08 | Сохранение/approval/queue не заменяют финальный admission непосредственно перед send; конкуренция сериализуется в PostgreSQL |
| FR09 | На stale-отказе provider delivery dispatch=0, `sent=false`, code=`context_changed`; свежий контекст возвращается только при соответствующем read scope |
| FR10 | Нет snapshot/чужой snapshot/legacy draft/client/direct endpoint/retry — отказ до delivery; понятные стабильные коды, без сырого exception |
| FR11 | Повтор terminal sent/failed/unknown возвращает исходную квитанцию; не создаёт вторую отправку и не превращает unknown в retry |
| FR12 | Последовательность продвигается только доказанным собственным confirmed эффектом; сторонний accepted event останавливает неотправленный хвост |
| FR13 | TXT/plain/markdown/rich и document MIME из #700 используют общую границу; PDF/ZIP/XLSX/PNG не имеют отдельного обхода |
| FR14 | Старый snapshot не освежается автоматически после отказа, restart, queue recovery или повторного открытия Work |
| FR15 | Каталог подключённого Work реально содержит новые input/output поля; старый клиент не обходит серверную проверку |

Не смешивать `provider delivery dispatch` с read-only connection probe. Текущий sender проверяет stale baseline до probe и повторяет проверку после него. Если требуется ноль любых provider вызовов для конкретного раннего отказа — проверить и probe отдельно. Событие во время уже выполненного probe может остановить send, но не отменить прошлое чтение provider. В отчётах указывать delivery calls и probe calls раздельно.

### 5.1 Состояния результата

| Ситуация | Обязательный результат |
|---|---|
| Stale найден до создания send attempt | Явный отказ; попытка внешней отправки не создаётся |
| Stale найден после reservation, до delivery HTTP | Существующий attempt становится failed/context_changed; delivery dispatch=0 |
| Provider подтвердил send и сохранён receipt | sent, проверяемая квитанция |
| Provider однозначно отверг send | failed по существующей проверенной классификации |
| HTTP/commit outcome не определён | unknown/reconciliation; никакого автоматического повторного delivery |
| Тот же запрос после terminal outcome | Idempotent replay исходной квитанции |

### 5.2 Что не входит в эту задачу

Не перепроектировать #736, не делать его зависимым от 0116. Не включать dormant Personal Telegram delivery или будущие albums. Не разбирать частную переписку для диагностики. Не возобновлять AQUB. Не создавать систему общего управления инфраструктурой, новую систему ролей, новый recurring heartbeat или дополнительный grant на всякий случай.

## 6. Сохраняемая архитектура

1. `app/application/dialog_snapshots.py` — формат opaque token и классификация supported events.
2. `app/infrastructure/dialog_snapshots.py` — каноническое состояние, scope completeness, row locks, проверка сохранённого baseline.
3. `app/infrastructure/telegram_processing.py` — deduplicated receipt + revision bump в одной транзакции.
4. `app/application/mcp_inbox.py` — initial/final snapshot при сборке контекста, raw accepted events до готовой проекции.
5. Draft/document preparation — неизменяемый baseline и payload binding.
6. `app/infrastructure/reply_sends.py` — preflight, locked reservation, сохранённый admission snapshot, второй locked admission, provider HTTP вне транзакции, честная finalization.
7. `app/infrastructure/reply_sequences.py` и sequence worker — тот же sender; продвижение через durable confirmed progress, без caller refresh.
8. `0116_reply_dialog_versions` — additive таблица с ключом `(business_connection_id, chat_id)` и неотрицательной revision.
9. `app/worker/reply_cutover.py` — фиксированный переход к 0116 при допустимой schema/отключённой доставке/отсутствующих active attempts. Helper не доказывает остановку remote fleet — это обязанность Ops.
10. Ops shared native core + независимые observer/verifier — точный target, source, backup, fleet и runtime receipts.

Не заменять уже принятую защиту новым механизмом без доказанного дефекта. Сначала проверить существующие seams. Документирование и настройка стенда не должны менять approved текст, MIME, entity formatting или unknown semantics.

## 7. Спринты и зависимости

Спринт — ограниченный проверяемый результат, а не обещание количества часов или фонового исполнения. Календарный срок всего выпуска назначается после S2: до этого неизвестна выполнимость инфраструктуры.

| Спринт | Выход | Зависимость | Разрешённая область до нового effect grant |
|---|---|---|---|
| S1 | Исправляемый и проверяемый Gate-owned control/source маршрут | Текущий bootstrap/владение | Исследование, default-off source, локальные проверки |
| S2 | Принятое техническое решение стенда: поддерживаемые limits/cost/cleanup proof | Может исследоваться вместе с S1; запуск только после своего admission | Первичные документы, синтетические readsets, exact разрешённое readonly наблюдение при наличии маршрута |
| S3 | Рабочие preparation/identity/durable ledger/cleanup adapters, default-off | S2; для delete-кода точное SOURCE-исключение | Только утверждённый source scope; никаких реальных ресурсов |
| S4 | Целостный exact candidate и независимый SOURCE ACCEPT | Совместимые S1/S3 | Local/CI/PG, source review; без Production |
| S5 | Реальный native стенд принят, включая recovery и cleanup | S2–S4 + точные physical grants | Только exact grant и зарегистрированный workflow |
| S6 | Выпущенный guard в Production | Native ACCEPT + свежие release facts + exact plan grant | Только утверждённый cutover, без ordinary-deploy подмены |
| S7 | Подключённый клиент и A01–A12 приняты | S6; клиентский сценарий готовится раньше | Live send только после exact canary grant |

S1 и документальная часть S2 не ждут v7. Клиентский план, регрессионная матрица и подготовка S4/S7 также могут выполняться без платных ресурсов. Нельзя искусственно делать весь проект waiting_owner из-за одного запрета, пока есть независимая разрешённая работа.

## 8. S1 — восстановить Gate-owned control и source admission

**Цель:** исправить последствия переноса repository и сделать точный маршрут технически готовым к последующей авторизации. Не запускать Production или native fixture.

**Владелец:** α Gate — требования и client; α Ops — центральные adapters и release contract после отдельного принятия зависимости. δ — независимая проверка своей попытки. [Ops packet](https://github.com/markmalina/ops/issues/375#issuecomment-6261157669) уже записан, но не принят автоматически.

### S1.1 Исследование и инвентаризация

- Повторить duplicate/lease check #745/#747 и только затрагиваемых supporting lanes. Не принимать старую истёкшую lease за активную работу.
- Собрать реестр всех действующих и подготовленных route: Gate #751 ordinary deploy, #745 snapshot control, P/C0/H2 и original artifact loader. Для каждого: exact file/blob, installation state, SHA/tree, actor, repository/owner IDs, environment, permissions и side effects.
- Обойти именно reachable runtime/source paths, а не только текстовый `uses`. Проверить `caller-context`, `profile-policy`, `contract`, source admission, OIDC subject/claims, artifact download repository, App requester и final verifier.
- Проверить стабильные Gate/Ops IDs и отдельные identities owner/actor/App. Не менять исторический Delta repository или immutable review URL по глобальной замене.
- Разобрать owner-body mismatch R04 и mutable-header проблему. Предложить один неизменяемый исходный owner record с явным scope; доказать, что никакой receipt/новый digest не выдан за свежий grant.
- Проверить текущие instructions/WORK-PARENT registry: устаревшие namespace могут быть историческим адресом, а не разрешением cross-project mutation.

### S1.2 Реализация

- Обновить минимальный набор caller/profile/provenance bindings для текущего Gate/Ops. Preserve fixed target, migration ceiling, distinct requester/approver, default-off release, no AQUB и deny unknown.
- Разделить immutable authorization и mutable current status. Если без изменения старого contract нельзя — подготовить строго ограниченную версию и проверяемую migration совместимость readers; не добавлять fallback на любой issue/comment.
- Использовать существующие источники и PR, без нового parallel guard implementation. Supporting source не интегрировать в main только для получения управления guard migration.
- Gate client pin/lock обновлять лишь после independently accepted exact Ops source. Main bootstrap должен оставаться runtime-unchanged; проверить actual autoDeploy перед любым main merge, способным запустить runtime.
- Подготовить original source certificate flow O → C0 → H2 → P → binding. Указать reviewed code и actual release/merge отдельно; исключить self-referential SHA в собственном manifest.
- Если отсутствует agent-accessible transport — оформить точный existing-lane repair, не отправлять неподдерживаемую команду и не переходить в provider UI.

### S1.3 Проверки

- Exact current Gate owner/repository/workflow/attempt1 проходит pure source admission.
- Старый org owner, другой personal repo, AQUB, forged actor, подменённый App, rerun, чужой environment и workflow bytes отвергаются до credentials/provider.
- Неверный owner-record digest, отредактированная исходная запись, незнакомый target/source/policy и чужой artifact origin отвергаются.
- Положительный synthetic test не даёт real effect authority. Disabled fixture/mutation не включаются успешной source-проверкой.
- Новые code heads получают соответствующие original CI/Security и независимую source-проверку по правилам проекта. Старый ACCEPT не переносить.

**Выходы:** route inventory; минимальный source diff и tests; обновлённый client lock с проверенными raw digests; явные непрошедшие gates; receipt в #745 с exact source и следующим owner.

**Приёмка S1:** перенос перестал ломать source/caller admission на синтетическом текущем контексте; чужой контекст запрещён; доступный readonly route либо реально принят отдельным разрешённым наблюдением, либо его отсутствие точно указано. Не называть source-ready workflow рабочим live маршрутом без actual receipt.

## 9. S2 — доказать выполнимость стенда до allocation

**Цель:** выбрать минимальный осуществимый native fixture, который проверяет тот же Production algorithm и имеет ограниченные затраты и исполнимую очистку. Не наращивать preparation/cleanup код вокруг недоказуемого provider контракта.

**Владелец:** α Gate формирует решение, α Ops подтверждает provider capability. Марк решает только конкретное изменение ограничений/новые эффекты.

### S2.1 Пакеты исследования

| Пакет | Что установить | Какой факт нужен на выходе |
|---|---|---|
| CPU/RAM | Supported exact setter, getter shape, единицы, minimum/maximum, момент применения и действительность во всех deployments | Primary source + fixed tested mapping + fresh allowed effective observation; неизвестный scalar не парсить догадкой |
| Physical fleet | Region/replica/rollout/predeploy/helper/build/queue/restart inventory и отсутствие посторонних writers | Полный набор инстансов и процессов, происхождение каждого, bounded pagination и zero unexpected |
| Том | Initial capacity до первого PG start, MB/MiB mapping, parent vs instance, mount/region и replica restrictions | Подтверждённая начальная capacity; не used bytes, не после resize |
| Backup | Eligibility с реальным PG/WAL/usage, completed workflow, locked state, права на cleanup каждого backup | Exact supported schema/permission и проверяемые postconditions |
| Изоляция | Отдельный project/environment, только синтетическая БД, private network, отсутствие inherited secrets и внешних bindings | Whitelist configuration; никакой Production DSN/Telegram/LLM key или пользовательского дампа |
| Стоимость | Plan/rate/region, preparation и retention, storage/backup/egress/observer, действие budget controls | Полная модель с доказанной областью cap либо конкретное честное ограничение модели |
| Права | Кто и каким route создаёт parent scope, services, volume, dedicated credentials; least-scope authority | Нет workspace token или копирования Production key; неподдержанный onboarding — отдельный blocker |
| Cleanup | Supported stop/delete graph, soft-delete latency, точные конечные состояния, surviving observer после parent deletion | Конечная доказуемая reconciliation каждого исходного ID, а не исчезновение из списка |

Для каждого неизвестного записать: predicate, primary URL/immutable source, response shape и units, разрешённый способ наблюдения, ограничения, owner следующего шага. Отрицательный результат code search не доказывает отсутствия API. Документация не является observed account state.

### S2.2 Целевая минимальная топология

Рабочее предложение: один отдельно разрешённый disposable project/environment, один Gate service, один PG service с одной физической PG instance, один parent volume/instance, один регион. Hbootstrap/Hreset/Hproof используют один Gate service последовательно; дополнительный helper service не создавать.

Во время Gate overlap учитывать две physical replicas: предложение 500 milli-vCPU/512 MiB на каждую сохраняет aggregate Gate ≤1 vCPU/1024 MiB. PG proposal ≤1 vCPU/1024 MiB. Это проект ограничений, не сведения о применённых настройках. Отдельно ограничить все оплачиваемые build/predeploy процессы; per-replica limit сам по себе их не покрывает.

Initial volume proposal остаётся ≤1024 MiB до старта. Если платформа предоставляет только больший default и не поддерживает меньший initial size, **не создавать ресурс**. Подготовить владельцу конкретную альтернативу с новой capacity/риском/стоимостью или иной эквивалентной топологией. Смена на другой provider без сопоставимого native cutover не является выполнением прежнего критерия.

Рассмотреть переиспользование уже существующего disposable inventory только если он реально найден разрешённым наблюдением, изолирован, подтверждены его owner/границы и новый grant явно разрешает использование/очистку. Не искать ресурс по похожему имени и не переназначать Production. До такого доказательства этот путь не считать доступным.

### S2.3 Решения и ограничения

- v7/lifecycle остаётся PROPOSED. До его принятия нельзя делать вид, что DELETED-by-T120 заменён запросом удаления.
- Current Ops AGENTS запрещает добавление delete/restore operation. Запросить только точное source exception для согласованного закрытого cleanup graph, после подготовки конкретного решения. Не запрашивать общий доступ к удалению.
- Новый source graph должен использовать одни original allocation-wide counters/clock. Зафиксировать одну непротиворечивую таблицу budget: действующие 1800 provider reads и предложенные 256 не смешивать; 72 часа не продлевают счётчик. Для изменённого протокола лимит выбирается и принимается явно.
- Не выделять платный ресурс «на минуту, чтобы посмотреть» до cost/cleanup/permission proof. Workflow timeout не доказывает остановку PG и прекращение storage charges.
- Если supported terminal authority или изолированный cost boundary недоступны — вернуть конкретный feasibility BLOCKED и ограниченный запрос изменения условий. Не строить заведомо неисполняемый issuer/cleanup framework.

**Выходы:** feasibility matrix по восьми пакетам; один выбранный исполнимый дизайн; список остаточных UNKNOWN; точный проект physical grant с targets/actions/cost/expiry/cleanup; отдельно source exception при необходимости.

**Приёмка S2:** для каждого обязательного ограничения есть проверяемый поддерживаемый механизм либо явно принятое изменение требования. Нет фиктивного API setter, обещанного invoice cap, выдуманного account plan или скрытого cleanup риска. Предложение само не даёт physical grant.

## 10. S3 — закончить preparation, DB identity и cleanup

**Вход:** результат S2, согласованные source scope/lifecycle, совместимые source pins. Для runtime всё ещё нужны отдельные physical grants. Пока не принято delete-source exception, выполнять независимые допустимые части, не добавлять запрещённый код.

### S3.1 Минимальная цепочка preparation

Продолжить существующие O/C0/H2/P seams. Не добавлять новый сервис авторизации, general dispatcher, произвольный backend, shell или GraphQL. Новые records вводить только там, где существующий B не может выразить подготовку до известного physical digest.

1. Protected source producer проверяет exact reviewed sources/checks и неизменяемый preparation owner record. В initial scope известны разрешённый parent project/environment, creation specification и source/config digests; неизвестные будущие service/volume IDs не выдумывать.
2. До первого potentially paid dispatch независимо и durably ARM cleanup obligation. Allocation-wide origin — самый ранний authenticated ARM; позже выданный grant/B/restart его не сбрасывает.
3. До каждого создания/настройки durably резервировать единственный slot; фиксировать dispatch отдельно от подтверждённого результата. HTTP timeout/потерянный ответ потребляют slot и оставляют UNKNOWN. Blind retry запрещён.
4. Original attributed provider results дают exact Gate/PG/parent-volume/instance IDs. Caps применены до допуска запуска. Нет workspace scan или name-based adoption.
5. Запустить первый Hbootstrap ровно один раз; атрибутировать actual deployment/source/config/role. Physical DB identity собрать отдельным bounded read.
6. Только после original identity artifact окончательный B и native effect grant могут связать реальный digest. Final B не заменяет исходный preparation grant и не обновляет время/лимиты.
7. Native controller один раз принимает original Hbootstrap ID из доверенной allocation lineage. Не разворачивает его повторно и не открывает публичный adopt(ID).

### S3.2 Аутентифицированный DB identity collector

Изменяемые Gate seams: `gate745_fixture_identity.py`, существующий hosted helper/health route, dedicated admission и tests. Pure `candidate_observation()` сохранить как untrusted validation, не объявлять её DB чтением.

- Fixed POST `/admin/ops/gate745-fixture-identity`; GET metadata не открывает БД. Обычный Production/UNBOUND request отвергается до env/credential/DB.
- Отдельный preparation audience/JTI, exact reviewed C0 workflow identity, immutable repo/owner/actor IDs, run/attempt/source/allocation digest и абсолютная expiry. Нельзя принять обычный H2 operation token или caller-supplied JWKS как effect authority.
- Использовать тот же fixed GitHub RS256/JWKS boundary, redacted errors и source-authentication pattern; не изобретать второй identity provider.
- Durable one-use reservation потребляется **до DB connection**. Существующая PG H2 ledger, которой для открытия нужна уже известная identity, не решает этот bootstrap круг. Резервирование preparation выполняется в уже аутентифицированном original controller ledger, с source-проверяемым exactly-once handoff. Process-local set не является durable защитой.
- Только dedicated private DB binding из server-side защищённого scope. Нет caller DSN/SQL/host/path/row selector, обычного DATABASE_URL fallback или credential export.
- Один invocation, connection, readonly transaction, statement и row. Connect ≤5 s, statement ≤5 s, total ≤10 s, request/response ≤131072 bytes. Если меньший remaining grant lifetime — использовать его; expiry проверять перед и после каждого await/внешнего read.
- Fixed SQL уже определён: `current_database()`, `inet_server_addr()`, `inet_server_port()` и `pg_control_system().system_identifier`. Роль должна иметь отдельно доказанные минимальные права; не расширять до superuser автоматически.
- Validate database `gate745_synthetic`, exact port, canonical address, uint64 identity, target/volume/runtime binding; known Production IDs запрещены в каждом slot.
- Receipt содержит source/run/attempt/owner/allocation/grant/reservation lineage, exact physical digest, readonly fact, фактические счётчики и начало/конец. Не содержит DSN/password/JWT/query exception или строк БД.
- Response нельзя маркировать authenticated только полем `evidenceKind` из caller JSON. Original uploaded artifact и producer/DAG/provenance должны проверяться независимо.

### S3.3 Устойчивость preparation и H2

- Сохранить existing H2 `operation_ledger`: permanent/logged, фиксированные schema/columns/constraints, без RLS/triggers/extra objects. RESERVED коммитится до mode effect; UNKNOWN блокирует повтор после restart/reset/потери ответа.
- Allocation counters, identity-read counters и H2 invocation counters различаются, но имеют общую origin/deadline lineage. Identity read — отдельная preparation операция, не скрытый metadata GET и не бесплатная unknown-proof.
- Сохранить 119 canonical baseline migrations для CONTROL helper; guarded source содержит 120 включая 0116. Installed wheel/source/Docker manifest нельзя путать. Путь из cwd/env и symlink substitution запрещены.
- Bootstrap — только заведомо пустая выделенная synthetic DB; reset — только после доказанной quiescence и retained success/live proofs. Reset сохраняет operation ledger и literal unknown journal. Restore/wipe/Production dump запрещены.
- Согласовать actual first Hbootstrap adoption с семью конфигурационными фазами; не пропустить или дважды посчитать deployment/configuration transition.

### S3.4 Закрытый cleanup graph

Реализовывать только после точного source exception. В production graph delete/restore не добавлять. Точные paths/graph брать из согласованного v3/v7 successor; при изменении semantics обновить всех strict readers явно.

- IDs поступают только из исходной authenticated allocation/ledger. Никакого discovery-then-delete, force, unlock, restore, wipe или wildcard target.
- Proposed максимальный closed write graph v7: до 9 известных Gate deployment stops, 1 PG stop, 2 exact locked backup deletes, 1 parent-volume delete, 2 service deletes, 1 disposable-project delete. Это до 16 wire mutations, не текущий grant. Ambiguous dispatch потребляет slot, остаётся UNKNOWN и не повторяется.
- Не начинать новые cases после раннего срока/T+100 min; оставить минимум 20 min на write cleanup. При принятом v7 до раннего write expiry/max ARM+120 min должны быть остановлены все compute/writers/restarts/queues и аутентифицированы разрешённые deletion requests.
- Stop не равен cancel queued deployment. Если требуются дополнительные cancellation операции, текущий граф не запускается до отдельного конкретного изменения; не подменять provider cancellation GitHub run cancel.
- До удаления parent доказать, как observer сохранит право наблюдать каждый исходный child ID. Если API теряет эту authority, allocation запрещён.
- Отдельный readonly terminal producer при принятом v7 ограничен original read budget и ранним observation expiry/max ARM+72 h. Restart/другой job не обновляют deadline или counters. Исчерпание лимита — NOT_PROVEN/FAIL, не автоматическое продление.
- Receipt различает STOPPED, DELETE_REQUESTED/QUEUED, DELETED и NOT_PROVEN. 403/null/list omission/deletedAt/sleep/token loss не доказывают purge.
- Не модифицировать retrospective original artifacts. Supplemental terminal proof связывается с исходными run/attempt/raw digests/resources/ARM/dispatch/counters. Старый failed/cancelled native run не становится успешным только потому, что ресурсы позже удалены.

### S3.5 Обязательные тесты

- Wrong scope/source/repo owner/workflow/token audience/JTI/expiry/body bytes → отказ до credential access.
- Два отдельных процесса используют одну reservation: к DB collection допускается максимум один; replay после crash/response loss также запрещён.
- Expiry во время первого read запрещает второй read и успешный receipt; финальная freshness проверяется после ожиданий.
- Изменённый DSN/production tuple/лишние JSON поля/дубли keys/nonfinite numbers/oversize → bounded redacted refusal.
- PG17 integration доказывает actual readonly transaction, fixed query, identity digest, ledger durability/reset и сохранение unknown. Моки не заменяют этот пункт.
- Partial allocation/stop/backup deletion/parent soft-delete/observer 403/timeout проходят fault injection; ни один UNKNOWN не переотправляется.
- Cleanup в запрещённом target и после expiry не вызывает wire mutation. Metadata GET открывает ноль DB connections.
- Старые receipt readers отклоняют новую несовместимую lineage; новый verifier не принимает старую cleanup форму как supplemental terminal proof.

**Выходы:** минимальные совместимые adapters/schemas/workflows и тесты, frozen exact source; default-off; pending effect gates перечислены. **Приёмка S3:** исполнители способны показать полный synthetic flow без backend-bypass в shipped graph; обязательные реальные PG проверки пройдены в соответствующем контуре, а source барьеры не сняты догадкой.

## 11. S4 — целостный кандидат и независимая SOURCE-приёмка

**Цель:** получить один проверенный комплект источников перед native, сохранив уже реализованный guard.

### S4.1 Сборка кандидата

- Перечитать fresh main/head/tree и владельцев. Продолжить PR747. Если main изменился — интегрировать только после анализа каждого пересечения; не включать untracked файлы соседнего Work.
- Сверить canonical tree/blob/modes и ordered parents synthetic/actual merge. Доказать источник каждого изменённого файла; source, wheel CONTROL119 и guard120 различать.
- Согласовать O/C0/H2/P/code/release certificates без cyclic self hash. Каждый lock pin — полный SHA и raw byte digests.
- Проверить, что все поддерживаемые send paths входят в общий guard, а legacy/direct send не обходят его. Не включать dormant delivery рядом с этой задачей.
- Подготовить обновления MCP docs/operator bridge и сценарий S7 до выпуска; actual connected-client check выполнить после S6.

### S4.2 Проверки

| Слой | Что обязательно доказать |
|---|---|
| Pure/unit | Token scope, canonical equality, unsupported/malformed inputs, any-side event semantics, отсутствие авто-refresh |
| Actual PostgreSQL | Разные процессы/соединения, оба порядка race, non-existent version row, duplicate receipt, raw receipt до projection, competing Works, terminal replay/unknown |
| Transport | Plain/markdown/rich/document, captions/entities/hash binding, stale TXT/PDF/ZIP/XLSX/PNG, legacy route refusal |
| Sequence | Queue не освежает baseline; собственный confirmed send даёт только durable progression; accepted event/echo/unknown останавливает tail |
| Migration | Единственный intended 0116 head, fixed 0115→0116, helper deny send-enabled/active-attempts/unexpected schema, journal unchanged |
| Artifact | Installed wheel вне checkout, CONTROL119 inventory, Docker layout, missing/extra/symlink resource denial |
| Ops | Caller/provenance/target/TTL/budget/unknown/cleanup negatives, stable redaction и отсутствие callable unsafe bypass |

Для race использовать barriers/latches и наблюдение фактического PostgreSQL blocking, а не случайные sleeps. Повторять тесты только по изменению/дефекту/неразрешённой неопределённости. Локальный SKIP именовать SKIP; закрыть обязательные SQL случаи настоящим CI PostgreSQL.

Сначала локальные детерминированные проверки; затем один необходимый original exact-head CI на материальный кандидат, плюс обязательные Security/Quest/build проверки соответствующего проекта. Не rerun зелёные или pending runs. Doc-only изменение не требует заново изображать runtime acceptance.

### S4.3 Независимость

Независимый δ получает self-contained immutable packet, не исправляет исходники до verdict. После terminal ACCEPT/REJECT/BLOCKED изменённая повторная попытка — новая Delta issue с predecessor по действующим правилам. Это история независимой проверки, а не новый implementation issue; создание/маршрут согласовать с действующими ограничениями владельца, не выдавать подготовленную ссылку за принятую работу.

Packet: цель, exact source pairs/tree, changed paths, original CI evidence, контрольные digests, test matrix, non-claims, residuals, target/effect gates. Старый ACCEPT315 не переносится на новый head, даже если часть guard blobs сохранена.

**Выходы:** frozen candidate, exact проверочные квитанции, новый независимый SOURCE verdict, актуальный список unresolved physical predicates. **Приёмка S4:** значимые source/PG/compatibility проверки пройдены без waiver ключевых инвариантов; native, merge, Production и продуктовый ACCEPT по этому результату не объявляются.

## 12. S5 — реальный native fixture: success, recovery, cleanup

**Вход:** принятые source S4, исполнимый дизайн S2, отдельные exact preparation/native/cleanup/observer grants, защищённые dedicated bindings и durable ARM. При отсутствии одного пункта не создавать ресурс.

### S5.1 Подготовка запуска

- Fresh checked-in target/manifest/release/pins, one original run attempt, owner/source/run identity и complete permission map.
- Фактические resources только из разрешённой creation lineage; Production IDs/host/credentials и AQUB запрещены.
- Все TTL/byte/read/write/cost counters связаны с одним original allocation ledger. Не расширять их новым nonce, head, run, actor или переподписанием.
- Exact configuration/readback и effective execution proof: какой image/source/command/config исполнялся на конкретном deployment, включая restart policy/predeploy/health path/port/autoDeploy.
- При неизвестной phase/config/runtime identity — STOP до effects следующего шага.

### S5.2 Последовательность

1. Preparation по S3; literal synthetic-only DB на 0115 с одним заранее известным unknown attempt. Сохранить immutable setup/identity/allocation evidence.
2. Первый success case: baseline exact source → отключение delivery → доказанная остановка всей Gate fleet и отсутствие active attempts → completed+locked bound backup → guarded source с delivery=false → 0116 и runtime/schema/journal verification → тот же source с delivery=true → independent live proof.
3. Сохранить original success и early-live bytes до reset. Нельзя построить их позже по пересказу.
4. Quiescent reset через accepted H2, сохранив original unknown journal и control ledger. Рецептура synthetic; чужие строки/objects запрещены.
5. Controlled recovery case с одной заранее выбранной ошибкой после schema/cutover effect, на shared native core. Доказать остановку delivery/fleet, schema 0116 preserved и unknown journal unchanged. Ошибка не должна требовать restore/drop/schema downgrade.
6. Hproof proof-only после остановки обычного Gate; bootstrap/reset route там отсутствуют. Сохранить actual post-stop journal/fleet proof, затем остановить Hproof.
7. Выполнить разрешённый cleanup graph. Early stop/request receipt и terminal reconciliation различать по принятому lifecycle. Финальный native verdict выдаётся только после всех обязательных cleanup proofs.

Тест не посылает реальные Telegram сообщения и не делает model/STT вызовы. Отключённый send flag не заменяет физическую изоляцию Business/LLM credentials.

### S5.3 Evidence и аварийные ветви

Обязательны success, controlled-recovery, live и cleanup как логические классы доказательств; original same-run immutable bytes и supplemental lineage соответствуют принятой версии протокола. Дополнительно сохраняются operation ledger/configuration/source identity. Producer и credential-free verifier разделены; protected producer не может сам выписать VERIFIED.

Неуспешный, cancelled или timed-out original run остаётся неуспешным. Cleanup obligation продолжает действовать своим механизмом; timeout job не является cleanup. Не обещать background observer, если durable delivery/clock/expiry механизм реально не установлен. Повтор scenario после terminal verdict получает новую независимую attempt и только остаточные разрешённые counters; grants автоматически не возобновляются.

**Приёмка S5:** native независимый ACCEPT для точного source/target/run, фактический success и recovery, сохранённый unknown, доказанная cleanup lineage. Позднее удаление ресурсов само по себе не исправляет отсутствующий early-live или failed case.

## 13. S6 — безопасный Production cutover

**Вход:** native ACCEPT; fresh owner-only facts/действующий scope; accepted guarded source; actual autoDeploy=false; полный fleet/DB/backup plan; отдельное точное owner approval digest перед apply. Ordinary #751 deploy не разрешает этот cutover.

### S6.1 До merge и plan

- Освежить actual Production baseline full SHA/deployment/schema, текущий guard SHA, все процессы/реплики/rollout overlap, DB/private-network/volume binding и source/config fingerprints.
- Registry из семи прежних service IDs — список кандидатов для проверки, не доказательство текущих семи ролей. Установить все реальные ingress/send/import/canonical/background writers; unknown resource blocks.
- Проверить provider autoDeploy именно для нужной source binding и всех затрагиваемых service roles. `railway.toml`, push status или health не доказывают false.
- До merge guard должен отсутствовать риск автоматического неподготовленного migration/deploy. Main bootstrap не использовать для обхода этого условия.
- Слить только принятый exact head разрешённым маршрутом, проверить actual merge tree и обязательные checks. Если merge tree отличается или main успел измениться — source drift, новый анализ/проверки.
- Связать release с actual merged SHA, свежим baseline, reviewed allocation policy и native proof. Не разворачивать исторические candidate303a/baseline8e6 из disabled release.json.
- Сформировать immutable plan: exact target/source/state fingerprint, backup, действия/порядок, TTL, stop/recovery и ожидаемые postconditions. Отдельный authenticated owner утверждает его digest; App не одобряет собственный apply.

### S6.2 Apply

1. Отключить delivery установленным fixed flag route. Зафиксировать исходные состояния и не считать flag=false завершённым drain.
2. Остановить все старые ingress/send writers и prove stopped; проверить queued/building/waiting/deploying/restarting instances, не только active list. Учитывать in-flight request outcomes отдельно.
3. По readonly DB metadata доказать reserved=0 и sending=0. Unknown не обнулять, не удалять и не переотправлять ради зелёного статуса.
4. Получить completed+locked backup именно bound DB volume, с workflow/ID/свежим доказательством. Backup request не равен completed backup.
5. Выпустить reviewed guard source с delivery=false. Фиксированный predeploy допускает только intended 0116 и безопасные schema/attempt conditions. Не выполнять произвольный `upgrade head` к будущим миграциям.
6. Проверить exact deployed source, effective command, 0116, protected health, отсутствие старых writers и сохранение unknown journal. Неполный remote fleet proof не заменяется app endpoint.
7. Включить delivery тем же guarded SHA; проверить, что не появился другой source/старый процесс. Independent live observer и credential-free terminal verifier подтверждают факты.
8. Измерить downtime/ingress recovery и зафиксировать известные принятые updates, queue/backlog и непроверенные интервалы. Не заявлять отсутствие пропущенных внешних updates без доказательства.

### S6.3 Остановка и recovery

- Любая неожиданная schema/source/DB/fleet/config, failed health, expired plan, duplicate delivery или unknown external operation останавливает следующий effect.
- Предпочтение — forward-safe состояние: guarded schema сохранена, delivery=false, известные writers остановлены, журнал не повреждён.
- Не включать старый ingress поверх новых active guarded writers. Не downgrade/drop 0116 ради binary rollback.
- LKG SHA — исходная точка плана, не доказательство совместимости со schema0116. Binary rollback допустим только при отдельно доказанной compatibility и принятом exact route.
- Restore/delete Production DB не входят в эту задачу. Потеря ответа apply не разрешает повторить deployment/migration/backup вслепую.

**Выходы:** exact deployed revision, schema0116, complete fleet/flag/DB/backup evidence, independent runtime verdict и recovery receipt при необходимости. **Приёмка S6:** защита физически работает во всех относящихся процессах; клиентская и пользовательская приёмка ещё отдельно.

## 14. S7 — подключённый MCP и пользовательская приёмка

### S7.1 Каталог

- Проверить реальные `tools/list` серверного endpoint и доступные инструменты нового Work, а не только checked-in JSON schema.
- `gate_get_reply_context` возвращает usable snapshot/context_ready/dialog_events в нужном scope.
- `gate_save_reply_draft` и `gate_prepare_reply_document` реально требуют `expected_context_snapshot`; approved send читает immutable saved value, без override.
- Старыми draft/client нельзя обойти guard. Пользователь видит способ перечитать контекст и пересоздать ответ.
- Проверить operator upload bridge, formatting hashes/entities, captions и поддержанные MIME #700. Не подменять реальную Office/image отправку проверкой одного только schema enum.

### S7.2 Подготовка bounded canary

До запроса разрешения подготовить один конкретный Mark-controlled Business dialog: exact installation/account/recipient/task, подтверждённый different recipient account, синтетические тексты и файлы с hashes, срок и enforceable budget. Не подставлять контакт из истории или Alpha group вместо Business dialog. Третьи лица не участвуют без отдельного согласования.

Проект бюджета, **не grant**: окно ≤15 минут; Gate delivery dispatch ≤6 суммарно; accepted deliveries ≤6; model/STT calls=0; прочие provider probes отдельно фиксированы и ограничены сценарием до подтверждения. Read-only MCP calls ≤30. Любой unknown outcome, третий участник, превышение бюджета, source drift или неверный адресат — STOP. Одного названия «owner-only» для этих фактов недостаточно.

Оптимизированная схема: один synthetic inbound event может инвалидировать три заранее подготовленных drafts (text/XLSX/PNG) на одном baseline; затем отдельный synthetic owner outbound проверяет те же три stale пути. У всех шести delivery dispatch=0. Для положительных send каждый раз читать новый snapshot: предыдущая собственная отправка меняет revision. Final exact texts/refs и количество probes фиксируются в конкретном canary packet, не выдумываются этим ТЗ.

Voice/edit/delete/редкие race и scope-negative сценарии доказывать synthetic PostgreSQL matrix; не запускать реальную транскрипцию ради теста инвалидации. Если A12 требует дополнительного live эффекта — сначала подготовить ровно его, показать владельцу и учесть в лимите.

### S7.3 Сценарии live

1. Неизменный диалог: read → draft → точное approval → send → receipt → replay; одно delivery.
2. Входящее принято до admission: старый ответ отказан; доступен новый context; delivery=0.
3. Исходящее Марка/другого разрешённого Work принято до admission: тот же отказ, без auto-refresh.
4. Новый цикл после отказа: read → новый content/approval → send при неизменном состоянии.
5. Оригинальные XLSX и PNG в document flow под тем же guard; approved bytes/caption/hash не меняются.
6. Queue/sequence: confirmed первый элемент, accepted intervening event, tail stopped; отсутствие второго delivery доказано receipt/counters. Если это не помещается в выбранный live budget — подготовить отдельный exact canary, не увеличивать cap молча.

### S7.4 Итог

Независимая техническая проверка и отдельное решение Марка по наблюдаемому пользовательскому результату. В #745 дать по каждому A01–A12 результат/evidence/ограничение. Issue закрывать только при выполнении всех критериев, а не после проверки каталога или первого send.

## 15. Трассировка A01–A12

| Критерий | Обязательные проверки | Этап доказательства |
|---|---|---|
| A01 неизменный диалог | Успешный одиночный send, receipt, terminal replay после subsequent event | S4 actual PG; S7 live |
| A02 входящее | Raw durable receipt без готовой projection; fresh context; stale delivery=0 | S4 actual PG; S7 live |
| A03 исходящее | Owner manual и другой Work; конкуренция baseline | S4 actual PG; S7 разрешённый live |
| A04 без смыслового фильтра | «угу», одинаковый текст, voice без transcript, file/photo, supported edit/delete | S4 pure + actual PG; не требуется платная реальная STT |
| A05 race | До reserve; после reserve/до admission; commit против финального row lock; HTTP уже начат | S4 отдельные PG соединения; S5 native boundary |
| A06 scope | Другой account/chat не блокирует; чужой token/hidden project/неполный context не даёт send | S4 PG/MCP auth tests |
| A07 без обхода | Missing snapshot, legacy draft/client/routes/file/media, queue recovery/retry | S4 negative surface; S7 actual old-client refusal |
| A08 видимый отказ | context_changed, sent=false, раздельные delivery/probe counters, scoped current_context | S4 contract; S7 live |
| A09 новый цикл | Новое чтение/content/approval после stale; baseline не освежается за Work | S4 tests; S7 live |
| A10 конкуренция/повторы | Два Work, sent replay, failed replay, transport/receipt unknown, restart и durable ledger | S4 PG; S5 fault recovery; bounded S7 replay |
| A11 #700 | PDF/ZIP/XLSX/PNG и остальные поддержанные документы, caption/hash/formatting integrity | S4 format/transport/PG; S7 XLSX/PNG |
| A12 доказательства | Source/CI/Delta, real native/fleet/schema/backup, connected MCP, exact authorized product check | S4–S7; owner acceptance |

Ни одна строка не отмечена выполненной только наличием этого ТЗ. Исторические evidence указываются с exact revision; новые версии проверяются заново в относящемся объёме.

## 16. Матрица прав и решений

| Действие | Текущая граница | Что должно появиться до эффекта |
|---|---|---|
| Читать текущие source/docs, проектировать, синтетические локальные проверки | Разрешено текущим поручением | Соблюсти privacy и scope |
| Обычные default-off source prerequisites | Разрешены в принятой задаче | Lease, минимальный diff, соответствующие checks/review |
| Добавить delete/restore operation | Current Ops запрет; v7 не принят | Точное source exception; restore Production не предлагается |
| Прочитать provider/account metadata | Только разрешённый pinned Ops route | Exact bounded readonly grant/admission; отсутствие его не обходить UI/API |
| Создать/оплатить/настроить fixture resources | Не разрешено этим ТЗ | Исполнимое S2 решение и отдельный preparation grant |
| Provision credentials/identity/OAuth | Не разрешено | Exact operator authorization; никогда значения в chat/Git |
| Native run/reset/cleanup/observer | Не разрешено описанием сценария | Exact accepted source/target/grants/ARM/limits |
| Merge/release/migration Production | Не разрешено коротким ordinary deploy | Действующий owner route и exact S6 gates |
| Реальные сообщения/пользовательская проверка | Не разрешено текстом canary proposal | Exact refs/content/hashes/expiry/caps и согласие владельца |

Запрашивать решения последовательно, когда конкретное действие готово к рассмотрению. Не просить общий «делай» вместо точного missing gate. Уже действующее согласие не переспрашивать. Отсутствие одного physical grant не блокирует независимую source/документальную работу.

## 17. Реестр открытых решений

| ID | Решение | Кто готовит / кто решает | Когда необходимо |
|---|---|---|---|
| D01 | Канонический immutable owner authorization record при переносе и mismatch body digest | Gate/Ops source owners; owner подтверждает только изменение authority | S1 до operational admission |
| D02 | Supported initial volume ≤1024 MiB либо конкретный новый capacity/cost вариант | Ops исследует, Gate предлагает, Марк решает изменение требования | S2 до allocation |
| D03 | Resource-limit shape/units/effective readback и coverage overlap/predeploy | Ops + source review | S2 до enable preparation |
| D04 | SOURCE exception cleanup и новый lifecycle v7 либо конкретный replacement | Gate/Ops готовят; Марк утверждает точный текст | До delete-code и изменения strict cleanup contract |
| D05 | Terminal exact-ID proof после parent deletion и retention cost boundary | Ops подтверждает feasibility; Марк решает только оставшуюся конкретную альтернативу | S2 до paid dispatch |
| D06 | Exact preparation/native/cleanup/observer effect grants | Gate/Ops готовят полный packet; Марк утверждает | До S5 |
| D07 | Fresh Production plan digest/source/fleet/backup/recovery | Ops route + Gate; отдельный authenticated approver | До S6 apply |
| D08 | Exact Mark-controlled canary refs/texts/files/probe/send caps | Gate готовит; Марк утверждает | До S7 live |

Срок ответа или молчание не означают согласия. D04 не блокирует S1 и исследование S2. D02/D03/D05 должны быть решены до создания ресурса; одобрение D04 само по себе не делает S5 запускаемым.

## 18. Правила завершения спринта и передачи

На каждый материальный выход записывать в существующую #745: выполненное, exact source/tree, changed paths, фактические проверки и SKIP, принятую независимую попытку, актуальный blocker, lease state и одного следующего owner. Не писать heartbeat-only commits/comments и не создавать per-project расписание.

Передача Ops — в существующую dependency issue с адресатом/контекстом/границами/материалами/доказательствами/критерием. Issue publication, доставка и принятие различаются. Не ждать, что Mark вручную выполнит доступный агенту routine technical шаг; не заявлять принятие чужим Work без его квитанции.

При внешнем STOP закрыть только завершённый bounded этап, освободить compute/source lease и дать конкретное необходимое действие. Всю #745 не закрывать. Background execution/observer обещать только при реально установленном механизме с наблюдаемым состоянием.

**Первый исполнимый следующий результат:** S1 — полный current route/authorization inventory и минимальный default-off repair packet, включая owner-body mismatch; техническое исследование S2 выполнять независимо. Для начала этих работ новое общее разрешение Марка не требуется.

## 19. Контроль качества самого ТЗ

- Найденные исторические планы сохранены как источники; новые факты отделены от предложений и полномочий.
- У каждого S1–S7 есть цель, вход, работа, проверки, выход и критерий приёмки; зависимости и owner gates явны.
- A01–A12 связаны с конкретными source/PG/native/client/live проверками.
- Нет новой product issue/branch/PR, автоматического переноса ACCEPT, обещания deploy или real-resource grant.
- Snapshot guard, helper source, infrastructure readiness, runtime и пользовательский результат не смешаны в один статус.
- Private переписка, credentials, provider tokens, DSN и пользовательские файлы не включены.
- Изученные provider docs объясняют дизайн, но не выданы за фактические account permissions, invoice cap, terminal deletion или effective runtime state.

## 20. Карта исходников и точек изменения

Таблица задаёт рабочие seams, а не разрешает изменение всех файлов сразу. Сначала доказать необходимость diff. Gate core переиспользовать, если новый тест не нашёл дефект. Пути C0/P уже существуют в отдельных supporting PR и могут отсутствовать в main/#747; их наличие в таблице не означает installation.

| Область | Конкретные файлы / каталоги | Работа |
|---|---|---|
| Guard state и ingress | `app/application/dialog_snapshots.py`, `app/infrastructure/dialog_snapshots.py`, `app/infrastructure/telegram_processing.py`, `alembic/versions/0116_reply_dialog_versions.py` | Сохранить алгоритм; проверить инварианты и все writers |
| Work/MCP contract | `app/application/mcp_inbox.py`, `app/application/reply_drafts.py`, `app/application/reply_documents.py`, `app/api/mcp_routes.py`, `app/api/mcp_schemas.py` | Полнота context, immutable baseline, redacted refusal и каталог |
| Send/sequence | `app/infrastructure/reply_sends.py`, `app/infrastructure/reply_sequences.py`, `app/worker/reply_sequences.py`, `scripts/gate_document_upload.py` | Общая admission граница, неизвестные исходы, queue и MIME regression |
| Fixed migration/runtime proof | `app/infrastructure/reply_cutover.py`, `app/worker/reply_cutover.py`, `app/api/health_routes.py`, `railway.toml` | Readonly counts, fixed 0116 gate; не считать app proof доказательством remote fleet |
| Gate cutover client | `.github/workflows/gate-snapshot-cutover.yml`, `agent-ops.gate-snapshot.lock.json` | Current caller, artifact repository, approved Ops source и точные raw digests |
| C0 в существующем PR760 | `.github/workflows/gate745-fixture-controller.yml`; `scripts/gate745/fixture-controller.mjs`, `fixture-artifacts.mjs`, `fixture-helper-transport.mjs`, `fixture-hosted.mjs`, `fixture-oidc.mjs`, `fixture-ops-source.mjs`, `fixture-reservation.mjs`, `fixture-source.lock.json`, `fixture-test-vector.json` | Совместимый source/owner/artifact/identity/preparation handoff; original reservation и run lineage |
| P в существующем PR762 | `.github/workflows/gate745-native-fixture.yml`, `scripts/gate745/fixture-caller.mjs`, `fixture-caller.test.mjs` | Старые owner/repository conditions и безусловный UNBOUND preflight; не включать job простым снятием `if: false` |
| H2/identity | `app/worker/gate745_fixture_identity.py`, `gate745_fixture_readiness.py`, `gate745_fixture_hosted.py`, `gate745_fixture_ledger.py`, `gate745_fixture_policy.py`, `gate745_fixture_controller_pin.py` | Distinct authenticated preparation read, durability, role/runtime/expiry и exact source |
| Ops cutover | `agent-ops/actions/gate-snapshot-cutover/{caller-context.mjs,profile.json,profile-policy.mjs,contract.mjs,action-runner.mjs,artifact-verifier.mjs,native-cutover-core.mjs,release.json}` | Current identity/owner binding, source compatibility, release gates и независимое verification |
| Ops native fixture | `agent-ops/actions/gate745-fixture-controller/{admission.mjs,source-admission.mjs,artifact-loader.mjs,configuration.mjs,native.mjs,controller.mjs,helper-transport.mjs,oidc.mjs,policy.mjs,policy.json,ledger.mjs,verifier.mjs}` | Реальные supported limits/config, congruent preparation/counters/lifecycle и отсутствие bypass |
| Cleanup/preparation additions | Только literal paths согласованного successor v3/v7 в owning repositories | Перед добавлением проверить source exception, отсутствие уже существующего adapter и exact finite graph |

### 20.1 Существующие тесты, которые нужно сохранить

`tests/test_dialog_snapshots.py` и `tests/test_dialog_snapshots_postgres.py` уже содержат:

- `test_unchanged_snapshot_sends_once_and_replays_after_dialog_changes`;
- `test_raw_receipt_stops_stale_draft_without_waiting_for_projection`;
- `test_stale_original_office_or_image_document_never_dispatches`;
- `test_raw_event_during_sequence_http_stops_the_unsent_tail`;
- `test_refusal_does_not_grant_context_read_permission`;
- `test_other_project_change_invalidates_without_exposing_its_content`;
- `test_history_without_connection_is_readable_but_never_authorizes_delivery`;
- `test_edit_or_delete_after_review_also_refuses`;
- `test_duplicate_receipt_is_stable_and_other_chat_does_not_block`;
- `test_save_requires_the_snapshot_actually_reviewed_by_work`;
- `test_change_during_connection_probe_is_rechecked_before_reservation`;
- `test_change_after_reservation_before_dispatch_is_failed_without_http`;
- `test_mcp_refusal_returns_new_text_direction_and_current_snapshot`;
- `test_legacy_draft_has_no_automatic_snapshot_backfill`;
- `test_two_works_with_one_review_cannot_both_send`;
- `test_ingress_commit_racing_dispatch_serializes_on_the_actual_version_row`.

`tests/test_reply_cutover_postgres.py::test_actual_cutover_aborts_active_attempts_and_preserves_unknown_journal` проверяет реальный переход в отдельной тестовой БД. Дополнительно сохранять H2/bootstrap/identity/readiness, reply-send/sequence recovery, Office/image и formatting suites. Число tests не критерий само по себе: важны выполненные сценарии и среда. Перед изменением названий сверить exact candidate; непрошедший обязательный PG test не заменять unit аналогом.

## 21. Нефункциональные условия и аварийная матрица

| ID | Условие | Проверка |
|---|---|---|
| NF01 | Scope/privacy сохраняются на success и refusal | Чужие payload не выходят через current_context, audit, exception или artifact |
| NF02 | DB locks не переживают HTTP и не превращаются в глобальный стоп | Разные dialogs выполняются независимо; порядок получения нескольких revision locks детерминирован; lock timeout/DB failure дают отказ без delivery |
| NF03 | История не обрезается ради ускорения snapshot | На synthetic размерах 10², 10³ и 10⁴ сообщений измерить query count, rows scanned, latency/lock wait и память; определить допустимый бюджет до S6. Если нужен performance fix — отдельно доказать сохранение полноты fingerprint; не делать last-message-only shortcut |
| NF04 | Перезапуск не обновляет approval, counters, deadlines или UNKNOWN | Cross-process/restart tests на durable store; состояние RESERVED/UNKNOWN не обнуляется |
| NF05 | Все reads/writes ограничены сроком, размером и счётчиком | Каждый await/transport проверен перед и после; timeout не выдаётся за отменённый side effect |
| NF06 | Наблюдаемость достаточна без переписки | Redacted code/stage/revision/run/hash/счётчики позволяют отличить before-delivery refusal, sent, failed и unknown |

Размеры NF03 — проект synthetic benchmark, не permission на чтение пользовательского корпуса и не обещание текущей производительности. Оптимизацию делать только по обнаруженному препятствию выпуску; принятие этой задачи не включает бесконечную перепись snapshot hashing.

| Отказ | Что остановить | Что сохранить | Что разрешено дальше |
|---|---|---|---|
| Caller/owner/source/digest mismatch | До credential/provider доступа | Redacted error и exact source | Исправить source в том же исполнении, новые checks/review |
| Неизвестные limits/config/cost/observer rights | До allocation/следующей mutation | Primary/read-only evidence с NOT_PROVEN | Исследование или конкретное изменение дизайна |
| Lost allocation response | Следующее создание/повтор той же операции | Consumed slot, UNKNOWN, original request digest/dispatch time | Только разрешённая reconciliation, без новой allocation по похожему имени |
| Identity read timeout/replay | DB collection/следующий helper step | Durable consumed reservation и точный error | Reconciliation по принятому протоколу; не автоматический retry |
| Native success отсутствует или recovery не сохранил journal | ACCEPT и Production promotion | Original failed artifacts и cleanup obligation | Остановить/очистить по имеющемуся grant; новая независимая попытка отдельно |
| DELETE_REQUESTED, purge не доказан | Native ACCEPT | Original exact resource IDs и pending state | Только разрешённое bounded readonly наблюдение |
| Production reserved/sending >0 | Migration/enable delivery | Attempts и их честные исходы | Разрешённый drain/recovery; не удалять journal |
| Source/schema/fleet drift после plan | Apply/следующий effect | Plan и наблюдение drift | Новый exact plan после reconciliation; старый digest не переиспользовать |
| Provider send outcome unknown | Повтор отправки и sequence tail | Original attempt и reconciliation evidence | Вернуть unknown; не подменять новым draft для обхода |
| Connected client без новых полей | Product ACCEPT и legacy send | Server/client schema observations | Обновить каталог и повторить относящуюся проверку |



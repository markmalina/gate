# Независимая проверка маршрута Watch0119

Дата: 2026-11-02. Заключение: **BLOCKED_ROUTE / SOURCE_ONLY**.

Проверка выполнена отдельным агентом чтением точных GitHub source objects. Запросов к Railway и runtime, remote mutations, запусков workflow/CI/deploy не было. Принятые проверки неизменённых исходников не повторялись.

## Точные версии и предел заключения

- Gate candidate: `6fc78006c5b209dc93ca2904ae349d6401eb922f`.
- Ops source: `ec4e689a0fa24305672ef4047ff7c5fac0666982`.
- Принятый архив предыдущего Work: `82260be000abeda8176bacda944fad7748e365f5`.
- Реальный Gate short-deploy caller закрепляет Ops `512decbbd81d1d824563af995fc0df435fbf2429`.
- Реальный Gate snapshot caller закрепляет Ops `b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`, а не автоматически текущий Ops main.

Компоненты backup и deploy существуют. Но текущий snapshot profile, core, caller и verifier относятся к Gate745/reply0116; применимый исполняемый Watch0119 contract отсутствует. Это не утверждение об отсутствии внешних резервных копий или других нерассмотренных механизмов.

## Реальные операции и доступы

| Этап | Существующий механизм | Необходимые доступы/операции | Предел применимости |
|---|---|---|---|
| Target и DB binding | `makeRailway().authenticate()`, `verifyDatabaseBinding()`, `verifyVolume()` в snapshot adapter | Railway project credential; project/environment/service queries; `variablesForServiceDeployment` для Gate и PostgreSQL; private-network endpoint и volume reads | Это source primitive. Проверка DSN/network/volume не является выполненной SQL-проверкой физической БД. Чтение variables возвращает все значения и требует явно разрешённой защищённой границы памяти |
| Ограждение процессов | `setSendEnabled(false)`, `stop(prior.id)`, `waitStopped`, `assertFrozen` | `variableUpsert(skipDeploys:true)`, `deploymentStop`, fleet reads | Ограждает reply sending. Не доказывает Watch Actions/persistent drain и отсутствие незавершённых claims |
| Backup | `createLockedBackup(name)` | `volumeInstanceBackupCreate` → `workflowStatus` → `volumeInstanceBackupList` → `volumeInstanceBackupLock` → повторный list | Один create и один lock фиксированного volume; текущая authority Gate745/reply0116 и release выключен |
| Migration/deploy | Gate `railway.toml`: `alembic upgrade head`; short `fastDeploy()` и snapshot `deploy(sha)` | GitHub contents:read для current main; Railway `serviceInstanceDeployV2` | Short deploy не связывает выпуск с backup, DB identity, Watch fence и схемой |
| Deployment version | `makeRailway().wait(serviceId,deploymentId,sha)` | Railway serviceInstance/latestDeployment/activeDeployments reads | Проверяет deployment ID, metadata SHA, SUCCESS и not-stopped; этого недостаточно для loaded schema/flags |
| Watch runtime | Candidate GET `/admin/ops/watch-voice`, `runtime_preflight()` | Существующий admin bearer; authenticated read-only GET | Доступен в source candidate. Его наличие в старой production не доказано; исторический runbook прямо отмечает отсутствие endpoint |
| Аварийный возврат | Snapshot catch path: flagfalse, stop известных candidate IDs, fleet verification | Узкие config/stop mutations | Результат `DELIVERY_STOPPED_SCHEMA_PRESERVED`/`BASELINE_STOPPED`, а не восстановление старого приложения |
| Restore backup | В проверенном production profile `allowRestore:false`; restore в adapter отсутствует | Потребуется отдельно проверенный операторский путь и разрешение | Автоматический restore не подготовлен и не обещается |

### Как проверяется backup

`createLockedBackup()` в `gate-snapshot-cutover.mjs:302–320`:

1. Проверяет volume и одноразовый latch `backupCreated`.
2. Выполняет ровно одно `volumeInstanceBackupCreate` с именем `agent-ops-gate745-${runId}`.
3. Ждёт `workflowStatus.status === 'Complete'` и `error === null`.
4. Находит единственный backup с точным именем.
5. Выполняет один `volumeInstanceBackupLock`.
6. Перечитывает список и требует exact ID/name с `expiresAt === null`.

Создание workflow не считается готовым backup. Этот source-код в настоящей проверке не исполнялся.

### Как проверяется DB binding

`verifyDatabaseBinding()` в `gate-snapshot-cutover.mjs:242–271` читает deployment variables Gate и PostgreSQL, в памяти сравнивает DATABASE_URL protocol/host/port/database/user/password, требует порт5432, затем связывает DSN с единственным ACTIVE private-network endpoint заданного PostgreSQL service. `verifyVolume():284–294` требует единственный READY volume этого service, без deleted/pending-deletion состояния.

Значения секретов не читались этой проверкой. Будущий вызов variables требует отдельного явного разрешения на обработку ALL variable values в защищённой памяти; ограниченный обычный read-only диагностический допуск не должен трактоваться как такое разрешение автоматически.

## Почему текущий snapshot нельзя запустить для Watch

Текущий `profile.json` задаёт `scope: Gate745-only`, migration `0116_reply_dialog_versions`, `allowSql:false`, `allowShell:false`, `allowRestore:false`. `release.json` содержит `enabled:false`, `fixtureAcceptance:PENDING`, `independentSourceReview:PENDING`, `reviewedExecutorSourceSha:null`, `allocationPolicy:null` и candidate `303a7aca19513070235a943bd5c798e9f4bf1b84`.

`native-cutover-core.mjs:28–34` жёстко требует runtime schema `0116_reply_dialog_versions` и `guard_contract:dialog-v1`. `makeFixedHealth()` в provider adapter закреплён на `/admin/ops/reply-cutover`, а не Watch endpoint. Core выполняет два deploy одного SHA с изменением `GATE_SEND_ENABLED` между ними. Простая замена SHA или имени миграции не адаптирует Watch fencing, evidence schema, verifier и recovery.

Gate workflow дополнительно закреплён на другом точном Ops SHA и issue745; он не получает новый capability от изменения Ops main. Исторические caller names/authorization не должны переноситься в Watch автоматически.

## Watch source facts

В проверенных migration files точная цепочка:

`0115_watch_voice_post_payment` → `0116_watch_voice_observability` → `0117_watch_voice_early_inbox` → `0118_watch_voice_work_results` → `0119_watch_voice_comment_ids`.

Upgrade добавляет таблицы, поля и constraints; текущие оригиналы и unidentified receipts не переинтерпретируются. Reply revision `0116_reply_dialog_versions` — другой контракт, он не заменяет Watch0116.

`railway.toml` candidate задаёт `preDeployCommand = "alembic upgrade head"`. Production start command default/unset mode `actions` включает Actions и выключает persistent; mode `disabled` выключает оба. Это source configuration, не свежая проверка effective runtime settings.

`runtime_preflight()` возвращает actual source/target IDs, loaded driver flags, actual Alembic heads и journal observation. Он намеренно сохраняет `effective_start_command:null`, `effective_predeploy_command:null`, `predeploy_execution_proven:false`. HTTP200/OBSERVED не заменяет доказательство predeploy или готовность выпуска. Отсутствие этого endpoint на старой production не разрешает bootstrap deploy.

## Подготовленный маршрут и точные оставшиеся изменения

Это план применения существующих компонентов после узкой адаптации, не выполненный выпуск и не готовый к запуску production contract.

1. Подготовить отдельный default-disabled Watch0119 contract на существующих snapshot provider/plan/apply/verifier primitives. Сохранить чужие Gate745 grants и профили. Закрепить exact source, исходную/целевую схему и target.
2. До любого применения установить scope credential, actual target, pending clean, autoDeploy=false, текущий baseline/SHA/schema/flags. Проверить DB/network/volume binding. Source IDs ниже не являются актуальным наблюдением.
3. Определить Watch-specific fence/drain для Actions/persistent и claims. Reply `GATE_SEND_ENABLED` не считать таким fence. Запланировать сохранение Actions mode; persistent не включать без отдельной готовности/разрешения. Unknown claims сохранять, не перезапускать автоматически.
4. После подтверждённого ограждения создать один backup, дождаться Complete, lock и readback. Зафиксировать backup ID и неизменяемую квитанцию.
5. Повторно сверить source/target/DB/fleet и выполнить один явно разрешённый exact Watch candidate deploy с native predeploy, переходящим только по принятому графу до0119. Не использовать короткий deploy как обход missing backup/fence gates.
6. Проверить provider deployment ID/SHA/status и actual runtime source/target/schema0119/Actions flags/persistent false. Подтвердить сохранность cursor/watermark/originals/journals и отсутствие повторных uncertain effects. Watch verifier должен проверять Watch evidence, а не reply guard contract.
7. Подготовить целевые tests изменённого adapter/contract/verifier, populated migration/backup/restore fixtures и независимое заключение. Принятые неизменённые source/CI проверки не повторять. До этого release остаётся disabled.
8. Для отдельного production-решения предоставить exact final merged candidate, актуальный baseline, operations ceiling, окно и recovery. Текущие20минут normal/28минут recovery принадлежат reply core; их достаточность для Watch не проверена.

Фиксированные source bindings: project `972b6ab7-988a-421c-abfa-425451ad714f`, environment `a148b138-44b3-4349-9d4b-292880503055`, Gate service `099cbf8c-1d94-43fe-8714-90725bf93998`, PostgreSQL service `561d3da3-ddb5-4d3d-8fa2-c9454b0996bf`, volume `7d2de9ed-c63e-454a-811e-4f206f3dfef3`.

### Возврат без потери истории

При неизвестном результате mutation — остановка и reconciliation, без слепого второго deploy/restore. При подтверждённом unhealthy candidate — заранее разрешённая безопасная остановка известных deployments/driver, сохранение схемы0119 и всех journals. Дальше совместимый fix-forward или заранее проверенный rollback SHA.

Исторический LKG `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` наблюдался на0115; его работоспособность на0119 не проверена этой работой. Его нельзя обещать как готовый возврат. `alembic downgrade`, удаление журналов, rearm unknown effects и автоматическое восстановление старого backup поверх новых effects не допускаются. В существующем profile отсутствует restore authority и executor.

## Источники

- [Принятое предыдущее приложение](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/S1-ROUTE-GAP.md).
- [Snapshot profile](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-snapshot-cutover/profile.json).
- [Snapshot release](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-snapshot-cutover/release.json).
- [Provider operations, DB binding, backup, health](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-snapshot-cutover/gate-snapshot-cutover.mjs).
- [Snapshot state machine и recovery](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-snapshot-cutover/native-cutover-core.mjs).
- [Gate short-deploy caller](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/.github/workflows/ops-deploy.yml).
- [Gate snapshot caller](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/.github/workflows/gate-snapshot-cutover.yml).
- [Watch source runtime implementation](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/app/infrastructure/watch_voice_release.py).
- [Startup/predeploy source](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/railway.toml).
- [Driver mode/rollback](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/docs/watch-voice-driver-mode.md).
- [Watch runbook и предел endpoint](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/docs/watch-voice-mark.md).
- [Watch0116](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/alembic/versions/0116_watch_voice_observability.py), [Watch0117](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/alembic/versions/0117_watch_voice_early_inbox.py), [Watch0118](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/alembic/versions/0118_watch_voice_work_results.py), [Watch0119](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/alembic/versions/0119_watch_voice_comment_ids.py).

Готовность S1 по критерию применимого исполняемого Watch route не подтверждена. Backup, migration, deploy, restart, configuration и S2 не выполнялись.

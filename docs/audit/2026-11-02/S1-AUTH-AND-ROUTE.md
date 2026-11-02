# S1 — credential provenance и маршрут Watch0119

Дата: 2026-11-02. Состояние: **BLOCKED / PREPARATION_COMPLETE**, не runtime acceptance.
Продолжение существующего execution `gate736-s1-scope-live-20261102`, задача [736](https://github.com/markmalina/gate/issues/736), [PR766](https://github.com/markmalina/gate/pull/766).

## Результат этого продолжения

Причина `Not Authorized` не доказана. Точно определено недостающее подтверждение: происхождение фактически подставленного GitHub secret и связь с действующим Railway token record нужного типа и project/environment. Контракт запроса соответствует официальной документации; новых подтверждённых дефектов клиента не найдено.

Подготовлена конкретная последовательность backup → Watch0119 → postverify с таблицей существующих операций, доступов, недостающей адаптации и возврата. **Исполнимого принятого Watch0119 release contract в текущих версиях нет.** Подготовленный маршрут не является выпуском или разрешением его выполнить.

Новый отдельный допуск владельца: максимум 3 read-only Railway обращения к прежнему target. Фактически **0/3**; workflow dispatch0, retries0. Предыдущие 3/3 и все historical originals сохраняются. Неиспользованный лимит не означает фоновое исполнение или разрешение на mutation. Работа после отчёта остановлена.

## Восстановленные версии и принятые доказательства

| Объект | Точный SHA / результат |
|---|---|
| Gate main | `86dbc0f429dce089bf733fc422f20f556535895b` |
| PR766, OPEN / unmerged | `6fc78006c5b209dc93ca2904ae349d6401eb922f` |
| Установленный Ops main | `ec4e689a0fa24305672ef4047ff7c5fac0666982` |
| Принятый архив предыдущего Work | `82260be000abeda8176bacda944fad7748e365f5`, 44 файла; [отчёт](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/S1-CONTINUATION.md) |
| Предыдущий docs head | `8953475c01bc472dd1275e8f32d1e87f112ac49f` |
| Последний provider run | [41449717673](https://github.com/markmalina/gate/actions/runs/41449717673), attempt1, HTTP200 / ENVIRONMENT / AUTHORIZATION_DENIED / facts:null |
| Operational448 | [BLOCKED / VERIFIED_STOP_ONLY](https://github.com/AlphaCenter/delta/issues/448#issuecomment-6578062247) |
| Archive449 | [ACCEPT DOCUMENTATION ONLY](https://github.com/AlphaCenter/delta/issues/449#issuecomment-6578903237) |

Принятый архив и проверки не повторялись. Прочитаны фактические источники, provenance последнего запуска и неизменённое заключение449. Последний POST происходил `2026-11-02T13:04:18.729–13:04:18.880Z`. Observation SHA256 `0d10a898d4ca71d0ccfb2bb8c19c45dacc269a2a62a0f78355f167fbe5b405a9`.

Счётчики не объединяются: old41425190543 отдельно spent; previous allowance reads41437551667/41440937308/41449717673 =3/3; 41444775597 остаётся consumed workflow attempt с0provider reads. Более старые41318810046/41341920729 не изменены. Новый допуск этого Work=0/3.

На свежей GitHub проверке примерно14:06–14:08UTC: Gate/Ops in-progress0, Ops queued0, единственный Gate queued31127247901 от6августа не связан с S1. Совпадающих включённых автоматизаций не найдено. Это наблюдение GitHub/расписаний, не новое состояние Railway production.

## Авторизация: что доказано и что нет

Путь: [Gate workflow](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/.github/workflows/ops-watch-release-preflight-v2.yml#L44) → `${{ secrets.RAILWAY_TOKEN }}` → `RAILWAY_PROJECT_TOKEN` → [Ops entry](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-scope-diagnostic-v2/entry.mjs#L25) → [collector](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-scope-diagnostic-v2/collector.mjs).

Endpoint `https://backboard.railway.com/graphql/v2`; header `Project-Access-Token`; query `environment(id:$environmentId, projectId:$projectId){id projectId unmergedChangesCount}`. Collector допускает строку20–512 символов без whitespace. Последний выполненный POST исключает пустое значение/whitespace, но не неверный тип или область доступа.

[Актуальная официальная Railway API документация](https://docs.railway.com/integrations/api) подтверждает этот header для project token, область одного environment/project и identity-query `projectToken { projectId environmentId }`. Для account/workspace/OAuth применяется Bearer. Официальный контракт ошибок отличает HTTP400 validation от HTTP200 GraphQL authorization denial. Сохранённая [официальная CLI schema](https://github.com/railwayapp/cli/blob/b62467ef1b5303e3e340283ed96425dcf9612993/src/gql/schema.json) содержит использованные поля и аргументы; это не live introspection.

**Уточнение прежнего отчёта:** `environment: gate-production` доказывает контекст job, но не environment-level происхождение secret. По [официальным правилам GitHub](https://docs.github.com/en/enterprise-cloud@latest/actions/reference/security/secrets) одноимённые значения выбираются environment > repository > organization. Из исходников нельзя исключить fallback к repo/org. Не читались значения secrets или защищённые secret endpoints.

| Возможная причина | Проверяемый вывод |
|---|---|
| Ошибка endpoint/header при настоящем project token | Не подтверждена: код соответствует документированному контракту |
| В secret account/workspace token | Не исключено; название переменной не удостоверяет тип |
| Project token отозван/не распознан | Не исключено; один Not Authorized не доказывает недействительность |
| Неверный project/environment scope | Не исключено: token identity не получена |
| Неверный target | Переданные IDs совпадают с сохранённым профилем; актуальная принадлежность у провайдера не доказана |
| Недостаточные права / server-side ограничение | Не исключено; нужны metadata/provenance или серверная корреляция |
| Неверные GraphQL поля | По официальной схеме дефект не найден; последний ответ не validation failure |
| Клиент потерял полезную информацию | Старые сообщения PROJECT_TOKEN сохранены как UNCLASSIFIED, сырой текст/trace не восстановить. Это известная консервативная проекция, не новый доказанный дефект |

Service `099cbf8c-1d94-43fe-8714-90725bf93998` закреплён в профиле, но ни один из этих query его не выбирает. Service/DB identity и effective права на backup/deploy не подтверждены. Старые PROJECT_TOKEN failures не приравниваются к последнему ENVIRONMENT denial.

## Почему новый лимит не расходован

Отдельный независимый auth-review не нашёл нового диагностического основания: оба корня уже проверялись, отказ локализован. Повтор не различит неверный тип, отозванный token, неверный scope и неверный secret source. Попытка Bearer наугад или обход через другой credential также не обоснованы. Изолированный projectToken без environment сейчас лишь повторяет уже локализованный отказ.

Установленный исполнитель дополнительно закреплён на прежний grant/ordinal3/environment-only query. Его нельзя переармить или назвать готовым для нового допуска. Если появится новое основание, нужна узкая новая привязка разрешения/учёта/query в существующем механизме с целевыми проверками; это не переустановка исправного компонента и не дефект текущей защиты от повторов.

Точное необходимое подтверждение от оператора без раскрытия значения:

1. Railway token record: тип, состояние active/revoked, project `972b6ab7-988a-421c-abfa-425451ad714f`, environment `a148b138-44b3-4349-9d4b-292880503055`.
2. Effective GitHub `RAILWAY_TOKEN`: уровень хранения и безопасная запись последней установки, связывающая именно выбранное значение с этим token record. Одного `updated_at` недостаточно. Если историческая provenance отсутствует — честно UNKNOWN, а не предположение.
3. Если эти сведения согласованы — серверная корреляция Railway по UTC последнего запроса, exact query и target. Trace ID не сохранён. Support-письмо не отправлялось.

Подключённый GitHub fetch исключает secrets APIs, интерфейса Railway account/token metadata в этой сессии нет. Поэтому эту связь нельзя получить имеющимся read-only клиентом. Новый provider query с прежним неизвестным значением не заменяет provenance.

Только после подтверждения, что effective credential является действующим project token ожидаемого target, первый допустимый диагностический шаг: один `projectToken { projectId environmentId }`, тот же endpoint/header/защищённый secret,0retry; сравнение IDs в памяти, в квитанции только match/mismatch. При mismatch/denial STOP. При совпадении отдельное чтение точного environment; это всё ещё не service/DB/release acceptance. Если metadata докажет wrong type/scope/revoked, сначала требуется отдельное решение о коррекции, а не запрос с прежним значением. Сейчас эти шаги не запускались; старые allowances не возобновляются.

## Конкретный маршрут backup → Watch0119 → postverify

Фиксированные source bindings: project/environment/Gate service выше; PostgreSQL service `561d3da3-ddb5-4d3d-8fa2-c9454b0996bf`, volumeInstance `7d2de9ed-c63e-454a-811e-4f206f3dfef3`. Их свежая physical identity неизвестна. Candidate для подготовки — PR766 `6fc78006…`; для выпуска потребуется точный принятый merged SHA, а не подстановка сегодняшнего main.

| Шаг | Существующий механизм / точная операция | Необходимый доступ и условие |
|---|---|---|
| 1. Target, DB, volume | Snapshot provider authenticate/observe/verifyDatabaseBinding/verifyVolume; service/environment/private-network/volume queries | Действующий project token нужной области. DB binding читает `variablesForServiceDeployment` для Gate и PostgreSQL, то есть все значения в защищённой памяти; этот доступ не предоставлен нынешними тремя diagnostic reads. SQL physical identity этим не доказывается |
| 2. Freeze/drain | В snapshot есть `variableUpsert(skipDeploys:true)`, `deploymentStop`, fleet verification | Нужна адаптация именно под Watch Actions/persistent/claims. Нынешний `GATE_SEND_ENABLED` ограждает reply senders и не заменяет Watch fence. Сохранять unknown effects, не повторять их |
| 3. Completed locked backup | `volumeInstanceBackupCreate` → `workflowStatus` → `volumeInstanceBackupList` → `volumeInstanceBackupLock` → повторный list | Exact volume, max1create/max1lock; Complete/error:null, unique backup ID/name, locked/expiresAt:null; новая отдельная mutation authority. При неизвестном результате не создавать второй backup |
| 4. Migration и deploy | Gate `railway.toml`: `alembic upgrade head`; short deploy `serviceInstanceDeployV2` на exact current main | Только после1–3, pending-clean/autoDeploy=false/source/fleet recheck. Отдельное разрешение migration/deploy; GitHub contents:read и Railway deploy права. Existing short route не связывает эти условия в один contract |
| 5. Установленная версия | Deployment ID/SUCCESS/not-stopped/metadata SHA + authenticated GET `/admin/ops/watch-voice` | Railway read и существующий Gate admin bearer в защищённой памяти. Exact SHA/target/deployment совпадают, actual sole schema head0119, loaded Actions mode, persistent=false; сохранены originals/cursor/watermark/journals |
| 6. Аварийный выход | Existing core умеет остановить известные deployments и сохранить schema/journals | Это STOP, не восстановление. Нужны заранее проверенный совместимый rollback SHA либо stop/fix-forward. Restore отсутствует; `allowRestore:false` |

Реальная Watch цепочка: `0115_watch_voice_post_payment` → `0116_watch_voice_observability` → `0117_watch_voice_early_inbox` → `0118_watch_voice_work_results` → `0119_watch_voice_comment_ids`. Reply `0116_reply_dialog_versions` — другая ветка.

Почему нельзя вызвать нынешний snapshot: profile Gate745-only, migration reply0116, health `/admin/ops/reply-cutover` и dialog-v1, два deploy одного SHA с переключением reply send flag; release OFF/PENDING. Замены SHA недостаточно. Реальные pins Gate: short deploy Ops `512decbbd81d1d824563af995fc0df435fbf2429`, snapshot Ops `b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`; более новый Ops main сам эти pins не переключает.

У `/admin/ops/watch-voice` поля effective_start_command/effective_predeploy_command=null, predeploy_execution_proven=false. Его успех не доказывает исполнение predeploy; deployment/predeploy evidence требуется отдельно. Endpoint находится в candidate, отсутствие его на старой production не разрешает bootstrap deploy.

Возврат после migration: никакого automatic `alembic downgrade`, удаления журналов или restore старой БД поверх новых эффектов. Исторический LKG `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` не проверен на0119. При UNKNOWN сначала reconciliation, без второго deploy/restore. При отсутствии подтверждённого rollback допустим только согласованный safe stop/fix-forward; работоспособность восстановления не обещается.

## Единый пакет решения владельца

**Ближайшее действие:** операторская проверка metadata/provenance из трёх пунктов выше, без замены token. Это точный отсутствующий вход; общее «разрешаю ещё три чтения» его не восполнит.

Если проверка докажет необходимость замены: отдельное решение на запись корректного project token в существующий environment `gate-production` / secret `RAILWAY_TOKEN`, без расширения до account token и без смены target. До отзыва старого — подтверждённый операторский способ возврата, поскольку прочитать сохранённый GitHub secret обратно нельзя. Сейчас необходимость замены не доказана, замена не выполнена.

Для будущего выпуска: узко адаптировать имеющиеся snapshot provider primitives/plan/apply/verifier под Watch0119, сохранив чужую reply ветку и grants. Сначала disabled contract, целевые migration/populated backup/restore fixtures и независимая проверка; затем пакет exact candidate/runtime baseline/DB/volume/backup/fence/commands/time window/recovery. Нынешний S1 разрешает эту подготовку маршрута, но не production backup/config/migration/deploy/restart. Не выдаётся готовая команда запуска, которая не соблюдала бы эти условия. Новая инфраструктура не создана, S2 не начат.

## Независимые заключения и проверки

Фактически завершены два отдельных supporting review, не назначения и не формальные Delta runtime acceptance:

- [Auth review](evidence/s1-auth-route/auth-review.md): BLOCKED / недостающая credential provenance; нового подтверждённого дефекта клиента нет;0Railway calls.
- [Route review](evidence/s1-auth-route/route-review.md): BLOCKED_ROUTE / SOURCE_ONLY; точные существующие операции и неприменимость reply contract к Watch;0Railway calls.

В этом продолжении исправлено описание источника secret и конкретизированы необходимые доступы, операции и recovery. Код приложения/клиента/Ops не менялся; переустановки и новый CI не требовались. Принятые проверки унаследованы, не представлены как заново выполненные. Документы проверены на корректность ссылок/пинов и сохранение исторического бюджета; проверка публикации фиксируется финальной записью736 после чтения сохранённого коммита.

**S1 не готов к закрытию:** effective credential/target и pending-clean не подтверждены; применимый принятый исполняемый Watch0119 release/rollback contract отсутствует. Результат текущего поручения — проверенная подготовка и точный вход для разблокирования. Runtime BLOCKED сохраняется. Все leases освобождаются финальной записью736; продолжения в фоне нет.

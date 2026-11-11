# Gate #745 — S2: выполнимость native-стенда до выделения ресурсов

Дата исследования: 19 октября 2026. Автор: α Gate. Это результат исследования и проект решения, а не разрешение на запуск и не независимый ACCEPT.

Execution: `gate-745-snapshot-20261008T1458Z`; существующие [#745](https://github.com/markmalina/gate/issues/745) / [PR747](https://github.com/markmalina/gate/pull/747) / `agent/745-dialog-snapshot`. [Принятие S2](https://github.com/markmalina/gate/issues/745#issuecomment-6264342769). Поручение Марка: S1 выполняет другой Work, здесь выполнять S2. S3 этим результатом не начинается.

## 1. Решение

Сохранить минимальную native-топологию на Railway: один отдельный disposable project/environment, один Gate service с последовательными Hbootstrap/baseline/guard/Hreset/baseline/guard/Hproof, один PostgreSQL service, один parent volume и одна volume instance. Четвёртый helper, новый постоянный сервис, workspace scanner и general dispatcher не нужны. Проверяется существующий общий алгоритм cutover, а не его облегчённая копия.

**Сейчас выделять ресурсы нельзя: `FEASIBILITY_NOT_PROVEN / NO_ALLOCATION`.** Найдены поддерживаемые части решения и конкретные недоказанные условия. Готового безусловно исполнимого дизайна при всех действующих ограничениях пока нет. Проект ниже условный; он не выдаётся за принятую готовность S2.

Исследовательская часть выполнена. Для приёмки S2 нужно закрыть перечисленные predicates подтверждённым механизмом либо отдельным точным решением изменить требование. Общее «делай» для этого повторно не требуется.

## 2. Точные источники и границы проверки

- Gate main: `86dbc0f429dce089bf733fc422f20f556535895b`, tree `7b5ddc0e6317fc475e2256953a69a07b109463dc`.
- PR747 при повторной проверке: OPEN/unmerged, head `6c30420acc733fbfc651ccc6f1c6f60d9d0e6b8a`, tree `f69298ae5786bba9ee94a401b3ed47785a1a3b17`. Последний commit добавляет пакет S1; здесь ветка не изменялась.
- Ops main исследования: `b2d0587126488cebb0ff48e16ba397cd7e97e27f`, tree `1441501e84bc4926cd99177bda655f1a59510ced`. S1 central repair принят [α Ops](https://github.com/markmalina/ops/issues/375#issuecomment-6264470101); его реализация и lease здесь не перехватываются.
- Official Railway CLI: `05ed159796ef6bf15de0dab388b1054c1af6f395`; schema `src/gql/schema.json`, blob `3f3c4ca20d99c07417db70882caedf47196308f9`.
- Official Railway docs: `72354493198302677fb6be6c692dd75c64cae2c6`.
- Official TypeScript SDK: `97fdb4f61d351319d5633c2c92a86ca2ff0062d8`; `src/iac/schema.ts`, blob `29b7b30e1cf828504307f43ae8658ea1ae200dd6`.

Проверены 21 полная локальная копия source/doc против Git blob SHA и 10 predicates из официальной schema. Выполнена офлайн арифметика Decimal. Это авторская проверка исследования. Реальных provider/account/DB reads, SQL connections, allocation, credentials, удаления, deployment и отправок не было. Открытие публичных документов/API schema не является наблюдением аккаунта. Не заявляется глобальный census пользователей или свежая runtime-приёмка.

## 3. Матрица восьми обязательных пакетов

| Пакет | Поддерживаемая часть | Чего недостаёт для допуска | Кто закрывает / проверяемый выход |
|---|---|---|---|
| CPU/RAM | `serviceInstanceLimitsUpdate(input:{environmentId,serviceId,memoryGB,vCPUs})`; getter `serviceInstanceLimits` учитывает plan defaults. SDK также описывает `deploy.limitOverride.containers.memoryBytes`. | Getter остаётся custom scalar без документированной формы; setter не доказывает effective caps и охват pre-deploy. Не определены подтверждённые единицы/минимумы и момент применения до workload. | α Ops: точная поддерживаемая форма/units и защищённый bounded readback; тесты reject неизвестной формы; proof для всех видов процессов. |
| Physical fleet | Schema имеет `Deployment.instances`, `deploymentStopped`, список deployments и pagination; activeDeployments отдельно от очереди. | Нет фактических IDs/инвентаря disposable target; caps должны охватывать rollout, pre-deploy, build, helpers, регионы и очереди. Список activeDeployments этого не доказывает. | α Ops + α Gate: полный original inventory с source/config/instance/region и отсутствие неожиданных writers; before/after admission. |
| Начальный том | IaC документирует `volume(...,{sizeMB:1024})`; официальный SDK передаёт volume config. Обычные `VolumeCreateInput` и `VolumeUpdateInput` поля размера не имеют. | Не доказаны provider-side порядок создания/первого запуска, принятый размер/units/minimum и readback до PG. Нельзя подставлять `sizeMB` в обычный `volumeCreate`. | α Ops: ограниченный путь создания volume-only в пустом fixture scope, original returned IDs и нормализованная capacity до первого PG; иначе конкретная альтернатива без allocation. |
| Backup | Create/delete возвращают `WorkflowId`; lock — Boolean, удаляет expiration. Нужны workflow completion и точный backup list/readback. Manual backup ограничен 50% capacity. | Actual PG/WAL/usage eligibility; право удаления locked backup; отсутствие скрытого restore/unlock/force; завершение удаления каждой копии и retention semantics. | α Ops: supported workflow/status/error/lock/delete mapping и права; α Gate: два original backup receipts, сохранённый unknown journal. |
| Изоляция | Private network ограничен project/environment; можно создать пустой service без source. | Нет наблюдённого чистого target. Private network не запрещает внешний egress. Нет доказательства отсутствия inherited secrets, auto-deploy, image updates, cron и других bindings. | α Ops: exact clean-scope allowlist; α Gate: synthetic-only dataset и disabled business adapters. Никаких Production DSN/дампов/Telegram/LLM/STT keys. |
| Стоимость | Опубликованы ставки CPU/RAM/storage/egress; builds объявлены бесплатными. Workspace compute hard limit минимум $10, охватывает весь workspace. | Неизвестны account plan/доступная квота/units/retention billing; нет isolated $5 provider cap. Не доказаны egress ceiling, GitHub costs и прекращение charges после observer expiry. | α Ops: current разрешённое billing evidence + supported controls. α Gate: полный расчёт и явный остаточный риск. Не менять shared-workspace hard limit ради теста. |
| Права и bootstrap | Project token scoped на environment; OAuth позволяет выбранный project:viewer/member или workspace:viewer/member/admin. | Token существующего environment не решает создание его parent и provisioning собственного token. Нет зарегистрированного bootstrap route/отдельного grant; не доказаны права post-delete observer. | α Ops: supported exact bootstrap/credential owner и сохранённые observation rights. Account/workspace token и новый OAuth не вводить как молчаливый обход. |
| Cleanup и наблюдение | Volume/API deletion имеет 48h soft-delete; есть точные service/volume/backup/project operations и поля состояния. | Нет авторитетного предиката physical purge и surviving exact-ID authority после parent deletion; нет доказанного автономного deadline driver. Boolean/DELETED/null/отсутствие из списка не являются достаточным доказательством. | α Ops: официальный supported contract + bounded route/receipt. α Gate: finite cleanup graph, original lineage, terminal verifier. До этого NO_ALLOCATION. |

Статусы означают `DOC_SUPPORTED`, `SOURCE_PRESENT` или `NOT_PROVEN`; ни один `DOC_SUPPORTED` не равен `ACCOUNT_OBSERVED`.

## 4. Что существенно уточнено относительно исходного ТЗ

1. **Не доказана невозможность небольшого начального тома.** Есть отдельный IaC-путь. Но наличие его поля не доказывает, что provider создаёт именно такой том до PG start. Default Hobby5GB/Pro50GB и запрет downsizing не подменяют эту проверку.
2. **Нельзя выдумать форму limits scalar.** В официальной schema `ServiceInstanceLimit` действительно SCALAR. Не считать любой JSON object доказанным cap и не угадывать `cpu`, millicpu или GB по названию.
3. **Pre-deploy — отдельный контейнер.** Он имеет доступ к environment/private network, не монтирует volume. Сейчас документация описывает отдельный `preDeployTimeoutSeconds`; в прочитанном `ServiceInstanceUpdateInput` такого поля нет. Fixed v2 configuration также его не устанавливает. Простой перенос timeout в этот input некорректен.
4. **IaC не является готовой очисткой.** CLI `change_set.rs` пропускает удаление standalone volume при исчезновении из desired config. «Применить пустую конфигурацию» не доказывает cleanup.
5. **1800 и 256 — разные, пока не согласованные числа.** Current native counter с именем reads увеличивается для каждого GraphQL dispatch, включая mutation, и хранится в памяти процесса. v7 предлагает 256 reads на весь lifecycle. Это не один durable budget.
6. **T100 недостаточно как единственная проверка начала case.** При 28min recovery и резерве20min на cleanup начало должно быть не позже T72, если общий write deadline=T120. Для normal20min — T80. Всегда учитывать более раннюю owner expiry.

## 5. Целевая топология и ограничения процессов

| Компонент | Проектный предел | Требуемое доказательство |
|---|---|---|
| Gate/helper runtime | 1 service, 1 регион; максимум 2 физических слота по 0.5vCPU/512MiB | Во время activation допускается old guarded runtime + pre-deploy либо old + new guarded runtime. Нельзя автоматически считать, что третьего процесса нет. Нужен полный observed fleet и подтверждённый cap pre-deploy. |
| PostgreSQL | 1 instance, максимум 1vCPU/1024MiB; без HA/replicas | Pinned image digest, dedicated synthetic DB/role, original physical identity и private binding. |
| Хранилище | 1 parent volume / 1 instance; capacity ≤1024MiB до первого PG | Exact-ID capacity/units/placement readback; used bytes не заменяют capacity. |
| Backups | Ровно 2 original manual completed locked snapshots | Success и controlled recovery; ни schedules, PITR, restore, wipe, ни дополнительных копий. |
| Остальные процессы | Без четвёртого helper, обычных workers/cron/queue/auto-updates | Build/pre-deploy отдельно перечислены. Бесплатность build не доказывает выполнение физического resource cap. |

Суммарная целевая граница Gate+PG — ≤2vCPU/2048MiB, только если подтверждён весь физический состав. Не выдавать per-replica setting за whole-project cap. Нельзя снижать caps ниже реально работоспособных значений только ради формального числа; если образ не помещается, нужен пересмотр до allocation.

## 6. Порядок подготовки, который должен быть доказан

1. До любых paid dispatch: accepted exact sources, owner scope, supported resource/cost/cleanup predicates, зарегистрированные полномочия и **durably ARMED независимая очистка**. Все будущие IDs происходят из original creation results; никакого поиска по имени.
2. Bootstrap parent/environment/token — отдельная подтверждённая возможность. Если её нет, остановиться до первого создания, а не использовать Production project token или более широкую session credential. Уже существующий disposable parent можно использовать только при exact registration, изоляции и новом явном use/cleanup scope; его наличие здесь не предполагается.
3. Создать два пустых service без source; поставить подтверждённые caps и fixed settings до назначения запускаемого source. Проверить отсутствие deploy triggers/pending changes и inherited bindings.
4. Рассмотреть **только volume-only** первый IaC change set в пустом scope: фиксированный address/region/size, без service/database create, source/variable writes, удаления или произвольного caller JSON. Official `environmentApplyChangeSet` имеет `baseConfigEtag` и async result; это candidate seam, не уже принятая Ops operation. Требуется доказать provider ordering и отсутствие неразрешённого auto-start.
5. Получить и проверить original parent-volume/instance IDs и capacity до PG start; затем фиксированное attachment к пустому PG. Если размер или порядок не подтверждены — не запускать PG и не обещать последующее уменьшение.
6. Назначить только pinned sources и dedicated synthetic variables. Оригинальный PG setup — один раз в preparation; native controller с `pgSetupDeploys=0` не должен создавать PG второй раз. Hbootstrap/identity идут по существующему preparation дизайну, с отдельной durable one-use reservation до DB connection.
7. В final binding/native grant использовать actual original identity, deployment/source/config и общий ARM/counters. Ни выдача grant, ни restart, ни новый B не обновляют lifetime.

Это спецификация порядка, не выполненный bootstrap. В S2 не создан ни один из этих ресурсов и не вызван preview/apply.

## 7. Время, остановка и очистка

Существующее [v7](https://github.com/markmalina/gate/issues/745#issuecomment-6124068885) остаётся **PROPOSED / NOT APPROVED**. Его разделение ранней остановки/запроса удаления и поздней проверки обосновано 48h recovery window. Самостоятельно заменять действующее требование DELETED-by-T120 нельзя.

Целевая поправка для последующего source/effect решения:

- `A` — самый ранний authenticated allocation-wide ARM. Write deadline — минимум всех исходных expiry и A+120min; terminal read deadline — минимум специальной observation expiry и A+72h.
- До раннего write deadline: все compute/writers/queues/build/pre-deploy/restarts остановлены и подтверждены, точные разрешённые deletion requests поданы. REQUESTED/QUEUED сохраняются как REQUESTED/QUEUED.
- Последний допустимый begin-case рассчитывается как `writeDeadline - cleanupReserve - caseWorstDuration`, дополнительно ограниченный исходным T100. При120/20/28 это T72, при120/20/20 — T80. Задержка уже учтённого ARM не компенсируется новым clock.
- После write deadline — только ранее разрешённые readonly observations. Никаких поздних deletes, force, restore, unlock, повторного create или расширения expiry.
- До terminal receipt native acceptance остаётся PENDING. Истечение72h или бюджета без доказательства означает NOT_PROVEN/FAIL; остановка наблюдателя не доказывает остановку storage charges.

Закрытый cleanup graph сохраняет максимум16 cleanup wire mutations: ≤9 stops исходных известных Gate deployments, 1 PG stop, 2 original backup deletes, 1 parent-volume delete, 2 service deletes, 1 project scheduled-delete. Это **не потолок всех preparation/native mutations**. Их отдельные точные caps входят в общий accepted operation manifest. Уже подтверждённо остановленный ID не останавливать повторно для заполнения отчёта; lost response потребляет слот и остаётся UNKNOWN.

Для parent выбрать и независимо проверить один supported scheduled-delete method; в официальной schema есть `projectScheduleDelete` с48h grace period. Не подставлять `projectScheduleDeleteForce`, legacy instant delete или provider cancellation. Наличие поля schema не разрешает вызов.

`volumeInstanceBackupDelete` возвращает workflowId. Нужны original workflow completion, exact backup reconciliation и поддерживаемый терминальный storage predicate. Lock не снимать автоматически. Parent deletion допускается только после подтверждённых child deletion requests и сохранения original inventory, если это отдельно согласовано; нельзя предполагать cascade или purge.

**Отдельный неустранённый риск:** Actions timeout/`finally`/cron сами по себе не гарантируют compute-stop к T120 при отмене controller, задержке runner или недоступности provider. До allocation нужен реально установленный независимый durable driver с наблюдаемым ARM и доказанными границами доставки/полномочий. Если абсолютный deadline не обеспечивается, это должно стать явным изменением требования/риска, а не обещанием автоматической остановки.

## 8. Что именно считать terminal proof

Должен существовать поддерживаемый контракт, который одновременно:

1. Аутентифицирует observer и его доступ к original exact IDs после каждого service/project deletion.
2. Отличает physical deallocation пользовательских ресурсов/восстанавливаемых копий от soft-delete и сохранённого metadata tombstone.
3. Определяет точный resource-specific response/error/state, область backup/volume-instance/parent-volume и retention/billing semantics.
4. Даёт отдельное подтверждение нулевого running fleet; отсутствие compute не заменяет storage proof.

Недостаточны: Boolean mutation success; `state=DELETED`; `isPendingDeletion=false`; deletedAt; null/403/404/GraphQL error; пропавший token; неполный список; отсутствие ID даже в полном списке без контракта physical purge; исходящий email; истечение48h/72h.

Project token ограничен environment; его способность подтверждать target после удаления parent **не доказана**. OAuth project:viewer документирован, но сохранение его прав после deletion тоже не доказано. Новый OAuth/account/workspace credential не является разрешённым решением по умолчанию. Provider-internal disaster backups отдельно упомянуты Railway; не обещать стирание всех внутренних копий по пользовательскому API.

Найден [публичный вопрос о том же API-контракте](https://station.railway.com/questions/graphql-contract-for-verifying-permanent-2d18830f). В прочитанном состоянии есть community reply и follow-up, но нет официального ответа, устанавливающего этот контракт. Сторонние IDs/логи в наш проект не копируются. Community совет «после48h нет в списке — удалено» не принимается за provider authority.

## 9. Один общий бюджет вместо смешения 1800/256

Предлагается сохранять **1800 как верхний allocation-wide потолок provider GraphQL dispatch**, включая mutations, на весь preparation/native/cleanup/terminal lifecycle и все restarts. Это не повышение current native ceiling, но существенно более строгая durability/общая область, чем process-local counter. Для этого потребуется явное согласование отличия от предложенных v7/256; v7 не переписывается задним числом.

Проект распределения: preparation128 + native1200 + early cleanup160 + terminal readonly288 + reconciliation reserve24 =1800. Это **не доказательство**, что полный native trace помещается в1200: до source acceptance нужен replay полного readset с задержками, ошибками и отказом начать case при нехватке зарезервированного cleanup остатка. Если не помещается — сначала сокращать повторные transport reads совместимым batching; не снимать проверки и не повышать cap молча.

Почему нельзя просто скопировать256: каждые5s в течение72h — 51840 ticks даже для одного read. Четыре отдельные проверки в час дают288 reads ещё без подготовки/native. Возможен новый supported batched readset, но его сейчас нет. Count budgets должны измерять dispatch, а не poll iterations.

Сохранить исходные отдельные GitHub/OIDC/JWKS/archive/helper/SQL ceilings. Повторный native grant не создаёт новый read budget. Предложенные v7 ceilings10s/read,15s/write и131072-byte сообщения не совпадают со всеми current native v2 timeout/byte values; совместимость producer/readers должна быть проверена до применения. Нельзя просто поменять числа в JSON и назвать это enforcement.

## 10. Расчёт стоимости — условная модель

По опубликованным container rates: CPU $0.000463/vCPU/min; RAM $0.000231/GB/min; volume/backup $0.000003472222222/GB/min; egress $0.05/GB. Для арифметического примера GB считается десятичным; фактическая billing unit ещё подлежит подтверждению.

| Статья | Условия примера | USD |
|---|---|---:|
| CPU+RAM | Весь fleet ≤2vCPU/2048MiB, не более120min от ARM | 0.170648247 |
| Volume+2backup | Консервативно полная1GiB capacity каждой из3копий,4320min | 0.048318382 |
| Egress | Пример1GB, не фактически установленная квота | 0.050000000 |
| Подытог | Без GitHub, subscription/tax и неизвестных | **0.268966629** |

Это **не invoice cap и не обещание bill≤$0.27 или≤$5**. Модель перестаёт ограничивать расходы, если compute не остановлен, retention продолжается после72h, egress не ограничен, units иные или существуют посторонние процессы. Incremental COW backup pricing не устраняет риск продолжающегося хранения. Free builds не делают бесплатными runtime/pre-deploy, GitHub runner minutes и artifacts.

Нужны actual current plan/rates, subscription/quota attribution, GitHub runner/artifact retention costs, весь service egress и поддерживаемый механизм остановки начислений. Минимальный workspace hard limit $10 и его shutdown всех workloads не дают изолированный cap $5 для fixture. В shared Production workspace его изменение затрагивает другой outcome и здесь не предлагается как routine шаг. Нет разрешения менять тариф, создавать workspace или оплачивать подписку.

## 11. Проект будущего physical grant — пока НЕ ГОТОВ К ВЫДАЧЕ

| Поле | Обязательное содержание до подписи |
|---|---|
| Owner/source | Immutable owner record; independently accepted exact Gate/Ops/C0/H2/P/cleanup sources и configuration/operation manifest digests. |
| Target | Exact disposable parent project/environment и credential scope; будущие IDs только из single-use creation receipts. Production IDs запрещены во всех slots. |
| Creation | Ровно два empty service, один volume/instance и исходный PG deployment; точный reviewed finite creation/attachment/caps/variables/source graph. Незаполненный bootstrap grant недействителен. |
| Native | Существующие helper≤3/baseline≤2/candidate≤4 deployments, config transitions≤7, original success+controlled recovery, backup create≤2/lock≤2, существующие helper/metadata/unknown-proof ceilings. Все дополнительные preparation operations посчитаны отдельно. |
| Resource/cost | Реальные supported caps-before-start и whole-fleet readback; initial capacity; named billing model/control, currency, максимальный новый расход и явно принятые остаточные риски. |
| Cleanup | Exact16-operation maximum graph и separate stop/delete/queued/purged predicates; surviving observer credential/route, не истекающие до нужного checkpoint. |
| Time/counters | Original ARM, absolute expiry, begin-case deadlines с cleanup reserve; один durable allocation-wide counter; no mutation retry после UNKNOWN. |
| Effects | Только synthetic fixture; credentials/onboarding отдельно; ноль Production, Telegram, model/STT, чужих данных, restore/force/wipe и credential export. |
| Evidence | Original attributed creation/dispatch/source/config/identity, backup workflows, live success/recovery, ранняя остановка, отдельное terminal reconciliation; schema-valid независимый verifier. |

Любой UNBOUND/UNKNOWN выше означает отказ до allocation. Этот шаблон не запрашивает разрешение Марка подписать незаполненный physical grant.

## 12. SOURCE-исключение и необходимость пути

Core snapshot guard сам по себе не требует delete/restore. Удаление требуется действующему native fixture cleanup criterion; CI-only или оставленный storage не выполняют его. Новый general infrastructure protocol ради #745 не нужен: использовать существующие O/C0/H2/P seams и finite driver, когда capability доказана.

Текущий [Ops AGENTS](https://github.com/markmalina/ops/blob/b2d0587126488cebb0ff48e16ba397cd7e97e27f/AGENTS.md) прямо говорит: “Never add arbitrary command execution, a delete/restore operation”. Прочитанный [Use Agent Ops safety](https://github.com/markmalina/ops/blob/b2d0587126488cebb0ff48e16ba397cd7e97e27f/agent-ops/skills/use-agent-ops/references/safety.md) относит “Delete a project, environment, service, database, volume, or route” к Always deny. Source exception не выдаёт runtime authority.

Конкретный scope будущего исключения уже ограничен: только разработка/независимая проверка default-off16-operation cleanup и строгой совместимости receipts, early write/late readonly lifecycle, без physical grants. Основа — v7 с явным решением о едином read/wire budget, а не ещё один general deletion API. Пока не разрешены provider feasibility blockers, одобрение одного исключения не сделает стенд исполнимым. В S2 такой код не добавлялся и новый grant не запрашивается преждевременно.

Альтернативы рассмотрены: CI/local PG сохраняются как source evidence, но не заменяют native; generic другой provider не воспроизводит Railway cutover; сохранение parent до purge меняет T120 parent-delete requirement и не решает автоматически terminal semantics. Ни одна альтернатива не объявлена принятой без решения.

## 13. Точный следующий технический пакет

Адресат зависимости — α Ops, после отдельного принятия **S2 capability scope**. Это не расширение текущего S1 поручения.

1. Подтвердить exact scalar shape/units/minimum/effective caps и их охват pre-deploy; выбрать supported pre-deploy timeout setting/readback.
2. Подтвердить volume-only IaC creation≤1024MiB до первого PG, без неразрешённых service starts; дать точный finite request/result mapping.
3. Определить supported resource-specific physical purge proof и observer authority после parent deletion, retention/disaster-copy/billing границы и право удаления locked snapshots.
4. Указать допустимый bootstrap clean parent/environment/dedicated credentials и действительно независимый durable cleanup driver. Отсутствие доступной capability вернуть явно; не предлагать workspace token/provider UI bypass.

Сначала публичная документация/официальное подтверждение и source-only fixtures. Если без account observation нельзя — подготовить один bounded protected readset по exact target через принятый маршрут: categories-only output, declared sensitive-memory boundary, exact method/counters/bytes/expiry/zero retries. Существующие grants и чувствительный read scope #736 не переиспользовать. Нет разрешения писать провайдеру/публиковать новый support ticket от имени Марка.

После ответа α Gate сводит матрицу и, только если обязательные условия достижимы, готовит одно точное необходимое owner decision. Если они недостижимы — конкретный вариант изменения условий, без создания ресурса «чтобы посмотреть».

## 14. Воспроизводимость и итоговый статус

Сопутствующий `analyze_feasibility.py` не имеет network/client/credential code. Он сверяет21Gitblob, проверяет10schema predicates, считает Decimal стоимость и polling budgets. `analysis-results.json` хранит исходные допущения и false invoice/launch authority. Для воспроизведения получить immutable blobs по `sources.json` в отдельный scratch-каталог, сохранив `local_path`, затем выполнить `python analyze_feasibility.py --sources-dir /absolute/path/to/canonical-copies`. `schema-excerpt.json` — выбранные типы из указанной официальной schema, а не live introspection. Полные сторонние источники не копируются в Gate. Первичная локальная проверка обнаружила обрезанную scratch-копию одного документа; она повторно получена с того же immutable ref и исправлена до успешного полного audit. Canonical sources не менялись.

Состояния раздельно:

- `S2_research_and_design: COMPLETED_AUTHOR_PACKET`.
- `S2_acceptance: PENDING / FEASIBILITY_NOT_PROVEN`.
- `physical_launch: DENIED_BEFORE_ALLOCATION`.
- `S1: separate α Ops accepted central repair; no source interference here`.
- `S3/native/Production/client/product: NOT_EXECUTED_BY_S2`.

Следующий технический владелец — α Ops **после принятия S2 capability dependency**; до принятия α Gate владеет её доставкой/учётом. Подготовка документа не является доставкой, а доставка не является принятием. Фонового observer или автоматического продолжения Work не создано.

## 15. Основные первичные материалы

- [Volumes](https://docs.railway.com/volumes/reference), [Backups](https://docs.railway.com/volumes/backups), [Manage volumes](https://docs.railway.com/integrations/api/manage-volumes).
- [Current pricing](https://docs.railway.com/pricing/plans), [Cost control](https://docs.railway.com/pricing/cost-control), [Pre-deploy](https://docs.railway.com/deployments/pre-deploy-command).
- [IaC reference](https://docs.railway.com/infrastructure-as-code/reference), [IaC lifecycle](https://docs.railway.com/infrastructure-as-code).
- [API auth/errors](https://docs.railway.com/integrations/api), [OAuth scopes](https://docs.railway.com/integrations/oauth/scopes-and-user-consent), [Private network](https://docs.railway.com/networking/private-networking/how-it-works).
- [Official schema](https://github.com/railwayapp/cli/blob/05ed159796ef6bf15de0dab388b1054c1af6f395/src/gql/schema.json), [IaC change-set semantics](https://github.com/railwayapp/cli/blob/05ed159796ef6bf15de0dab388b1054c1af6f395/src/iac/change_set.rs), [SDK wire types](https://github.com/railwayapp/railway-ts-sdk/blob/97fdb4f61d351319d5633c2c92a86ca2ff0062d8/src/iac/schema.ts).
- [Provider API soft-delete announcement](https://blog.railway.com/p/your-ai-wants-to-nuke-your-database).

Для динамических страниц факты относятся к чтению19.10.2026. Source/doc blobs и их SHA256 зафиксированы отдельно в source manifest; перед будущим запуском требуется fresh account/runtime proof.

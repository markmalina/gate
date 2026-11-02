# S1: проверка доступов и пакет разрешения
Дата наблюдения: 2 ноября 2026, 11:08–11:16 МСК.
Статус: **S1 НЕ ЗАВЕРШЁН. WAITING_OWNER; runtime и operational review не выполнены.**
Спринты S2–S6 не запускались. Принятый владельцем порядок: основной Watch и guard выпускаются до Work STT;60/180 секунд — целевые ориентиры для измерений S3.

## Что выполнено
- Продолжены существующие Gate736 / Ops399 / Ops404 / Gate766; новый implementation поток не создан.
- GitHub connector вернул identity markmalina/23397294 и admin/maintain/push/pull для Gate и Ops. Это права учётной записи, не доказательство всех OAuth scopes или доступа к secrets.
- Свежие main: Gate86dbc0f429dce089bf733fc422f20f556535895b; Opsc8add259924d9046794b37623599263c797ee520. PR404 и766 OPEN/unmerged; heads ниже неизменны.
- Скачаны59 source files из exact OpsF; каждый Git blob SHA проверен локально. Никаких изменений этих исходников не сделано.
- Воспроизведён существующий набор scope-diagnostic-v2: **28 PASS /0FAIL /0SKIP**. Включает routing всех четырёх purposes, spent/history, JSON types, owner/release default-off, native admission→synthetic provider→persistence→credential-free verification, STOP и UNKNOWN.
- Node24.22.1; локально установлены только тестовые зависимости ajv8.20.0,ajv-formats3.0.1,yaml2.9.0 с ignore-scripts. Первая попытка тестов не стартовала из-за отсутствия зависимостей; после установки28 тестов прошли. Это не полный локальный suite/CI и не live provider.
- Проверены фактические original CI41344396793 и Security41344396831: SUCCESS. Прочитан полный job124961006188,300886 символов: literal F checkout,2483 tests/pass,0fail. Остальные полные логи в этой S1-подготовке заново не перечитывались.
- Перечитан фактический [Delta425 ACCEPT](https://github.com/AlphaCenter/delta/issues/425#issuecomment-6563174356). Он касается DEFAULT_OFF_SOURCE и прямо исключает installation/operational/user acceptance.
- Повторно прочитан [исторический run41341920729](https://github.com/markmalina/gate/actions/runs/41341920729): attempt1,CANCELLED, headC; jobs API=[],artifacts API=[]. Новый запуск не создавался.
- Проверка существующих автоматизаций не обнаружила включённого задания с названием Gate/Delta/Center. Это не доказательство отсутствия всех внешних исполнителей или GitHub schedules.

## Доступы и блокеры
| Обязательная возможность | Наблюдение | Следствие |
|---|---|---|
| Репозиторий, refs, source, PR, issue metadata | Доступны; права Gate/Ops подтверждены | Исходники, пакет и связанные записи можно готовить |
| Control transport | В connector есть comment/label/refs/merge; source использует PR label | workflow_dispatch не требуется для этого маршрута; доступность конкретной write операции до исполнения не доказана |
| Full workflow history | Fetch двух вариантов /actions/workflows/403635446/runs отклонён connector INVALID_ARGUMENT400 | Это не GitHub404 и не billing failure. Нельзя заявлять полный свежий global inventory из этой среды; runner обязан получить его сам до provider |
| Protected environment metadata | /environments/gate-production отклонён connector INVALID_ARGUMENT400 | Наличие RAILWAY_TOKEN, approvals и текущая пригодность token остаются UNKNOWN до штатного protected runner |
| Jobs/artifacts/logs конкретного run | Доступны; реально прочитаны | Можно проверять результат после запуска; доступ к будущим ещё не существующим артефактам не утверждается |
| Actions/budget | Последние exact source CI завершены SUCCESS | Текущие минуты/лимит/готовность будущего runner неизвестны; прежний success не равен будущей доступности |
| Новый immutable owner record | Отсутствует;6562244412 SPENT | Нельзя подставить прежний grant или считать старт S1 согласованием конкретного Railway query |
| Binding/release/client | F default-off, fixturePENDING, reviewedExecutor=null; клиент ещёC | До label требуются реальные подготовленные pins, CI и независимое заключение по ним |
| Независимая проверка | Source425 ACCEPT существует. Binding/release/client/operation новых кандидатов ещё нет | Эти проверки НЕ ВЫПОЛНЕНЫ. Operational review заблокирована отсутствием real run/receipts; запрос reviewer не публиковался как будто это результат |

S1 нельзя обещать полностью завершить в текущей среде заранее: credential и hosted execution пока не наблюдены; при STOP/UNKNOWN спринт останется BLOCKED. Прямой Railway API, dashboard, новый credential или новый infra framework не используются.

## Пакет S1-20261102-scope-F
**Это предложение разрешения, не разрешение и не исполняемый запрос.**
Использует уже существующий [пакет736/6563197789](https://github.com/markmalina/gate/issues/736#issuecomment-6563197789) с уточнением технических повторов и конечных условий. Старый пакет и история не редактируются.

### Точные входные версии
| Часть | Версия |
|---|---|
| Gate main | `86dbc0f429dce089bf733fc422f20f556535895b` |
| Gate766/C, единственная база нового control client | `2b30b2b694093208dad6462b2bf5e787be784f13` |
| Watch application evidence, не разрешение выпуска | `ddc509c733c9b4750f717518cca7a92bffb5982e` |
| Ops main/LKG control source | `c8add259924d9046794b37623599263c797ee520` |
| Ops404/F, принятый source | `b2c34489d32025218c3879162d148ef3578a01f8` |
| F tree | `e02b9dbf18c072013cf713c2dade375eafdf5fdd` |
| Profile SHA256 | `ad6cd934a0116956817bcfad1bf9101cebb6ea85f4f25d962c4eac29899df3df` |
| Runtime bundle SHA256 | `4014c644f011404bb89d2a3c7191c727a3dbcb40ccf19eaf4380695f056ec364` |
| Fixed query SHA256 | `855aa1af93916e6d3b5f9c5946d59fc7fff5bf3fae64cb9e7bfec8a8488d9183` |

Финальные SHA binding/release/client пока **не существуют**: fixture включает ID/время/hash будущего действительного согласия; release включает будущий независимый verdict. Их нельзя выдумать или заменить F. Пакет ограничивает ниже допустимые производные изменения; перед запуском все производные SHA должны быть реально получены, проверены и записаны. Никакой непроверенный placeholder в workflow не допускается. Если требуется менять исполняемую логику F вне указанных control routing изменений, этот пакет не переносится на такой новый source.

### Target
- targetId и protected environment: `gate-production`.
- Railway project: `972b6ab7-988a-421c-abfa-425451ad714f`.
- Environment: `a148b138-44b3-4349-9d4b-292880503055`.
- Service context: `099cbf8c-1d94-43fe-8714-90725bf93998`; service не читается этим query.
- Gate repo1293746267, owner23397294; issue736,PR766.
- Workflow403635446: `.github/workflows/ops-watch-release-preflight-v2.yml`.

### Предлагаемое разрешение и действия
1. Правдиво сохранить новое согласие через connected-agent transport в Gate736. Не утверждать, что владелец лично ввёл GitHub command; прежние grants не оживлять.
2. В существующем Ops399/404 подготовить binding от F: новая owner fixture, производный bundle digest и необходимые tests/docs; executable collector/query/history/caps не менять. Затем отдельный read-only release record с реальным независимым verdict. Пройти необходимые exact CI/reviews и установить проверенный source с fresh expected-main, без force и без потери чужих файлов. Не создавать новый инфраструктурный механизм.
3. В существующем Gate766 подготовить один sole-parent child C, изменяющий ровно5 control files:
   - `.github/workflows/ops-watch-release-preflight-v2.yml`;
   - `agent-ops.watch-release-preflight-v2.lock.json`;
   - `.github/workflows/ops-watch-release-preflight.yml`;
   - `.github/workflows/ops-watch-scope-diagnostic.yml`;
   - `.github/workflows/ops-cli-state-observer.yml`.
   Вставить literal installed executor SHA и проверенные digests; сохранить985 прочих blobs. Не менять app/migrations. Exact client CI/Quest и независимая приёмка обязательны.
4. При неизменных версиях, свободной lease, действующем15-минутном request и допустимой полной истории — один новый original run посредством существующего request+label.
5. Выполнить максимум **один Railway POST суммарно**: фиксированный query projectToken scope + environment.unmergedChangesCount. Таймаут10s,response≤65536bytes,collector≤15s; jobs≤3min; native GitHub GET≤28. Никаких автоматических HTTP retries.
6. Получить3 safe artifacts: observation JSON,detached SHA256,verification JSON; retention3days. Проверить реальные run/jobs/artifact IDs/provenance/raw digests и credential-free verifier. Завершить независимую operational проверку фактическим заключением. Только после этого оценить DONE/BLOCKED S1.

### Не более двух технических повторов
**Предлагается совместимый с существующим кодом предел:**
- максимум2 технических повтора отправки управляющего действия только при положительном доказательстве, что предыдущая попытка отвергнута ДО создания original run и ДО внешнего действия;
- например, локальная валидация инструмента отказала до отправки. Наличие таймаута, отсутствующего ответа, пустого списка artifacts или UNKNOWN не является таким доказательством;
- изменённая причина отказа должна быть установлена до повтора; версии,target и scope сохраняются; fresh request может требовать обновления времени;
- вся цепочка всё равно создаёт максимум1 original и максимум1 provider POST;
- после создания original — **никаких rerun/relabel/rearm**, даже если он cancelled до jobs. Это действующее ограничение `assertHistory`, требующего ровно2 исторических +1 текущий run;
- если владелец подразумевает2 дополнительных hosted original после доказанного zero-provider исхода, текущая реализация этого НЕ поддерживает. Потребуется отдельная узкая source/review поправка к history/client/admission/receipts. Она не выполнена и не маскируется инструкцией. Не разрабатывать общий retry controller в S1.

Исторические41318810046 и41341920729 сохраняются. Любой неизвестный исход требует выяснения фактического состояния до дальнейших действий.

### Ожидаемый результат и предел доказательства
Новый original реально дошёл до jobs; наблюдение содержит scopeMatches=true,countObservation=ZERO,pendingChanges=false; verifier выдаёт VERIFIED_SCOPE_ONLY с releaseAuthorized=false; независимый reviewer принимает именно реальный результат.
VERIFIED_STOP_ONLY/UNKNOWN/нет jobs не закрывают S1. Scope-query не проверяет backup, schema, fleet, write access, фактические app flags или пользовательскую функцию.

### Срок и возврат
Предлагаемый срок согласия —24часа с момента его неизменяемой фиксации; запрос запуска —15минут. Ранее срока прекращают допуск drift,target mismatch,active competing run,STOP/UNKNOWN или превышение лимита.
До триггера возврат — оставить default-off и не создавать request/label. После попытки — не выполнять новые управляющие действия, сохранить историю/receipts. При дефекте установленного control client возможен отдельный проверенный revert только этих control changes к C/LKG, не force; старые spent grants при этом остаются spent. App/БД/worker не менялись, откат приложения/данных в этом пакете отсутствует.

### Независимая проверка
Existing425 принимается только для exact F. Binding/release/client требуют фактических отдельных заключений по своим immutable candidates согласно действующим правилам. Independent operational verdict требует real run и artifacts. Публикация issue, приглашение reviewer или синтетический28PASS не заменяют заключения. При невозможности получить reviewer execution/результат — явный BLOCKED, не self-acceptance.

## Готов ли путь к S2
**Нет, пока не подтверждён.**
Известен существующий deployment route: Gate `.github/workflows/ops-deploy.yml`, issue751, action `markmalina/ops/agent-ops/actions/gate-deploy@512decbbd81d1d824563af995fc0df435fbf2429`.
Он разрешает только exact current main и вызывает serviceInstanceDeployV2; код этой action сам не создаёт backup.
Watch candidate railway.toml содержит preDeploy `alembic upgrade head`, default driver actions; значит будущий выпуск затронет миграции0116–0119.
Пока нет нового scope/pending receipt, свежего связанного backup, фактической schema/fleet/config проверки и допуска выпуска. Возможность получить обязательный backup через существующий разрешённый механизм ещё не доказана в S1; один deploy workflow её не доказывает. Новую backup capability самостоятельно не разрабатывать.
Ожидаемый S2 checklist: actual main candidate / target / migration0119 / actions-only и early+Work off / exclusive writer / bound backup+restore evidence / binary compatibility and safe rollback. Значения runtime остаются UNKNOWN до фактических проверок.
Даже успешное scope-наблюдение не разрешает запуск S2 автоматически; по текущему поручению после отчёта S1 — остановка.

## Что установлено сейчас
Ничего нового в Ops main, Gate main или Production не установлено. Исходники F лишь локально проверены повторно; установленным остаётся OpsR, клиентC в открытом766. Новых provider/STT/DB/config/app effects не было.
Единственная запись этого этапа — данный пакет и фактический статус в существующей736; не итог успешного спринта.

## Основание отдельного разрешения
[Use Agent Ops SKILL.md](https://github.com/markmalina/ops/blob/b2c34489d32025218c3879162d148ef3578a01f8/agent-ops/skills/use-agent-ops/SKILL.md): «Read-only still requires target, caller, lock, and policy checks».
[Gate AGENTS.md](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/AGENTS.md) сохраняет exact owner authorization для credential scope.
Исполняемый [admission.mjs](https://github.com/markmalina/ops/blob/b2c34489d32025218c3879162d148ef3578a01f8/agent-ops/actions/gate-watch-scope-diagnostic-v2/admission.mjs) требует immutable approval fixture,spent-denylist,exact owner request; [release.json](https://github.com/markmalina/ops/blob/b2c34489d32025218c3879162d148ef3578a01f8/agent-ops/actions/gate-watch-scope-diagnostic-v2/release.json) остаётсяOFF.
Поручение «начинай S1» принято как разрешение работы; оно не было ложно записано как согласие с ещё не показанным exact query/versions/target/budget.

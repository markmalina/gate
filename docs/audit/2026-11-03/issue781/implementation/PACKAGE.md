# Gate781 — единый пакет S1, без запуска Railway

Дата: 2026-11-03. Product: https://github.com/markmalina/gate/issues/781.
Принятый #780 используется как предыдущий результат; его аудит не повторялся.
Production, Mark driver и S2 не изменяются и не запускаются.

## Закреплённая реализация

| Компонент | Immutable candidate |
|---|---|
| Gate runtime, локальные PG/контейнерные проверки | `45ba499a5f1852208ba8a478a606358fd0fe3bd5`, PR766 |
| Ops native boundary, controller, admission, verifier, cleanup, IaC | `0c8fc49b2e9d973d5f7e58289726e54e5f6ec2e4`, PR414 |
| Принятый Gate #780 | `897b2f1b882326cdc77777f4c600d8087cfbcdb8` |
| Принятый Ops #780 | `0079fc2f3f5048cb54fc0907364945779d89a6ca` |
| PostgreSQL base | `postgres:17@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1` |
| Python base | `python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28` |
| Official Railway IaC SDK | `railway@3.7.0`, существующий dependency lock Ops |
| Official schema / CLI source | `railwayapp/cli@dc804165e0cc976036d0f4f328cd564033bdc3b5` (CLI5.64.2), schema blob `3f3c4ca20d99c07417db70882caedf47196308f9` |

Из Watch0119 выделены общие `freezeWatchBoundary`, `nativeLockedBackup`, `nativeRequest`.
Fixture действительно вызывает эти функции и те же native stop/backup/lock documents.
Production wrapper сохраняет прежний fixed target; restore/delete доступны только отдельному fixture controller.
Production completion не объявляется усиленным: новые process witnesses принадлежат инструментированному fixture.

Реализовано:

- Полный capture двух writer instances, независимые provider log tags, cgroup v2 CPU/RAM.
  Каждый writer запускает два реальных дочерних процесса; барьер завершения — `waitpid`,
  включая delayed shutdown и SIGKILL fallback. Потеря heartbeat не считается доказательством.
- Повторный пустой fleet непосредственно перед backup; PG остаётся точно исходным deployment.
  Проверяются проект с единственным environment, два разрешённых сервиса, source SHA,
  start/Dockerfile/config fingerprints, отсутствие cron/predeploy/autodeploy/pending changes.
- Native backup → workflow Complete → lock → `expiresAt=null` readback.
- Остановка PG → native restore на новый том → строгая проверка mount-only staged patch
  и повторное чтение перед commit → запуск PG → read-only verifier → cleanup.
- Реальные Watch tables из текущих ORM definitions: original bytes/hash, cursor,
  processing/sending/unknown, request counters и text receipt journal. Это не новый аудит
  миграций #780. Незавершённые транзакции откатываются; verifier ничего не отправляет провайдерам.
- До любого seed PostgreSQL обязан вернуть `cluster_name`, вычисленный из nonce и PG service ID.
  Чужая DB отвергается до DDL/INSERT. Нет LLM, Telegram, Dropbox credentials, webhook или HTTP API.
- Одноразовый owner grant, durable claim в Ops414 вне восстанавливаемой DB,
  fsync journal каждой mutation, запрет implicit retry при UNKNOWN.
  Безопасные acknowledged workflow/resource IDs сохраняются и при последующем timeout.
- Отдельный credential-free verifier перепроверяет native transcript, scope, caps,
  lock readback, оба source SHA, parent/mount томов, config fingerprints и порядок
  stop → terminal → witness → backup, workflow completion → зависимое действие.
  Token echo блокируется до artifact/ACK; failure не публикует raw ошибку провайдера.
- Удаляются оба backup, оба тома и оба fixture service. `DELETION_QUEUED` отделён от
  authenticated absence; физическое окончательное удаление не утверждается.

Все UUID с префиксом `00000000-` в тестах — **SYNTHETIC_ONLY**, не реальные Railway ресурсы.
Live manifest их отвергает. `release/gate781.json` остаётся `enabled:false`.

## Что существует и какой механизм доступен

Подтверждено прежними GitHub metadata receipts: в `markmalina/ops` существует Environment
`agent-ops-disposable-backup-fixture`. Это не доказательство Railway проекта или безопасного
secret binding. В Gate соответствующий Environment не найден. Эти receipts не переаудировались.

Физическое Railway test environment **не установлено**: пользователь прямо запретил Railway
calls до отдельного допуска. Существование production Railway известно из принятого контекста,
но его доступ, credentials и ресурсы здесь не проверялись.

Старый установленный disposable workflow имеет прежний repo/CLI contract и не подходит
для этого Watch0119 сценария. Предлагаемый новый native workflow уже подготовлен в PR414;
он пока не установлен на default branch и не запускался. Произвольный Railway MCP/CLI
из агентской сессии не используется.

Штатное создание ресурсов описано готовой IaC-конфигурацией:
`fixtures/gate781/.railway/railway.ts` и `environment-plan.mjs`. Она компилируется настоящим
закреплённым Railway SDK в CI без provider access. `bootstrap-binding.json=null` означает
отсутствие реальной привязки, а не отсутствие реализации сценария.

## Один выбранный вариант окружения

Новый отдельный проект в существующем платном workspace владельца:

| Ресурс | Конкретное назначение и ограничения |
|---|---|
| Project | `gate781-s1-disposable`; никаких production services, forks или shared variables |
| Environment | единственный `s1-disposable`; пустой новый environment, не clone production |
| Region | `europe-west4`, один регион |
| `gate781-writer` | Gate exact SHA выше, `Dockerfile.gate781-writer`, 2 replicas; затем тот же service становится 1 read-only verifier replica |
| `gate781-pg` | тот же Gate SHA, `Dockerfile.gate781-postgres`, 1 replica, PostgreSQL17 |
| `gate781-data` | 1024MiB, `/var/lib/postgresql/data`; PGDATA — подкаталог `pgdata` |
| Restore volume | один дополнительный provider-created volume, максимум 1024MiB; исходный сохраняется до успешной проверки |
| Capacity | каждая replica ≤1 vCPU и ≤512MiB RAM; одновременно не более 3 replicas; NEVER restart, retries0, draining15s, overlap0 |
| Данные | только фиксированный synthetic набор; observed volume usage ≤256MiB; ожидаемый data digest `sha256:53355133c4e16e2f4d140ce2c3d5f9bb8b55e11b2730ef07ae0261beda527085` |
| Сеть | private PG DNS/reference, без public/custom domain, TCP proxy и внешних product credentials |
| Время | общий runtime TTL120min от создания binding; GitHub scenario job ≤25min; PG shutdown allowance ≤15s |

Сервисы строятся непосредственно из pinned Gate SHA. Публиковать GHCR images не требуется.
DB password генерируется заново 32 random bytes на защищённом исполнителе и передаётся как
Railway variable/reference; не помещается в git, чат или receipt. Код IaC не использует
детерминированный `ctx.randomString` для password.

Создание имеет отдельную границу допуска: **один reviewed IaC plan/apply, ровно 2 service
и 1 volume, 0 destroy/update посторонних ресурсов**. На новом проекте перед native испытанием
auto-deploy обеих услуг должен быть отключён и проверен. Нельзя незаметно увеличивать том
до стандартных 5/50GB: если 1024MiB intent не поддержан планом/провайдером, остановиться.

## Привязка доступа и разделение действий

Владельцу не нужно проектировать сервисы, лимиты, DB, restore или cleanup: всё выше и IaC уже заданы.

1. **Марк:** после отдельного допуска выбрать существующий workspace с правом создания проекта;
   создать пустой `gate781-s1-disposable` с единственным `s1-disposable` и выдать новый
   environment-scoped Project Token. Это account-level bootstrap, недоступный environment token.
   Выбор workspace/плана и выпуск credential остаются действием владельца.
2. **Марк:** сохранить новый token только как Environment secret
   `GATE781_RAILWAY_PROJECT_TOKEN_V1` в существующем Ops Environment. Не использовать старый
   `RAILWAY_TOKEN`. Установить protection approval и ограничение main; подтвердить, что у нового
   имени нет repo/org fallback. Значение token не сообщать. Нужна квитанция создания новой
   привязки и URL нового проекта, а не повторная проверка прежней авторизации.
3. **Исполнитель:** по этой новой привязке получить реальные immutable IDs, заполнить prepared
   bootstrap binding, сгенерировать password и точный IaC plan; owner применяет готовый plan
   штатным Railway IaC (`config apply --plan …`) в рамках отдельного допуска создания.
   Эта owner операция нужна потому, что существующий установленный Ops маршрут не создаёт
   новую fixture; сам native workflow не притворяется bootstrap executor.
   Plan не требует от владельца выбора архитектуры или редактирования service settings.
4. **Исполнитель:** после создания получить полный readback services/deployments/volume,
   отключённый auto-deploy, exact SHA, cgroup witnesses; сформировать реальный native manifest,
   три config fingerprints и durable resource ledger. При scope/cap/source mismatch — остановка.
5. **Исполнитель:** установить только новый fixture workflow и release binding на Ops default
   branch в отдельном reviewed install change. Не сливать весь production S1 PR ради установки.
   Runtime checkout остаётся immutable Ops SHA выше. Это внешнее изменение также входит в
   запрашиваемый отдельный допуск, сейчас не выполнено.
6. **Марк:** только когда разрешено именно испытание, свежий digest-bound grant на Ops414
   для опубликованного реального release manifest. Сейчас такого grant не запрашивали и не использовали.
   Executor запускает один workflow; same-run artifact проверяет отдельный job без Railway secret.

Итого owner действия: account/credential bootstrap, применение уже спроектированного IaC plan,
затем отдельный допуск реального испытания. Исходники и native implementation от этих действий
не зависят. Наличие/создание проекта нельзя доказывать synthetic UUID.

## Native действия, бюджет и остановка

Успешная последовательность — 15 GraphQL mutations:
writer stop; backup create; backup lock; исходный PG stop; restore; commit mount patch;
configure verifier; deploy verifier; восстановленный PG stop; 2 backup delete;
2 volume delete; 2 service delete. Лимит transport — 16 mutations и 240 reads, без автоматических
повторов mutations. Один запрос ≤15s и response≤256KiB; poll≤24 попытки с интервалом5s.
GitHub grant/claim — максимум8 HTTP requests, из них один POST claim. LLM/Telegram/Dropbox calls — 0; фактические Railway calls в этой подготовке — 0.

UNKNOWN после отправленного effect прекращает дальнейшие mutations. Его не исправляют rerun
того же grant. Known safe IDs остаются в fsync journal. Процессы самостоятельно завершаются
по TTL независимо от доступности GitHub runner. На failure сохранённые backup/volumes не удаляются
вслепую: новый scoped reconciliation read устанавливает исход уже отправленной операции,
а cleanup получает отдельное явное разрешение на конкретные подтверждённые ресурсы.

IaC creation — отдельная owner операция с одним apply и фиксированным resource plan;
внутренний HTTP счётчик официального CLI здесь не выдаётся за ограниченный нашим native transport.
До её допуска нужно принять именно эту разницу; blanket unlimited Railway разрешения пакет не содержит.

## Расходы, восстановление и очистка

Оценка при максимальной нагрузке3replicas ×(1vCPU+512MiB) ×120min: примерно $0.21 compute;
2GiB томов с запасом48h на provider deletion — около $0.02. Синтетические backup малы;
ожидаемая добавочная стоимость сценария менее $0.30 при существующем paid workspace.
Предлагаемый owner stop-loss — **$1 добавочных расходов**, не гарантия provider billing cap.
Денежный порог не заменяет фактические CPU/RAM/TTL и request caps.

Новая подписка не приобретается автоматически. Hobby $5/month и Pro $20/month — отдельные
минимальные обязательства, если существующий workspace не подходит; тогда bootstrap останавливается.
Service builds по текущему тарифу бесплатны. Источники:
https://docs.railway.com/pricing/plans ; https://docs.railway.com/pricing/understanding-your-bill .

Успешный cleanup удаляет original+copied backup, запрашивает удаление обоих томов и двух services,
перечитывает environment. Pending deletion честно остаётся `DELETION_QUEUED`, не `PERMANENTLY_DELETED`.
После независимого результата владелец отзывает временный token и удаляет пустой новый проект;
GitHub proof artifact живёт7days, существенные receipts архивируются в Gate.

Восстановление — только новый fixture volume. Production backup/restore не предусмотрены.
До успешного content verifier исходный том и locked backup сохраняются. При ошибке restore
не запускается старый writer и не снимаются durable processing/sending/unknown claims.

## Точные внешние зависимости

| Нужный факт | Что от него зависит; что уже сделано независимо |
|---|---|
| Workspace entitlement, регион и право создать новый проект | Можно ли применить фиксированный 1GiB/3replica plan без новой подписки. IaC и caps уже реализованы и тестируются offline. |
| Реальные project/environment IDs и freshly scoped token | Допуск первых provider reads, затем неизменяемый manifest; synthetic targets live запрещены. Core/controller/verifier от ID не заблокированы. |
| Новая Environment-secret provenance без inherited fallback | Допуск credential-bearing job; прежнюю авторизацию не перепроверяем. Новый grant + external claim уже реализованы. |
| GitHub source integration нового проекта читает pinned Gate SHA | Возможность двух Docker builds из repository. Dockerfiles и реальные local builds проверены. |
| Native service instance/log fields содержат полный replica inventory и tags | Возможность доказать all-observed-writer completion на Railway. Реальные ОС процессы уже проверены в CI; missing provider evidence даёт отказ. |
| Форма patch, фактически выдаваемая native restore | Применяется ли строгий mount-only контракт: old mountPath=null, new PG mount. Pinned CLI type допускает форму, но не доказывает emission. Любые дополнительные изменения — отказ до commit; runtime не расширяет allowlist. |
| Server effective caps, volume size, names/IDs copy backup, retained/deleted volumes | Native preflight, restore mapping и cleanup. Семантика и отрицательные сценарии реализованы; физических readback до допуска нет. |

Native commitStaged не имеет CAS-параметра в используемой схеме. Проверяется повторное чтение
точного patch перед commit; поэтому этот пакет требует эксклюзивного одноразового environment
без параллельных operators/autodeploy. Это не обещание устранённой provider-side TOCTOU.

## Приёмка

Offline tests/CI/review подтверждают подготовленную реализацию и её отказы; они не являются
`ACCEPT_NATIVE_FIXTURE`, реальным Railway backup или завершением S1. Финальные run/review URLs
и архив raw receipts записываются вместе с этим пакетом в #781. До отдельного допуска — Railway calls0.


Финальные проверки и raw receipts: [EVIDENCE.md](EVIDENCE.md), [provenance.json](provenance.json).
Независимый ограниченный functional review: https://github.com/AlphaCenter/delta/issues/451 .
Это один пакет решения; исходный inventory-only отчёт сохранён отдельно и не заменяет текущую реализацию.

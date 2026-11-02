# Независимая проверка авторизации S1

Статус: **BLOCKED — источник отказа credential не установлен**. Проверка исходников и публичной официальной документации; не runtime acceptance и не разрешение выпуска. Новых подтверждённых дефектов диагностического клиента не найдено.

## Объём и пределы

- Railway API calls: **0**.
- CI executions/reruns: **0**.
- Credential reads, secret API calls, provisioning, configuration changes, deploy/restart/migration: **0**.
- Удалённые изменения и внешние сообщения: **0**.
- Проверены точный Gate client `6fc78006c5b209dc93ca2904ae349d6401eb922f`, установленный Ops `ec4e689a0fa24305672ef4047ff7c5fac0666982` и архив Gate `82260be000abeda8176bacda944fad7748e365f5`.
- Официальная CLI schema прочитана из `railwayapp/cli` commit `b62467ef1b5303e3e340283ed96425dcf9612993`; это сохранённая официальная схема, не live introspection.
- Актуальная официальная документация Railway и GitHub прочитана 2 ноября 2026. Проверка не удостоверяет текущее состояние провайдера или значения защищённых секретов.

## Проверенные исходники и цепочка credential

1. [Gate workflow](https://github.com/markmalina/gate/blob/6fc78006c5b209dc93ca2904ae349d6401eb922f/.github/workflows/ops-watch-release-preflight-v2.yml#L44), строка44: `environment: gate-production`; строка56: `${{ secrets.RAILWAY_TOKEN }}` передаётся как `RAILWAY_PROJECT_TOKEN`. Вызов закреплён на Ops `ec4e689a0fa24305672ef4047ff7c5fac0666982`.
2. [Ops entry](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-scope-diagnostic-v2/entry.mjs#L25), строка25: `env.RAILWAY_PROJECT_TOKEN` передаётся collector.
3. [Collector](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-scope-diagnostic-v2/collector.mjs#L9), строки9–11: фиксированный environment query. Строка27 требует строку20–512 символов без whitespace. Строки46–48 отправляют один POST на `https://backboard.railway.com/graphql/v2`, заголовок `Project-Access-Token`, фиксированные project/environment из профиля. Встроенный latch запрещает повтор; service ID этим query не наблюдается.
4. [Response classifier](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-scope-diagnostic-v2/response-diagnostic.mjs#L11) выдаёт `AUTHORIZATION_DENIED` только при точном равенстве сообщения `Not Authorized`; произвольные сообщения остаются `UNCLASSIFIED`.
5. [Network reader](https://github.com/markmalina/ops/blob/ec4e689a0fa24305672ef4047ff7c5fac0666982/agent-ops/actions/gate-watch-release-preflight-v2/network.mjs) принимает HTTP200, ограниченный размер, валидные UTF-8 и JSON; запрещает redirect. Поэтому последняя GraphQL-ошибка не является свидетельством HTTP500.

Source target:

| Ресурс | Закреплённый ID |
|---|---|
| Project | `972b6ab7-988a-421c-abfa-425451ad714f` |
| Environment | `a148b138-44b3-4349-9d4b-292880503055` |
| Gate service | `099cbf8c-1d94-43fe-8714-90725bf93998` |

Точные profile/query/archive согласованы. Это не заменяет подтверждения актуальной принадлежности ресурсов у Railway. Service ID не входит в последний query и не был им проверен.

## Сопоставление с официальным контрактом

Официальная [Railway Public API documentation](https://docs.railway.com/integrations/api) определяет project token как доступ к одному environment одного project. Он использует `Project-Access-Token`; account/workspace/OAuth используют `Authorization: Bearer`. Документированный identity-query — `projectToken { projectId environmentId }`. HTTP200 с `INTERNAL_SERVER_ERROR` и `Not Authorized` означает отказ авторизации; синтаксические/validation ошибки описаны отдельно как HTTP400. Следовательно, контракт клиента соответствует project-token API, но фактический тип секрета этим не удостоверен.

Самостоятельно прочитана [официальная CLI schema](https://github.com/railwayapp/cli/blob/b62467ef1b5303e3e340283ed96425dcf9612993/src/gql/schema.json). В ней существуют:

- `Query.environment(id: String!, projectId: String): Environment!`;
- `Query.projectToken: ProjectToken!`;
- `Environment.id`, `Environment.projectId`, `Environment.unmergedChangesCount`;
- `ProjectToken.projectId`, `ProjectToken.environmentId`.

Эти поля не deprecated. [Официальный CLI ProjectToken query](https://github.com/railwayapp/cli/blob/b62467ef1b5303e3e340283ed96425dcf9612993/src/gql/queries/strings/ProjectToken.graphql) также использует nested project/environment. Доказательства дефекта selections или необходимости их переустановки не получено.

Существенное уточнение: workflow `environment: gate-production` **не доказывает environment-level происхождение значения**. Официальная [GitHub secrets reference](https://docs.github.com/en/enterprise-cloud%40latest/actions/reference/security/secrets) задаёт приоритет одноимённых секретов environment → repository → organization. Repo/org secrets читаются при постановке workflow в очередь, environment secrets — при старте job. Поэтому source доказывает environment context и mapping `secrets.RAILWAY_TOKEN`, но не уровень фактически выбранного значения. Тип не следует и из имени `RAILWAY_TOKEN`.

## Сохранённые доказательства и различение гипотез

Прочитаны [debug packet](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/S1-RAILWAY-DEBUG-PACKET.md), [scalar query](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/evidence/s1-continuation/scalar-query.json), [environment query](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/evidence/s1-continuation/environment-query.json) и [provenance последнего original41449717673](https://github.com/markmalina/gate/blob/82260be000abeda8176bacda944fad7748e365f5/docs/audit/2026-11-02/evidence/s1-continuation/run-41449717673/provenance.json).

Scalar query включал одновременно `projectToken { projectId environmentId }` и environment; сохранённый отказ локализован на `PROJECT_TOKEN`. Последний environment-only запрос дал `ENVIRONMENT/AUTHORIZATION_DENIED` при HTTP200. Сырой message/traceId архивом не сохранялся; классификация точной строки подтверждается кодом. Принятые artifact/CI проверки не повторялись.

| Гипотеза | Вывод |
|---|---|
| Пустой secret или whitespace | Исключено для последнего POST: предварительный guard завершил бы выполнение с `E_CREDENTIAL`. |
| Неверный endpoint/header для project token | Не подтверждается: совпадают с официальным контрактом. |
| В effective secret лежит account/workspace token | Возможно: фактический тип не удостоверен. Менять header наугад нельзя. |
| Project token отозван либо не распознаётся Railway | Возможно; один `Not Authorized` не доказывает invalid credential. |
| Project token другого project/environment | Возможно: token identity не получена. |
| Неверный target | Source и архив совпадают; актуальная принадлежность провайдером не подтверждена. |
| Недостаточные права или ограничение Railway | Возможно; нужны сведения token/account или серверная корреляция. |
| Ошибка GraphQL selections | Проверенная официальная schema допускает использованный query; дефект не установлен. |
| Транспортная ошибка или HTTP500 | Последняя попытка дошла до HTTP200/JSON/GraphQL denial. |
| Старый успешный observer доказывает тот же secret | Нет: отсутствует доказательство неизменной версии effective secret. |

## Точно недостающее non-secret подтверждение

Авторизованный оператор должен проверить и сообщить без значения credential:

1. Railway token record: тип, действующий/отозванный статус, project `972b6ab7-988a-421c-abfa-425451ad714f`, environment `a148b138-44b3-4349-9d4b-292880503055`.
2. Связь именно этого record с **effective** `RAILWAY_TOKEN`, использованным job: уровень secret, время установки/изменения, безопасная операторская provenance последней записи. Одна metadata `updated_at` не удостоверяет содержимое.
3. Если тип/scope/связь корректны — корреляцию Railway server logs по exact query/target и интервалу последнего запроса `2026-11-02T13:04:18.729–13:04:18.880Z`. Trace ID отсутствует; его нельзя придумывать.

Для этих подтверждений не нужно выводить, читать обратно, публиковать или хешировать значение секрета. Замена credential, расширение прав и изменение конфигурации требуют отдельного конкретного решения; здесь не выполнены и не предлагаются как доказанно необходимые.

## Новые диагностические чтения

При текущих данных новое чтение не рекомендовано: основание осталось прежним, повтор отказа не различит гипотезы. Изолированный `projectToken` без environment уменьшает документ, но предыдущая ошибка уже локализована на самом `projectToken`; убедительного нового основания для расходования допуска пока нет.

После появления новой non-secret provenance применим первый условный запрос:

```graphql
query GateWatchCredentialIdentity {
  projectToken {
    projectId
    environmentId
  }
}
```

Тот же endpoint/header/защищённый credential; один POST, ноль retry. Сравнить оба ID с exact target в защищённой памяти, сохранять лишь match/mismatch и безопасную категорию ошибки. При подтверждённом совпадении отдельный следующий read может проверить точный environment. При несовпадении либо повторном denial остановиться, не расширять target, не подбирать другой header/credential. Это условный маршрут для нового допуска, не возобновление старых3/3, не выполненный запрос и не готовый новый установленный executor.

## Итог независимой проверки

Подтверждён отказ авторизации environment, а не недействительность credential. Контракт клиента соответствует project-token авторизации и официальной schema; новых подтверждённых source дефектов нет. Не хватает доказательства типа/состояния/scope и injection provenance effective credential. **S1 не готов к закрытию.** Готовность backup → Watch0119 → проверка выпуска этим auth review не удостоверяется и требует отдельного source/route заключения. S2 не запускался.

# Railway S1: пакет для выяснения серверного отказа

Статус: диагностические доказательства; обращение в support не отправлено.
Не является разрешением на provider queries, token rotation, service configuration,
deploy, migration или restart. Значение credential не читать и не прикладывать.

## Фиксированный контекст

- Endpoint: `https://backboard.railway.com/graphql/v2`, GraphQL POST query.
- Auth header name: `Project-Access-Token`; credential из существующей защищённой
  GitHub environment `gate-production`, secret `RAILWAY_TOKEN` (значение не извлекалось).
- Project: `972b6ab7-988a-421c-abfa-425451ad714f`.
- Environment: `a148b138-44b3-4349-9d4b-292880503055`.
- Закреплённый Gate service: `099cbf8c-1d94-43fe-8714-90725bf93998`.
  Диагностические query не наблюдают service/DB/runtime identity.

## Уже выполненные чтения

| Попытка | UTC | Запрос | Фактический ответ |
|---|---|---|---|
| Старая, отдельно spent41425190543 | 2026-11-02 около10:00Z | nested projectToken + environment | GraphQL INTERNAL_SERVER_ERROR; исходная проекция не сохраняла path/message category. |
| Новая1,41437551667 | 2026-11-02T11:32:34.010–11:32:34.290Z | [nested](evidence/s1-continuation/nested-query.json) | HTTP200, data:null, INTERNAL_SERVER_ERROR, PROJECT_TOKEN, UNCLASSIFIED. |
| Новая2,41440937308 | 2026-11-02T11:59:08.802–11:59:08.945Z | [scalar token](evidence/s1-continuation/scalar-query.json) | Тот же закрытый тип ошибки. |
| Новая3,41449717673 | 2026-11-02T13:04:18.729–13:04:18.880Z | [environment-only](evidence/s1-continuation/environment-query.json) | HTTP200,data:null,INTERNAL_SERVER_ERROR,ENVIRONMENT,AUTHORIZATION_DENIED: точное Not Authorized. |

**Бюджет3/3 исчерпан; VERIFIED_STOP_ONLY, facts:null.**

Каждое новое чтение — один POST, ноль retry. Отсутствуют мутации и изменения
credential/target. Сырой ответ намеренно не сохранялся; сохранена только безопасная
проекция. UNCLASSIFIED означает, что сообщение не совпало с точной строкой
`Not Authorized`; это не доказательство отсутствия иной ошибки авторизации.
Trace ID в квитанциях отсутствует; его значение не выдумано.

## Отказ допуска, который не является Railway чтением

Original41444775597 (2026-11-02T12:26:56Z) завершился admission E_RESPONSE_SIZE
12:27:19.8036088Z. Observe/verify SKIPPED,0Railway requests,0artifacts; независимая444.
Исходный environment-only query тогда не был отправлен. Эта попытка workflow
сохраняется отдельно; она не повторялась и не добавляет provider read к итоговому расходу3/3.
Подтверждённый дефект вместимости GitHub history исправлен отдельно от Railway.

## Установленные факты и запрос к авторизованному оператору

Два query с `projectToken` дали ошибку на этом root, в том числе без nested полей.
Следовательно, наличие nested selections не является необходимым условием отказа.
Конкретная внутренняя причина Railway и token identity не установлены.

Необходимое действие: по существующим account/project-token сведениям и серверным
логам Railway сопоставить UTC/target/query последнего чтения и установить причину
подтверждённого ENVIRONMENT/Not Authorized: проверить effective authorization,
validity/scope/тип credential. Прежние PROJECT_TOKEN/UNCLASSIFIED остаются отдельной
неразрешённой историей и не объявляются тем же отказом без доказательства. Передавать
только IDs/query/timestamps/безопасные квитанции, без secret value.

Если устранение потребует смены credential или service configuration, сначала
подготовить конкретный diff/target/проверку/возврат и получить отдельное разрешение.
Слепой повтор, переоформление spent попыток и применение staged changes не предлагаются.

## Конкретный пакет восстановления доступа — подготовлен, не исполнен

1. Авторизованный Railway оператор проверяет тип, validity, project/environment scope и effective authorization существующего credential для exact target выше, сопоставляет серверные журналы с UTC последнего чтения. Значение секрета не публиковать и не извлекать через диагностику. Причины expired/wrong type/wrong scope пока гипотезы.
2. Только если проверка установит необходимость замены: отдельно согласовать замену **существующего GitHub environment gate-production secret RAILWAY_TOKEN** корректным project token для того же target. Без расширения до account PAT, смены target/header наугад или изменения service variables/config. Действие в этом поручении не выполнялось.
3. До отзыва прежнего credential обеспечить операторский способ возврата; GitHub secret прочитать обратно нельзя. Не обещать автоматический rollback. При отсутствии контролируемого восстановления остановиться.
4. После исправления отдельное новое ограниченное разрешение на read-only проверку доступа: нынешние3/3 израсходованы. Связать проверку с exact installed SHA/query/target и требовать полную fixed-target/token-scope/pending-clean квитанцию. Частичное environment чтение не закрывает S1.

Отдельный пробел Watch0119 route описан в [S1-ROUTE-GAP](S1-ROUTE-GAP.md). Этот пакет не разрешает deploy, миграцию, restart, обработку аудио, отправку сообщений или S2.

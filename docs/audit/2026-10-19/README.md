# Gate: аудит и документация · 19 октября 2026

Это точка входа для человека и ИИ, который будет дорабатывать Gate. Пакет описывает **фактический source**, незавершённые изменения и ограничения доказательств. Он не выдаёт разрешения на новые внешние действия.

- Репозиторий: [markmalina/gate](https://github.com/markmalina/gate), private, ID 1293746267.
- Задание: [Gate #767](https://github.com/markmalina/gate/issues/767), прямое поручение Марка Малиновского.
- Проверенная основа: [86dbc0f429dce089bf733fc422f20f556535895b](https://github.com/markmalina/gate/commit/86dbc0f429dce089bf733fc422f20f556535895b), main от 15.10.2026 10:10:25 UTC.
- Git tree: `7b5ddc0e6317fc475e2256953a69a07b109463dc`.
- Срез GitHub/backlog: 19.10.2026; каждое наблюдение датировано в машинных файлах.
- Production не изменялся. Выполнено одно scoped read-only чтение Gate M status между 16:41:49Z и 2026-10-19T16:42:51.938Z; это не live Telegram/provider probe и не глобальная приёмка.

## Что это

Gate — сервер коммуникаций: принимает события внешних каналов, сохраняет факт получения, ограничивает доступ, даёт владельцу контекст/черновик и выполняет точно разрешённую доставку с квитанцией. Сейчас основной транспорт — Telegram; есть Dropbox voice/calls и доставка приватных watch-note транскриптов в Mark GitHub Inbox.

По назначению Gate — транспортная граница между каналом и интеллектом/продуктом. По фактическому коду это большой модульный монолит: в нём также остались Alpha/Beta, CRM, BECOME/program/quest, billing и исторический AQUB. Их наличие не делает Gate каноническим владельцем всех этих предметных областей.

## Короткий вывод

База транспорта и проверок развита: durable ingress/outbox, scoped OAuth, ручное подтверждение, журналы попыток, PostgreSQL concurrency tests, миграции, backup/restore drill и непривилегированные Docker images. Но наличие source и green CI нельзя превращать в обещание работающей возможности.

Наиболее важные разрывы:

1. **MCP composition:** 24 определения в source, но первый HTTP dispatcher обслуживает 19; пять transcription tools перекрыты другим POST /mcp.
2. **Отдельный Alfa STT:** не использует общий hard-budget admission перед provider I/O; его лимиты расходятся с закреплённым голосовым контуром.
3. **Worker lifecycle:** новый Alfa worker объявляет чужие running parts unknown; часть FFmpeg subprocess не имеет deadline/kill cleanup.
4. **Невыпущенные защиты:** snapshot guard, атомарный inbound stop и параллельные серии находятся в открытых PR.
5. **Интеграция:** открытые media/sequence PR создают разные Alembic ветви; независимый green каждой ветки не доказывает общий выпуск.
6. **Документация:** старые operational snapshots, eleven-tools и правила automatic deploy противоречат более новым source/policy.

Подробности, влияние и проверяемые критерии исправления: [04-FINDINGS.md](04-FINDINGS.md).

## Масштаб проверенной основы

| Показатель | Подтверждено |
| --- | ---: |
| Все файлы текущего дерева | 943 |
| Проверенные Git blob SHA и размеры | 943; 0 расхождений |
| Объём исходных байтов | 8 637 975 |
| Файлы app | 309 |
| Файлы tests | 325 |
| Файлы docs до этого пакета | 81 |
| ORM tables, статический индекс | 113 |
| Проиндексированные ORM mapped columns | 1 473 |
| Alembic revisions | 119 |
| Единственный source schema head | 0115_watch_voice_post_payment |
| Settings validation_alias | 161 |
| Открытые domain issues до собственного #767 | 34 |
| Открытые PR | 14 |
| Найденные ветки до audit branch | 616 |
| Находки | 16: 6 P1, 8 P2, 2 P3 |

Строки в inventory считаются как split по LF, включая завершающую пустую строку. Это размер кода, не его качество и не test coverage.

## Как читать

| Читатель / задача | Порядок |
| --- | --- |
| Марк / новый человек | Этот файл → [назначение и архитектура](01-PRODUCT-AND-ARCHITECTURE.md) → [находки](04-FINDINGS.md) → [остаток и план](05-BACKLOG-AND-PLAN.md) |
| Разработчик | Архитектура → [техническая спецификация](02-TECHNICAL-SPEC.md) → [разработка и эксплуатация](03-DEVELOPMENT-AND-OPERATIONS.md) → нужные source paths |
| ИИ перед первой правкой | [AI-START.md](AI-START.md), затем root AGENTS и текущий issue/PR/lease |
| API/MCP | [API-CATALOG.md](API-CATALOG.md) + [api-catalog.json](api-catalog.json) |
| База данных | [DATA-MODEL.md](DATA-MODEL.md) + [data-model.json](data-model.json) |
| Конфигурация | [CONFIGURATION.md](CONFIGURATION.md) + [config-catalog.json](config-catalog.json) |
| Проверка происхождения | [EVIDENCE-AND-COVERAGE.md](EVIDENCE-AND-COVERAGE.md), [source-inventory.json](source-inventory.json), [validation.json](validation.json) |

Машинные индексы — навигация по source, не новые runtime registries. `backlog.json` — датированный read model GitHub задач, а не второй task manager.

## Проверки и пределы

Для исходного main подтверждён [CI 38718360230](https://github.com/markmalina/gate/actions/runs/38718360230), включая literal checkout, Ruff, mypy по 309 source files, **2914 passed / 1 skipped / 12 subtests**, реальные PostgreSQL checks, Alembic/schema drift/restore/downgrade и web/contact Docker builds. Дата проверки кода — 15 октября, а не свежая runtime приёмка.

Аудит сверил байты всех 943 файлов и построил индексы, подробно исследовал критические entrypoints, SQL/provider/worker boundaries, документацию и открытый backlog. Это не построчное независимое ревью каждого из 8.6 MB и не pentest. Не выполнялись live Telegram/Dropbox/OpenAI вызовы, чтение переписки, credential/secret inventory, Production SQL, восстановление реальной backup или полная проверка Git history/CVE. Локальные Python/Docker проверки в этой среде недоступны; их выполнение не заявляется.

## Как поддерживать пакет

1. Перед изменением заново запросить main, relevant issues/PR и текущую authority.
2. После нового source audit создать новую датированную основу; этот снимок не переписывать как сегодняшнее состояние.
3. Новую возможность описывать отдельно по source / tests / runtime / client / owner acceptance.
4. Изменять claims только вместе с evidence; старые verdicts сохранять.
5. Не вставлять в документы secrets, raw media, сообщения, person identifiers или приватные значения настроек.

Этот пакет дополняет исторические документы. Приоритеты authority определяются предметом утверждения: текущий owner intent — для цели и разрешения, root AGENTS/current policy — для выполнения, source — для поведения, timestamped receipt — для наблюдённого runtime.

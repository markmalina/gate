# Gate: исследование состояния и причин незавершённой разработки
Дата исследования: 2026-11-02, прямое наблюдение Gate около 10:10 МСК.
Статус: завершённое исследование; не план реализации и не разрешение на выпуск.

## Область и метод
Исследованы текущие исходники main, состав открытых задач и PR, выбранная история коммитов, оригинальные результаты/логи CI, инструкции, последние комментарии ключевых потоков и прямой авторизованный gate_get_status. Прежний аудит использовался как указатель, существенные приведённые ниже положения перепроверены. Общего локального запуска тестов, реальных отправок, STT, изменения инфраструктуры или рабочих веток не было.
Владелец отдельно поручил сохранить результат в репозиторий 02.11.2026. Этот документ публикуется в отдельной архивной ветке без PR, merge и изменений задач. Это публикация исследования, не новая продуктовая реализация, lease или приёмка существующих исполнений.

## Заключение
Gate работает частично. Два нужных улучшения остановлены между проверкой исходников и выпуском пользователю. Подтверждены реальные дефекты, разрастание невыпущенных пакетов, сложный маршрут выпуска и незавершённые передачи между исполнителями. Требования к Watch и отказу от устаревших ответов достаточно определённы; доказательств, что первичная причина — непонятная постановка владельца, не найдено.

## Карта системы
| Часть | Назначение и места |
|---|---|
| Приём | Telegram webhook → durable receipt → обработка → сообщения/задачи: app/api/telegram_ingress.py, app/infrastructure/telegram_processing.py |
| Work | OAuth/MCP, контекст, черновики, документы, отправка и серии: app/api/mcp_routes.py (4395 строк в main) |
| Доставка | Проверка согласованного черновика и соединения, durable send attempt, sent/failed/unknown: app/infrastructure/reply_sends.py, app/worker/reply_sequences.py |
| Аудио | Отдельные пути Telegram voice, Dropbox calls, Watch и Alfa transcription; разные workers, очереди и ограничения |
| Хранение | PostgreSQL/SQLAlchemy/Alembic: сообщения, источники, оригиналы, тексты, попытки и журналы |
| Фоновая работа | Workers вместе с приложением и внешний Watch Actions driver |
| Продуктовые модули | Alpha/Beta/BECOME/CRM/billing находятся рядом с транспортом и расширяют регрессионную область |
| Выпуск | PR → проверки → main → закреплённый Gate-owned Ops executor → Railway/migrations → проверка версии → пользовательская приёмка |

Источники: [main.py](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py), [ops-deploy.yml](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.github/workflows/ops-deploy.yml), [railway.toml](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/railway.toml).
Изучение Ops отвечало на вопрос, почему проверенный код не становится сервисом; MCP — доступны ли возможности текущему Work.

## 1. Что работает
1. Прямой scoped gate_get_status успешно получен: production, manual, commit afe0c6b85193, OAuth enforced. Это наблюдение текущего сервиса, не глобальная проверка всех пользователей/процессов.
2. Main 86dbc0f429dce089bf733fc422f20f556535895b от 15 октября. [CI38718360230](https://github.com/markmalina/gate/actions/runs/38718360230) SUCCESS; статусы шагов тестов, миграций, backup/restore PostgreSQL и контейнеров проверены.
3. Live Watch: actions enabled, ordinary_worker_enabled=false, 1183 run claims; 67 transcribed/67 delivered; pending delivery=3; transcription received=1, unknown=1, rejected=1. Это сохранённые агрегаты; тексты всех карточек в этом исследовании не перечитывались.
4. [Watch CI41340272490](https://github.com/markmalina/gate/actions/runs/41340272490), exact 2b30b2b694093208dad6462b2bf5e787be784f13: оригинальный backend log содержит 3204 passed/1 skipped/12 subtests passed. [Reply CI40466472765](https://github.com/markmalina/gate/actions/runs/40466472765), exact b975475c1c6c81035e38d9bd13a40d777933e83c: 3311 passed/1 skipped/12 subtests passed. PG/schema/restore/build steps SUCCESS. Это источник/CI, не product acceptance.
5. Успешный пример полного выпуска — [#751](https://github.com/markmalina/gate/issues/751), отключение зависимости от AQUB. [Run38028594276](https://github.com/markmalina/gate/actions/runs/38028594276) SUCCESS, текущая версия сервиса соответствует тому выпуску. Историческая квитанция подтверждает ограниченный freeze outcome, не весь Gate.

## 2. Текущий разрыв
На срезе: 38 open issues / 16 open PR. Среди них документы, frozen/historical и настоящие продуктовые остатки; все open нельзя считать активными исполнениями.
Production afe0c6b8519381a0007c0dcc1c145207b5d54a3f отстаёт от main на 10 коммитов. Сравнение показывает изменения контрольного workflow, MTProto foundation, Docker/dependencies/docs/tests; новые Watch/guard в main отсутствуют.

| Поток | Состояние |
|---|---|
| [PR766](https://github.com/markmalina/gate/pull/766) | OPEN/unmerged, head2b30b2b694093208dad6462b2bf5e787be784f13; 34 коммита,68 файлов,+11033/-165 |
| [PR747](https://github.com/markmalina/gate/pull/747) | OPEN/unmerged, headb975475c1c6c81035e38d9bd13a40d777933e83c;35 коммитов,94 файла,+14432/-363 |
| [PR720](https://github.com/markmalina/gate/pull/720) | OPEN с27.09;29 коммитов,91 файл; атомарный inbound stop/parallel sequences не принят как выпущенный продукт |
| [PR768](https://github.com/markmalina/gate/pull/768) | Открытый документационный аудит; наличие документации не изменяет runtime |

### Watch #731/#736/#770
Требование: завершённая запись Galaxy Watch → телефон/Dropsync → Dropbox /Alpha/Voice → ранняя private Mark issue → полный текст в той же issue; координация Gate/Work и восстановление без дублей.
Подтверждено:
- В railway.toml текущей версии persistent worker принудительно выключен; live status это подтверждает.
- [app/worker/watch_voice.py:550](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/watch_voice.py#L550): публикация выбирает только transcribed notes. Пока STT не завершена, требуемой ранней карточки нет.
- Три pending записи имеют разные состояния; причина каждой по агрегату неизвестна.
- Новые claim/start/submit отсутствуют в текущем подключённом каталоге. [Контракт кандидата](https://github.com/markmalina/gate/blob/2b30b2b694093208dad6462b2bf5e787be784f13/docs/watch-voice-work-mcp.md) признаёт отсутствие самостоятельного Work STT. Координационный адаптер не является распознавателем.
- [Последний run41341920729](https://github.com/markmalina/gate/actions/runs/41341920729) 01.11.2026 19:28:41–42Z CANCELLED; прямой jobs API вернул пустой список. Нативное наблюдение состояния выпуска не подтверждено.
- [Ops PR404](https://github.com/markmalina/ops/pull/404) исправляет диагностический маршрут, но OPEN/unmerged/not installed.
Не приняты: реальная новая ранняя карточка, полная транскрипция в ней обоими путями, измеренные задержки, restart/recovery, отсутствие повторной STT после принятого Work результата.

### Ответы #745
Требование: новое сообщение любой стороны, durably принятое Gate до допуска отправки, останавливает старый ответ; новый текст готовит Work после чтения актуального контекста.
В main есть проверки изменения соединения и набора reply tasks, но не весь version-bound контракт: [reply_sends.py:506](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/reply_sends.py#L506).
В PR747 есть [dialog_snapshots.py](https://github.com/markmalina/gate/blob/b975475c1c6c81035e38d9bd13a40d777933e83c/app/infrastructure/dialog_snapshots.py), revisions, scope-bound token и context_changed. Код не развёрнут.
[Run41339046002](https://github.com/markmalina/gate/actions/runs/41339046002) SUCCESS — вспомогательная metadata observation, не guard acceptance.
[Delta420](https://github.com/AlphaCenter/delta/issues/420) на чтении OPEN/comments0, REQUEST_PUBLISHED/NOT_ACCEPTED/NOT_STARTED. Опубликованное поручение не выполненная проверка.

## 3. Существенные проблемы
| ID | Установленный факт / место | Последствие |
|---|---|---|
| P01 | Кандидаты766/747 вне main/production | Проверенный код не приносит новые функции |
| P02 | Persistent Watch off; публикация после transcribed | Нет гарантии быстрой автономной карточки |
| P03 | Work claim/start/submit не подключены; standalone STT отсутствует | Резервный путь не является законченной услугой |
| P04 | Полный dialog guard вне main | Нет принятой гарантии против устаревшего ответа |
| P05 | Watch0116→0119 и reply0116; общие API/config/tests | Green отдельных веток не доказывает общий upgrade |
| P06 | Workflow-level одна concurrency group, фильтры на job уровне | Неeligible запуск способен конкурировать с нужной диагностикой |
| P07 | Несколько POST /mcp; первый feed router делегирует core dispatcher | Отдельный transcription dispatcher перекрывается по структуре маршрутов |
| P08 | TEAM_WORKFLOW auto-deploy/CI completion против AGENTS Ops/runtime acceptance | Неодинаковые определения готовности и маршрута работы |
| P09 | README: июльские pointers,11tools/schema0041; main schema0115/live18tools | Повторное восстановление контекста каждым исполнителем |
| P10 | /ready проверяет DB/schema, не весь пользовательский путь | Здоровый HTTP не доказывает работающий worker/продукт |
| P11 | Большие невыпущенные PR и повторы тяжёлого CI | Длительность, стоимость, конфликты и повторные проверки |
| P12 | Неработавший original расходует one-use grant; требуется новый пакет | Техническая неудача создаёт новый цикл ожидания владельца |
| P13 | Передача Delta420 не подхвачена; исполнение и request разные | Цепочка останавливается между работниками |
| P14 | Исторические и frozen задачи соседствуют с текущими | Риск повторения старой работы и неверного чтения статуса |
| P15 | Transport содержит Alpha/Beta/BECOME/CRM/billing | Широкая область регрессий и связности |
| P16 | Общая история входящих/исходящих, receipts/unknown и privacy требуют сохранения | Упрощение без этих инвариантов может вызвать дубли, утрату истории или неразрешённые эффекты |

Доказательства:
- P05: [актуальное согласование интеграции](https://github.com/markmalina/gate/issues/745#issuecomment-6562923852).
- P06: [Gate778](https://github.com/markmalina/gate/issues/778); прочитаны .github/workflows/ops-watch-release-preflight-v2.yml и ops-watch-scope-diagnostic.yml на2b30b2b. Общая группа gate736-cli-state-observation, cancel-in-progress:false, guards ниже уровня concurrency. Точный cancelling actor/reason конкретного run неизвестен; связь с наблюдавшейся отменой — гипотеза.
- P07: [main.py:388](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L388), [mcp_metadata_routes.py](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_metadata_routes.py), [communication_feed_mcp.py:235](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/communication_feed_mcp.py#L235), [mcp_transcription_routes.py](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_transcription_routes.py). Статический вывод, новый HTTP repro не запускался.
- P08/P09: [AGENTS](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/AGENTS.md), [TEAM_WORKFLOW](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/docs/TEAM_WORKFLOW.md), [README](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/README.md).
- P10: [health_routes.py:83](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/health_routes.py#L83).
- P11: [ci.yml](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.github/workflows/ci.yml): full pytest и повторные rehearsal suites, PG/restore/containers; draft guard есть только у backend, нет у Windows.
- P12: [текущий пакет Watch](https://github.com/markmalina/gate/issues/736#issuecomment-6563197789); current grant consumed even if setup failure.
- P13/P14: [Delta420](https://github.com/AlphaCenter/delta/issues/420), [#731](https://github.com/markmalina/gate/issues/731), [#745](https://github.com/markmalina/gate/issues/745), [bootstrap](https://github.com/markmalina/gate/blob/main/docs/work-bootstrap.md).

## 4. Причины и история
1. После15.10 main не менялся, production соответствует выпуску10.10, при этом ветки продолжали изменяться. Основная работа не пересекала пользовательскую границу.
2. PR747 +14432 строки: docs6161, tests4254, вспомогательный fixture code3067. Задача о свежести требует гонок/процессов, но накопленный пакет значительно шире самой проверки.
3. PR766 также включает наблюдение, ранние карточки, Work journal, миграции и диагностические клиенты. [#770](https://github.com/markmalina/gate/issues/770) — отдельное расширение27.10, добавленное до выпуска прежнего пакета.
4. Проверки инфраструктуры сами обнаруживают новые дефекты: originalcancelled → queue collision fix → schema mismatch → SHA coercion fix → accepted source still not installed ([Ops404](https://github.com/markmalina/ops/pull/404)). Проверки полезны, но пока улучшают механизм будущей проверки.
5. Протокол grants/отдельных source-release-client-operational reviews увеличивает число переходов. Вывод о задержке обоснован; ненужность каждого ограничения не доказана.
6. Подготовленные handoff не гарантируют фактическое принятие исполнителем (Delta420).
7. Документы дают устаревшую картину; реальные состояния приходится восстанавливать из длинной истории.

Сравнение:
- #751: конкретный credential blocker устранён → успешный deploy → fingerprint совпал; завершён limited runtime outcome.
- [#773](https://github.com/markmalina/gate/issues/773): source review нашёл hotloop при отсутствии ключа и3stale migration assertions; исправление и successor review завершены, issueclosed. Это выполненный source fix, не выпуск Watch.
- #736: source/CI success → диагностический blocker → ожидание нового допуска; productOPEN.
- #745: guard source/CI success → инфраструктурная подготовка/проверка → неподхваченный review; productOPEN.
Нельзя переносить старый дефект773 на текущий исправленный head. Нельзя по одному номеру773 делать вывод о billing: прочитанный issue773 относится к code defects.

## 5. Неизвестные и пределы
- Полное live Railway состояние: fleet/process count, pending changes, effective settings, deployment identity — MCP агрегатов недостаточно.
- Точный cancelling actor/reason41341920729 неизвестен.
- Остаток минут/баланс GitHub и реальные затраты не проверены. Успешные CI01.11 не доказывают нынешний остаток, но не позволяют объявить неоплату единственным блокером.
- Полнота/качество всех67текстов и latency текущих записей не проверены.
- Telegram ready — сохранённые probes BY06.10/RU24.10; реальных отправок не было.
- Причины зависания самих сессий Work/ChatGPT без внутренней телеметрии не установлены.
- Количественное ухудшение с лета не доказано: нет сопоставимого ряда продуктовых метрик.
- Все дополнительные находки прежнего аудита (budget/retention/logging и т.п.) не перепроверены; они не выдаются здесь за новые подтверждённые дефекты.

Исследование закончено. Планирование и реализация — отдельное поручение. Архивирование документа не меняет статусы существующих задач/PR и не принимает их результат.

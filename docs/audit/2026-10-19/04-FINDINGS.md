# Результаты аудита и проверяемые исправления

Source 86dbc0f429dce089bf733fc422f20f556535895b; issue[767](https://github.com/markmalina/gate/issues/767). Это собственный технический аудитα, не независимыйδ verdict и не разрешение исправлять найденные задачи без их принятия.

## Приоритеты

- P1: устранить или предметно разрешить до затронутой активации/интеграции;6 находок.
- P2: ограниченная работа над контролем, эксплуатацией и доказательствами;8 находок.
- P3: архитектура и организация дальнейших изменений;2 находки.
- P0 incident не заявлен: реальная эксплуатация уязвимости, перерасход или потеря данных не воспроизводились.

Наличие source evidence и runtime воспроизведение различаются. Для A01–A04/A08 показан конкретный путь кода; production effects не запускались.

| ID | Приоритет | Вид | Наблюдение |
| --- | --- | --- | --- |
|[GATE-A01](#gate-a01)|P1|defect|Первый маршрут /mcp перекрывает пять действий транскрипции|
|[GATE-A02](#gate-a02)|P1|policy_enforcement_gap|Длинная Alfa-транскрипция обходит общий budget-admission голосового пути|
|[GATE-A03](#gate-a03)|P1|concurrency_defect|Старт одного Alfa worker объявляет все running parts прерванными|
|[GATE-A04](#gate-a04)|P1|resource_control_gap|У Alfa ffprobe/ffmpeg нет wall-clock deadline и гарантированного kill|
|[GATE-A05](#gate-a05)|P1|known_unreleased_requirement|Snapshot guard и атомарный inbound-stop остаются вне main|
|[GATE-A06](#gate-a06)|P1|integration_risk|Открытые feature PR создают конкурирующие Alembic heads|
|[GATE-A07](#gate-a07)|P2|observability_gap|/ready и ops health не отражают все фоновые задачи|
|[GATE-A08](#gate-a08)|P2|latent_guard_gap|Перекрытый transcription dispatcher не применяет общий GATE_MCP_ENABLED|
|[GATE-A09](#gate-a09)|P2|documentation_drift|Корневые документы и capability registry дают устаревшие операционные правила|
|[GATE-A10](#gate-a10)|P2|logging_risk|Некоторые exception logs могут включить SQL parameters и частные значения|
|[GATE-A11](#gate-a11)|P2|security_assurance_limit|Secret guard покрывает ограниченное текущее дерево|
|[GATE-A12](#gate-a12)|P2|configuration_drift|Пример окружения неполон и не отражает startup overrides|
|[GATE-A13](#gate-a13)|P2|reproducibility_gap|Пины зависимостей не составляют полный lock/образный SBOM|
|[GATE-A14](#gate-a14)|P2|data_lifecycle_assurance_gap|Нет единого доказанного lifecycle для всех классов сохранённых данных|
|[GATE-A15](#gate-a15)|P3|architecture_debt|Transport boundary смешан с продуктовыми реализациями и большим legacy stack|
|[GATE-A16](#gate-a16)|P3|backlog_state_drift|Открытый issue не равен невыполненной реализации или действующей lease|

## GATE-A01

**P1 · Первый маршрут /mcp перекрывает пять действий транскрипции**

main включает mcp_metadata_router раньше mcp_transcription_router; metadata включает communication_feed router с POST /mcp. Его _delegate вызывает основной mcp_routes напрямую. Основной каталог и dispatcher знают 19 действий, пять transcription действий добавляет только перекрытый endpoint. Итого 24 определения в source,19 достижимых по показанной ASGI-композиции.

**Последствие.** Создание/список/получение/артефакты/retry транскрипций через публичный MCP нельзя считать реализованными; tools/list и вызовы попадают в другой dispatcher.

**Исправление.** Свести /mcp к одной точке сборки с feed/core/transcription dispatch; общий registry определений, scopes, metadata и output schemas. Исправление отдельным bounded outcome.

**Приёмка.** ASGI TestClient/ASGITransport на create_app, со mocked DB/auth/lifespan: tools/list; каждый из 24 tools/call; отрицательная scope/feature проверка; отсутствие неоднозначного POST /mcp. Не тестировать только _tool_definitions.

**Доказательства:**

- [app/main.py:391](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L391); blob `a644f7fae23a7868ec40d7d6d36c698065930c6f`.
- [app/api/mcp_metadata_routes.py:18](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_metadata_routes.py#L18); blob `9d814a23f70a44fc6f8d815f57c47dfcd175111f`.
- [app/api/communication_feed_mcp.py:244](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/communication_feed_mcp.py#L244); blob `5511f0fbc2ff57fee864a4d1f724b28d19911e61`.
- [app/api/mcp_transcription_routes.py:65](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_transcription_routes.py#L65); blob `c0ed06825818bcf9aae1bef14d7d0b54e3e78705`.
- [tests/test_mcp_surface_consistency.py:159](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/tests/test_mcp_surface_consistency.py#L159); blob `7748e1614b10e23c8802b035d95c61ddc574250b`.
- [tests/test_alpha_transcription_product.py:94](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/tests/test_alpha_transcription_product.py#L94); blob `5c582bcd8d9c5e4f51bb732af40f7b0044a84400`.

Связанные существующие pointers: [#467](https://github.com/markmalina/gate/issues/467).

## GATE-A02

**P1 · Длинная Alfa-транскрипция обходит общий budget-admission голосового пути**

Отдельный alpha_transcriptions worker создаёт OpenAITranscriptionPartClient и вызывает provider.transcribe_part без вызова SqlBillingBudget/BillingService reserve/settle. actual_microusd записывается после ответа. Он выбирается при наличии credentials, принимает параметры 2GiB/24h/512parts и timeout300s, тогда как pinned lock задаёт 100MiB/20parts/60s и ceilingUSD3 для закреплённого voice/Calls/Watch контура.

**Последствие.** Запись фактической стоимости не предотвращает расход. Эквивалентность двух контуров политике не доказана; запуск credential-driven worker не является отдельным согласием на provider effects. Реального перерасхода аудит не устанавливает.

**Исправление.** До активации/расширения этого пути определить принятую policy именно для Alfa jobs; общий hard-budget admission до I/O, release/unknown/settlement и явный default-off/data gate. Не расширять lock молча и не утверждать общий USD3 охват.

**Приёмка.** Тест с zero/absent entitlement даёт 0 provider calls; exact approved policy/model/endpoint/timeout/source/parts/output bounds; restart/concurrent budget tests на PG; runtime-only проверка отдельным разрешением.

**Доказательства:**

- [app/main.py:88](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L88); blob `a644f7fae23a7868ec40d7d6d36c698065930c6f`.
- [app/worker/alpha_transcriptions.py:149](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L149); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.
- [app/worker/alpha_transcriptions.py:357](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L357); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.
- [app/application/alpha_transcription_product.py:17](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/application/alpha_transcription_product.py#L17); blob `9fc305866240ff09ec66e5e060febe8eb358726e`.
- [model-access.lock:29](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/model-access.lock#L29); blob `0fec68d448d848186756cad77629a6b5ca2d6cfd`.
- [app/infrastructure/billing_budget.py:97](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/billing_budget.py#L97); blob `4c2fa8279a00f3db2592c6686e5f6b489283fc21`.

Связанные существующие pointers: [#467](https://github.com/markmalina/gate/issues/467), [#712](https://github.com/markmalina/gate/issues/712).

## GATE-A03

**P1 · Старт одного Alfa worker объявляет все running parts прерванными**

reconcile_interrupted_transcriptions при каждом старте выбирает все parts state=running без owner/process/lease/age predicate и переводит их в unknown. При rolling overlap или втором web process это затрагивает ещё работающий первый process.

**Последствие.** Ложный unknown, незавершённая сборка и потеря нормального terminal completion во время допустимой работы другого process. Фактическое число реплик не наблюдалось.

**Исправление.** Привязать claims к boot/lease generation; восстанавливать только истёкшие ownership claims с CAS. До этого документировать single-worker topology и запрет overlap для этого контура.

**Приёмка.** Два независимых процесса/сессии PG: старт B не меняет свежий partA; staleA однократно terminal unknown; поздний finishA не перезаписывает successor.

**Доказательства:**

- [app/worker/alpha_transcriptions.py:55](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L55); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.
- [app/worker/alpha_transcriptions.py:78](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L78); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.
- [app/worker/alpha_transcriptions.py:87](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L87); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.

Связанные существующие pointers: [#467](https://github.com/markmalina/gate/issues/467).

## GATE-A04

**P1 · У Alfa ffprobe/ffmpeg нет wall-clock deadline и гарантированного kill**

probe_duration_millis и encode_window ожидают process.communicate без wait_for/timeout и без finally terminate/kill/wait. Источник допускает 2GiB; subprocess stdout/stderr собираются в PIPE.

**Последствие.** Повреждённый/медленный файл может удерживать worker или дочерний процесс; штатная отмена asyncio не гарантирует освобождение process. Эксплуатация с реальным файлом не выполнялась.

**Исправление.** Ограничить probe/encode время, concurrency, temp-disk/memory/output; terminate→kill→wait на timeout/cancel; cleanup независимо от исхода.

**Приёмка.** Синтетический зависающий дочерний process и cancellation завершаются за bound;0 orphan processes; temp cleanup; нормальная media regression сохраняется.

**Доказательства:**

- [app/infrastructure/alpha_media_chunking.py:71](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/alpha_media_chunking.py#L71); blob `5f504ed4b0ad219589810b24605f4aa3771e1497`.
- [app/infrastructure/alpha_media_chunking.py:89](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/alpha_media_chunking.py#L89); blob `5f504ed4b0ad219589810b24605f4aa3771e1497`.
- [app/infrastructure/alpha_media_chunking.py:136](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/alpha_media_chunking.py#L136); blob `5f504ed4b0ad219589810b24605f4aa3771e1497`.

## GATE-A05

**P1 · Snapshot guard и атомарный inbound-stop остаются вне main**

Основной worker проверяет newer NEEDS_REPLY task перед отправкой; это отдельные чтения, а не version-bound admission любого изменения диалога. Полный snapshot guard находится в открытом PR747; атомарный stop/parallel sequence runtime — в PR720/724.

**Последствие.** Нельзя обещать, что любой новый inbound/outbound гарантированно отменяет ранее подготовленный ответ или tail series в текущем main.

**Исправление.** Продолжать существующие #745 и #722/#718/#716 с их release gates, без новой конкурирующей реализации. Сначала обеспечить canonical identity и общий writer/ingress fence.

**Приёмка.** Гонки inbound/outbound/voice/attachment против claim/provider-call; изменённый snapshot отклонён до I/O; возвращён свежий context; old approval нельзя реанимировать флагом.

**Доказательства:**

- [app/worker/reply_sequences.py:331](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/reply_sequences.py#L331); blob `322e8b138e18501e5d20ba0bbf29c4d393b0daac`.
- [app/infrastructure/reply_sends.py:506](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/reply_sends.py#L506); blob `229a45ff6b6dd27db68aa659359de99a5035e200`.
- [app/application/reply_sequences.py:15](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/application/reply_sequences.py#L15); blob `e5d9a7fa3a0562a16a35f7dd833a48319d2c2158`.

Связанные существующие pointers: [#745](https://github.com/markmalina/gate/issues/745), [#718](https://github.com/markmalina/gate/issues/718), [#716](https://github.com/markmalina/gate/issues/716), [#722](https://github.com/markmalina/gate/issues/722).

## GATE-A06

**P1 · Открытые feature PR создают конкурирующие Alembic heads**

main head0115_watch_voice_post_payment; PR744 добавляет 0115_reply_media_packages с родителем 0114_watch_voice_recovery_claims; PR720 начинает отдельную цепочку 0112→0115 от 0111; PR747 добавляет 0116 от текущего watch0115. Совпадение числового префикса не означает одинаковую revision.

**Последствие.** Независимый green CI веток не доказывает upgrade объединённого дерева; текущий preDeployCommand alembic upgrade head требует согласованной topology.

**Исправление.** Перед merge согласовать порядок интеграции, зависимость/rebase или явную merge revision, не переименовывая уже применённую history; проверить union graph и actual-PG upgrade/schema.

**Приёмка.** Один ожидаемый head объединённого кандидата; upgrade с принятого текущего schema, schema drift, synthetic restore и forward rollback с сохранением реальных journals.

**Доказательства:**

- [railway.toml:6](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/railway.toml#L6); blob `d26281d309732ade5b8a9787d97e679398dec283`.
- [alembic/versions/0115_watch_voice_post_payment.py:9](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/alembic/versions/0115_watch_voice_post_payment.py#L9); blob `e0fcd12f8e40e2bc17f9ce211c9d5b58c002ce5f`.
- [alembic/versions/0114_watch_voice_recovery_claims.py:9](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/alembic/versions/0114_watch_voice_recovery_claims.py#L9); blob `264f278c144b6e8592779ef651d6a05e5dcc7744`.

Связанные существующие pointers: [#742](https://github.com/markmalina/gate/issues/742), [#722](https://github.com/markmalina/gate/issues/722), [#745](https://github.com/markmalina/gate/issues/745), [#696](https://github.com/markmalina/gate/issues/696).

## GATE-A07

**P2 · /ready и ops health не отражают все фоновые задачи**

/ready проверяет DB и Alembic; WorkerHealthRegistry в lifespan регистрирует только Telegram и reply_sequence workers. Dropbox Calls, Watch, Alfa transcription, Beta и reconciliation tasks не представлены единообразно; task.done/exception не превращает процесс в failed readiness автоматически.

**Последствие.** HTTP/DB/schema healthy может сосуществовать с остановленной прикладной обработкой.

**Исправление.** Явный supervisor и freshness/last-progress/error/queue health каждой включённой миссии; отличать disabled от healthy; health не должен отправлять сообщения или делать model calls.

**Приёмка.** Убить synthetic worker, проверить degraded + безопасный error code; healthy web не выдаётся за product readiness; новые метрики не содержат payload.

**Доказательства:**

- [app/main.py:119](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L119); blob `a644f7fae23a7868ec40d7d6d36c698065930c6f`.
- [app/main.py:183](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L183); blob `a644f7fae23a7868ec40d7d6d36c698065930c6f`.
- [app/api/health_routes.py:88](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/health_routes.py#L88); blob `23be1a3b8692f44bd4360d24a2ba14f1cafa8a98`.
- [app/api/health_routes.py:125](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/health_routes.py#L125); blob `23be1a3b8692f44bd4360d24a2ba14f1cafa8a98`.

## GATE-A08

**P2 · Перекрытый transcription dispatcher не применяет общий GATE_MCP_ENABLED**

Собственные 5tools/call в mcp_transcription_endpoint сразу вызывают _require_mcp_principal; _require_mcp_ready вызывается только legacy endpoint. Upload также проверяет principal без общего MCP gate. Сейчас MCP ветвь перекрыта A01; починка routing может сделать latent gap доступным.

**Последствие.** Флаг выключения и metadata auth/enabled перестанут однозначно описывать доступность transcription operations.

**Исправление.** При исправлении A01 решить и явно применить общий availability/data gate ко всем 5 действиям и upload; auth остаётся обязательным независимо от flag.

**Приёмка.** Каждый HTTP entrypoint при выключенном feature возвращает установленный disabled outcome без DB/storage/provider effects; включённый требует scope/workspace.

**Доказательства:**

- [app/api/mcp_transcription_routes.py:109](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_transcription_routes.py#L109); blob `c0ed06825818bcf9aae1bef14d7d0b54e3e78705`.
- [app/api/mcp_routes.py:653](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_routes.py#L653); blob `66b6f528cf9d826b0dfdbbf9dc84be0df4e347d4`.
- [app/api/mcp_metadata_routes.py:88](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_metadata_routes.py#L88); blob `9d814a23f70a44fc6f8d815f57c47dfcd175111f`.

## GATE-A09

**P2 · Корневые документы и capability registry дают устаревшие операционные правила**

README и registry содержат июльские fingerprints, eleven-tools и прошлые OAuth blockers. TEAM_WORKFLOW описывает automatic deploy от main, rootAGENTS — independent Gate-only Ops. WORK-PARENT и часть workflow pins сохраняют AlphaCenter namespace после transfer.

**Последствие.** Новый разработчик может продолжить старую очередь или выбрать уже отменённый deploy/authority route.

**Исправление.** Использовать текущий audit index; сохранять датированную историю как историю. Отдельно согласовать обновление нормативных registries/pins с владельцами; не делать массовый search/replace namespace.

**Приёмка.** Каждая current claim имеет revision/time; приоритет owner intent→rootAGENTS/current policy→source→receipt по предмету; старые snapshot даты видимы; current tooling установлено по routing.

**Доказательства:**

- [README.md:37](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/README.md#L37); blob `eb9c223cba9fafd3b75cbdaaa7ab453fcbf75b90`.
- [registry/gate-capabilities.v1.json:129](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/registry/gate-capabilities.v1.json#L129); blob `d212d8de1c42c5e3b2fc30d86951711652407621`.
- [docs/TEAM_WORKFLOW.md:328](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/docs/TEAM_WORKFLOW.md#L328); blob `d95130c46848ba0bacd1c0ecb3ec7dd1edab3f38`.
- [AGENTS.md:30](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/AGENTS.md#L30); blob `87ee6bf879da3e52ccb76879332139f74d678f02`.
- [WORK-PARENT.json:3](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/WORK-PARENT.json#L3); blob `b32f8cbd945f4714dbc4d8e788a8177de50cbd31`.

Связанные существующие pointers: [#764](https://github.com/markmalina/gate/issues/764), [#745](https://github.com/markmalina/gate/issues/745), [#277](https://github.com/markmalina/gate/issues/277).

## GATE-A10

**P2 · Некоторые exception logs могут включить SQL parameters и частные значения**

database engine создан без hide_parameters; alpha_transcriptions и main используют logger.exception для произвольных исключений. SQLAlchemy/HTTP exceptions могут включать statement parameters/URLs. Это риск по source, не установленная утечка из продакшн логов.

**Последствие.** Сообщения/транскрипты/параметры могут попасть в diagnostic output при ошибке.

**Исправление.** Безопасные typed error codes, минимальные контексты; hide_parameters и явный redaction boundary для nested exceptions; не копировать provider response или SQL payload в receipts.

**Приёмка.** Синтетический sentinel в SQL/HTTP exception отсутствует во всех log handlers и traceback output; остаются code/phase/correlation digest.

**Доказательства:**

- [app/infrastructure/database.py:30](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/database.py#L30); blob `d182ce7fd94fe347f2925b383777afb78a7bd5cb`.
- [app/worker/alpha_transcriptions.py:74](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/worker/alpha_transcriptions.py#L74); blob `b5aa0284e961b4bd38b28af5bb4075af71b1e3f9`.
- [app/main.py:338](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/main.py#L338); blob `a644f7fae23a7868ec40d7d6d36c698065930c6f`.

## GATE-A11

**P2 · Secret guard покрывает ограниченное текущее дерево**

repository_sensitive_data_guard сканирует app/scripts/.github/agent_bundles и 4root files; docs, contracts, tests, migrations, другие root files и Git history вне этой проверки.

**Последствие.** Green guard не является свидетельством отсутствия secrets/PII во всём репозитории или истории. Утечка этим аудитом не заявляется.

**Исправление.** Разделить runtime literal guard и полный secret/privacy scan с безопасными allowances для synthetic fixtures; проверять новые docs/fixtures; historical scan отдельным bounded pass без публикации matches.

**Приёмка.** Положительные/отрицательные synthetic tokens по каждой директории; findings только path/line/code; ни один secret не выводится.

**Доказательства:**

- [scripts/repository_sensitive_data_guard.py:11](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/scripts/repository_sensitive_data_guard.py#L11); blob `b68832b4474c5604e77d0e3a5a5001fa2338d6fa`.
- [scripts/repository_sensitive_data_guard.py:104](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/scripts/repository_sensitive_data_guard.py#L104); blob `b68832b4474c5604e77d0e3a5a5001fa2338d6fa`.

## GATE-A12

**P2 · Пример окружения неполон и не отражает startup overrides**

В Settings определены 161 validation_alias; в .env.example — 118 присваиваний, причём 48 aliases отсутствуют в примере. Пять имён из примера читаются вне Settings либо требуют отдельной проверки. Railway start принудительно отключает persistent Watch, включает OIDC и document send, задаёт default rich=true; значения отличаются от defaults Settings.

**Последствие.** Настройка только по .env.example или Settings не воспроизводит итоговую конфигурацию запуска; наличие credentials не доказывает готовность.

**Исправление.** Использовать config-catalog.json и явный порядок overrides. Синхронизацию sample выполнить отдельным изменением без секретов.

**Приёмка.** Сверены aliases, example и прямые getenv; проверен start command и единственный driver mode; значения secrets отсутствуют. Deployed effective config подтверждается отдельным допущенным наблюдением.

**Доказательства:**

- [app/core/config.py:20](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/core/config.py#L20); blob `bbad7efb30411ffcc67431acb3de3f8272facab8`.
- [.env.example:1](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.env.example#L1); blob `f3b264d7e574f065c4f771c95fe82baa22736990`.
- [railway.toml:7](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/railway.toml#L7); blob `d26281d309732ade5b8a9787d97e679398dec283`.

Связанные существующие pointers: [#736](https://github.com/markmalina/gate/issues/736), [#731](https://github.com/markmalina/gate/issues/731).

## GATE-A13

**P2 · Пины зависимостей не составляют полный lock/образный SBOM**

Базовый Docker image закреплён digest, ряд прямых зависимостей закреплён constraints. openpyxl/pypdf/python-docx и транзитивные зависимости полностью не зафиксированы. apt ffmpeg install и pip upgrade выбирают доступные версии. Отдельный Dockerfile.i4 не собирается generic CI.

**Последствие.** Повторная сборка одного source SHA может дать другой набор зависимостей. Наличие или отсутствие актуальных CVE аудитом не установлено.

**Исправление.** Подготовить полный lock/constraints с hashes, resolved manifest, SBOM и ограниченную advisory-проверку. Определить необходимость I4 image check по его текущему lifecycle.

**Приёмка.** Две disposable сборки одного SHA дают сравнимые resolved manifests; pip check проходит; найденные advisory привязаны к точным версиям.

**Доказательства:**

- [Dockerfile:20](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/Dockerfile#L20); blob `78e94f24fea683302c49fbe4badce55fff281023`.
- [constraints/runtime.txt:1](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/constraints/runtime.txt#L1); blob `dab75bae3799ac25f2ddaccd9c7edd37f5351826`.
- [pyproject.toml:15](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/pyproject.toml#L15); blob `36641df10ea7e0e7488d40f48d2ec3a5ceba6fca`.
- [.github/workflows/ci.yml:152](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.github/workflows/ci.yml#L152); blob `e441d9db93205f743e17f4148fc8ebfa2ba30b62`.

## GATE-A14

**P2 · Нет единого доказанного lifecycle для всех классов сохранённых данных**

Gate хранит raw Telegram receipts, оригиналы и тексты voice/calls, Watch BYTEA и приватные Dropbox artifacts. Локальные retention/dry-run процедуры существуют, но единая действующая матрица TTL/export/delete/backup/tombstone и свежие purge receipts для всех контуров не установлены. Старый Food lifecycle #617 относится к замороженному AQUB.

**Последствие.** Удаление одного raw_data или наличие retention-модуля не подтверждает полный export/delete по subject/source во всех хранилищах и backup.

**Исправление.** Согласовать data-class matrix: цель, владелец, доступ, срок, purge, backup и отдельные решения владельца. Проверять dry run, ограниченное выполнение и terminal receipt. В ходе аудита данные не удалять.

**Приёмка.** Синтетический source полностью прослеживается; export/delete scopes точны; частичный сбой видим; receipts не содержат payload; восстановление backup учитывает tombstones.

**Доказательства:**

- [app/models/watch_voice.py:33](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/models/watch_voice.py#L33); blob `4583169b0fb95beff0dfed2a45fd0283b6e72950`.
- [app/application/alpha_retention.py:10](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/application/alpha_retention.py#L10); blob `79ca2d9d2ed062e695e2ea732ba56bda2b3b2f56`.
- [app/infrastructure/dropbox_transcription_storage.py:27](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/infrastructure/dropbox_transcription_storage.py#L27); blob `30ce4a98dca2568c37c9c3cf2ca46774d419f8fd`.

Связанные существующие pointers: [#617](https://github.com/markmalina/gate/issues/617).

## GATE-A15

**P3 · Transport boundary смешан с продуктовыми реализациями и большим legacy stack**

В app 309 файлов и 106541 строка с учётом завершающих пустых строк. Gate включает CRM, Alpha/Beta, BECOME quests/programs/invitations и billing. mcp_routes.py — 4395 строк, Telegram worker — 2167; один durable ingress предшествует цепочке из 11 legacy webhook handlers. Это инвентаризация структуры, а не разрешение удалить её.

**Последствие.** Большая область изменения и сложная сборка усложняют проверку и выпуск. Описание «транспорт» само по себе не объясняет фактический код.

**Исправление.** Сначала characterization tests и карта владения. Затем по одной границе упрощать dispatcher и выносить продуктовую семантику, сохраняя contracts/history. Переписывание всего gateway не требуется.

**Приёмка.** Characterization сохраняет ingress/send/replay/scope; каждый перенос принят владельцем домена; не появляется второй master данных.

**Доказательства:**

- [app/api/telegram_ingress.py:221](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/telegram_ingress.py#L221); blob `8469f77886fde7538eb39469366ebc02a12fe259`.
- [app/models/relationship_crm.py:27](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/models/relationship_crm.py#L27); blob `89820cf7ce06b42d0d05a8c670031454b403484b`.
- [app/models/become_quest.py:26](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/models/become_quest.py#L26); blob `8c0c98c2a373ea9922776eb6629ef3f9a5761958`.
- [app/api/mcp_routes.py:210](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/app/api/mcp_routes.py#L210); blob `66b6f528cf9d826b0dfdbbf9dc84be0df4e347d4`.

Связанные существующие pointers: [#570](https://github.com/markmalina/gate/issues/570).

## GATE-A16

**P3 · Открытый issue не равен невыполненной реализации или действующей lease**

Среди 34 открытых задач есть planning, source-accepted/unreleased, historical residual, старые execution claims и замороженный AQUB. Открыты 14 PR, некоторые пересекаются; перечисление веток дало 616. Тела #696/#745 содержат прежние heads, тогда как latest comments и PR head новее; P0 в #277 завершён, но issue открыт.

**Последствие.** Команда «выполнить все open» может возобновить замороженную или непринятую работу либо создать дубли. Исторический running не подтверждает активный процесс.

**Исправление.** Использовать backlog.json с отдельными source/tests/runtime/acceptance. После предметного принятия обновлять существующие pointers. В этом аудите чужие задачи не закрывать и не принимать.

**Приёмка.** Все 34 задачи и 14 PR представлены по одному разу; AQUB не включён в исполняемый план; следующий владелец и действие указаны; действующая lease не выводится из старого status.

**Доказательства:**

- [docs/work-bootstrap.md:48](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/docs/work-bootstrap.md#L48); blob `6c1253074366edadf515b93ec818a8df79d6d40f`.
- [docs/aqub-boundary.v1.json:7](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/docs/aqub-boundary.v1.json#L7); blob `2ec010f0298b1dbc4cccb2fca9e68ff513eeeeaf`.
- [registry/gate-capabilities.v1.json:4](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/registry/gate-capabilities.v1.json#L4); blob `d212d8de1c42c5e3b2fc30d86951711652407621`.

Связанные существующие pointers: [#277](https://github.com/markmalina/gate/issues/277), [#272](https://github.com/markmalina/gate/issues/272), [#736](https://github.com/markmalina/gate/issues/736), [#745](https://github.com/markmalina/gate/issues/745), [#696](https://github.com/markmalina/gate/issues/696).

## Сильные стороны, которые следует сохранить

1. Durable Telegram receipt до processing и fail-closed503 при отказе сохранения.
2. OAuth asymmetric/JWKS/issuer/audience/scope checks и explicit account/project containment.
3. Exact draft/recipient/payload confirmation и durable send attempts.
4. Unknown не объявляется failed и не разрешает blind resend.
5. Watch OIDC pin/current-main/run/replay checks, permanent recovery claims и private-destination boundary.
6. Реальные PostgreSQL negative/concurrency/migration tests в CI.
7. DB/schema readiness, backup/restore drill и non-root containers.
8. Явный frozen AQUB boundary и независимый Gate Ops requester.

Рефакторинг не должен ослабить эти инварианты ради упрощения тестов или получения green CI.

## Чего этот аудит не утверждает

Нет заявления о текущей утечке, подтверждённой эксплуатации, полном отсутствии secrets/CVE или готовности всех 19 доступных source tools к реальному owner use. Runtime scoped status проверен отдельно в[runtime-observation.json](runtime-observation.json); он не закрывает перечисленные product gates.

Найденные изменения не внесены в app/models/workers/workflows. Новая документация делает следующий bounded implementation reviewable.

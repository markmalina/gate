# Gate RU: доказательный обзор MTProto OSS и готовых MCP

Дата проверки: 2026-10-11 UTC. Только публичные исходники, документация и метаданные. Telegram network connections, live login, отправка сообщений, upstream suites/live smoke и deploy не выполнялись. Отдельно выполнен описанный ниже offline synthetic Session experiment; итоговые документы сохраняются в существующем PR Gate. Скачанный wheel Telethon только прочитан как ZIP, SHA-256 проверен. Это архитектурный выбор и выборочный source review, не полный security audit. Проверены исходники, версии и лицензии перечисленных проектов; ссылки закреплены на просмотренных commit SHA. В ходе исследования сохранены SHA-256/размеры 150 snapshot-файлов для локальной сверки.

## Решение

**Основная зависимость для существующего Python/FastAPI/SQLAlchemy/PostgreSQL Gate — прямой Telethon 1.x, за узким собственным адаптером. Не встраивать целиком ни один Telegram MCP.** Сохранить воспроизводимый pin 1.44.0 до отдельной проверки обновления; затем целевой upgrade на выпущенную 1.45.0, а не безусловное следование ветке. Изначальный выбор библиотеки разумен, но слой durable orchestration, владения сессиями, идемпотентности и транспортного доступа остаётся обязанностью Gate.

**Сильнейший альтернативный движок — официальный TDLib** за отдельным стабильным внутренним интерфейсом, если потребуются его более полная модель клиента, auth state machine, local database, упорядоченные updates или возможности, неудобные/неподдерживаемые Telethon. **gotd/td — альтернативный Go worker**, когда измерения подтвердят выгоду отдельного Go runtime. Обе миграции дороже сохранения текущего Python стека и не устраняют необходимость Gate-auth, session ownership и durable send tracking.

Для переиспользования готовых MCP наиболее ценен **tolboy/telegram-mcp-tdlib как образец журналирования неизвестных результатов и scoped access**, **chigwell как источник Python mock-test сценариев/allowlist/timeout поведения**, **mcp-telegram как образец одного локального владельца с IPC**, **better-tg-cli как образец общего transport dispatcher и отключения write-инструментов**. Это не готовые заменители Gate control plane.

## Свежесть, версии и лицензии

Оценка основана на default-branch commit, реальном коде и release metadata. `pushed_at` может отражать другую ветку/tag, поэтому не подменяет дату default head. Stars не использованы. Лицензии указаны по файлам/metadata; у зависимостей второго порядка свои лицензии.

| Проект | Проверенная версия / source SHA | Дата default head / статус | Лицензия | Решение для Gate |
|---|---|---|---|---|
| Telethon | pin 1.44.0 `2373d0058f88962812112234490199b46c3aad30`; released 1.45.0 `bad095bd5b3dc9f852cfbeeba300a421169c9b1e`; v1 HEAD `0205278e715dd4a7454de683b9b9b76f0e13db85` | v1 HEAD 2026-10-10; GitHub архив 2026-02-21 означает переезд на Codeberg; v1 maintenance mode | MIT | Основная dependency; upgrade по контрактным тестам |
| tdlib/td | HEAD сообщает 1.8.68; `c15d3f5a5de6e3ba5839822c451152e5e18bb700` | 2026-10-08; активен | Boost Software License 1.0 (`BSL-1.0`, это не Business Source License) | Основной запасной движок |
| gotd/td | latest release v0.162.0; `be24580163e18c2b4d3557ffa03e250ec6a30335` | 2026-09-18; активен; latest release 2026-09-18 | MIT | Go worker при доказанной потребности |
| gram-js/gramjs | package.json HEAD 2.26.8; `3dc2346fb15ee5a73a03bce3bd191e7d28c76f06` | 2026-07-14; официально archived/no longer maintained | MIT | Новый runtime на исходном GramJS не выбирать |
| sanyok12345/teleproto | 1.230.0; `2ccf33e7bf8e66c64ad1b5e4193ffa595a68acdc` | 2026-10-09; активный successor, прямо рекомендован README GramJS | MIT | Только если выбирается TypeScript worker |
| pyrogram/pyrogram | 2.0.106; `30de1e21e3e8b5949971ba0bba56bb818ba43b71` | 2024-12-23; archived | LGPL-3.0-or-later по source header | Новый проект на оригинальном Pyrogram не начинать |
| Mayuri-Chan/pyrofork | 2.3.69; `6bbff464174ea80336e2352ce7679324d6acad09` | 2025-12-10 default head; API archived=true, pushed_at 2026-07-01 не означает обновление main | LGPL-3.0-or-later | Не считать активным fork по старому README/search snippet |
| Kurigram (старый URL KurimuzonAkuma/kurigram редиректит в kurigram-org/kurigram) | 2.2.26; `2047fca6d157b5caf815658183ee8406221dcd14` | 2026-10-10; активен | LGPL-3.0-or-later; COPYING/COPYING.lesser/NOTICE | Следить, но переход с Telethon не окупается текущим scope |
| chaindead/telegram-mcp | `52d7991146a57ad42c7ef6394e14f031523176db`; gotd v0.121.0, mcp-golang v0.8.0 | 2026-05-28 | MIT | Не dependency; узкий пример draft/read mapping |
| chigwell/telegram-mcp | project 2.0.1; `0910170285526d289420acb413b2f741ff2ae0bb`; requires telethon>=1.45.0 | 2026-10-10 | Apache-2.0 | Python patterns/tests, не wholesale integration |
| tolboy/telegram-mcp-tdlib | README 1.17.0; `5b712c9daae5176f329fbd3c44fd4c329e6dfe68`; tdlight-java `3.5.3+td.1.8.65` | 2026-09-29 default head; pushed_at 2026-10-10 | Apache-2.0 проекта | Наиболее интересный образец durable send/permissions |
| mcp-telegram/mcp-telegram | 1.43.4; `69bfe549fbb307168207b3590e86095237753bac`; dependency `telegram:^2.26.22` | 2026-10-05 | MIT | Local owner/IPC pattern; есть важный timeout gap |
| TheVilfer/better-tg-cli | 0.34.0; `13cc02b1be0f6d8fd00c145a6cab919f9cb8cfdb`; teleproto ^1.229.0 bundled | 2026-10-10 | MIT | Хороший локальный CLI/transport reference, не backend dependency |
| tulir/telethon-session-sqlalchemy | `d498503ddde332e190bfa47e70f0bfa59fe6b5ef` | 2020-10-16; archived | MIT | Не подключать только из-за совпадения SQLAlchemy |

Метаданные: `https://api.github.com/repos/<owner>/<repo>` и `/commits?per_page=1`; сверены API metadata и pinned source snapshots. Canonical Codeberg: [v1 branch API](https://codeberg.org/api/v1/repos/Lonami/Telethon/branches/v1), [tags API](https://codeberg.org/api/v1/repos/Lonami/Telethon/tags?limit=10), [PyPI JSON](https://pypi.org/pypi/Telethon/json). HTML Codeberg был недоступен web-parser из-за robots, его публичный API и raw files успешно прочитаны напрямую; mirror не использован как authority.

## Telethon: переиспользовать зрелый протокол, отдельно закончить сервисную обвязку

Официальные [changelog](https://docs.telethon.dev/en/stable/misc/changelog.html) и PyPI подтверждают 1.44.0 от 2026-06-15 (layer 227), 1.45.0 от 2026-09-10 (layer 229). v1 HEAD от 2026-10-10 обновляет layer 230, но это ещё не основание ставить непроверенный branch snapshot. GitHub README прямо указывает [переезд](https://github.com/LonamiWebs/Telethon/blob/dab6bc4d4fc0c7815f6f19c6f643d9f75d2d930c/README.rst), поэтому архив GitHub не является причиной миграции с Telethon.

Проверенный wheel 1.44.0: `telethon-1.44.0-py3-none-any.whl`, SHA-256 `52fc49efb67a4916c2aedcb295ad286f4afa2aba9bf15d83ed2acdc64af0c718`. sdist SHA-256 из PyPI: `7de1473e9e412e20bd57b102d8f50d48372712462be9ff6c003bb81c50ad7e98`. Проверка wheel проведена; sdist не скачивался.

Что брать:

- Саму библиотеку: MTProto, TL generation, DC migration, transport crypto, Telegram auth primitives, raw API, update handling. Не переписывать MTProto/SRP/crypto.
- Public `TelegramClient`, `Session`, `MemorySession`/`StringSession` interfaces. [Session interface](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/sessions/abstract.py) даёт основу для собственного persistence adapter; [SQLite implementation](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/sessions/sqlite.py) показывает полный набор полей, entities и update state.
- Upstream pure-unit fixtures для TL serialization, text entities, parsing, message assembly, не live examples. [Tests tree](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/tests/telethon).

Границы интеграции:

1. [StringSession](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/sessions/string.py) сериализует DC, address, port, auth key в base64. Это credential, не encryption-at-rest. Entity cache и update cursors не сериализуются этой строкой. Encrypted StringSession подходит как узкий credential store; полноценный reader/update service требует отдельного durable peer/access_hash/update-state решения. Простое `save()` строки не решает recovery updates.
2. [Client configuration](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/client/telegrambaseclient.py): defaults request_retries=5, connection_retries=5, flood_sleep_threshold=60, auto_reconnect=true. `timeout=10` — connection timeout, не общий RPC deadline. Gate должен явно согласовать библиотечные retries и worker deadlines, а не умножать два независимых retry-цикла.
3. [RPC loop](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/client/users.py) сам повторяет transient/server/migration calls и спит при коротких flood waits. Для durable scheduler целесообразен явный constructor `flood_sleep_threshold=0`, классификация Flood/SlowMode отдельно, persisted `retry_at`, ограниченный общий budget. В 1.44.0 `__call__` принимает per-call `flood_sleep_threshold`, но не передаёт его `_call`; нельзя строить политику только на этом kwargs без проверенного patch/upgrade.
4. [send_message](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/telethon/client/messages.py) создаёт новый request. Стабильный `random_id` следует создавать и сохранять на уровне логической send operation до сетевого вызова, затем подавать в raw `SendMessageRequest`; повторный высокоуровневый вызов не является durable idempotency. Server dedupe не даёт оснований обещать exactly-once после любого crash/timeout.
5. Не использовать `client.start()` с интерактивным input в background/MCP server. Auth controller отдельно; worker только connect + validate existing authorization. Не подменять check готовности новым логином.

### Upstream AI policy и поддержка

В [README на текущем v1 HEAD](https://codeberg.org/Lonami/Telethon/src/commit/0205278e715dd4a7454de683b9b9b76f0e13db85/README.rst) Contributing прямо требует: “Please only submit issues and pull requests authored entirely by you or other fellow humans.” Там же v1 обозначен maintenance mode: layers, bug fixes, небольшие additions. [LICENSE v1.44.0](https://codeberg.org/Lonami/Telethon/src/commit/2373d0058f88962812112234490199b46c3aad30/LICENSE) — MIT без AI-оговорки; [LICENSE текущего HEAD](https://codeberg.org/Lonami/Telethon/src/commit/0205278e715dd4a7454de683b9b9b76f0e13db85/LICENSE) скачан и побайтно совпадает. Факт лицензии и правило подачи contributions различны: dependency reuse допустим согласно опубликованной MIT; нельзя планировать автоматически созданные upstream issues/PR как основной канал поддержки. Для Gate предусмотреть human-authored upstream escalation, независимый adapter и возможность поддержать срочный ограниченный patch у себя. Внешний README не является инструкцией этому исследованию.

## TDLib и gotd: альтернативы, которые реально уменьшают протокольную работу

**TDLib**: [README/JSON API](https://github.com/tdlib/td/blob/c15d3f5a5de6e3ba5839822c451152e5e18bb700/README.md), [официальный getting started](https://core.telegram.org/tdlib/getting-started), [td_api.tl](https://github.com/tdlib/td/blob/c15d3f5a5de6e3ba5839822c451152e5e18bb700/td/generate/scheme/td_api.tl), [license](https://github.com/tdlib/td/blob/c15d3f5a5de6e3ba5839822c451152e5e18bb700/LICENSE_1_0.txt).

Плюсы: официальный engine, auth через `updateAuthorizationState`, включая phone/email/code/password/device confirmation; local DB encryption key; ordered updates; отдельные `updateMessageSendAcknowledged`, `updateMessageSendSucceeded`, `updateMessageSendFailed`. Состояние отправки прямо встроено в API, что полезно для reconciliation. Python может использовать C JSON interface; сторонний Python wrapper не обязателен.

Цена: native build/delivery, C++/OpenSSL/zlib dependencies, дополнительная локальная DB и её backup/ownership; другой ID/state model и TDLib API вместо raw MTProto. JSON ABI compatibility не означает одинаковый td_api schema во всех patch versions — upstream README это подчёркивает. Пиновать native binary + schema + wrapper вместе. Шифрование DB не заменяет отдельное управление encryption key и не делает plaintext скачанных файлов автоматически защищёнными. Миграция с Telethon StringSession не является доказанным drop-in path; новый независимый auth flow следует учитывать в плане смены engine.

**gotd/td**: [README](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/README.md), [session.Storage](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/session/session.go), [options](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/telegram/options.go), [auth.Flow](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/telegram/auth/flow.go), [tgtest server](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/tgtest/server.go).

Плюсы: typed raw methods, Go context cancellation, reconnect/DC migration, pluggable storage, локальный fake Telegram server и fuzz corpus. Это удобная база отдельного worker при реальной потребности в Go. Числа throughput/memory в README — заявления проекта, в Gate не измерялись. [FileStorage](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/session/storage_file.go) пишет 0600, но не шифрует; mutex привязан к object, не является distributed ownership, запись `os.WriteFile` не обещает atomic swap. [TelethonSession converter](https://github.com/gotd/td/blob/be24580163e18c2b4d3557ffa03e250ec6a30335/session/telethon.go) существует, но переносит credential, не durable update/entity cache и не разрешает одновременную работу двух клиентов с ключом. `tgtest` — источник тестовых подходов; совместимость fake server с Python client в этом исследовании не проверена.

## GramJS, Pyrogram и forks

[GramJS README](https://github.com/gram-js/gramjs/blob/3dc2346fb15ee5a73a03bce3bd191e7d28c76f06/README.md) официально направляет на [teleproto](https://github.com/sanyok12345/teleproto). Новый TS service оценивать на teleproto, не старой GitHub ветке GramJS. MIT, active head и release 1.230.0 подтверждены, но перенос Python Gate в TS только ради клиента не даёт преимуществ для текущего scope. `StringSession` в этом семействе тоже не сервис credential encryption. Разные значения package.json в архивном GitHub и version range npm в MCP-wrapper не следует смешивать: npm artifact происхождение требует отдельного lockfile/provenance review.

Исходный [Pyrogram README](https://github.com/pyrogram/pyrogram/blob/30de1e21e3e8b5949971ba0bba56bb818ba43b71/README.md) сообщает остановку поддержки. [Pyrofork pyproject](https://github.com/Mayuri-Chan/pyrofork/blob/6bbff464174ea80336e2352ce7679324d6acad09/pyproject.toml) и [Kurigram pyproject](https://github.com/kurigram-org/kurigram/blob/2047fca6d157b5caf815658183ee8406221dcd14/pyproject.toml) имеют LGPL-3.0-or-later; это не запрет backend-использования, но скопированные/изменённые библиотечные части не стоит незаметно смешивать с MIT-адаптером. Kurigram активен и добавляет новые Telegram features; активность fork не доказывает качество сессий и resend semantics. В отсутствие блокирующего Telethon gap смена Pyrogram-family потребует адаптации API/session/tests без доказанной выгоды.

Старый [telethon-session-sqlalchemy](https://github.com/tulir/telethon-session-sqlalchemy) всё ещё указан в Telethon docs, но сам архивирован и main не обновлялся с 2020. Его нельзя автоматически принимать как современную SQLAlchemy 2/PostgreSQL/encrypted-session dependency. Upstream Session abstraction плюс Gate persistence — более узкий, проверяемый контракт.

## chaindead/telegram-mcp: недостаточная база runtime

Snapshot [tree](https://github.com/chaindead/telegram-mcp/tree/52d7991146a57ad42c7ef6394e14f031523176db), [license](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/LICENSE).

- [serve.go](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/serve.go) регистрирует лишь me/dialogs/history/draft/read и stdio. `tg_send` — **SaveDraft**, не отправка сообщения. `--dry` совершает `SendDraft` и `ReadHistory`: это не offline dry-run. Не запускать для безопасной диагностики.
- [client.go](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/internal/tg/client.go): каждый tool создаёт новый gotd client на одном FileSessionStorage, `NoUpdates=true`; ошибки OptionsFromEnvironment игнорируются. [draft.go](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/internal/tg/draft.go) использует `context.Background`, поэтому cancellation клиентского запроса/общий deadline не прокинуты.
- [auth.go](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/auth.go) логирует phone, api-hash и session path; пароль принимается CLI flag. [internal auth](https://github.com/chaindead/telegram-mcp/blob/52d7991146a57ad42c7ef6394e14f031523176db/internal/tg/auth.go) читает code из stdin, `--new` удаляет файл. Это локальный interactive bootstrap, не broker auth service.
- Нет показанного distributed ownership, encrypted DB store, durable send ledger, tenant-scoped authorization или HTTP parity. Локальные cmd/test/examples используют live account; в основной tree нет unit `_test.go` файлов. Переносить можно компактные TL-to-tool mappings с MIT notice, практической экономии для Gate мало.

## chigwell/telegram-mcp: полезный Python reference, но не production control plane

Snapshot [tree](https://github.com/chigwell/telegram-mcp/tree/0910170285526d289420acb413b2f741ff2ae0bb), [pyproject](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/pyproject.toml), [license](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/LICENSE).

Это уже не минимальный старый скрипт: account labels, allowed chats/tools, read-only subsets, session pool/locks, metadata annotations, filesystem guardrails, bounded calls. В [runtime.py](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/runtime.py) timeout формулирует неопределённый результат записи, что правильно. Но upstream требует telethon>=1.45.0 и добавляет media/PDF/transcription/MCP dependencies; пакет не drop-in в существующий pin1.44.

Конкретные несовпадения с нужным server runtime:

| Область | Наблюдение в source | Что брать / что изменить |
|---|---|---|
| Auth startup | [runner.py](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/runner.py) не делает interactive start, но повторяет AuthKeyDuplicated до 4 раз | Брать noninteractive worker; terminal key error не повторять |
| Ownership | [singleton.py](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/singleton.py) — локальный OS file lock; shared mode комментируется как safe same IP | Не использовать shared mode как гарантию. Нужен единственный service owner, включая hosts/restarts |
| Flood/deadline | runtime default flood_sleep_threshold=60, tool ceiling=55 | 56–60s auto-sleep может превратиться в generic timeout вместо точного retry-after; scheduler должен владеть ожиданием |
| Persistence | session strings из env + MemorySession, warm all dialogs | Не encrypted durable session/entity store. Pool — набор разных authorizations, не fencing одного owner |
| Send | [messages.py](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/tools/messages.py) вызывает send_message или новый random_id, возвращает текстовый success | Нет persisted business operation ID; повтор MCP request может повторить send |
| Transport | [HTTP middleware](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/transport/http.py), runner поддерживают stdio/http/sse | Наличие трёх runners не доказывает authorization/timeout/replay parity. FastMCP не настроен здесь как Gate OAuth resource server |
| MCP SDK | [protocol.py](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/telegram_mcp/transport/protocol.py) monkey-patches глобальные версии SDK и HTTP factory | Не переносить в общий FastAPI process; брать поддерживаемую pinned SDK реализацию и conformance tests |

HTTP localhost default и Host/Origin protection полезны, но не tenant identity. CORS `*` на discovery/preflight не является само по себе доказанным exploit; при перенесении на публичный endpoint это ещё не законченная auth model. Audience annotations также не создают security boundary от prompt injection.

Сильные test references: [flood_wait](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/tests/test_flood_wait.py), [session_pool](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/tests/test_session_pool.py), [runner](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/tests/test_runner.py), [content_safeguards](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/tests/test_content_safeguards.py), [protocol_negotiation](https://github.com/chigwell/telegram-mcp/blob/0910170285526d289420acb413b2f741ff2ae0bb/tests/test_protocol_negotiation.py). Последний проверяет HTTP/TestClient и session handshake; это не доказательство полного paired tools/call поведения HTTP/stdio и сетевых отказов.

## Новые альтернативные wrappers

### tolboy/telegram-mcp-tdlib

[README](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/README.md), [build](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/build.gradle.kts). Kotlin/Java/Spring + tdlight/native TDLib. Наиболее сильный reference из просмотренных wrappers: account scopes, API keys/JWT, recoverable sends, status lookup и typed errors.

[SendOperationJournal.kt](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/src/main/kotlin/dev/telegrammcp/server/service/SendOperationJournal.kt) сохраняет reservation до send, сверяет fingerprint/chat по idempotency key, повторно возвращает receipt и оставляет UNKNOWN после исключения; есть late-completion reconciliation. Паттерн стоит воспроизвести в PostgreSQL. Но implementation — локальные JSONL и FileChannel locks, не distributed PG outbox/fencing; idempotency key в SendOperationService опционален. Целиком добавить этот сервис означает ещё один state owner, JVM/native deployments, собственные auth scopes и journal, которые надо согласовать с Gate.

[AccountAccessPolicy.kt](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/src/main/kotlin/dev/telegrammcp/server/security/AccountAccessPolicy.kt) явно выбирает account и проверяет scope; при отсутствующем JWT accounts claim возвращается null, то есть сама эта функция не вводит ограничение accounts. При копировании изменить на соответствующий Gate fail-closed контракт; итоговая policy зависит и от AccessPermissionService/configuration, поэтому это не утверждение обхода всей upstream security.

Тестовые образцы: [SendOperationJournalTest](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/src/test/kotlin/dev/telegrammcp/server/service/SendOperationJournalTest.kt), [MessageSendTrackerTest](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/src/test/kotlin/dev/telegrammcp/server/client/MessageSendTrackerTest.kt), [PermissionDispatchTest](https://github.com/tolboy/telegram-mcp-tdlib/blob/5b712c9daae5176f329fbd3c44fd4c329e6dfe68/src/test/kotlin/dev/telegrammcp/server/security/PermissionDispatchTest.kt). Apache-2.0 attribution/notice сохранять при переносе существенного кода; для Python полезнее перенести invariants/scenarios, а не JVM реализацию.

### mcp-telegram/mcp-telegram

[package](https://github.com/mcp-telegram/mcp-telegram/blob/69bfe549fbb307168207b3590e86095237753bac/package.json), [daemon docs](https://github.com/mcp-telegram/mcp-telegram/blob/69bfe549fbb307168207b3590e86095237753bac/docs/guides/shared-daemon.md). Не путать этот local/stdio project с отдельным `mcp-telegram-cloud` и его OAuth-hosted deployment: исследование не переносит автоматически гарантии одного в другой.

Хороший паттерн: один master держит Telegram client, другие stdio processes — IPC proxies; [serve.ts](https://github.com/mcp-telegram/mcp-telegram/blob/69bfe549fbb307168207b3590e86095237753bac/src/serve.ts) отказывается стартовать вторым владельцем. Есть unit tests lock/IPC/flood waits. Однако [master.ts](https://github.com/mcp-telegram/mcp-telegram/blob/69bfe549fbb307168207b3590e86095237753bac/src/master.ts) `withTimeout` явно оставляет promise работать и освобождает globalLock после timeout. Вывод: очередь больше не гарантирует отсутствие пересечения поздней записи с новым действием; unknown outcome нужно учитывать отдельно. [rate-limiter.ts](https://github.com/mcp-telegram/mcp-telegram/blob/69bfe549fbb307168207b3590e86095237753bac/src/rate-limiter.ts) повторяет переданную closure после network/timeout, не имея параметра persisted operation identity. Нельзя переносить общий retry helper на mutating sends без доказанной idempotency каждой closure. Local socket permissions не заменяют multi-host fencing.

### better-tg-cli

[package](https://github.com/TheVilfer/better-tg-cli/blob/13cc02b1be0f6d8fd00c145a6cab919f9cb8cfdb/package.json), [MCP](https://github.com/TheVilfer/better-tg-cli/blob/13cc02b1be0f6d8fd00c145a6cab919f9cb8cfdb/src/mcp.ts), [HTTP](https://github.com/TheVilfer/better-tg-cli/blob/13cc02b1be0f6d8fd00c145a6cab919f9cb8cfdb/src/mcp-http.ts). Более сильная локальная safety-by-construction модель: общий dispatcher для stdio/HTTP, write/read tools, read tool проставляет `TG_READ_ONLY`, write state в OS secret store, TTL write enable, Bearer compare через digest/timingSafeEqual, HTTP запрещает browser Origin.

Но это CLI per-call child/worker со строковым argv schema, общим bearer для local account и OS keychain/1Password dependencies; не tenant-aware FastAPI worker. На timeout [mcp.ts](https://github.com/TheVilfer/better-tg-cli/blob/13cc02b1be0f6d8fd00c145a6cab919f9cb8cfdb/src/mcp.ts) убивает child SIGKILL и возвращает generic timeout. Это не отменяет уже принятую Telegram запись и не даёт receipt recovery. Serial queue внутри одного HTTP process не координирует отдельные CLI/stdio processes. Брать dispatcher parity + negative write tests и principle secret-store separation, а не spawning/approval UX/secret provider весь пакет.

## Инвариант сессии: официальный источник важнее комментариев wrapper

[Telegram errors](https://core.telegram.org/api/errors), раздел 406, явно описывает `AUTH_KEY_DUPLICATED`: параллельные main TCP/MTProto sessions на non-media DC могут конфликтовать **даже с одного IP**. Исключение — сервер явно разрешил `tmp_sessions>1`; media transfer sessions отдельно. При получении ошибки ключ уже инвалидирован и требуется новая authorization.

Вывод для reuse: не копировать chigwell `shared`-lock reasoning «same IP safe» и startup retry terminal key; не использовать session-string pool для обхода account rate limits. Один owner на authorization key — базовое безопасное правило; failover должен прекратить старый connection прежде нового owner. Наличие row lease в DB само по себе не прекращает живую TCP session старого процесса.

## Что переносить первым и как проверять

| Reuse | Источник | Проверка в Gate |
|---|---|---|
| MTProto engine/TL/auth primitives | Telethon dependency | Pinned artifact hash, fake-client contract suite; без live login |
| Durable unknown/receipt/fingerprint cases | tolboy SendOperationJournal tests | Crash до send, после send до commit; same key+different payload; late success; restart; no blind resend |
| Session ownership failure cases | chigwell local lock/pool tests + mcp-telegram IPC tests | Two workers/hosts, lease loss, frozen old process, permanent revoked/duplicated key |
| Flood/SlowMode mapping | Telethon typed errors + chigwell test scenarios | Persisted retry_at, no in-request long sleep, cancellation budget, no immediate retry loop |
| Tool/chat allowlist and file guards | chigwell tests | Denied read/write through all entrypoints; unknown ID, account mismatch, traversal/symlink |
| Shared dispatcher/transport policy | better-tg-cli + tolboy dispatch tests | Same tool schema/results/errors/permissions/deadline semantics for REST/MCP HTTP/stdio |
| Cold peer/entity behavior | Telethon Session + chigwell resolve tests | Restart with only encrypted StringSession; known peer id lacking access_hash; deterministic handling |
| Send rendering/TL fixtures | Telethon pure unit tests | Raw request contains persisted random_id; formatting/media respects adapter contract |

Нельзя считать `tool annotations`, localhost binding, encrypted string или dependency retry заменой этим инвариантам. Тесты wrappers — материал для Gate-specific tests; upstream test suite здесь не запускалась и его прохождение не утверждается.

## Условия смены решения

- **Telethon → TDLib:** обязательная feature/auth/update-state capability отсутствует/нестабильна в Telethon и не решается узким adapter; доказанные повторяющиеся update recovery failures; готовность эксплуатировать native worker и отдельную локальную state DB. До решения — capability matrix и одинаковые recorded/fake сценарии по обоим adapters.
- **Telethon → gotd:** Python engine измеримо не выполняет нужные latency/memory/concurrency goals, а Go prototype выполняет их при одинаковых safety semantics. Учитывать полный service cost, не README benchmark.
- **Telethon → Kurigram/teleproto:** только специфический блокирующий feature gap плюс подтверждённое качество нужных source paths и release provenance. Текущая Python reuse economy не даёт причины такого перехода.
- **Целиком MCP-wrapper:** только если его account/auth/persistence/lease/send lifecycle становится единственным owner и реально заменяет, а не дублирует Gate control plane. Для текущего Python+PostgreSQL Gate это условие ни одним просмотренным проектом не доказано. Даже сильный tolboy требует существенной интеграции и второго стека; перенос его invariants выгоднее whole-service adoption.

Решение устойчиво к изменению популярности проектов: менять его по feature/maintenance/operational доказательствам. Максимальное переиспользование здесь — зрелая протокольная библиотека плюс проверенные recovery/security test scenarios, без импорта чужой application policy и параллельного state owner.


## Дополнительные решения: session persistence, очередь и owner setup

### Session: сохранить готовый формат, не писать полный Telegram storage

Для командного режима использовать built-in StringSession и небольшой encrypted persistence adapter поверх существующего PostgreSQL. StringSession сериализует auth/DC; выбранные typed peer/access_hash хранятся отдельными защищёнными pins Gate. Полный update-state/entity mirror здесь не требуется.

Исходники Telethon1.44 вызывают await maybe_async для save/close, auth-key callback и ряда state hooks. Но utils.maybe_async прямо помечает async sessions как experimental: это основание для узкого version-pinned adapter и contract tests, не обещание произвольных async overrides. Свойства остаются синхронными; MemorySession entity lookup не превращается автоматически в async.

Provider-free эксперимент выполнен на Telethon1.44.0: generated synthetic auth, encrypted in-memory repository, штатный StringSession round-trip, реальный _auth_key_callback, auth/DC transition, transient clone, persistence failure и async close. Все шесть проверок прошли; Telegram network calls0. PostgreSQL, live DC migration и deploy/restart не проверены этим экспериментом. V0 обязан добавить эти boundaries в воспроизводимые fixtures и повторить совместимость на выбранном pin.

Ключевые требования к обёртке:
- Вызывать StringSession.save(instance) явно; один сериализованный async save и CAS revision.
- clone возвращает transient MemorySession, не второй writer основного encrypted record.
- При обнулённом auth_key во время migration не публиковать пустую строку как готовую авторизацию; сохранять not-ready transition.
- Persistence/ownership failure прекращает новые effects. Политика recovery не должна молча поднимать старый credential как актуальный.
- QR enrollment может временно требовать updates; обычный runtime не запускает глобальный ingest.

Старый SQLAlchemy adapter уже рассмотрен выше. Ещё один reference — [mautrix-telegram Python-final PgSession](https://github.com/mautrix/telegram/tree/e8114ff5ad642446162618e9453d2195e9480e05): AGPL-3.0-or-later, зависимость tulir-telethon==1.99.0a6 и собственный async_db. Его async entity methods не являются drop-in для upstream Telethon1.44. Текущий mautrix с v26.04 переписан на Go. Наблюдение transient clone полезно; копирование bridge или AGPL реализации не требуется.

### Очередь: один журнал вместо двух систем состояния

Gate уже использует PostgreSQL, SQLAlchemy asyncio/asyncpg и короткий claim. Обязательная Operation/Attempt модель получает queued/available_at/owner/state; это одновременно durable очередь и продуктовый журнал. Идемпотентность начинается с prepare: стабильный scoped request key, повтор возвращает ту же operation, mismatch даёт conflict. Не держать транзакцию во время Telegram RPC. Polling достаточен для одного RU worker; LISTEN/NOTIFY можно добавить только как ускоряющий сигнал с обязательным startup/reconnect/periodic scan.

| Решение | Проверенный source/version | Вывод |
| --- | --- | --- |
| Gate native operation queue | [текущий claim pattern](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/infrastructure/telegram_processing.py) | Использовать механизм claim, отдельно реализовать outbound UNKNOWN semantics |
| Procrastinate | 3.10.0, MIT, [release source](https://github.com/procrastinate-org/procrastinate/tree/8b6398329d915b7299c0d936e567facaebea8903) | Поддерживаемый PG scheduler, но добавляет schema/job lifecycle/psycopg pool; оставить для общей будущей потребности |
| Taskiq + taskiq-postgres | 0.13.0 / 0.9.1, MIT; [broker source](https://github.com/danfimov/taskiq-postgres/blob/4922d4b7a32f818da32d8a0d74c0f61a7f92a4cc/src/taskiq_pg/asyncpg/broker.py) | В проверенном broker нет startup/periodic backlog scan, processing lease recovery и durable delayed wake-up; не выбирать |
| Authlib | Документация1.8.0, BSD-3-Clause, [Starlette integration](https://docs.authlib.org/en/stable/oauth2/client/web/starlette.html) | Если Gate не имеет готового browser OIDC клиента, использовать для protected owner setup; version/dependency compatibility проверить до pin |

Taskiq PostgreSQL result backend и PostgreSQL broker — разные компоненты. Наличие сохранённых результатов не обеспечивает durable delivery. Procrastinate также не решает атомарность внешнего Telegram effect: его [stalled-job recovery](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html) нельзя превращать в повтор UNKNOWN send.

Основание выбора: [PostgreSQL SKIP LOCKED](https://www.postgresql.org/docs/18/sql-select.html), [LISTEN startup race](https://www.postgresql.org/docs/18/sql-listen.html), [NOTIFY semantics](https://www.postgresql.org/docs/18/sql-notify.html). Нотификации не заменяют durable rows; Telegram effect не входит в транзакцию PostgreSQL.

## Gate source-аудит: что мешает бесконфликтному MTProto

Pinned source: main461088d68d047e4cd08c7a4f581300d591af241e. Это статические выводы о коде; activation каждого writer и наличие дефекта в конкретных production данных этим не доказаны. Реальная RU конфигурация проверяется отдельным scoped inspection в V0/V1.

| Находка | Точное основание | Требуемое изменение |
| --- | --- | --- |
| Shared fence не универсален | personal_telegram_fence.py54–171 требует ReplyDraft/Conversation; другие writers перечислены ниже | Standalone operation + общий account/typed-peer admission; guard на конечном RU Business sink |
| Смена Business connection может обходить Conversation lock | domain.py368–486 включает connection_id в identity; reply_sends.py1200–1260 блокирует Conversation | Общий RU peer key + reserved/sending/unknown по всем Conversations и AgentSendAttempt/Mayak journals |
| Canonical uniqueness не включает Account | models/canonical.py51–179: workspace+connector+dialog, затем dialog+message | Additive MessageIdentity/Observation, без fake bot и глобального backfill |
| ACK и echo могут создать две legacy строки | reply_sends.py877–920 без canonical_message_id; inbox47–132 пишет по canonical identity | Atomic exact provider identity mapping; ACK/echo order-independent |
| Outgoing echo может закрыть новый inbound task | inbox126–132,376–387 закрывает все NEEDS_REPLY; sender825–866 знает covered_task_ids | Причинное закрытие frozen task set; ad-hoc/manual outgoing не закрывает всё |
| OAuth partial claims могут расширяться через workspace fallback | mcp_routes.py680–787 | Независимый immutable RU intersection на каждом tool/job/cursor/file/setup пути |
| confirmed=true не независимое свидетельство человека | mcp_routes.py3559+; reply_sends application80–96 | Protected owner delegation + exact operation binding; host approval отдельно |
| Документы привязаны к существующим задачам | application/infrastructure reply_documents; существующий baseline8MiB | Standalone personal media + ранний реальный Work file spike |
| Настройки локального sender уже ограничивают retry | app/personal_telegram/local_runtime.py176–184 | Сохранить request/connection retries0 и auto_reconnect=False в worker |

Ссылки на pinned исходники:
- [personal fence](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/infrastructure/personal_telegram_fence.py), [reply sender](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/infrastructure/reply_sends.py).
- [Business inbox](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/infrastructure/telegram_business_text_inbox.py), [canonical models](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/models/canonical.py), [legacy models](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/models/domain.py).
- [MCP routes](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/api/mcp_routes.py), [OAuth validation](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/core/mcp_oauth.py), [local runtime](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/personal_telegram/local_runtime.py).

### Business-capable send/edit inventory

Найдено 17 send call sites и 3 edit call sites в 13 writer files. Это полный static inventory по проверенному дереву, не утверждение, что все endpoints сейчас включены. Pure bot sends без business_connection_id не являются отправкой от личного RU аккаунта.

| Файл / строки | Назначение | Нынешний общий personal fence |
| --- | --- | --- |
| app/infrastructure/reply_sends.py208,232,241,248 | Modern MCP draft: document/rich/formatted/plain | Да, ограничен Conversation/draft |
| app/infrastructure/universal_mayak_delivery.py212 | Automatic agent Business reply, отдельный AgentSendAttempt | Нет |
| app/api/sprint17_send_draft_routes.py207 | Отправка draft | Нет |
| app/api/sprint9_routes.py278,364 | Отправка draft; admin reply | Нет |
| app/api/sprint14_routes.py433 | Business ping/admin/result | Нет |
| app/api/sprint14b_routes.py191 | Scoped admin reply | Нет |
| app/api/sprint15b_routes.py134 | Scoped admin reply | Нет |
| app/api/sprint16_raw_routes.py108 | Raw-view admin reply | Нет |
| app/api/sprint16d_clean_inbox_routes.py114 | Clean-inbox admin reply | Нет |
| app/api/sprint17_send_draft_routes.py111 | Command acknowledgement | Нет |
| app/api/sprint18b_draft_ux_routes.py107 | Draft UX reply | Нет |
| app/api/routes.py339,405 | Base admin/ping reply | Нет |
| app/api/routes.py758 | Edit test-guide card | Нет |
| app/api/sprint10_routes.py236 | Edit saved draft card | Нет |
| app/api/sprint11b_routes.py281 | Edit reply markup | Нет |

Root всех ссылок таблицы: [проверенное дерево Gate](https://github.com/AlphaCenter/gate/tree/461088d68d047e4cd08c7a4f581300d591af241e). Дополнительные wrappers: sprint10_routes.py488–539,566 меняют dispatch sprint9; sprint11b_routes.py134 вызывает wrapper; проверки draft status не равны durable reservation.

Общий transport sink [app/services/telegram.py](https://github.com/AlphaCenter/gate/blob/461088d68d047e4cd08c7a4f581300d591af241e/app/services/telegram.py): send_message66, send_plain_message105, send_formatted_message132, send_rich_message160, send_business_document181, edit_message_text253, edit_message_reply_markup290. Сейчас эти HTTP-методы не требуют shared RU reservation.

Правило rollout: каждый достижимый RU writer либо подключается к admission, либо явно отказывает до HTTP, либо доказанно недостижим/default-off в выбранном rollout. Итоговая совместимость не может означать молчаливое отключение действующего Business сценария. Общие изменения должны сохранять поведение других аккаунтов; их реальная история не нужна для тестов.

## Первичные контракты, влияющие на ТЗ

| Источник | Проверенный контракт | Проектный вывод |
| --- | --- | --- |
| [Telegram updates](https://core.telegram.org/api/updates) | Account-specific message IDs; Business использует IDs подключённого пользователя; random_id связывает send/updateMessageID; server500 может следовать за effect | Typed account/peer/message mapping; не разрешать unknown по совпадению текста |
| [Connected Business bots](https://core.telegram.org/api/bots/connected-business-bots) | Connection меняется с настройками; выбранные/paused chats; ограниченный набор bot methods | Business transport остаётся отдельным; нет автоматического MT fallback или pause toggle |
| [Bot API](https://core.telegram.org/bots/api) | Business rights, separate edits/deletes, sender_business_bot provenance | Не выводить ownership/direction из отсутствия одного поля |
| [Bot API IDs](https://core.telegram.org/api/bots/ids) | User/chat/channel ID spaces различаются | Account + peer_type + ID вместо одного integer |
| [Telegram errors](https://core.telegram.org/api/errors) | AUTH_KEY_DUPLICATED возможно и на одном IP; key invalidated | Один main connection owner, termination перед takeover |
| [Railway deployment teardown](https://docs.railway.com/deployments/deployment-teardown) | Deploy может иметь old/new overlap | replicas=1 недостаточно для session ownership |
| [OpenAI plugin reference](https://developers.openai.com/plugins/reference) | File parameters/helpers и tool annotations имеют конкретный host contract | Проверить реальный Gate M/Work, не считать документацию доказательством frontend поддержки |
| [Work cloud security](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-cloud-security) | Host permissions/approvals и server account authority различаются | OAuth не является signed evidence конкретной реплики |
| [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | Tool metadata/annotations — описания для клиента | Enforcement остаётся на сервере |
| [Telegram API Terms](https://core.telegram.org/api/terms), [Content Licensing](https://telegram.org/tos/content-licensing) | AI-use и context-sensitive ограничения; Business docs отдельно упоминают AI assistants | До live AI-read установить конкретный допустимый контекст; не объявлять blanket permission или blanket ban |

## Итог выбора и границы доказательств

Переиспользуется: Telethon protocol/auth/TL, Gate OAuth/MCP/SQLAlchemy/PostgreSQL, существующие send/group foundations, форматирование и проверенные test scenarios открытых проектов. Новая разработка ограничена Gate-specific account admission, standalone operations, durable session owner, Business reconciliation, Work capability adapters и cloud lifecycle.

Не добавляются без нового основания: второй Telegram MCP server, второй OAuth server, новый broker, полный account mirror/update engine, vector index, отдельный LLM provider, перенос на JVM/Go/TypeScript.

Это source-backed архитектурное исследование. Полного security audit всех dependencies, нагрузочного benchmark, live Telegram tests, новых secret reads/config changes/DB migrations/deploy не было. Пройден только описанный provider-free synthetic Session experiment; существующий production MTProto по redacted status не подключён. Решение о готовности принимается по V0–V7 и acceptance T01–T28 в [основном ТЗ](MTPROTO_RU_ARCHITECTURE_AND_SPRINTS.md), а не по объёму документации.


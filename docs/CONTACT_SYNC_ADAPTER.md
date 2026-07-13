# PR: offline Telegram Desktop contact adapter with user-session fallback

## Цель

Добавить отдельный одноразовый CLI-компонент, который получает доступные имена и Telegram user ID из локального Telegram Desktop JSON export либо из Telegram user-session и публикует только очищенные account-scoped snapshots в существующий `POST /admin/contact-snapshots`.

Этот компонент не запускает постоянный MTProto worker и не отправляет сообщения. Предпочтительный offline path не требует Telegram API credentials или user-session: оператор передаёт локальный `result.json`, а parser извлекает только identity владельца и пары user ID/name. MTProto остаётся fallback для полного phone-to-ID matching.

## Компоненты

- `app/contact_sync/telethon_reader.py` — только `contacts.getContacts` и `contacts.getSaved` через contact-only takeout;
- `app/contact_sync/desktop_export_reader.py` — offline parsing `result.json` без phone/message output;
- `app/contact_sync/service.py` — нормализация, phone match, детерминированные revision и чанки до 1000;
- `app/contact_sync/gate_client.py` — публикация через защищённую границу Gate;
- `app/contact_sync/cli.py` — команды `authorize` и `sync`;
- `Dockerfile.contact-sync` — отдельный image, не меняющий runtime основного Gate service.

## Источник имени и identity match

### Telegram Desktop export

Desktop JSON содержит Telegram user ID в `personal_chat` и `frequent_contacts`, но `contacts.list` содержит только phone-book names/phones без Telegram user ID. Поэтому offline importer:

1. проверяет `personal_information.user_id` и передаёт его Gate для fail-closed сверки с target account;
2. предпочитает непустое имя из `personal_chat`, затем использует `frequent_contacts` как fallback;
3. игнорирует message bodies, groups, channels, media и phone values;
4. не угадывает соответствие оставшихся phone-only contacts;
5. отражает их только aggregate `skipped_contacts` count.

Это даёт надёжные имена для экспортированных личных диалогов, но не является полным импортом всей телефонной книги.

### Telegram user-session fallback

Telegram `contacts.getSaved` возвращает сохранённые phone-book first/last name и требует takeout session. `contacts.getContacts` возвращает Telegram users с user ID и доступным phone. Адаптер:

1. нормализует phone до десятичных цифр;
2. выбирает последнюю сохранённую запись для дубликата phone;
3. сопоставляет saved phone с Telegram user phone;
4. отправляет только `(telegram_user_id, saved display_name, optional username)`;
5. пропускает записи без надёжного phone → user ID match;
6. сортирует результат по Telegram user ID перед вычислением revision.

Phone numbers и исходный saved-contact список не передаются в Gate.

## Read-only граница

Разрешённые Telegram-вызовы:

- проверка авторизованной user-session;
- чтение текущего пользователя для инициализации takeout;
- `contacts.getContacts`;
- `contacts.getSaved` внутри takeout с `contacts=true`, остальные export flags выключены.

Запрещены:

- message/history/dialog reads;
- `send_message` и любые send/edit/delete operations;
- import/add/delete contacts;
- обработка Telegram updates;
- использование Bot API send path;
- автоматическая повторная авторизация.

## Конфигурация

Только имена переменных, реальные значения хранятся как secrets:

```text
TELEGRAM_CONTACT_API_ID
TELEGRAM_CONTACT_API_HASH
TELEGRAM_CONTACT_PHONE
TELEGRAM_CONTACT_SESSION_STRING
TELEGRAM_CONTACT_SESSION_FILE
GATE_CONTACT_SYNC_ACCOUNT_ALIAS
GATE_CONTACT_SYNC_URL
GATE_CONTACT_SYNC_BEARER_TOKEN
```

Для `sync` задаётся ровно один session source: string или file. Session string, API hash и bearer token запрещено передавать как CLI arguments, печатать, логировать, сохранять в Gate DB или коммитить.

`GATE_CONTACT_SYNC_BEARER_TOKEN` в текущей одноразовой v1-схеме содержит secret, соответствующий серверному `ADMIN_BEARER_TOKEN`. Перед превращением адаптера в постоянно работающий schedule необходим отдельный service-to-service credential и server-side scope.

## One-time onboarding

Предпочтительный Desktop path:

1. В Telegram Desktop экспортировать JSON отдельно для каждого аккаунта.
2. Выполнить `gate-contact-sync sync --desktop-export <result.json>` с точным account alias и secret-backed Gate bearer token.
3. Проверить только aggregate result; raw export не копировать в Git, Docker image, Railway volume или логи.
4. Gate отвергает export, если owner Telegram user ID не совпадает с target account.

MTProto fallback:

1. Установить package с extra `contact-sync`.
2. В secret-backed окружении задать Telegram API ID/hash и phone.
3. Выполнить `gate-contact-sync authorize --session-file <name>.session`.
4. Переместить mode-0600 session file в одобренное secret storage; не помещать его в Git, обычные артефакты или логи.
5. Для каждого аккаунта выполнить `gate-contact-sync sync` с его session и точным Gate account alias.
6. Проверить только aggregate result и Gate audit counts; не публиковать список контактов.

Telegram может отложить инициализацию takeout. Адаптер возвращает безопасный bounded delay и не делает скрытые retries.

## Идемпотентность и чанки

- contacts сортируются детерминированно;
- SHA-256 считается по всему очищенному snapshot;
- до 1000 contacts отправляются одним chunk;
- для нескольких chunks revision содержит snapshot digest, index и total;
- повтор того же snapshot безопасно replay-ится;
- частичный сбой исправляется повторным запуском с тем же содержимым;
- response содержит только aggregate counts.

## Acceptance criteria

1. Основной Gate image не устанавливает Telethon и не зависит от user-session.
2. Отдельный contact-sync image собирается с pinned Telethon.
3. Sync не авторизует истёкшую/пустую session автоматически.
4. Reader использует только contact-only Telegram requests.
5. Private saved names берутся из `contacts.getSaved`, а не угадываются из profile/message text.
6. Phone используется только в памяти для identity match и не отправляется в Gate.
7. Unmatched contacts пропускаются и отражаются только aggregate count.
8. Более 1000 matches публикуются детерминированными bounded chunks.
9. Ошибки и CLI output не содержат token, session, phone, names или Telegram IDs.
10. Ruff, mypy, pytest, Alembic и оба Docker build проходят.
11. После merge основной Railway service работает на exact merge commit; health, MCP OAuth и RU/BY Business connection health не ухудшились.
12. Desktop export path не требует Telegram API credentials/session и не публикует phone/message fields.
13. Source account identity проверяется сервером до любого contact upsert.

## Не входит в PR

- создание или изменение production secrets;
- постоянный scheduler/service в Railway;
- чтение/импорт Telegram messages;
- backfill historical numeric display names;
- MCP display cutover;
- contact deletion/full replace;
- автоматическая отправка сообщений.

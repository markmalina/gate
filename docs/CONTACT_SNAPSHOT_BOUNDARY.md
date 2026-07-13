# PR: защищённая граница импорта снимка Telegram-контактов

## Цель

Добавить в Gate ограниченный, защищённый и идемпотентный вход для сохранённых имён контактов из отдельного read-only Telegram user-session адаптера.

PR создаёт только принимающую границу Gate. Он не добавляет Telegram user-session, не выполняет авторизацию Telegram-аккаунта, не читает контакты самостоятельно и не отправляет сообщения.

## Продуктовый результат

Gate сможет принять для существующего аккаунта RU или BY очищенный снимок:

```text
Gate account
+ Telegram user ID
+ сохранённое Марком имя
+ optional username
+ revision
```

и обновить существующие `Person` / `PersonIdentity` так, чтобы сохранённое имя имело приоритет над именем Telegram-профиля.

## API v1

```http
POST /admin/contact-snapshots
Authorization: Bearer <ADMIN_BEARER_TOKEN>
Content-Type: application/json
```

Запрос:

```json
{
  "account_alias": "telegram_mark_ru",
  "revision": "contacts-export-2026-07-13T12:00:00Z",
  "contacts": [
    {
      "telegram_user_id": "123456789",
      "display_name": "Сохранённое имя",
      "username": "optional_username"
    }
  ]
}
```

Ответ не возвращает список контактов:

```json
{
  "status": "ok",
  "account_alias": "telegram_mark_ru",
  "revision_sha256": "<sha256>",
  "counts": {
    "received": 1,
    "created": 0,
    "updated": 1,
    "unchanged": 0
  }
}
```

## Ограничения входа

- только существующий активный Gate account;
- `account_alias`: 1–128 символов;
- `revision`: 1–128 символов;
- 1–1000 контактов в одном запросе;
- `telegram_user_id`: только десятичные цифры, до 255 символов;
- `display_name`: непустое нормализованное значение, до 255 символов;
- `username`: optional, до 255 символов, ведущий `@` удаляется;
- неизвестные поля запрещены;
- повторяющиеся Telegram user ID внутри одного пакета запрещены.

## Авторизация

В v1 используется уже настроенный `ADMIN_BEARER_TOKEN`, чтобы релиз не требовал новой production-конфигурации.

Это временная граница для первого PR. Перед подключением отдельного постоянно работающего user-session адаптера необходимо выделить отдельную service-to-service авторизацию. Ослаблять OAuth/MCP или публиковать endpoint без bearer-защиты запрещено.

## Правила хранения

Для каждой записи:

- identity ищется по `(account_id, platform="telegram", platform_user_id)`;
- новая identity создаёт нового `Person`;
- сохранённое имя записывается в `PersonIdentity.metadata.telegram_contact_name`;
- revision записывается в `telegram_contact_revision`;
- source фиксируется как `telegram_user_session`;
- `Person.display_name` получает сохранённое имя;
- Telegram profile name сохраняется отдельно и не затирается;
- одна и та же Telegram identity может иметь разные сохранённые имена в RU и BY.

Отсутствующий в пакете контакт не удаляется. v1 является идемпотентным upsert, а не destructive full-replace.

## Идемпотентность и конфликт

- hash revision и hash всего нормализованного пакета фиксируются в безопасном import-ledger через `AuditLog`;
- точный повтор запроса с той же revision возвращает `unchanged`, даже если после него уже применялись более новые revisions;
- старый replay не может откатить новое сохранённое имя;
- та же revision с любым другим содержимым пакета возвращает conflict;
- новая, ранее не обработанная revision может обновить сохранённое имя;
- commit выполняется один раз после успешной обработки всего пакета;
- при ошибке весь пакет откатывается.

## Аудит и приватность

`AuditLog` содержит только:

- действие `telegram_contact_snapshot_imported`;
- account id;
- hash revision;
- received/created/updated/unchanged counts.

Запрещено писать в аудит и операционные логи:

- список контактов;
- имена контактов;
- Telegram user ID;
- username;
- Telegram session, API hash, auth key или токены.

## Acceptance criteria

1. Endpoint недоступен без настроенного admin token.
2. Неверный bearer возвращает 403 до чтения/изменения данных.
3. Неизвестный или неактивный account отклоняется.
4. Один контакт создаёт `Person` и account-scoped `PersonIdentity`.
5. Существующая profile identity получает сохранённое имя, не теряя profile metadata.
6. Повтор идентичного пакета ничего не дублирует и не откатывает более новую revision.
7. Повтор revision с любым другим содержимым пакета отклоняется целиком.
8. Одинаковый Telegram user ID в RU и BY остаётся двумя независимыми identities.
9. Ответ и AuditLog не содержат контактные данные.
10. PostgreSQL integration test проверяет create → replay → update и account isolation.
11. `ruff`, `mypy`, `pytest`, Alembic upgrade/downgrade и Docker build проходят.
12. После merge production fingerprint соответствует merge commit, а Gate health, MCP OAuth и RU/BY connection health остаются исправны.

## Не входит в PR

- Telegram user-session процесс;
- хранение session string;
- импорт истории сообщений;
- удаление отсутствующих контактов;
- MCP tool для контактов;
- автоматическая отправка сообщений;
- изменение Railway/Auth0 secrets;
- backfill исторических сообщений и финальный display cutover.

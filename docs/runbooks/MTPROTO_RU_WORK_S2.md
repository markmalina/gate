# Gate Work S2 — проверка подключения MTProto RU

[Задача Gate #696](https://github.com/markmalina/gate/issues/696)

S2 проверяет отдельную пользовательскую сессию MTProto RU и подтверждает её
владельца через Telegram. Telegram Web использует собственную сессию. BY можно
использовать вторым участником в живых тестах последующих этапов.

## Изолированный стенд

1. Подготовить отдельный процесс и PostgreSQL стенда, установить пакет
   `.[personal-telegram]` той же ревизии. Проверить резервную копию и довести
   схему до `0125_personal_runtime_check`. Использовать отдельный UUID
   привязки, закреплённый за Work-пространством и OAuth-владельцем RU.
2. Настроить через хранилище секретов `GATE_PERSONAL_RUNTIME_DATABASE_URL`,
   `GATE_PERSONAL_RUNTIME_WORKSPACE`, `GATE_PERSONAL_RUNTIME_OWNER`,
   `GATE_PERSONAL_RUNTIME_BINDING_ID`,
   `GATE_PERSONAL_RUNTIME_EXPECTED_PROVIDER_OWNER` и два отдельных ключа
   Fernet: `GATE_PERSONAL_RUNTIME_SESSION_KEY` и
   `GATE_PERSONAL_RUNTIME_COMMAND_KEY`. Использовать согласованное Telegram
   приложение с `GATE_PERSONAL_TELEGRAM_API_ID` и
   `GATE_PERSONAL_TELEGRAM_API_HASH`. Идентификатор аккаунта в Telegram сверить
   с владельцем в защищённом процессе подключения.
3. При наличии зашифрованной RU-сессии импортировать её в эту привязку командой
   `gate-personal-telegram-runtime import-existing`. Она использует
   существующий зашифрованный формат `gate.personal-telegram.cloud-session/v1`
   и переменные `GATE_PERSONAL_TELEGRAM_OWNER_USER_ID`, `…_SESSION_REF`,
   `…_SESSION_CIPHERTEXT`, `…_ENCRYPTION_KEY`. Повторный запуск сохраняет
   существующую привязку. При отсутствии сессии сначала требуется защищённый
   вход владельца в MTProto. Вход через Telegram Web его не заменяет.
4. Выполнить `gate-personal-telegram-runtime inspect`. Состояние
   `configured=true` подтверждает доступность зашифрованной сессии.
5. Только для процесса проверки выставить
   `GATE_PERSONAL_RUNTIME_ENABLED=true` и запустить
   `gate-personal-telegram-runtime run --seconds 300`. Во время работы
   повторно выполнить `inspect` с той же конфигурацией. Текущая проверка
   владельца подтверждается одновременно `runtime_connected=true`,
   `authenticated=true`, номером аренды и временем проверки. По истечении
   окна или при штатном сигнале остановки процесс отключается от Telegram.
6. После остановки получить `runtime_connected=false`. Повторить запуск:
   новая аренда должна получить следующий номер и заново проверить владельца.
   Если предыдущий процесс завершился с потерей связи с БД, проверить
   фактическое завершение процесса по
   [процедуре восстановления](MTPROTO_RU_SOURCE_FOUNDATION.md#ownership-and-recovery).

В отчёт включить ревизию, статусы до запуска, во время работы, после
остановки и после перезапуска. На этом этапе проверяем ноль отправленных
сообщений и созданных групп. Значения ключей, сессии и параметры входа хранятся
в защищённой среде.

## Импорт с тома существующего стенда

На отдельном Railway S2 используется `gate-personal-telegram-runtime bootstrap-volume`.
Эта команда допускает только штатный сервис с томом `/gate-vault` и приватную
PostgreSQL 17 базы `gate_s2_ru` на `gate-ru-store.railway.internal`. Она
выполняет миграции Gate до `0125_personal_runtime_check`, проверяет
аутентифицированный контейнер и идентичность Telegram-приложения, затем
перешифровывает сессию под отдельным ключом командного хранилища. ID владельца
берётся внутри процесса из контейнера проверенного входа. Секрет и идентификатор
владельца не выводятся. Привязка использует заданного реального владельца Work.

На первом запуске образа `Dockerfile.personal-s2` оператор выставляет
`GATE_S2_BOOTSTRAP_COMMAND_STORE=true`. Образ вызывает CLI в отдельном
процессе при запуске стенда. Успех подтверждает отметка
`s2_ru_command_store_verified_prepared` после чтения записи из базы,
повтор на той же сессии даёт `s2_ru_command_store_verified_already_prepared`.
Другая сессия не перезаписывает привязку.
После успешной сверки выключить bootstrap. Для последующего запуска рабочего
процесса на этом стенде команды `inspect --s2-owner` и `run --s2-owner`
получают ожидаемый Telegram ID из того же тома.

## Одноразовая проверка жизненного цикла

Для одного явно зарегистрированного S2-прогона оператор задаёт уникальный
`GATE_S2_RUNTIME_CANARY`. Стенд сначала сохраняет неизменяемую отметку на
защищённом томе, затем запускает отдельный `run --s2-owner`. Приёмка требует
живого `getMe`, свежего `runtime_connected=true`, отказа второму процессу до
создания Telegram-клиента, штатной остановки, следующего номера аренды после
перезапуска и конечного `runtime_connected=false`. Отдельный fail-closed probe
использует неверную одноразовую учётную строку той же приватной базы и должен
остановиться до Telegram-клиента. База и её действующие настройки не меняются.

Успех обозначается только журналом
`s2_ru_lifecycle_verified_twice_and_disconnected`. Проверка не имеет
диспетчера обновлений или команд, не читает диалоги и не умеет отправлять
сообщения, создавать группы или вызывать бота. Повтор того же идентификатора
запрещён. После подтверждения переменную выключают без нового развёртывания.

## Защищённый статус в ChatGPT Work

S2 открывает `/s2/work-status` только с отдельным 256-битным bearer token из
Railway variables. Ответ читает command store через `inspect --s2-owner`, не
подключается к Telegram и содержит только account RU, configured/authenticated,
lease generation, health, last successful check и явные нули разрешений на
отправку и создание групп. Ответы не кэшируются. Неверный токен получает 404.

Owner-OAuth S1 MCP хранит точный URL и тот же token в переменных
`GATE_PERSONAL_STAGE_S2_STATUS_URL` и
`GATE_PERSONAL_STAGE_S2_STATUS_TOKEN`. Его существующая `readiness` сохраняет
синтетический `runtime_connected=false` для command dispatcher и отдельно
возвращает вложенный `s2_status`. Поэтому status не активирует очередь или
Telegram writer. При недоступности bridge вызов завершается
`s2_status_unavailable`, а не использует старое состояние.

Для живой приёмки один уникальный
`GATE_S2_WORK_STATUS_ACCEPTANCE` запускает bounded read-only worker после
неизменяемого claim на томе. Work должен увидеть `s2_status.runtime_connected`
сначала false, затем true с новым `last_successful_check`, а после очистки флага
и перезапуска снова false. Процесс при завершении приложения обязан подтвердить
disconnect. На всём этапе исходящие сообщения, диалоги, группы и bot actions
остаются недоступны.

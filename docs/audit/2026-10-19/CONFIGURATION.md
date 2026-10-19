# Конфигурация и режимы запуска

Baseline 86dbc0f429dce089bf733fc422f20f556535895b. Значения secrets не собирались.

## 1. Источники и порядок

Settings(BaseSettings) читает environment/.env и validation_alias. get_settings кеширует объект на процесс. Отдельные paths читают os.environ/os.getenv напрямую. Railway start command экспортирует ряд значений перед uvicorn; они перекрывают более ранние значения shell/env этого запуска.

Поэтому defaults Settings, .env.example, repository railway.toml и фактически применённая provider configuration — четыре разных утверждения.

## 2. Текущий source start

Основной railway.toml:

- preDeployCommand: alembic upgrade head;
- GATE_WATCH_VOICE_ENABLED=false принудительно;
- GATE_WATCH_VOICE_MARK_OIDC_ENABLED=true;
- pinned private caller workflow SHA256;
- GATE_DOCUMENT_SEND_ENABLED=true;
- GATE_RICH_SEND_ENABLED получает существующее значение или defaulttrue;
- uvicornapp.main:app, host0.0.0.0, portPORT либо 8000;
- healthcheck/ready; retry policyON_FAILURE, max3.

Staging override содержит только uvicorn. Реальная effective provider конфигурация в этом аудите не наблюдалась. #736/PR766 — proposed explicit driver mode, не baseline.

## 3. Семейства настроек

| Семейство | Что определяет | Что не доказывает |
| --- | --- | --- |
| DATABASE_URL |Подключение asyncSQLAlchemy|Правильный target/schema/backup |
| TELEGRAM_* |Credentials, usernames, webhook secrets, connectors|Grant на чтение/отправку |
| MCP_* / GATE_MCP_ENABLED |Auth mode, resource/JWKS, claim mapping, temporary read grants|Доступность перекрытых tools; all data authorization |
| GATE_SEND_ENABLED и account flags |Дополнительные send conditions|Exact draft/recipient approval |
| Document/rich |Staging/cleanup/runtime format gate|Business account capability и клиентское отображение |
| Telegram/sequence workers |Запуск/polling|Leader/health всех workers или атомарный inbound stop |
| Alpha/Beta/BECOME flags |Default-off product contours|Consent, installation readiness, owner-only cap |
| OpenAI/model settings |Server credential/endpoint/model selection|Новый model grant или funded quota |
| Dropbox Calls |Account/app credentials, intake/poll|Право на любые старые записи |
| Watch |Data gate/workspace/driver/source filter/writer/OIDC pin|Private binding validity и future-only acceptance |
| PersonalTelegram |Encrypted custody и offline/cloud foundation|Connected session / право на live |
| AQUB flags |Historical compatibility settings|Разморозку AQUB |
| Deployment metadata |Source fingerprint/service labels|Runtime product readiness |

## 4. Флаги

| Environment name | Source default |
| --- | --- |
|GATE_MCP_ENABLED|False|
|GATE_SEND_ENABLED|True|
|GATE_RICH_SEND_ENABLED|False|
|GATE_DOCUMENT_SEND_ENABLED|False|
|GATE_DOCUMENT_CLEANUP_ENABLED|True|
|GATE_ACCOUNT_RU_SEND_ENABLED|True|
|GATE_ACCOUNT_BY_SEND_ENABLED|True|
|GATE_TELEGRAM_WORKER_ENABLED|False|
|GATE_ALPHA_CONNECTOR_ENABLED|False|
|GATE_BETA_CONNECTOR_ENABLED|False|
|GATE_BETA_CHECKPOINT_DISPATCH_ENABLED|False|
|GATE_BECOME_CONNECTOR_ENABLED|False|
|GATE_AQUB_MEDIA_FORWARD_ENABLED|False|
|GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED|False|
|GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED|False|
|GATE_BETA_SHADOW_WORKER_ENABLED|False|
|GATE_BETA_CANARY_ENABLED|False|
|GATE_BETA_KATYA_SPACE_ENABLED|False|
|GATE_BETA_KATYA_DELIVERY_ENABLED|False|
|GATE_BECOME_SHADOW_WORKER_ENABLED|False|
|GATE_BECOME_CANARY_ENABLED|False|
|GATE_BECOME_DELIVERY_ENABLED|False|
|GATE_BECOME_I4_CHAIN_EXECUTION_ENABLED|False|
|GATE_BECOME_I4_RECOVERY_EXECUTION_ENABLED|False|
|GATE_BECOME_VOICE_TRANSCRIPTION_ENABLED|False|
|GATE_BECOME_PILOT_SLICE1_ENABLED|False|
|GATE_BECOME_PILOT_SLICE2_ENABLED|False|
|GATE_BECOME_PILOT_SLICE2_DATA_GATE_APPROVED|False|
|GATE_BECOME_PILOT_SLICE3_ENABLED|False|
|GATE_BECOME_PILOT_SLICE3_DATA_GATE_APPROVED|False|
|GATE_BECOME_PILOT_SLICE3_DELIVERY_ENABLED|False|
|GATE_BECOME_PILOT_SLICE3_RELATIONS_ENABLED|False|
|GATE_ALPHA_SHADOW_WORKER_ENABLED|False|
|GATE_ALPHA_CANARY_ENABLED|False|
|GATE_BETA_CHECKPOINT_OIDC_ENABLED|True|
|GATE_ALPHA_DELIVERY_ENABLED|False|
|GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED|False|
|GATE_UNIVERSAL_MAYAK_DELIVERY_ENABLED|False|
|GATE_UNIVERSAL_MAYAK_MODEL_DATA_GATE_APPROVED|False|
|GATE_TELEGRAM_VOICE_ARCHIVE_ENABLED|True|
|GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED|False|
|GATE_DROPBOX_CALL_INTAKE_ENABLED|False|
|GATE_WATCH_VOICE_MARK_OIDC_ENABLED|False|
|GATE_WATCH_VOICE_ENABLED|False|
|GATE_WATCH_VOICE_DATA_GATE_APPROVED|False|
|GATE_ALPHA_MODEL_RUNTIME_ENABLED|False|
|GATE_ALPHA_MODEL_DATA_GATE_APPROVED|False|
|GATE_REPLY_SEQUENCE_WORKER_ENABLED|False|
|GATE_REPLY_SEND_RECOVERY_ENABLED|False|

Не все security-related switches defaultfalse. Например GATE_SEND_ENABLED и account send flags defaulttrue, но выполнение требует остальных scope/approval/connection условий. Archive/cleanup тоже могут быть defaulttrue. Поэтому фраза «всё default-off» неверна; default-off нужно называть для конкретной возможности.

Credential-presence Alfa worker — отдельный случай: он стартует при наличии OpenAI+Dropbox конфигурации, без собственного featureflag. Это findingA02, не рекомендуемый pattern для новых workers.

## 5. Полнота примера

161 validation_alias;118 assignments в.env.example;48 aliases отсутствуют. Пять example names не являются Settings aliases, но часть читается напрямую, поэтому это не автоматически unused/dead config.

Пропуски включают Watch/Calls, voice, send/recovery/sequence и deployment metadata. Полный список names/types/source lines/in_example/default expressions: [config-catalog.json](config-catalog.json). Source defaults там технические; реальных credential values нет.

Все 161 Field aliases включены, в том числе четыре parenthesized aliases. Прямые env reads дополнительно перечислены; не считать 161 исчерпывающим числом всех runtime environment names.

## 6. Безопасная смена

Назвать exact effect/mode/target/revision и источник authority. Проверить effective config через проектный допущенный operation route. Смена featureflag не обновляет credentials, durable grants, route generation, consent или spent counters. Credentials остаются server-side; новые secrets не создаются и не копируются из AQUB по общему поручению на документацию.

# Модель данных Gate

Основа:86dbc0f429dce089bf733fc422f20f556535895b. Это статический ORM/schema справочник, не dump и не introspection Production.

## 1. Сводка

113 ORM tables,1473 mapped column declarations,119 Alembic revisions. Единственный source head — `0115_watch_voice_post_payment`. Полный индекс моделей/полей/inline ForeignKey/строк — [data-model.json](data-model.json). Composite constraints и runtime SQL-функции проверять по source/migrations: этот индекс не заменяет их.

## 2. Семейства хранения

| ORM module | Tables | Назначение |
| --- | --- | --- |
| [agent.py](../../../app/models/agent.py) |5|Agent runtime и установки/инструкции/turns. |
| [alpha.py](../../../app/models/alpha.py) |4|Alpha group/review/delivery. |
| [alpha_preflight.py](../../../app/models/alpha_preflight.py) |1|Модельный preflight и evidence. |
| [alpha_transcription.py](../../../app/models/alpha_transcription.py) |2|Alfa long-file jobs и parts; отдельный provider path. |
| [alpha_voice.py](../../../app/models/alpha_voice.py) |2|Voice/transcription intake и jobs. |
| [aqub.py](../../../app/models/aqub.py) |4|Frozen AQUB bindings/routes/media; история сохраняется. |
| [aqub_live.py](../../../app/models/aqub_live.py) |1|Frozen downstream transport receipts. |
| [become.py](../../../app/models/become.py) |12|BECOME controlled/pilot media, transcription, consent и delivery state. |
| [become_program_draft.py](../../../app/models/become_program_draft.py) |3|Product program drafts и approvals. |
| [become_quest.py](../../../app/models/become_quest.py) |6|Quest/workflow/enrollment/projection. |
| [become_recovery.py](../../../app/models/become_recovery.py) |3|Durable recovery scopes/receipts. |
| [beta_context.py](../../../app/models/beta_context.py) |5|Encrypted context/checkpoint/export. |
| [beta_material.py](../../../app/models/beta_material.py) |2|Beta materials и enrichment. |
| [beta_space.py](../../../app/models/beta_space.py) |8|Beta isolated space/event/work/reply/admission. |
| [beta_wedding_voice.py](../../../app/models/beta_wedding_voice.py) |2|Исторический специализированный Beta binding/voice. |
| [billing.py](../../../app/models/billing.py) |4|Workspaces/entitlements/reservations/usage accounting. |
| [canonical.py](../../../app/models/canonical.py) |6|Workspace/connector/channel account/dialog/canonical message и identity mappings. |
| [domain.py](../../../app/models/domain.py) |26|Legacy транспорт, аккаунты, диалоги, задачи/черновики/попытки, raw receipts/outbox, voice/call archive, compatibility person/project. |
| [relationship_crm.py](../../../app/models/relationship_crm.py) |12|12 compatibility CRM tables; канонический домен Relations. |
| [watch_voice.py](../../../app/models/watch_voice.py) |5|Watch cursor/note/drain/recovery/post-payment journals. |

Все ссылки на source из этого каталога используют три уровня до root; точные immutable refs также доступны в JSON. Имена классов и таблиц не содержат реальные rows.

## 3. Основные связи и идентификаторы

| Семейство | Ключи и связи | Что проверять при изменении |
| --- | --- | --- |
| Legacy account/project | Account/Project UUID; Bot и Business connection | Account ownership/read/send flags, active connection, default/UNCLASSIFIED projection |
| Conversation/message/task | Conversation + Message → ReplyTask → ReplyDraft → SendAttempt | account/project совпадают во всей join цепочке; trigger/recipient не подменяется |
| Telegram receipt/job | bot_key + update_id; raw payload и processing state | Atomic insert, unique replay, lease generation, poison/dead-letter |
| Canonical message | Workspace → connector/account/dialog → canonical message | Receiving-account-relative identity; raw provider ID не глобальный person key |
| Agent installation | Opaque installation ref + workspace/connector/dialog | Exact approved binding, lifecycle state, capacity/consent |
| Billing | Scope/product/operation/period/reservation key | Workspace serialization, hard entitlement до I/O, unknown holds, usage actual |
| Watch | Workspace + Dropbox account/file/revision | Immutable source/hash/time, cursor, durable publication and permanent recovery caps |
| Alfa jobs | Workspace UUID + job/part/attempt_version | Source hash dedupe, lease/ownership, artifact hashes, unknown не retry |
| Beta checkpoint | Installation + sequence range/hash/recipient | Read не должен незаконно продвинуть cursor; ACK exact committed batch |
| CRM/пилоты | Собственные product IDs и projections | Не становиться вторым master Relations/LMS; consent и lifecycle имеют собственных владельцев |

Key и UUID намеренно не смешиваются. Из OAuth string claim нельзя без resolver делать произвольный foreign Workspace lookup. Test fixtures должны использовать типы production contract, а не случайные одинаковые строки.

## 4. Классы данных и lifecycle

| Класс | Где | Доступ/владелец | Что подтверждено и что ещё нужно |
| --- | --- | --- | --- |
| Raw Telegram update | PostgreSQL receipt/payload | Gate, exact receiving connector scope | Durable acceptance есть; общий TTL/delete/backup receipt не установлен |
| Canonical text / reply context | Message/canonical/task projections | Workspace/account/project principal | Scoped reads; correction/deletion требуется прослеживать по всем projections |
| Draft и send intent | ReplyDraft, document storage, SendAttempt | Exact approval, Gate delivery | Сохранять audit при отмене; не replay unknown |
| Voice original/transcript | Voice archives/call records | Разрешённый source/data scope | Несколько retention/recovery путей; не считать общий lifecycle доказанным |
| Watch original | BYTEA + note metadata | Private owner scope | Оригинал не загружается в GitHub; journals сохраняются на rollback |
| Watch transcript | Gate + private Mark issue/comments | Явное private destination exception | Это отдельный разрешённый destination, не shared Git payload |
| Alfa source/parts/artifacts | Private Dropbox + job metadata | Workspace job access | Source и versioned artifacts; общий retention policy надо согласовать |
| Budget ledger | PostgreSQL billing | Scope/period/product | Применяется не ко всем STT paths; см.A02 |
| Beta checkpoint | Persisted encrypted batch/manifest | Exact OIDC/recipient workspace | Ciphertext тоже имеет lifecycle и ACK provenance |
| Historical AQUB media | Сохранённые models/history | Frozen, без reactivation | Старое raw_data TTL не равно общему Gate deletion |
| Backup | Реальное внешнее private storage | Отдельный Ops/owner scope | CI restore synthetic; настоящая recovery/freshness аудитом не проверена |

В документации и shared receipts оставлять metadata/digests, а не реальные значения полей.

## 5. Миграционный граф

Текущая цепочка заканчивается 0112_watch_voice_inbox →0113_watch_voice_drain_runs →0114_watch_voice_recovery_claims →0115_watch_voice_post_payment.

Исторические merge points:

-0059_merge_audio_beta_heads объединяет 0058_long_call_chunking и 0058_beta_checkpoint_exports.
-0083_become_quest_corrective_m объединяет 0083_become_quest_corrective и 0083_alpha_transcription_product.

Открытые ветки нельзя применять в произвольном порядке:

| PR | Добавляемая topology | Состояние |
| --- | --- | --- |
|747|0116_reply_dialog_versions от current watch0115|Source candidate, не main |
|744|0115_reply_media_packages от 0114_watch_voice_recovery_claims|Конкурирующая head, exact CI failure |
|720|0112_reply_sequence_control →0113_reply_canary_budget →0114_reply_continuity_epoch →0115_reply_canary_binding от 0111_reply_documents|Отдельная ветвь от предыдущей schema |

Числовые префиксы не уникальны. Нужен union-graph план, принимающий реальную deployed schema и данные. Уже применённую revision/history не редактировать. Новая merge revision разрешается только после проверки constraints/data ordering.

## 6. Изменение DB

1. Найти модели и фактические migration refs нужных tables.
2. Определить ownership и private data class.
3. Проверить current head и все competing PR.
4. Подготовить additive/default-off migration и обратимый application plan.
5. Прогнать независимые PG sessions, upgrade/schema drift/restore и negative constraints.
6. Проверить, что code image знает DB revision после rollback.
7. Production apply только через принятый Ops contract; rollback без потери journals.

Автоматически выполнять migrations, TTL purge, export/delete или schema introspection по этой инструкции не разрешено.

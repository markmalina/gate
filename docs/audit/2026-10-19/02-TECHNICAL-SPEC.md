# Техническая спецификация: фактический контракт и требования изменений

Source baseline: `86dbc0f429dce089bf733fc422f20f556535895b`. Спецификация описывает систему для доработок. **FACT** — подтверждённый source; **REQUIRED** — инвариант принятого проекта или критерий безопасной реализации; **GAP** — найденное расхождение. Новые требования аудита являются предложениями до отдельного принятия реализации.

## 1. Сервис и интерфейсы

Основной сервис запускает `uvicorn app.main:app`. Web process создаёт один DatabaseRuntime на lifespan. Незаполненный DATABASE_URL приводит к отказу запуска. /live показывает способность ASGI ответить; /ready проверяет DB/schema, но не готовность всех прикладных функций.

Интерфейсы:

- Telegram Business webhook /telegram/webhook.
- Отдельные /telegram/alpha/webhook, /telegram/beta/webhook, /telegram/become/webhook.
- JSON-RPC MCP на /mcp с protocolVersion2025-06-18.
- OAuth protected-resource metadata и /mcp/metadata.
- Приватный Actions drain /internal/watch-voice/drain.
- Контактный snapshot/admin, Beta OIDC checkpoints, Dropbox call notification.
- Historical owner-one-off transcription route и admin HTML/draft helpers.

Точные routes, ordering, scopes и эффекты: [API-CATALOG.md](API-CATALOG.md). Наличие HTTP route не означает разрешённую активацию.

## 2. Identity и scope

**REQUIRED**

- Внутренние account/project identifiers — UUID grants; workspace identity обрабатывается по контракту конкретного bounded context.
- OAuth actor и data subject различаются.
- Workspace grant не даёт права на произвольный foreign account/project.
- Connector выбирается серверной конфигурацией; model output не выбирает credentials или recipient.
- Opaque installation ref не преобразуется в raw Telegram ID на стороне вызывающего ИИ.
- Account RU/BY — label для exact confirmation, не замена ID/connection binding.
- Связи между legacy и canonical identity имеют provenance и проверяемые uniqueness constraints.

**FACT:** OAuth validator проверяет asymmetric algorithm allow-list, JWKS HTTPS, подпись, issuer, audience, exp/iat/sub и нужный scope. При отсутствии explicit account/project claims сервер разрешает их из активных readable Accounts workspace и связанных active Projects.

**GAP:** нельзя обобщать OAuth workspace string на любой storage API. Alfa job repository требует UUID workspace.id, тогда как ряд других repositories принимает workspace key. При подключении нового dispatcher нужен явный typed resolver; конкретная live несовместимость этим аудитом не проверялась.

## 3. Durable ingress

| ID | Требование / фактическое состояние | Основной source |
| --- | --- | --- |
| ING-01 | Проверять webhook secret до записи/обработки | api/telegram_ingress.py |
| ING-02 | update_id — integer, не bool, 0…signed64max | api/telegram_ingress.py |
| ING-03 | Сначала durable receipt и outbox/job, затем обработка/ACK | application + infrastructure/telegram_processing.py |
| ING-04 | Повтор одного bot/update не создаёт независимую повторную обработку | SQL uniqueness и receipt service |
| ING-05 | Ошибка durable acceptance возвращает 503 | durable_telegram_webhook |
| ING-06 | Business text/voice canonical worker, остальные классы — явная legacy delegation | include_telegram_ingress |
| ING-07 | Ошибки/poison/unknown отражаются в durable status, а не скрываются успехом продукта | worker и queue incident modules |

Уровень ACK, source receipt, normalized message и reply task — разные стадии. Исторический exception handler возвращает 200 для poison Telegram update после durable seam; не выводить из 200 готовый ответ.

## 4. Reply context и draft

**REQUIRED:** чтение ограничено exact principal scope. Контекст включает исходное событие и доступные quoted/reply/attachment/voice данные по конкретному selector. Нельзя выбирать чужой chat по неподтверждённому отображаемому имени.

Draft создаётся как сохранённый объект с task/conversation/account/project/trigger binding. Plain omission сохраняет literal legacy text; markdown/rich требуют явного режима. Форматирование имеет preview/hash; подтверждать следует фактический payload, а не только первоначальную строку.

Document preparation — write operation, не отправка. Исходные файлы проходят bounds, filename/type/magic checks и durable storage. Send document — отдельное exact approval. Детали форматов и limits живут в application/document_formats.py, telegram_rich.py и reply_documents.py; их нельзя заменять только MIME из клиента.

## 5. Delivery

| ID | Инвариант |
| --- | --- |
| SEND-01 | Только gate:messages:send и explicit data scope |
| SEND-02 | confirmed=true плюс exact account/recipient/draft или document |
| SEND-03 | Проверка task/draft state, Business connection и server send flags |
| SEND-04 | Exact payload digest, если preview его требует |
| SEND-05 | Durable reservation/idempotency до provider attempt |
| SEND-06 | Повтор attempt возвращает его результат, а не новый эффект |
| SEND-07 | Definite rejection и unknown не смешиваются |
| SEND-08 | Unknown не разрешает слепой retry |
| SEND-09 | Поздняя квитанция не переписывает чужую generation |
| SEND-10 | После новой реплики/изменения snapshot требуется fresh context/approval |

SEND-01…09 имеют несколько реализационных границ и тестов; их конкретную достаточность надо проверять для затрагиваемого writer. SEND-10 в полной форме — незавершённый #745, а не гарантия baseline.

Состояния SendAttempt в source: [data-model.json](data-model.json), models/domain.py. Практический цикл: reserved → sending → sent / definite failed / unknown. Recovery сохраняет неизвестность; CI не доказывает доставку.

## 6. Серии

**FACT:** 2–50 различных draft IDs, тот же task/conversation/account/project/trigger и recipient; interval строго 60s. Scheduled metadata хранится в prompt_context.reply_sequence. Worker выбирает по одному due item на series и обрабатывает due list последовательным await. Следующий item ждёт предыдущий sent и не менее 60s после finished_at.

**GAP:** атомарная отмена любого inbound до claim, durable sequence supervisor/admission и bounded parallel send находятся в PR720; source baseline содержит более слабую newer-task проверку. Rich sequence/captions не поддерживаются Sprint1; media Sprint2 не merged.

**Критерии будущей приёмки:** две одновременно due серии имеют bounded overlapping in-flight; один stop/unknown не отменяет чужую серию; inbound/dispatch race не проходит sender barrier; рестарт и флаг не оживляют отменённый tail; account/bot/global rate budgets и final readback доказаны.

## 7. Голосовые контракты

### 7.1 Закреплённый voice / Calls / Watch policy

model-access.lock и project policy фиксируют:

| Параметр | Значение закреплённого контура |
| --- | --- |
| Provider / endpoint | openai / https://api.openai.com/v1/audio/transcriptions |
| Обычная модель | gpt-4o-mini-transcribe |
| Изолированное согласованное recovery | gpt-4o-transcribe |
| Dropbox Calls source | 100 MiB |
| Upload одной части | 25 000 000 bytes |
| Частей | максимум 20 |
| Timeout части |60s |
| Automatic provider retries |0 |
| Transcript | максимум 100000 characters |
| Shared monthly hard ceiling |3000000 micro-USD = USD3 |
| Retention claim | Не используется для training; ZDR eligibility не означает ZDR provisioning |

Это source policy snapshot; цены и текущие provider terms аудит заново не исследовал. До изменения server-side model/transcription требуется закреплённый use-model-access skill, lock и exact policy. Отсутствие pin или расширенный эффект — stop, не повод выбрать аналог по памяти.

### 7.2 Alfa transcription jobs: отдельная реализация

В source есть другой контракт: 2GiB, до 24h, максимум 512 parts, windows230s/stride225s, provider timeout300s. Артефакты: verbatim.md, clean.md, необязательный analysis.md, manifest.json; приватное Dropbox хранение.

Его cost fields после запроса не заменяют reservation; common budget admission отсутствует на просмотренном worker path. Не объявлять его автоматически покрытым policy7.1. Исправление/разрешение GATE-A02 — prerequisite активации/расширения.

Text cleanup и heuristic analysis не являются достоверным speaker recognition. #712 — отдельный план, не принятая diarization.

### 7.3 Watch

Durable key: workspace/account/file_id/revision; исходные bytes/hash/time сохраняются. Recording start time не следует из filename или client timestamp.

Actions driver принимает только пустой body, exact private Mark OIDC/run/main/workflow pin и request-local token. Проверяет destination repository identity/private status до audio/STT. Один run постоянно потреблён hash journal; один note/одна publication part за run. Unknown GitHub POST только read-reconcile; no matching receipt не разрешает повторный POST.

Initial exact note, quota probe, post-payment continuation и future-only — разные принятые scopes с отдельными permanent claims. Старый grant/counter не сбрасывается новым head/nonce/config. Future-only cutoff исключает preexisting entries по Dropbox server time; не silent backfill.

Main start принудительно GATE_WATCH_VOICE_ENABLED=false; PR766 предлагает explicit driver mode. #736 остаётся release/product blocked.

## 8. Миграции и хранение

Baseline содержит 119 revisions, один head0115_watch_voice_post_payment. Две исторические merge revisions согласуют предыдущие ветви. Нельзя выводить topology из числового префикса filename.

Перед объединением PR744/720/747 нужен union migration graph. Проверки: alembic heads; upgrade с принятой deployed schema; alembic check; schema/constraints/concurrency tests; disposable backup/restore; downgrade только disposable DB. После реальных voice/journal данных разрушительный downgrade не является допустимым rollback.

113 ORM tables и 1473 mapped declarations проиндексированы статически. Это не introspection Production DB.

## 9. Health, configuration и наблюдаемость

- /health: legacy service status/fingerprint, не product acceptance.
- /live: process liveness.
- /ready: DB и точное совпадение текущих и source Alembic heads; timeout2s.
- /admin/ops/health: protected aggregate health двух зарегистрированных worker families.
- gate_get_status: owner-scoped projections; отдельный workspace не доказывает глобальное число пользователей.
- Deployment fingerprint сообщает revision, но не принятую функциональность.

Каждый новый включаемый worker должен иметь observed_at, last progress, queue state, safe error code, ownership lease и stop/recovery semantics. Нельзя публиковать в health raw content, chat IDs, credential metadata beyond approved safe shape.

## 10. Совместимость и release acceptance

Для любого изменения вести отдельные поля:

| Уровень | Что доказывает |
| --- | --- |
| Specification | Согласованный контракт и bounds |
| Source | Exact immutable code/tree |
| Verification | Проверки этой ревизии, skips и test environment |
| Integration | Совместимость с current main/schema/related writers |
| Runtime | Наблюдение exact deployed revision/config/health |
| Client | Доступные поля и фактический результат через нужный MCP/Telegram client |
| Product | Достигнутый критерий владельца |
| Authorization | Разрешён именно этот эффект/target/revision/expiry |

Обязательные негативные сценарии: foreign workspace/account/project; unsupported type; stale approval; mutated replay; duplicate concurrent request; lost ACK; timeout; crash before/after reservation/provider/finalize; no entitlement; secret/payload in error; disabled feature; overlap двух процессов; migration from actual accepted prior schema.

Доказательства runtime/provider effects собираются только отдельным разрешённым сценарием через project-owned operation route. Этот документ не разрешает их запуск.

# Gate — глубокий системный аудит

> **Назначение документа:** канонический управленческий аудит транспортного
> контура ALPHA VIA; публикационная проекция для «Альфа Центавры» должна
> строиться из этого документа, а не становиться вторым источником истины.  
> **Рекомендуемый канонический путь в репозитории:** `docs/analysis/GATE-SYSTEM-AUDIT.md`  
> **Репозиторий:** [`markmalina/gate`](https://github.com/markmalina/gate)  
> **Именование:** полное имя — ALPHA VIA; монограмма — αV; центр и сайт —
> «Альфа Центавра».  
> **Source revision:** [`cca4eeb91c2d7ea6f0160c6690a849319ad1a0db`](https://github.com/markmalina/gate/commit/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db)  
> **Main commit time:** `2026-07-30T05:54:03Z`  
> **Source captured:** `2026-07-30T05:59:00Z` (`2026-07-30T07:59:00+02:00`)  
> **Fresh main/runtime observation:** [`27fa336648e4c5aadf02fa3f001c5fffe47fee9c`](https://github.com/markmalina/gate/commit/27fa336648e4c5aadf02fa3f001c5fffe47fee9c), production `27fa336648e4`, captured `2026-07-30T06:10:16Z`  
> **Freshness note:** полный source audit pinned к `cca4eeb…`; последующий bounded delta #393 и его live fingerprint проверены отдельно  
> **Audit prepared:** `2026-07-30`  
> **Режим аудита:** read-only; без GitHub mutation, deploy, provider call,
> отправки, публикации, OAuth, credentials, чтения частных payload и
> real-person content.  
> **Метки уверенности:** **[уверен]** — подтверждено source/runtime/official
> documentation; **[вероятно]** — сильный вывод из нескольких фактов;
> **[гипотеза]** — направление, требующее проверки.

---

## 1. Короткий вердикт

**[уверен] Gate уже не прототип webhook-бота.** Это развитый communications
control plane с:

- двумя готовыми Telegram Business connections;
- durable Telegram ingress через receipt + outbox;
- идемпотентными delivery grants и terminal receipts;
- owner-only Universal Mayak, который уже отправил пять из пяти разрешённых
  ответов без зафиксированных failed/unknown;
- полностью обработанной текущей очередью voice notes — `84/84`;
- защищёнными health/readiness проекциями;
- зрелой системой CI, миграций, backup/restore и deployment verification;
- дисциплиной «один issue → один execution → одна lease → одна branch → один
  PR».

**[уверен] `main` и production сейчас совпадают на
`27fa336648e4...`.** До этого
[`PR #391`](https://github.com/markmalina/gate/pull/391) прошёл полный CI, но
Railway pre-deploy migration упала на production check constraint:
`recovery_v8` записывался раньше, чем становился допустимым значением
`ck_phone_call_recordings_transcription_status`. Transaction откатилась, и
production правильно остался на `7456d064fc48`.

[`PR #392`](https://github.com/markmalina/gate/pull/392) исправил порядок
constraint/update, прошёл exact-head CI #1330 и развёрнут как
`cca4eeb91c2d`. Затем
[`PR #393`](https://github.com/markmalina/gate/pull/393) сократил bounded PCM
WAV parts с десяти до пяти минут, прошёл exact-head CI #1332, merged как
`27fa336…` и уже наблюдается в production. Это закрывает release drift, но
ещё не доказывает terminal runtime outcome самого длинного звонка: отдельный
redacted acceptance receipt после v9 на момент свежего наблюдения отсутствует.

**[уверен] Первой демонстрации BECOME NOW QUEST мешает не отсутствие большого
нового backend, а четыре конкретных транспортных риска:**

1. четыре несовпадения Business text parity (`996/1000`, статус blocked);
2. общий Telegram worker как единый failure domain для разных миссий;
3. ошибка handoff-семантики Universal Mayak: terminal `FAILED`/`UNKNOWN`
   нижележащего сервиса сейчас всё равно проецируется как `PROCESSED` и
   подавляет human reply task;
4. Instagram пока не имеет live adapter.

Недавний failed production migration остаётся release-engineering риском, хотя
текущий fingerprint уже восстановлен.

**[уверен] Instagram в Gate пока существует только как provider-free
rehearsal-контракт.** Реального OAuth/runtime publish path в проверенном коде не
обнаружено. Для первого BECOME demo Gate должен уметь принять один
авторизованный media delivery grant, создать и дождаться Instagram media
container, опубликовать его ровно один раз и вернуть provider-neutral receipt.
Контент, расписание, сценарий прогрева и смысл публикации Gate не принадлежат.

**[уверен] Личный контур Марка следует строить на Telegram Business для новых
сообщений и voice notes, а телефонные разговоры — через уже спроектированный
Samsung recorder → Dropbox intake.** Bot API не даёт доступ к личному
Telegram-call media stream; MTProto call API предназначен для user clients,
требует полного E2E/VoIP клиента и не является серверным «архивом звонков».

Главный архитектурный вывод:

```text
Gate = авторизованный транспорт, durable delivery и provider evidence.

Gate ≠ BECOME saga.
Gate ≠ память Alfa.
Gate ≠ CRM/identity truth.
Gate ≠ MAYAKI program/progress.
Gate ≠ TAKE media production.
```

---

## 2. Зачем Gate нужен ALPHA VIA

У ALPHA VIA много продуктов, но внешний мир видит только каналы: Telegram,
Instagram, звонки, файлы, email и будущие provider surfaces. Каждый provider:

- имеет собственную аутентификацию и права;
- повторяет webhooks;
- теряет или задерживает updates;
- возвращает неоднозначные результаты;
- меняет API и scopes;
- может списать деньги до получения ответа;
- содержит персональные данные;
- не знает продуктовых границ ALPHA VIA.

Если каждый продукт подключает provider самостоятельно, возникают:

- несколько OAuth/token stores;
- параллельные webhook consumers;
- дублированные отправки;
- несовместимые модели retries;
- разные consent/identity трактовки;
- невозможность показать инвестору сквозное доказательство;
- неконтролируемое смешение личных коммуникаций Марка и продуктовых сообщений.

Gate нужен как одно место, где внешний effect превращается в проверяемый
контракт:

```text
provider update
  → authenticated receipt
  → normalized transport fact
  → exact domain route
  → authorized delivery grant
  → one bounded provider attempt
  → terminal delivery fact
  → redacted evidence for «Альфа Центавры»
```

У Gate ровно две миссии.

### Миссия A — transport boundary BECOME NOW QUEST

Gate:

- принимает provider events;
- проверяет connection, signature/secret, scope и idempotency;
- передаёт в BECOME provider-neutral facts;
- принимает только готовый, разрешённый delivery intent;
- выполняет отправку/публикацию;
- возвращает `sent`, `failed`, `unknown`, `blocked` или provider receipt;
- считает transport cost и latency.

Gate не решает:

- на каком этапе Quest находится человек;
- кто имеет право участвовать;
- какой текст, практика или медиа нужны;
- выполнено ли задание;
- когда открывать следующий шаг;
- что показывать инвестору как продуктовый результат.

### Миссия B — личный communications boundary Марка для Alfa

Gate:

- подключает разрешённые Telegram Business dialogs;
- принимает входящие и исходящие сообщения;
- хранит транспортную provenance;
- принимает voice notes;
- импортирует телефонные записи из owner-controlled archive;
- транскрибирует только при явном data gate;
- позволяет Alfa предложить ответ;
- отправляет только после соответствующей authority policy;
- выдаёт Alfa минимальную проекцию вместо provider credentials и raw update.

Gate не становится памятью Марка. Long-term meaning, персональная модель и
решения принадлежат Alfa; identity/relationship/consent truth — Relations.

---

## 3. Граница этого аудита

### В аудит вошли

- обязательный bootstrap и execution policy;
- README, handoff, product-platform и voice/Instagram документы;
- open issues, PR, exact-head CI и текущий coordination pointer;
- application, infrastructure, worker и health code на exact `main`;
- свежая redacted production проекция через Gate M;
- read-only попытка получить Railway deployment evidence;
- официальные Telegram, Meta, OpenTelemetry, SRE, outbox и privacy sources;
- продуктовая граница BECOME, Gate, Alfa, Relations, MAYAKI и TAKE;
- состояние первого BECOME demo с точки зрения Gate;
- target contracts, acceptance predicates, SLO, риски и последовательность
  развития.

### В аудит не вошли

- raw message text, usernames, phone numbers, chat IDs и provider IDs;
- реальные provider calls;
- отправка сообщений или публикация;
- OAuth/credentials/secret inspection;
- Railway reauthentication;
- production deployment;
- изменение issue, branch, PR или файлов GitHub;
- юридическое заключение о Telegram/Meta/GDPR;
- BECOME product roadmap.

---

## 4. Источники и обязательный bootstrap

### 4.1. Канонические repository sources

| Источник | Exact source | Что он доказывает |
|---|---|---|
| Gate Work instructions | [`AGENTS.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/AGENTS.md), blob `7cf71733a20b0888e5ccb00590a842105d088f97` | bootstrap, standing authorization, owner gates |
| Execution protocol | [`docs/work-bootstrap.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/work-bootstrap.md), blob `10e4f67d5d6a85ff7f244e2e810b87d1c6b2e5d9` | lease, duplicate prevention, state transitions |
| Entrypoint | [`README.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/README.md), blob `a75c58c917a800d379a07b0e676c0409966b2a5b` | declared runtime and operational entrypoint |
| Current handoff | [`docs/GATE_HANDOFF_2026-07-25.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/GATE_HANDOFF_2026-07-25.md), blob `0bc0ec0de401dc11816d582f2d7747d9bca64897` | accepted stages and constraints |
| Team workflow | [`docs/TEAM_WORKFLOW.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/TEAM_WORKFLOW.md), blob `d95130c46848ba0bacd1c0ecb3ec7dd1edab3f38` | branch/PR/CI collaboration |
| Product boundary | [`docs/GATE_PRODUCT_PLATFORM.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/GATE_PRODUCT_PLATFORM.md), blob `679b945283432ecc12378cabd156b57ebc2906ca` | provider-neutral platform design |
| Project handoff | [`docs/PROJECT_HANDOFF.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/PROJECT_HANDOFF.md), blob `717d5d2ca598e5c506fdac153702184426afecb5` | historical pointers and operational state |
| Voice state | [`docs/voice/README.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/voice/README.md), blob `7448b92884bc0ebac7103422a5264c8654ff5c25` | voice archive/transcription/reconciliation design |
| Voice continuation | [`docs/voice/NEW_WORK_PROMPT.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/voice/NEW_WORK_PROMPT.md), blob `57c981f7f49a1119ce79a57a5593300e4f663936` | issue #354 continuation |
| Instagram handoff | [`docs/INSTAGRAM_CONNECTION_HANDOFF.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/INSTAGRAM_CONNECTION_HANDOFF.md), blob `9f33165eda8d04cf74a0067cc795bcecc5a1601c` | Meta app preparation, runtime gap |
| Capability registry | [`registry/gate-capabilities.v1.json`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/registry/gate-capabilities.v1.json), blob `d212d8de1c42c5e3b2fc30d86951711652407621` | declared capabilities as of 2026-07-23 |

### 4.2. Code evidence

| Область | Exact source | Наблюдение |
|---|---|---|
| Process composition | [`app/main.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/main.py), blob `fc433ebcdc30745f5d814b6b23ee8014b2c501a0` | web, Telegram, reply-sequence и Dropbox loops живут в одном ASGI process |
| Durable ingress | [`app/api/telegram_ingress.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/api/telegram_ingress.py), blob `cc3ea53f49aa5fc509c411d376c66333f9619f55` | ordinary Business text и voice сначала получают durable receipt |
| Processing contract | [`app/application/telegram_processing.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/application/telegram_processing.py), blob `a46336118d70b5a68d3b62f437885cb28de78ccb` | retry/terminal state machine |
| SQL queue | [`app/infrastructure/telegram_processing.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/infrastructure/telegram_processing.py), blob `1329afa7e9529aedb149e0bd27e51cea6ace57c1` | same-transaction receipt/outbox, unique provider update, `SKIP LOCKED` claim |
| Shared worker | [`app/worker/telegram_processing.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/worker/telegram_processing.py), blob `6c538c22cbeac5b7d7c8f2a6f508d39faca5e423` | Business, voice, Alpha, Beta и Universal Mayak делят loop/failure domain |
| Universal Mayak admission | [`app/application/universal_mayak_private_ingress.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/application/universal_mayak_private_ingress.py), blob `31fe94b09e90732a01a4c10ae797df9062ac69b2` | exact binding хорошо ограничен; terminal result ошибочно всегда становится `PROCESSED` |
| Universal Mayak effect | [`app/application/universal_mayak_demo.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/application/universal_mayak_demo.py), blob `9a1647ad3e5204df22e208682f13f923ca437e74` | immutable prompt, bounded context, terminal unknown, no blind retry |
| Model boundary | [`app/infrastructure/universal_mayak_model.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/infrastructure/universal_mayak_model.py), blob `6f60792009f26da9924b12b3cabff22487c8032d` | provider IDs не передаются модели |
| Meta rehearsal | [`app/application/meta_transport_rehearsal.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/application/meta_transport_rehearsal.py), blob `7fb65915a9e1ef17f534254d990660a0061ac0b6` | deterministic fake provider, не live Instagram runtime |
| Health | [`app/application/worker_health.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/application/worker_health.py), blob `fbcd25809179df4d3dc77a664628e316203e6fad` | in-process heartbeat, redacted error code |
| Health routes | [`app/api/health_routes.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/app/api/health_routes.py), blob `3228a9afa092118c0118f4e226b94ccc37eb0ef5` | public DB/migration readiness и owner-only worker health |
| Current recovery migration | [`alembic/versions/0064_long_call_claim_v8.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/alembic/versions/0064_long_call_claim_v8.py), blob `76a3d21f73c1fb27ba9d66bdc3705a01a3ba71de` | constraint расширяется до bounded update; downgrade возвращает state до сужения |
| Migration regression | [`tests/test_audio_provider_limit.py`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/tests/test_audio_provider_limit.py), blob `8d53053dd9fa13725ac008f51d8cf5cb3887fea8` | проверяет upgrade/downgrade operation order |
| Fresh bounded delta | [`PR #393`](https://github.com/markmalina/gate/pull/393), merge `27fa336…` | five-minute PCM WAV parts, isolated `recovery_v9`; full source table выше остаётся pinned к `cca4eeb…` |

### 4.3. PR и CI

- Последний merged change:
  [`PR #393 — Retry the long call as five-minute WAV parts`](https://github.com/markmalina/gate/pull/393).
- Merge commit/current `main`:
  [`27fa336648e4c5aadf02fa3f001c5fffe47fee9c`](https://github.com/markmalina/gate/commit/27fa336648e4c5aadf02fa3f001c5fffe47fee9c).
- Commit receipt сообщает exact-head CI #1332 `success`.
- Fresh Gate status сообщает production `27fa336648e4`.
- Open draft PR #394 содержит только этот системный аудит и не является
  вторым product execution.

Проверенный run включал sensitive literal scan, Ruff, Mypy, Pytest, Alpha
rehearsals/evidence, Alembic upgrade/schema drift/downgrade, disposable
PostgreSQL backup/restore, Docker/unprivileged checks, contact-sync build и
Windows DPAPI round-trip.

**[уверен] Green CI доказывает exact-head build/test contract. Он не доказывает
production deployment или пользовательский runtime.**

Полная source-таблица выше pinned к `cca4eeb…`. Последующий delta до
`27fa336…` ограничен recovery v9 из PR #393 и проверен по merge/CI/runtime
receipts; этот документ не переинтерпретирует старые blob proofs как будто они
были сняты с нового commit.

---

## 5. Правила mutation, merge и CI

Эти правила важны для любой дальнейшей работы и не являются рекомендацией
аудитора — они следуют из `AGENTS.md` и `docs/work-bootstrap.md`.

### 5.1. Mutation protocol

1. Сначала читаются current issue, latest implementation receipt и latest
   pointer.
2. До изменения файлов проверяется существующая lease.
3. Для bounded stage допускаются ровно:
   - один `execution_id`;
   - одна активная lease;
   - одна branch;
   - один PR.
4. Второй execution до expiry первого обязан fail closed.
5. Повтор с тем же `execution_id` — resume, а не новая работа.
6. Expired lease заменяется только явным receipt, связывающим старый и новый
   execution.
7. Не создаются дубли issue, branch, PR, automation или delivery effect.

### 5.2. Merge/CI protocol

1. Локальные проверки выполняются до remote CI.
2. Запускается один необходимый CI на exact PR head.
3. Merge разрешён только для того exact head, который прошёл требуемый CI.
4. CI/merge/deployment dashboard сами по себе не означают readiness.
5. Production change завершается exact deployed revision и redacted runtime
   acceptance либо rollback receipt.

### 5.3. Authority protocol

Repository-local standing authorization действует только при свежем
доказательстве:

- `pre_user_mode: true`;
- `external_users: 0`;
- нет third-party payload/participant;
- owner-only check ограничен одной exact installation и hard provider/send
  caps;
- записаны last-known-good revision и rollback condition.

Credentials, secrets, OAuth, identity, payments, legal actions, publication,
destructive data/history action и новый external-user processing остаются
exact owner gates. При любом сомнении состояние — `waiting_user`.

**В этом аудите mutation не выполнялись.**

---

## 6. Текущее runtime-состояние

### 6.1. Снимок `2026-07-30T06:10:16Z`

| Контур | Свежий факт | Оценка |
|---|---|---|
| Release | production `27fa336648e4`, main `27fa336648e4...` | fingerprint aligned; v9 long-call terminal receipt pending |
| Telegram accounts | 2 | наблюдаются |
| Business connections | RU и BY — `ready`, `can_reply=true` | транспорт готов |
| Canonical messaging | 69 conversations, 2333 messages | используется |
| Human workflow | 1038 reply tasks; 29 unanswered; 743 answered; 75 drafts | существенный live state |
| Universal Mayak | `active_observed`; 1 active installation; 1 active session | live canary существует |
| Universal Mayak effects | 5 proposals, 5 grants, 5 sends `sent`, 0 failed, 0 unknown | ограниченный положительный runtime proof |
| Telegram voice queue | 84 received, 84 delivered, 0 processing/retry/dead-letter | очередь чиста |
| Voice accounting | 126 settled, 0 open, 5 unknown | reconciliation required |
| Business text parity | 1000 total, 996 matched, 4 mismatches | `cutover_ready=false` |
| Alpha delivery | top-level delivery enabled, но model delivery blocked/config incomplete | status semantics требуют прояснения |
| MCP surface | live 15 tools | README говорит 11: documentation drift |

В status не публиковались private identifiers или message content.

### 6.2. Что доказано

- **[уверен]** Обе Telegram Business connections обслуживаются.
- **[уверен]** Durable voice path очистил все 84 текущих queue items.
- **[уверен]** Universal Mayak прошёл пять bounded live effects.
- **[уверен]** Есть реальный human messaging workload, а не только synthetic
  tests.
- **[уверен]** Voice ledger содержит пять provider-unknown outcomes и требует
  reconciliation.
- **[уверен]** Business parity не готова к cutover из-за четырёх mismatch.
- **[уверен]** Production содержит последний merged recovery v9.
- **[уверен]** Сам terminal result длинного звонка свежим status snapshot не
  доказан.

### 6.3. Что не доказано

- пять успешных Mayak sends не доказывают устойчивость под restart/concurrency;
- чистая voice queue не доказывает закрытие provider accounting;
- ready Business connection не доказывает end-to-end BECOME saga;
- green CI не доказывает production fingerprint;
- 15 MCP tools не доказывают, что каждый контракт безопасен для внешнего
  пользователя;
- отсутствие open PR не доказывает отсутствие незавершённой работы;
- успешный deployment context и совпавший fingerprint не доказывают terminal
  business outcome мигрированной записи.

### 6.4. Deployment incident и восстановление

Read-only запрос списка Railway projects вернул
`oauth_refresh_token_missing`; connector требует reauthentication.

Позднейший канонический receipt в issue #354 уже содержит Railway log evidence:

```text
deployment 071fb019-b73b-4551-be35-4dd74bf2ab08
build success
pre-deploy failure
migration 0064_long_call_wav_retry → 0065_long_call_claim_v8
check violation on recovery_v8
transaction rolled back
```

PR #392 расширил constraint до bounded update и был развёрнут автоматически.
GitHub deployment context и свежий Gate status согласованы на
`cca4eeb91c2d`. PR #393 затем ввёл materially different five-minute v9 и
свежий Gate status уже согласован с его merge `27fa336648e4`.

**[уверен]** Причина release failure установлена и deploy восстановлен.
**[уверен]** V9 deployed; terminal long-call result отдельно ещё не принят.
**[уверен]** Этот аудит не обходил Railway reauthentication, не запускал manual
deploy и не делал provider call. Evidence получено из issue receipt, GitHub
status и redacted runtime status.

---

## 7. Текущая архитектура

```text
Telegram / Dropbox / future Meta
               │
               ▼
        FastAPI ASGI process
               │
        durable ingress receipt
               │
        PostgreSQL outbox/queue
               │
       shared Telegram worker
        ├── Business text
        ├── voice archive/transcription
        ├── Alpha group
        ├── Beta
        └── Universal Mayak
               │
        canonical/domain adapters
               │
       provider delivery + receipt
```

### 7.1. Сильные стороны

#### Durable-before-effect

`telegram_update_receipts` и outbox создаются в одной SQL transaction.
Уникальный ключ `(bot_key, update_id)` отсекает provider redelivery.
`FOR UPDATE SKIP LOCKED` позволяет безопасно claim due work.

Это соответствует transactional outbox practice:

- [AWS Transactional Outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html);
- [Azure Transactional Outbox](https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos).

Правильная семантика — **at-least-once processing + idempotent consumer**, а не
маркетинговое «exactly once».

#### Exact authority

Delivery строится вокруг exact grant, idempotency key, approval reference,
bounded scope и terminal unknown. Неоднозначный provider response не повторяется
вслепую. Это особенно важно для сообщений, публикаций и платного
transcription.

#### Privacy-aware code

Universal Mayak получает opaque Gate references. Telegram connection/chat/actor
IDs используются только для exact binding и не попадают в model request.
Worker health экспонирует redacted codes, а не exception text.

#### Release engineering

CI проверяет не только unit tests, но миграции, downgrade, backup/restore,
container и Windows credential protection. Это сильнее типичного раннего
стартапа.

### 7.2. Архитектурные ограничения

#### Монолитный process и общий worker

Web server, Telegram worker, reply sequence и Dropbox call intake запускаются
из одного lifespan. Внутри Telegram loop разные продуктовые пути делят один
processor.

**[уверен]** Это уже приводило к инцидентному классу: ошибка конфигурации Alpha
group прервала loop и оставила 30 voice receipts. Следовательно, общий worker —
не абстрактный долг, а наблюдённый cross-mission failure domain.

#### Process-owned resources создаются как iteration-owned

В `app/worker/telegram_processing.py` `_run_from_settings` создаёт и dispose
SQLAlchemy engine на каждой worker iteration. Idle loop повторяет это примерно
каждую секунду.

**[уверен]** Это добавляет connection churn, усложняет health interpretation и
противоречит process-owned pool model. Факт нагрузки на конкретный PostgreSQL
не измерялся, поэтому влияние на latency/cost — **[вероятно]**, не доказано.

#### Readiness уже, чем пользовательская готовность

`/ready` проверяет DB и migrations. Worker health живёт в памяти текущего
process и вынесен в owner-only endpoint.

**[уверен]** Deployment может быть «ready», когда queue worker degraded,
production revision устарел или provider parity blocked. Для orchestrator это
может быть допустимо, но для product readiness нужна отдельная release
projection.

#### Часть update classes остаётся в frozen legacy router

Durable-first покрывает ordinary Business text и voice; иные classes
делегируются legacy chain. Глобальный Telegram exception handler отвечает HTTP
200 даже на unhandled exception, чтобы прекратить provider retries.

**[вероятно]** Этот подход безопасен только если каждый принятый update сначала
durably записан или явно признан disposable. Нужна машинно проверяемая матрица
update class → durable owner → retry semantics; сейчас она не доказана для
всего Bot API update surface.

---

## 8. Находки по приоритету

### P0-1. Full CI не воспроизвёл populated-production migration

**Доказательство:** exact-head CI #1328 для PR #391 прошёл fresh PostgreSQL
upgrade/downgrade, schema drift и Docker build. Production pre-deploy всё равно
упал: migration записывала `recovery_v8`, когда check constraint ещё не
разрешал это значение. Transaction откатилась. PR #392 добавил правильный
порядок, exact-head CI #1330 прошёл, deployment восстановлен.

**[уверен]** Текущий `main` и production теперь совпадают:
`cca4eeb91c2d...`.

**Риск:** fresh-database CI не доказывает корректность data-dependent migration
на production-like populated state. Следующий guarded migration может
повторить тот же класс сбоя.

**Текущее acceptance:**

```text
production fingerprint == cca4eeb91c2d7ea6f0160c6690a849319ad1a0db  [принято]
migration transaction succeeded                                          [принято]
long-call terminal outcome matches bounded recovery                      [ещё не доказано]
response-unknown call remained untouched                                 [последний receipt: да]
```

**Системное acceptance на будущее:** CI fixture с production-like constraint и
bounded row state обязан выполнить upgrade и downgrade в том же order, который
нужен live data.

### P0-2. Shared worker нарушает изоляцию двух миссий

**Доказательство:** один processor обслуживает Business, voice, Alpha, Beta и
Universal Mayak; реальный сбой одного пути уже остановил другой.

**Риск для BECOME:** голосовой архив или экспериментальная Alpha group
конфигурация может остановить participant messages.

**Риск для Марка:** BECOME traffic может ухудшить личные reply tasks и voice
ingestion.

**Целевой принцип:** один repo и одна DB допустимы, но отдельные process-owned
worker commands, queue partitions, heartbeats и concurrency limits должны
разделить минимум:

- `telegram-business-core`;
- `telegram-voice`;
- `become-delivery`;
- экспериментальные Alpha/Beta routes.

Сразу дробить систему на сетевые микросервисы не требуется. Сначала достаточно
разнести failure domains внутри одного deployable repo.

### P0-3. Universal Mayak может подавить human work после `FAILED`/`UNKNOWN`

`ProcessUniversalMayakPrivateMessageService.execute()` получает
`UniversalMayakDemoResult`, но вне зависимости от его disposition возвращает:

```text
PrivateIngressDisposition.PROCESSED
suppress_human_reply_task=true
```

При этом нижележащий сервис явно имеет terminal `FAILED` и `UNKNOWN`; tests
подтверждают, что ambiguous delivery становится `UNKNOWN` и exact replay не
повторяет effect. Worker fallback создаёт human task только если private ingress
вернул не `PROCESSED`.

**[уверен] Это логическая ошибка текущего кода.**

Не доказано, что она уже затронула человека: свежие пять Mayak sends все
`sent`. Но при первом model failure, stage failure или provider unknown человек
может не получить ни автоматического ответа, ни human reply task.

**Acceptance:**

| Lower result | Private route | Human task |
|---|---|---|
| `SENT` / exact `REPLAYED(sent)` | processed | suppress |
| `PAUSED` / `HUMAN_TAKEOVER` | terminal control | create/retain as policy requires |
| `BLOCKED` | unclaimed | create exactly once |
| `FAILED` | unclaimed/error | create exactly once |
| `UNKNOWN` | reconciliation + human visibility | create/retain exactly once; never blind retry |

Нужны application tests, проверяющие именно mapping всех lower dispositions,
а не fake, который сам заранее согласует `PROCESSED`.

### P0-4. Business text parity blocked

**Доказательство:** `999 total`, `995 matched`, `4 mismatches`,
`telegram_business_text_parity_reply_task_mismatch`,
`cutover_ready=false`.

**Риск:** четыре входящих могут иметь отличающиеся canonical и reply-task
проекции. До объяснения mismatch нельзя считать BECOME participant path
предсказуемым.

**Acceptance:**

- каждый mismatch классифицирован без private payload в issue receipt;
- root cause устранён или доказано, что строки принадлежат pre-contract
  historical state;
- repeatable parity returns zero unresolved mismatch;
- restart/replay не создаёт дополнительные reply tasks.

### P1-1. Instagram не имеет live adapter

Repository содержит хороший provider-neutral rehearsal, но provider
детерминированно fake. Handoff не доказывает active OAuth, token custody,
webhook verification, container polling или publication receipt.

**Следствие:** формулировка «Gate умеет Instagram» сейчас неверна. Верно:
«Gate имеет rehearsal-контракт будущего Meta transport».

### P1-2. Provider-unknown voice accounting не закрыто

Queue чиста, но voice ledger показывает пять unknown. Это может быть
историческая accounting ambiguity, а не пять неполученных transcript.

**Acceptance:** каждая unknown reservation получает `settled`, `released` или
documented terminal `unknown`; суммарный budget invariant сходится; никакой
provider call не повторяется только ради закрытия бухгалтерии.

### P1-3. In-process health недостаточен для release truth

При process restart heartbeat обнуляется; stale deployment сам себя healthy
показывает; нет durable oldest-item age, queue partition lag и exact revision в
едином readiness response.

### P1-4. Нет доказанного distributed tracing

Поиск OpenTelemetry instrumentation в проверенном source не дал реализации.
Есть correlation/idempotency в отдельных контрактах, но нет единой trace:

```text
provider update
→ Gate receipt
→ BECOME event
→ delivery grant
→ provider attempt
→ Center evidence
```

Это не требует переносить payload в trace. OpenTelemetry рекомендует отдельные
`create`, `send`, `receive`, `process`, `settle` spans:
[Messaging semantic conventions](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/).
Личные данные нельзя класть в baggage:
[OpenTelemetry baggage](https://opentelemetry.io/docs/concepts/signals/baggage/).

### P1-5. Long-term personal-data boundary не завершена

Gate по необходимости касается raw provider payload, но не должен становиться
долговременным хранилищем личности, отношений и памяти. В audit sources не
найдена принятая единая retention/deletion matrix для message content,
voice media, transcripts, raw updates, provider receipts и model context.

**[вероятно]** Сейчас политики определены по частям, а не как проверяемый
end-to-end lifecycle.

### P1-6. MTProto → Alfa остаётся compliance-blocked

Repository audit правильно оставляет personal Telegram read default-off.
Текущие [Telegram API Terms](https://core.telegram.org/api/terms) содержат
ограничение на AI/ML use данных, полученных через Telegram API.

Это не следует автоматически распространять на каждый Bot API сценарий без
юридической интерпретации. Но любой MTProto history/content path в Alfa должен
fail closed до recorded compliance basis и exact owner data authorization.

### P2-1. Documentation и live registry расходятся

- README описывает 11 MCP tools; live status — 15.
- capability registry снят `2026-07-23` и описывает Mayak как contract-only;
  live runtime уже имеет пять sends.
- handoff/README указывают старые issue pointers.

**Риск:** новый Work может продолжить устаревший pointer и создать duplicate.

### P2-2. Static typing coverage неоднороден

`pyproject.toml` включает strict Mypy, но несколько крупных integration modules,
включая `app.main`, routes и Telegram service areas, исключены через
`ignore_errors`. Это не означает низкое качество кода, но оставляет самые
связанные runtime boundaries менее защищёнными типами, чем domain core.

### P2-3. Status semantics неоднозначны

Alpha status одновременно сообщает top-level `delivery_enabled=true` и
model-delivery blocked/config incomplete. Машина и человек могут по-разному
интерпретировать такой snapshot.

Нужны независимые поля:

```text
configured
credential_ready
data_gate_approved
model_ready
delivery_authorized
provider_ready
runtime_observed
```

---

## 9. Coordination/backlog snapshot

На момент снимка открыты семь issues, open PR нет.

| Issue | Наблюдаемая роль | Аудиторская оценка |
|---|---|---|
| [`#354`](https://github.com/markmalina/gate/issues/354) | voice reconciliation и long-call recovery | единственный текущий live continuation |
| [`#300`](https://github.com/markmalina/gate/issues/300) | Universal Mayak private DM runtime | issue text/lease отстаёт от уже наблюдённого live canary |
| [`#261`](https://github.com/markmalina/gate/issues/261) | Alpha group canary | coordination state выглядит старее live installations |
| [`#260`](https://github.com/markmalina/gate/issues/260) | TAKE capture canary | соседний integration workstream; не transport priority аудита |
| [`#365`](https://github.com/markmalina/gate/issues/365) | Beta checkpoint | за пределами двух текущих миссий Gate |
| [`#277`](https://github.com/markmalina/gate/issues/277) | product-platform master coordination | umbrella pointer, риск конкуренции с конкретными stages |
| [`#272`](https://github.com/markmalina/gate/issues/272) | Center cutover coordination | исторический cross-project pointer |

**[уверен]** Это не семь независимых следующих задач. Несколько issues —
historical/umbrella coordination. Создание нового «аудит issue» или нового
execution без сверки lease увеличило бы рассинхронизацию.

Этот файл ничего не закрыл, не переоткрыл и не прокомментировал на GitHub.

---

## 10. Межпроектные границы

### 10.1. Матрица владения

| Домен | Владеет | Не владеет | Контракт с Gate |
|---|---|---|---|
| **BECOME** | saga, journey, stage, eligibility, participant state, Quest UX, product acceptance | provider token, webhook retry, publish attempt | выдаёт delivery intent/authority; получает ingress и delivery facts |
| **Gate** | connection, authenticated ingress, transport normalization, outbox, provider attempt, terminal receipt, transport cost | Quest stage, person truth, life memory, program content, media creation | исполняет bounded transport effect |
| **Alfa** | личная интерпретация Марка, память, reasoning, reply proposal | provider credentials, connection lifecycle, прямой send без authority | получает минимальную projection; возвращает proposal |
| **Relations** | Person, channel identity link, consent/relationship truth, correction/merge | webhook и provider delivery | разрешает opaque recipient/consent reference |
| **MAYAKI** | программа, экспертный контент, практика, entitlement, progress, content schedule meaning | provider attempt, OAuth, delivery retry | передаёт product intent через BECOME/approved coordinator |
| **TAKE** | asset, edit, render, rendition, approved publication package | аудитория, campaign decision, Meta OAuth/publish attempt | выдаёт immutable media package ref |

### 10.2. Что физически может проходить через Gate

Gate неизбежно видит минимальный transport payload, иначе он не сможет
доставить сообщение. Но это не даёт ему доменное владение.

| Данные | Допустимая роль Gate | Канонический владелец |
|---|---|---|
| provider account/connection ref | encrypted operational binding | Gate |
| provider update ID, delivery attempt, receipt | durable transport evidence | Gate |
| raw webhook | short-lived encrypted recovery buffer | Gate, с TTL |
| person mapping | opaque `person_ref`/`consent_ref` only | Relations |
| Quest stage/progress | correlation ref only | BECOME |
| practice/expert meaning | package ref only | MAYAKI |
| media bytes/master/edit | validated rendition URL/ref only | TAKE |
| Mark long-term memory | не хранить | Alfa |
| investor narrative | redacted projection only | «Альфа Центавра»/Center |

### 10.3. Запрещённые обратные зависимости

- BECOME не читает Gate SQL tables напрямую.
- Gate не вызывает «перевести человека на следующий stage».
- Alfa не получает Telegram token/session.
- Alfa не вызывает provider send adapter напрямую.
- Gate не объединяет две личности без Relations decision.
- Gate не создаёт/редактирует media master.
- TAKE не хранит Instagram access token.
- MAYAKI не считает provider receipt выполненной практикой без BECOME
  acceptance.
- «Альфа Центавра» не становится writable control path для provider effects
  только потому, что отображает их status.

---

## 11. Вклад Gate в первую демонстрацию BECOME NOW QUEST

Этот раздел намеренно не повторяет BECOME roadmap. Он описывает только
Gate-owned predicates. Смысл этапов, тексты, задания, прогресс и demo narrative
остаются в BECOME.

### 11.1. Минимальная демонстрационная трасса

```text
1. Человек инициирует разрешённое взаимодействие в Telegram
2. Gate durably фиксирует provider update
3. Gate выпускает provider-neutral ingress fact с correlation_id
4. BECOME принимает факт и самостоятельно меняет saga
5. BECOME выдаёт один exact delivery grant
6. Gate выполняет один provider attempt
7. Gate возвращает terminal delivery fact
8. Для внешнего привлечения Gate публикует один одобренный TAKE asset в Instagram
9. «Альфа Центавра» показывает redacted trace, revision, latency и cost
```

### 11.2. Gate-owned demo acceptance

#### `G-DEMO-01 — release truth`

- live fingerprint равен принятому revision;
- deploy receipt содержит exact revision;
- rollback revision известен;
- runtime health относится к тому же revision.

#### `G-DEMO-02 — Telegram ingress`

- одна allowlisted Business connection;
- webhook secret/connection rights verified;
- provider redelivery создаёт один receipt и один canonical fact;
- ingress acknowledgment укладывается в SLO;
- private content отсутствует в CI/log receipt.

#### `G-DEMO-03 — BECOME round trip`

- один `correlation_id` связывает ingress, BECOME decision, delivery grant и
  terminal fact;
- Gate не получает stage/progress payload;
- один grant создаёт максимум один externally visible send;
- exact replay возвращает прежний receipt;
- changed payload under same idempotency key fail closed.

#### `G-DEMO-04 — human safety`

- `FAILED`, `BLOCKED` и `UNKNOWN` automation outcomes создают/сохраняют ровно
  один human-visible work item;
- `UNKNOWN` не повторяется автоматически;
- `/human` и pause обрабатываются без model/provider effect;
- takeover видно владельцу.

#### `G-DEMO-05 — Instagram publication`

- один Professional account;
- current Instagram Login scopes;
- один immutable TAKE rendition;
- один owner/content authority reference;
- container проходит `IN_PROGRESS → FINISHED`;
- `media_publish` вызывается один раз;
- provider media/container refs сохраняются как transport evidence;
- ambiguous publish переводится в reconciliation, а не в blind retry.

#### `G-DEMO-06 — investor-safe evidence`

Проекция в «Альфа Центавре» показывает:

- source revision и production revision;
- channel/capability, но не account ID;
- received/processed/sent timestamps;
- latency;
- terminal state;
- settled/unknown cost;
- code version и policy version;
- redacted failure code;
- ссылку на канонический audit version.

Она не показывает message text, username, phone, raw transcript, provider token,
internal chat ID или private prompt.

### 11.3. Что не является Gate acceptance

- «пользователь прошёл Quest»;
- «практика полезна»;
- «контент конвертирует»;
- «инвестор понял продукт»;
- «человек дал корректное согласие»;
- «Alfa правильно поняла отношения».

Это результаты других доменов. Gate доказывает только то, что разрешённое
сообщение/публикация прошли транспортный контур без дубля и с известным
результатом.

---

## 12. Целевые transport contracts

Ниже — рекомендуемая эволюция уже существующих provider-neutral contracts, а
не требование переписать работающие таблицы.

### 12.1. Ingress fact

```yaml
contract: gate.ingress-fact
version: 1.0.0
event_id: opaque
occurred_at: UTC
received_at: UTC
idempotency_key: provider-neutral opaque key
correlation_id: opaque
trace_id: opaque
payload:
  channel: telegram_business | instagram
  capability: message | voice_note | comment | dm
  connection_ref: opaque
  actor_ref: opaque
  content_ref: opaque encrypted/minimized reference
  consent_ref: opaque optional
  provider_event_ref: opaque
  policy_version: opaque
```

Не передавать в envelope product stage, campaign name, телефон, username или
raw provider object.

### 12.2. Delivery grant

Существующий `gate.delivery-grant` — правильная основа. Для multi-product
runtime должны быть явно проверяемы:

```yaml
delivery_intent_ref: immutable
recipient_ref: Relations-resolved opaque ref
channel_capability: exact
content_package_ref: immutable
authority_ref: exact
authorized: true
expires_at: short lived
idempotency_key: stable for the logical effect
correlation_id: end-to-end
trace_id: end-to-end
data_class: bounded
project_ref: cost attribution only
```

Gate не должен принимать произвольный «prompt, затем сам реши, кому и что
отправить». Вход effect boundary — уже сформированный intent.

### 12.3. Delivery fact

```yaml
outcome: sent | delivered | failed | unknown | blocked
attempt_ref: opaque
provider_receipt_ref: opaque optional
reconciliation_required: boolean
settled_cost_microusd: integer optional
failure_code: bounded, payload-free
first_attempt_at: UTC
terminal_at: UTC
```

`UNKNOWN` — terminal для автоматического retry, но не terminal для
операционного reconciliation.

### 12.4. Reconciliation fact

Отдельный факт должен закрывать uncertainty:

```yaml
original_attempt_ref
previous_outcome: unknown
resolved_outcome: sent | failed | cancelled | still_unknown
evidence_class: provider_query | owner_observation | ledger_repair
resolved_at
resolver_policy_version
```

Он не изменяет историю исходного provider attempt задним числом.

### 12.5. Contract evolution

- additive field — minor version;
- semantic/state change — major version;
- consumer advertises accepted versions;
- producer хранит contract schema/hash;
- incompatible event fail closed и попадает в quarantine;
- no dual-write без parity proof;
- migration acceptance содержит old/new replay fixtures.

---

## 13. Telegram: возможности и ограничения

### 13.1. Business/Bot API

Официальный [Telegram Bot API](https://core.telegram.org/bots/api) поддерживает:

- `business_connection`;
- `business_message`;
- edited/deleted business-message updates;
- rights, включая разрешённые reply/read действия.

[Connected business bots](https://core.telegram.org/api/bots/connected-business-bots)
документируют важные ограничения:

- к одному business account подключается один business bot;
- изменение connection создаёт новый connection ID;
- новые/edited/deleted events приходят как updates;
- старые connection updates не становятся автоматически replayable backlog.

**Следствие для Gate:**

- connection ID — versioned binding, не постоянная identity;
- re-connect должен автоматически quarantine старый binding;
- webhook recovery не заменяет historical import;
- connection rights входят в readiness, а не проверяются только перед send;
- обновление/удаление сообщения должно проецироваться как correction/tombstone.

### 13.2. Personal history

Bot API не предоставляет account-wide `getDialogs/getHistory` для произвольной
личной истории Марка. MTProto это умеет, но даёт существенно более широкие
права.

Правильный порядок:

1. новые события — Business API;
2. bounded owner-selected history — offline export;
3. MTProto — только residual exact need после terms/data gates;
4. bulk history, contacts, secret chats и silent read mutation — вне первой
   версии.

Repository audit
[`TELEGRAM_PERSONAL_READ_CAPABILITY_AUDIT.md`](https://github.com/markmalina/gate/blob/cca4eeb91c2d7ea6f0160c6690a849319ad1a0db/docs/TELEGRAM_PERSONAL_READ_CAPABILITY_AUDIT.md)
правильно оставляет live read disabled.

### 13.3. Voice notes

Текущий контур уже доказывает:

- durable receipt;
- archive/transcription budgeting;
- provider-unknown treatment;
- `84/84` delivered;
- queue recovery после incident.

До внешнего масштаба нужны:

- отдельный worker partition;
- MIME/size/duration caps;
- malware/media parser isolation;
- encrypted object retention;
- language/model version в receipt;
- transcript correction/deletion;
- per-project cost attribution;
- explicit data gate до модели;
- terminal human fallback при failed transcription.

---

## 14. Telegram calls и телефонные разговоры

### 14.1. Чего не даёт Bot API

В Bot API нет documented personal call media stream или call recording
endpoint.

MTProto методы
[`phone.requestCall`](https://core.telegram.org/method/phone.requestCall) и
[`phone.acceptCall`](https://core.telegram.org/method/phone.acceptCall)
помечены как user-only. Официальная
[E2E calls documentation](https://core.telegram.org/api/end-to-end/video-calls)
описывает полный cryptographic/VoIP client protocol.

**[уверен]** Даже user API integration не превращает Telegram в простой
server-side recorder. Для ALPHA VIA это был бы отдельный высокорисковый клиент,
а не небольшой Gate adapter.

### 14.2. Практичная граница

Для телефонных разговоров Марка правильная текущая цепочка:

```text
Samsung call recorder
  → owner-controlled file sync
  → Dropbox intake folder
  → Gate durable file receipt
  → bounded metadata/virus/type check
  → transcription under explicit data gate
  → opaque transcript projection to Alfa
  → retention/deletion receipt
```

Gate владеет intake и provenance. Alfa владеет смыслом разговора. Relations
разрешает связь с Person. Исходный audio не должен автоматически попадать в
BECOME, MAYAKI, Center или investor view.

### 14.3. Правовые и человеческие gates

Запись телефонного разговора зависит от юрисдикции и согласия участников.
Этот аудит не даёт юридического заключения. До external-person rollout должны
быть записаны:

- lawful/consent basis;
- disclosure mechanism;
- purpose;
- allowed processors/models;
- retention;
- deletion/export/correction;
- запрет автоматической отправки выдержек;
- процедура mistaken-identity.

---

## 15. Instagram runtime

### 15.1. Текущий факт

**[уверен]** В exact source есть только deterministic
`MetaTransportRehearsal`. Он:

- проверяет authority envelope;
- поддерживает idempotent replay;
- моделирует `delivered/failed/unknown`;
- выдаёт provider-neutral fact;
- не вызывает Meta.

Это отличный contract test, но не connection.

### 15.2. Официальная поверхность

Официальный verified Meta workspace:
[Meta on Postman](https://www.postman.com/meta/).

[Instagram API with Instagram Login](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login)
предназначен для Professional accounts — Business и Creator. Актуальные scopes:

- `instagram_business_basic`;
- `instagram_business_content_publish`;
- `instagram_business_manage_messages`;
- `instagram_business_manage_comments`.

Старые `business_*` scope names были deprecated 27 January 2025, поэтому
hard-coded старые scopes нельзя переносить из примеров.

[Reels publishing](https://www.postman.com/meta/instagram/folder/y6xustx/reels-publishing)
использует последовательность:

```text
create media container
→ poll container status
→ media_publish(creation_id)
→ persist provider receipt
```

Официальный
[`/{ig_user_id}/media_publish`](https://www.postman.com/meta/instagram/request/23987686-f1c081c0-be35-4ffa-84bb-2c1726860c2b)
принимает `creation_id`.

[Instagram messaging](https://www.postman.com/meta/instagram/folder/23987686-f05b6c9f-a4be-4511-9f88-1cd94828fdf3)
имеет продуктовые ограничения:

- пользователь должен сначала написать Professional account;
- group messaging не поддерживается;
- messages/comments имеют отдельные permissions;
- webhook notifications предпочтительнее polling.

### 15.3. Минимальный publish adapter

#### Connection state

```text
disconnected
→ oauth_pending
→ connected
→ permission_verified
→ account_verified
→ ready
→ degraded / revoked
```

Token хранится только в secret manager/provider integration, никогда в DB
receipt, issue, CI, trace или «Альфа Центавре».

#### Media validation

Перед provider effect Gate проверяет:

- TAKE package immutable;
- MIME/container/codec/duration/size;
- rendition URL доступна Meta, но не является публичным долговременным master;
- caption/alt text уже approved;
- package hash совпадает grant;
- grant не истёк;
- Professional account и publish scope verified.

#### Idempotency

Meta publish API нельзя считать exactly-once только на основании HTTP response.
Gate сохраняет container ID до polling и media ID после publication.

```text
нет container ref → разрешён один create
есть container ref, status pending → только poll
status finished, нет publish attempt → один publish
publish response unknown → reconciliation, не create/publish заново
media ID найден → terminal sent/published
```

#### Webhooks

Для messaging/comments:

- verify Meta signature;
- persist raw receipt before response;
- dedupe event;
- normalize minimal fact;
- не использовать polling как основной live path;
- unrecognized update class quarantine;
- edited/deleted content сохранять как correction/tombstone.

### 15.4. Чего не должно быть в первом adapter

- автоматического выбора контента;
- самостоятельного написания caption;
- campaign/funnel logic;
- audience segmentation;
- ads;
- bulk DM;
- group messaging emulation;
- retry ambiguous publication;
- скрытой публикации без authority;
- переноса Instagram identity truth в Gate.

---

## 16. Reliability model

### 16.1. Основной инвариант

Для одного logical effect:

```text
много ingress deliveries допустимы
много internal process attempts допустимы
не более одного externally visible effect допустимо
```

Это достигается не обещанием «exactly once», а сочетанием:

- durable receipt;
- unique logical idempotency key;
- transactional outbox;
- lease/claim;
- immutable payload fingerprint;
- one provider attempt record;
- terminal unknown;
- reconciliation;
- idempotent downstream consumers.

### 16.2. State machine

```text
RECEIVED
  → CLAIMED
  → PROCESSING
  → SENT / FAILED / BLOCKED / UNKNOWN
  → RECONCILED (для UNKNOWN отдельным фактом)
```

`PROCESSING` не может быть бессрочным. Сейчас generic
`ProcessTelegramOutboxService` ловит только ожидаемые exceptions; unexpected
exception может оставить item processing, а stale recovery усилена прежде всего
для voice path.

**Рекомендуемое правило:**

- claim имеет lease expiry;
- worker crash не меняет payload fingerprint;
- expired processing получает deterministic recovery decision;
- внешне неоднозначный attempt никогда не возвращается в auto-send;
- internally unattempted item может быть reclaimed;
- recovery различает «effect точно не начат» и «response unknown».

### 16.3. Backpressure

Для каждого partition:

- max concurrency;
- oldest due age;
- queue depth;
- retry depth;
- provider rate-limit state;
- circuit breaker;
- dead-letter/quarantine count;
- cost budget.

BECOME transport не должен конкурировать с bulk history, media transcription
или experimental groups за один semaphore.

### 16.4. Deployment safety

Release acceptance должна сравнивать:

```text
git main revision
CI exact head
deployment requested revision
runtime reported revision
schema revision
worker policy/config version
```

Любое расхождение — `degraded` или `blocked`, а не «green».

---

## 17. Observability и SLO

### 17.1. Signals

[OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/)
разделяют traces, metrics, logs и baggage. Gate нужны все первые три:

- **trace:** один correlation path без payload;
- **metrics:** rates, latency, queue age, error/unknown, cost;
- **logs:** structured redacted operational events;
- **baggage:** только opaque project/policy refs, без личности и текста.

### 17.2. Предлагаемые SLIs/SLO

Это стартовые targets, а не наблюдённые гарантии.

| SLI | Предлагаемый SLO | Почему |
|---|---|---|
| Webhook durable acknowledgment | 99.9% ≤ 2 s за 30 дней | provider retry и user responsiveness |
| Eligible Telegram text to terminal transport decision | 99% ≤ 15 s | first BECOME interaction |
| Voice note to terminal archive/transcription result | 99% ≤ 5 min | тяжелее text |
| Queue oldest age, Telegram core | < 60 s в 99% samples | обнаружить stuck loop |
| Logical duplicate external sends | 0 для одного idempotency key | safety invariant |
| Unknown automatically retried | 0 | safety invariant |
| Demo-correlation unresolved unknown | 0 к demo acceptance | investor trace |
| Production drift detection | ≤ 5 min | current incident class |
| Permission/token revocation detection | ≤ 15 min | provider readiness |
| Redacted receipt completeness | 100% accepted effects | auditability |

Google SRE рекомендует пользовательские SLO и error budget, а не цель 100%:
[Service Level Objectives](https://sre.google/sre-book/service-level-objectives/).

### 17.3. Alerts

Нужны alerts, а не только dashboard:

- runtime revision ≠ expected;
- worker heartbeat stale;
- oldest queue age breach;
- parity mismatch > 0;
- unknown outcome created;
- token/permission revoked;
- budget remaining below threshold;
- duplicate idempotency conflict;
- human fallback missing after failed automation;
- raw payload retention expiry failed.

У каждого alert есть owner, severity, runbook и auto-stop condition.

---

## 18. Privacy, security и data lifecycle

### 18.1. Принципы

Европейская комиссия формулирует
[GDPR principles](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/principles-gdpr_en):
purpose limitation, data minimization, storage limitation, accuracy и security.

Для Gate это означает:

- transport purpose не превращается в бессрочную memory purpose;
- raw provider payload хранится минимально;
- identity mapping отделён;
- delete/edit provider events исполняются;
- model processing — отдельный data gate;
- investor view — отдельная disclosure projection.

### 18.2. Рекомендуемая retention matrix

Конкретные сроки должны быть приняты владельцем и legal/privacy policy.

| Класс | Default target | Условие продления |
|---|---|---|
| raw webhook body | часы/дни, encrypted | только incident/legal hold |
| normalized transport envelope | пока нужен delivery/audit | без raw identity/content |
| delivery receipt | долгосрочно как redacted evidence | project/accounting policy |
| provider credential/token | только secret manager | пока connection active |
| voice media | short bounded TTL | explicit archive purpose |
| transcript | Alfa/Relations-approved store | purpose + deletion policy |
| model prompt/context | minimum provider setting | recorded data gate |
| investor projection | redacted snapshot | disclosure version |

### 18.3. Secrets

- no secrets in GitHub, logs, traces, receipts, Center or audit;
- OAuth refresh token не читается application UI;
- rotate/revoke имеет terminal receipt;
- per-provider least privilege;
- production/test tokens разделены;
- callback URLs allowlisted;
- webhook signature/secret verification mandatory;
- secret absence возвращает not-ready, а не silent partial operation.

### 18.4. Threat cases

| Угроза | Control |
|---|---|
| webhook replay | provider ID dedupe + signature + time/scope validation |
| changed payload under same key | immutable fingerprint conflict |
| compromised product service | Gate validates authority, expiry, recipient, capability |
| prompt asks to send elsewhere | model has no recipient/provider authority |
| leaked trace | no PII/content in attributes/baggage |
| stale business connection | binding version + rights readiness |
| token revoked | readiness and circuit breaker |
| ambiguous provider timeout | terminal unknown + reconciliation |
| mistaken person merge | Relations decision; Gate uses opaque refs |
| malicious media | isolated validation, caps, no inline parser in web process |
| internal operator mistake | one execution/lease/PR + owner gates |

---

## 19. Cost model

### 19.1. Наблюдённые internal ledgers

На runtime snapshot:

- Telegram voice budget: `$1.00`;
  - settled approximately `$0.315918`;
  - reserved approximately `$0.211100`;
  - remaining approximately `$0.472982`;
  - five outcomes still unknown.
- Alpha model budget: `$50.00`;
  - reserved/used projection approximately `$0.405504`;
  - remaining approximately `$49.594496`.

Это application ledger, а не vendor invoice.

### 19.2. Чего аудит не знает

- фактический Railway invoice;
- GitHub Actions cost;
- Dropbox storage/egress;
- Meta operational cost;
- OpenAI invoice reconciliation;
- стоимость инженерного времени.

Поэтому нельзя честно назвать monthly burn Gate.

### 19.3. Нужная cost projection

Каждый charge/reservation:

```text
project_ref
mission: become | mark_personal
capability
provider
model
units
currency
reserved
settled
released
unknown
correlation_id
occurred_at
```

Без Person ID, message text и provider account ID.

Для первой демонстрации «Альфа Центавра» должна показывать:

- transport cost одного demo journey;
- model/transcription cost отдельно;
- settled против reserved/unknown;
- инфраструктуру отдельной строкой, когда появятся invoices;
- число externally visible effects.

### 19.4. Архитектурный cost trade-off

Разделение worker processes может увеличить число Railway processes/replicas.
Но это не аргумент сохранять общий failure domain.

Рациональная последовательность:

1. измерить queue age/CPU/connection churn;
2. вынести отдельные worker commands;
3. дать каждому независимые concurrency/resource caps;
4. масштабировать только bottleneck;
5. не вводить сетевые микросервисы без доказанной необходимости.

---

## 20. Последовательность дальнейшего развития

Это архитектурная последовательность после единственного текущего указателя,
не новые executions и не дубликат BECOME roadmap.

### Фаза 0 — завершить runtime acceptance recovery v9

- Railway root-cause evidence получено;
- exact production fingerprint `27fa336648e4` получен;
- осталось записать redacted terminal acceptance long-call recovery v9;
- не касаться response-unknown calls без отдельной authority;
- сохранить deploy/acceptance либо rollback receipt.

### Фаза 1 — закрыть demo safety gaps

- исправить Universal Mayak result → human fallback mapping;
- объяснить четыре parity mismatch;
- доказать restart/replay;
- отделить Telegram core от voice/experimental failure domains;
- убрать per-iteration engine churn;
- добавить durable queue-age/revision health.

### Фаза 2 — формализовать BECOME transport

- закрепить ingress/delivery/reconciliation contracts;
- добавить contract fixtures между репозиториями;
- провести provider-free end-to-end rehearsal;
- провести один owner-bounded Telegram canary;
- вывести redacted trace в Center.

### Фаза 3 — подключить Instagram

- owner OAuth/identity gate;
- Professional account/scopes readiness;
- publish adapter;
- TAKE package validation;
- container polling;
- terminal publication receipt;
- webhook ingress для разрешённых comments/DM;
- один bounded BECOME publication canary.

### Фаза 4 — завершить личный контур Марка

- Business messages/voice notes как primary;
- reply proposal Alfa без provider authority;
- exact human/owner send policy;
- Dropbox call intake;
- transcription data gate;
- Relations identity projection;
- correction/deletion/retention receipts.

### Фаза 5 — production operations

- OpenTelemetry;
- SLO/error budgets;
- alert/runbooks;
- cost attribution;
- token rotation/revocation rehearsal;
- backup/restore для новых Meta/receipt tables;
- chaos tests: worker crash, provider timeout, duplicate webhook, revoked token.

### Stop conditions для любой фазы

- неожиданная third-party identity/payload;
- expired/missing lease;
- second active execution/PR;
- production revision mismatch;
- permission scope шире записанного;
- unknown externally visible effect;
- duplicate delivery;
- privacy/terms uncertainty;
- budget breach;
- rollback revision отсутствует.

---

## 21. Риски

| Риск | Вероятность | Влияние | Владелец решения | Mitigation |
|---|---|---|---|---|
| stale production после green merge | высокая: уже наблюдается | критическое | Gate release | revision gate + alert |
| cross-route worker crash | высокая: уже происходило | критическое | Gate runtime | partitions/process isolation |
| silent loss of human fallback | средняя | критическое | Gate application | disposition mapping tests/fix |
| duplicate Instagram publish после timeout | средняя | критическое | Gate Meta adapter | container/attempt state + unknown |
| Telegram connection rotation | средняя | высокое | Gate connection | versioned binding/readiness |
| MTProto terms violation | неизвестна | критическое | owner/compliance | fail closed; Business/export first |
| raw personal data over-retention | средняя | высокое | Gate/privacy | lifecycle matrix + deletion jobs |
| wrong person association | средняя | высокое | Relations | opaque refs + correction |
| product semantics leak into Gate | высокая без guardrails | высокое | architecture | contracts + ownership tests |
| Center exposes private evidence | средняя | высокое | Center/access | disclosure projections |
| cost unknown accumulates | средняя | среднее | Gate finance | reservation reconciliation |
| docs trigger duplicate work | высокая: drift visible | среднее | Gate governance | generated status + one pointer |

---

## 22. Где хранить и как публиковать этот аудит

### 22.1. Source of truth

Канонический документ принадлежит Gate:

```text
markmalina/gate
└── docs/
    └── analysis/
        └── GATE-SYSTEM-AUDIT.md
```

`GATE-SYSTEM-AUDIT.md` — единственный стабильный канонический файл. История
существует только в Git commits/tags; ручные dated copies не создаются.

### 22.2. Manifest для Center

Center хранит не ручную копию текста, а manifest:

```yaml
project: gate
title_ru: Gate
canonical_repository: markmalina/gate
canonical_path: docs/analysis/GATE-SYSTEM-AUDIT.md
source_revision: cca4eeb91c2d7ea6f0160c6690a849319ad1a0db
captured_at: 2026-07-30T05:59:00Z
content_sha256: generated-at-import
classification: owner
public_projection: false
status: audited
```

### 22.3. Страница в «Альфа Центавре»

Страница Gate должна иметь:

- executive verdict;
- current source revision;
- production revision и drift indicator;
- две миссии;
- runtime health cards;
- BECOME demo acceptance;
- architecture/dependencies;
- risk register;
- cost projection;
- current single continuation;
- full Markdown download;
- version history;
- role-based redaction.

### 22.4. Versioning

1. Анализ изменяется PR в `markmalina/gate`.
2. Exact source revision входит в front matter/manifest.
3. Git commit/tag является версией и immutable historical reference.
4. Center importer проверяет content hash.
5. Site build показывает revision и captured time.
6. Старая версия открывается по Git revision/tag, не по ручной dated copy.
7. Manual site edit не может изменить канонический текст.

### 22.5. Disclosure levels

| Роль | Видит |
|---|---|
| Марк/owner | полный audit, private operational aggregates, costs |
| core team | architecture, stages, redacted incidents, assigned work |
| project collaborator | только нужный project view |
| investor | product proof, dates, throughput, reliability, spend aggregate; без personal content и secrets |
| public | только специально принятая marketing projection |

Owner-only ChatGPT/Sites access к целому сайту сам по себе не доказывает
page-level role enforcement.

---

## 23. Non-claims

Чтобы этот документ не превратился в маркетинговую легенду:

1. Мы **не утверждаем**, что совпадение production revision с
   `cca4eeb91c2d...` доказывает terminal outcome длинного звонка.
2. Мы **не утверждаем**, что Railway auto-deploy системно сломан: конкретный
   failed rollout имел доказанную migration-причину, а repair rollout успешен.
3. Мы **не утверждаем**, что четыре parity mismatch означают четыре потерянных
   сообщения.
4. Мы **не утверждаем**, что пять voice unknown означают пять неуспешных
   транскрипций.
5. Мы **не утверждаем**, что Universal Mayak fallback bug уже повредил
   пользователю; доказан latent code path.
6. Мы **не утверждаем**, что Universal Mayak готов к внешним пользователям:
   свежий proof owner-bounded.
7. Мы **не утверждаем**, что Instagram подключён.
8. Мы **не утверждаем**, что Meta app/token/scopes существуют или действуют.
9. Мы **не утверждаем**, что Gate принимает Telegram calls.
10. Мы **не утверждаем**, что MTProto разрешён для Alfa.
11. Мы **не утверждаем**, что Telegram API Terms одинаково применяются к
    каждому Bot API use case; нужен точный compliance review.
12. Мы **не утверждаем**, что raw personal data хранится бессрочно; единая
    lifecycle policy не доказана.
13. Мы **не утверждаем**, что CI покрывает все runtime classes.
14. Мы **не утверждаем**, что internal budget ledger равен provider invoice.
15. Мы **не утверждаем**, что Gate выполняет BECOME journey или MAYAKI
    программу.
16. Мы **не утверждаем**, что «Альфа Центавра» уже импортирует этот документ.
17. Мы **не утверждаем**, что role-based views сайта уже enforce.
18. Мы **не утверждаем**, что рекомендации этого аудита являются
    GitHub-approved work.

---

## 24. Единственный существующий `next_pointer`

```yaml
repository: markmalina/gate
issue: https://github.com/markmalina/gate/issues/354
source_receipt: https://github.com/markmalina/gate/pull/393
stage: five-minute-wav-recovery
execution_id: gate-voice-deploy-safe-long-call-claim-20260729T191800Z
latest_issue_receipt_state: stale_blocked_before_repairs
failed_pr: https://github.com/markmalina/gate/pull/391
failed_merge_revision: ba63ff34ec5b41c71797cf75ad1c5975de2239c8
failed_deployment: 071fb019-b73b-4551-be35-4dd74bf2ab08
failure: pre-deploy check violation for recovery_v8; transaction rolled back
repair_pr: https://github.com/markmalina/gate/pull/392
repair_exact_head: d5e0e9f2ca232aaa92b91523b34d2619c1c43db2
repair_ci: 1330 / success
repair_merge_revision: cca4eeb91c2d7ea6f0160c6690a849319ad1a0db
v9_pull_request: https://github.com/markmalina/gate/pull/393
v9_merge_revision: 27fa336648e4c5aadf02fa3f001c5fffe47fee9c
v9_ci: 1332 / success
observed_production_revision: 27fa336648e4
fresh_evidence_state: v9_deployed_awaiting_terminal_runtime_receipt
exact_action: >
  Continue the same issue #354 execution from deployed recovery v9. Observe
  and record only redacted terminal runtime evidence for the exact bounded long-call:
  terminal recording state, voice queue, settled/open/unknown accounting and
  worker health. Keep the separate response-unknown call excluded; do not make
  a manual or blind provider retry; do not create a new issue, execution,
  branch or PR. Close the stage only with an exact revision runtime acceptance
  or rollback receipt.
```

Это единственный рабочий указатель данного аудита. Все последующие фазы выше —
архитектурная последовательность, не активные claims.

---

## 25. Итог

Gate уже содержит правильное ядро: durable ingress, provider-neutral
contracts, exact authority, terminal unknown и сильную release discipline.
Его не нужно превращать в общий backend ALPHA VIA.

Ближайшая задача Gate как системы — сделать доказательство сильнее
функциональности:

- один revision от PR до live;
- один изолированный transport effect;
- один human-safe fallback;
- один end-to-end BECOME correlation trace;
- одна реальная Instagram publication;
- одна privacy-preserving личная проекция для Alfa;
- один redacted evidence stream для «Альфа Центавры».

После этого Gate станет не «набором интеграций», а проверяемой границей между
ALPHA VIA и внешним миром — именно тем, что нужно и первой демонстрации BECOME
NOW QUEST, и личной Alfa Марка.

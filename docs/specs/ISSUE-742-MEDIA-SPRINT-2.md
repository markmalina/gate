# Gate #742 — готовые изображения из Work

Контракт второго спринта [#742](https://github.com/AlphaCenter/gate/issues/742),
родитель [#735](https://github.com/AlphaCenter/gate/issues/735). Он расширяет
принятый текстовый Rich #737 и сохраняет отдельный PDF/ZIP-контракт #700.
Основа реализации — интегрированный main
`d5a9fbb8a0b5444c0734cba040e0a67bd1a7424e`. Это исходная ревизия исполнения,
не утверждение о текущем Production SHA или о завершённой live-приёмке.

Execution: `gate-media-742-20261007T1041Z`, неизменяемый срок разрешённых действий:
`2026-10-09T10:37:34Z`. Live scope — собственный RU Business владельца → его
собственный BY-получатель. BY native outbound отложен владельцем; покупки Premium
и изменения credentials/OAuth отсутствуют. Проверка BY в синтетических тестах
не доказывает его действующее право на Rich-отправку.

## Рабочий путь

Work читает выбранные PNG/JPEG и передаёт их байты структурированным
`content_base64` в `gate_prepare_reply_media`. Локальный путь Work не является
путём Gate. URL, `artifact_ref`, произвольный server path и загрузка через
служебную отправку в Telegram не входят в реализованный путь.

1. Выбрать текущую scoped task UUID собственных RU→BY и проверить readiness.
   `follow_up=true` использовать только при обоснованном follow-up replied task.
2. Подготовить все N файлов одним `gate_prepare_reply_media`. Повтор того же
   scoped `staging_key` с идентичным содержимым возвращает существующий пакет
   без продления expiry; изменение содержимого приводит к staging conflict.
3. Показать MCP image content blocks в порядке элементов, весь snapshot,
   исходные/подготовленные SHA256, transformations, captions, mode, account,
   recipient и ожидаемое число видимых сообщений. `sent=false` и
   `approval_required=true` означают, что внешней отправки ещё не было.
4. При необходимости повторить `gate_preview_reply_media` с сохранённым
   `package_id`. Preview проверяет scope, snapshot и фактические хэши байтов.
5. После exact approval вызвать `gate_send_approved_reply`: `draft_id`,
   `confirmed=true`, `expected_account`, `expected_recipient_chat_id`,
   `expected_media_package_sha256` из этого preview и тот же `execution_id`.
   Действующий owner запуск разрешает только зафиксированные ниже fixtures.
6. Сопоставить durable receipt, исходящую операцию и реальное отображение
   получателя. `sent` подтверждает provider receipt; визуальное качество,
   жесты и листание требуют отдельного наблюдения в доступном клиенте.

Structured preview содержит account/recipient/connection и полный immutable
snapshot; они доступны лишь в защищённом owner scope. Не переносить реальные
байты, base64, captions, account/recipient/connection/message IDs в публичные
issue, логи или текст draft. Git содержит только разрешённые synthetic fixtures.

## Пять режимов и версии

| Mode | Items | Provider | Видимые сообщения |
| --- | --- | --- | --- |
| `photo` | 1 | `sendPhoto` | 1 |
| `album` | 2–10 | `sendMediaGroup` | N |
| `rich_photo` | 1 | `sendRichMessage`, native photo block | 1 |
| `rich_slideshow` | 2–10 | `sendRichMessage`, native slideshow/photos | 1 |
| `rich_collage` | 2–10 | `sendRichMessage`, native collage/photos | 1 |

Альбом сохраняет отдельные фотографии и captions. Collage сохраняет нативные
photo blocks; единое сведённое изображение не создаётся. Порядок элементов
одобренного пакета фиксирован; пиксельная сетка зависит от Telegram-клиента.

Package schema — `gate.media.v1`; compiler — `gate.media-package.v1`.
Media Rich — `gate.rich.v2`, converter `gate.telegram-rich-media.v1`.
Текстовый контроль T использует прежний `gate.rich.v1`; прежние v1 hashes и
смысл compiler сохраняются.

Rich prepare принимает локальные batch aliases `item:1`…`item:N` в `media_ref`.
Все элементы должны встретиться ровно один раз и в исходном порядке, в режиме
соответствующего блока. `_normalize_rich` заменяет aliases на opaque refs,
вставляет output `sha256` и `version=1`. На alias не требуется исходный hash;
если `sha256` всё же передан, он должен совпасть с hash исходного файла.
Opaque refs и output hashes реального prepare являются окончательными.

У ordinary фото caption находится в `items[].caption`, формат `plain` или
`markdown`. В rich режимах item captions пустые; native подписи задаются через
`caption: {"text": RichText}` у photo и slideshow/collage. Compiler не полагается
на игнорируемый caption вложенного `InputMediaPhoto`.

## Фактические application limits

Это ограничения Gate, а не обещание максимальных возможностей Telegram.

| Проверка | Ограничение |
| --- | --- |
| Тип | PNG/JPEG; MIME + extension + signature + decode согласованы |
| Source и normalized файл | каждый 1 byte–8 MiB |
| Base64 string файла | до 11 184 812 ASCII characters, strict decode |
| Сумма source; отдельно сумма normalized | каждая до 24 MiB/package |
| Items | 1–10; одиночные modes ровно 1, групповые 2–10 |
| Максимальная сторона | 4096 px |
| Width + height | 8192 px |
| Pixels | 16 000 000 |
| Aspect ratio | максимум 20:1 |
| Filename | basename, без traversal/control, до 255 UTF-8 bytes |
| Ordinary caption | максимум 1024 UTF-16 units после conversion |
| Rich source/native JSON | максимум 65 536 UTF-8 bytes |
| Rich blocks / text / inline | 32 / 16 000 UTF-16 units / 512 nodes, depth 4 |
| Staging/execution key | 1–128 ASCII `[A-Za-z0-9._:-]`, начало alphanumeric |
| Execution expiry | timezone-aware, future, максимум 48h от prepare |
| Asset/package expiry | min(prepare time + 24h, execution expiry) |

Raw JSON caption schema допускает до 4096 characters до conversion; service
строго проверяет итоговые 1024 UTF-16 units. PNG animation/trailing data/CRC
errors, JPEG trailing/invalid container, повреждённые и неполностью декодируемые
изображения отклоняются; Pillow bomb warning/error приводит к отказу. Все
изображения и captions проверяются до SQL writer, а scoped package сохраняется
транзакционно. Не обещать обнаружение любого мыслимого polyglot.

Validator учитывает EXIF orientation, приводит RGB/RGBA, удаляет metadata и
переупаковывает PNG либо JPEG quality=95/subsampling=0. Размеры не меняются:
crop/resize/ретушь отсутствуют. Transform version содержит версию Pillow;
source и output SHA256 хранятся раздельно. Telegram photo может дополнительно
перекодировать изображение: byte-perfect доставка original не заявляется.

Отдельный фиксированный application memory/time/concurrency лимит decoder
этот документ не заявляет: нужны точные runtime bounds и их проверки перед
приёмкой. Byte/pixel bounds выше не заменяют такой проверки.

## Approval, долговечность и retention

Package digest связывает schema/converter, mode, порядок, captions, output
hashes, transforms, scope/workspace/principal/account/project/recipient/
connection, draft revision и execution expiry. Approval старого/чужого digest
не разрешает изменённый пакет. Media marker отдельно блокирует legacy text edit
и отправку через прежнюю text ветвь. Prepare не меняет внешнее состояние Telegram.

Send reservation, пакетный receipt и execution counters долговечны. Один
execution имеет caps **12 provider writes / 20 visible messages**, включая
контроль T. Album резервирует N сообщений при одной provider операции; Rich
медиапакет — одно. Unknown/partial удерживает ожидаемую cardinality и fences.
Обычный text send с `execution_id` расходует тот же существующий media budget;
он не создаёт независимый бесплатный canary budget. Повтор durable результата
не создаёт второй provider POST и не повторно списывает бюджет.

Album требует ровно N различных подтверждённых message IDs, согласованные
chat/Business connection и media group. Short/duplicate/malformed reply не
объявляется полным успехом. Timeout, 5xx, crash after POST и pending receipt
возвращают truthful unknown/partial без слепого retry или fallback. Generic
provider 400 — подтверждённый отказ операции, но сам по себе не доказательство
`unsupported` для аккаунта/режима. Заглушка не считается native delivery.

Expiry блокирует новые preview/send просроченных байтов. `prune_expired_reply_media`
удаляет bytes у истёкших пакетов, сохраняет hashes/metadata/receipts и пропускает
reserved/sending/unknown операции до reconciliation; используются те же draft
locks, что у send. Existing `run_reply_document_cleanup_loop` вызывает media
cleanup при запуске, затем раз в 3600 секунд; lifespan запускает этот loop при
`GATE_DOCUMENT_CLEANUP_ENABLED` (default true), отдельно от send switches.
На healthy worker bytes истёкшего обычного пакета удаляются следующим проходом.
Deployment/worker failure и unknown fences могут продлить фактическое хранение.
Факт worker health и удаления конкретных bytes доказывается отдельным receipt;
наличие wiring само по себе не является proof Production cleanup.

Сохраняются existing send global/per-account switches, Business connection
origin, can_reply, task/follow-up admission, policy/canary/process fences.
Актуальный integrated baseline не содержит ещё не слитый **G1a** admission;
не заявлять его наличие или 24h hardening на основании этого media PR.

## Точные synthetic fixtures P/A/R/S/C/T

Generator `scripts/generate_media_sprint2_fixtures.py` создаёт четыре PNG
800×600 RGB с PNG sRGB intent 0, без EXIF, крупными номерами 1–4, точными
подписями `Gate — тестовое изображение N`, различными контрастными фонами и
простыми фигурами. Это обычная Pillow graphics: model/imagegen/transcription
calls=0. Labels расположены внутри безопасных полей.

Исходники: `tests/fixtures/gate_media_sprint2/image-{1,2,3,4}.png`.
`manifest.json` содержит SHA256 исходных файлов и output hashes, полученные
реальным `_validate_image` локально, transform/version и pin renderer/font.
Pillow 12.3.0 / FreeType 2.14.3 / zlib 1.3.2 и SHA256 DejaVuSans.ttf фиксированы;
несовпадение pin вызывает отказ регенерации вместо тихой замены файлов.
Manifest — локальная synthetic provenance; runtime prepare обязан вернуть свои
output hashes/transform version, которые проверяют перед exact approval.

| Fixture | Точное содержимое | Writes/messages |
| --- | --- | --- |
| P | image1; `Gate Media Sprint 2 — одиночное фото 1.` | 1/1 |
| A | image1…4; `Gate Media Sprint 2 — кадр N из 4.` у каждого | 1/4 |
| R | heading `Gate Media — фото в Rich`; paragraph `Синтетическая проверка изображения внутри Rich.`; image1 caption `Кадр 1.` | 1/1 |
| S | heading `Gate Media — слайд-шоу`; native photos1…4 captions `Кадр N из 4.`; caption `Синтетическая проверка листания и порядка.` | 1/1 |
| C | heading `Gate Media — коллаж`; native photos1…4 captions `Кадр N из 4.`; caption `Синтетическая проверка совместного отображения.` | 1/1 |
| T | v1 heading `Gate Media — контроль текста`; paragraph `Синтетическая проверка прежнего текстового Rich.` | 1/1 |

План — **6 writes / 9 messages**; общий верхний предел 12/20. Дополнительные
BY seeds, посторонние получатели, BY native canary и auto retries не разрешены.

[Operator JSON](ISSUE-742-MEDIA-OPERATOR-EXAMPLES.json) содержит полные synthetic
base64 и source hashes для P/A/R/S/C, exact T и шаблоны preview/send. Это валидный
JSON, но `${X...}` — response bindings, которые нельзя передавать инструменту.
UUID `74200000-0000-4000-8000-000000000001` — пример task, не существующая
подтверждённая task. До вызова подставляют текущий защищённый task и recipient,
полученный из того же snapshot и сверенный с own BY scope. Package digest и IDs
не выдумываются заранее. Deadline исходного execution не продлевается.

```sh
PYTHONPATH=. python scripts/generate_media_sprint2_fixtures.py
# Для текущего защищённого task, при обоснованном follow-up:
PYTHONPATH=. python scripts/generate_media_sprint2_fixtures.py --task-id TASK_UUID --follow-up
ruff check scripts/generate_media_sprint2_fixtures.py
```

Первая команда воспроизводит Git fixtures в pinned environment. Вторая локально
готовит operator JSON; реальные route values не коммитить в публичный Git.

Локальная проверка β 07.10.2026: повторная генерация всех PNG, manifest и JSON
дала идентичные SHA256; все P/A/R/S/C прошли фактический
`PrepareReplyMediaService` с memory-only writer без SQL. R/S/C дополнительно
прошли `_normalize_rich`, `compile_rich_media` и проверку native attachment map;
T прошёл прежний `compile_rich`. Ruff generator PASS. Это synthetic validation,
не проверка persistence/send и не provider delivery. Примеры PNG 1 и 4 визуально
прочитаны локально: номера, точные labels и безопасные поля соблюдены.

## Source map и доказательства

| Контракт | Реализация |
| --- | --- |
| Work structured upload / image content preview | `app/api/mcp_media.py`, `app/api/mcp_routes.py` |
| All-items validation / normalization / application limits | `app/application/reply_media.py` |
| Scoped atomic staging / aliases / immutable snapshot / expiry cleanup | `app/infrastructure/reply_media.py`, `app/worker/reply_documents.py`, `app/main.py` |
| v2 media compiler / native attachment validation | `app/application/telegram_rich_media.py` |
| Durable reservations / shared execution ledger / receipts / admission | `app/infrastructure/reply_sends.py`, `app/models/reply_media.py` |
| Existing v1 text contract | `app/application/telegram_rich.py` |
| Multipart provider | `app/services/telegram.py` |
| Synthetic reproducibility | generator, fixtures manifest, operator JSON |

Эта документация фиксирует контракт и локальную synthetic проверку; она не
объявляет hosted CI, PostgreSQL concurrency/restart, независимый δ ACCEPT,
merge/deploy, Work tools visibility, provider delivery или Telegram renderer
PASS. Их подтверждают отдельные exact-head receipts и доступное клиентское
наблюдение. Desktop, телефон и прежняя Copy clipboard остаются UNVERIFIED,
пока не появится отдельная фактическая проверка; старый clipboard не блокирует
media sprint. Итог #742 обязан сослаться на доказательства B01–B26 текущего ТЗ.

## Rollback

До публикации сохранить current-main/runtime receipt и текущую совместимую
схему. При дефекте остановить media canaries и использовать поддерживаемый
kill switch либо reviewed media-only revert/fixforward. Production маршрут
`/ops-deploy production target=gate` принимает **текущий main** и возвращает
новый deploy receipt; произвольный исторический SHA этот lane не принимает.

Recovery сохраняет Alembic head 0115, модели и существующие private payloads: старый d5 codehead 0114 после migration не проходит exact /ready. Использовать reviewed fix-forward текущего main, отключающий новую media behavior при сохранённой совместимой схеме.

Revert сохраняет интегрированные voice и прежний text Rich. Не откатывать всё
дерево к устаревшему #737 SHA. Media schema хранит неизменяемые evidence;
destructive downgrade/delete DB и удаление Telegram сообщений не нужны.
Unknown/in-flight операции удерживают fences и требуют reconciliation. После
rollback проверить прежний текст и запрет повторной media отправки; schema
совместимость и факт rollback доказываются отдельными checks/receipts, а не
наличием инструкции. Реальные новые canaries после stop требуют действующих
срока, scope и remaining budget этого execution.


## Каноническое ТЗ на начало исполнения

Источник: GitHub issue #742, зафиксировано 2026-10-07; ниже сохранён полный текст.

## 1. Поручение, адресат и статус

Автор: Марк Малиновский. Адресат: **α Gate отдельного Work**; α руководит требованиями, решениями и приёмкой, β реализует, независимая δ проверяет. Родитель: https://github.com/AlphaCenter/gate/issues/735. Это единственное реализационное поручение второго спринта; не создавать параллельный outcome/PR/lease.

**ПОДГОТОВЛЕНО К ПЕРЕДАЧЕ. Реализация здесь не запускалась.** Марк запускает эту задачу в отдельном Work. Получатель фиксирует получение и отдельно принятие; создание issue не является доставкой/принятием.

Цель — отправка **готовых изображений** через подключённый ChatGPT Work → Gate M → Telegram Business: одиночное фото, обычный альбом, фото внутри Rich, нативное slideshow и нативный collage. Work выполняет весь цикл до опубликованной функции, реального вызова из подключённого Work, демонстрации и доказательного отчёта.

## 2. Подтверждённая основа и актуальный RU scope

Перед подготовкой ТЗ 07.10.2026 проверено:
- #737 закрыта completed: https://github.com/AlphaCenter/gate/issues/737.
- Изменения приняты в RU scope: https://github.com/AlphaCenter/gate/issues/737#issuecomment-6034137377.
- Независимый terminal ACCEPT Delta269: https://github.com/AlphaCenter/delta/issues/269#issuecomment-6034114152.
- PR740 merged: https://github.com/AlphaCenter/gate/pull/740; Rich merge 3ce7f8d99652cf030316b922880812d4ed29fb8d.
- Current integrated main/runtime: d5a9fbb8a0b5444c0734cba040e0a67bd1a7424e. Свежий scoped Gate status в этом Work сообщил production/d5a9fbb8a0b5/send_enabled=true. Это подтверждение текущего fingerprint, а не повторная live-демонстрация.
- Реальная native RU→собственный BY доставка шести текстовых блоков, native table и exact code подтверждены предыдущим execution и независимой приёмкой; durable replay без второго provider call.
- BY как Rich-отправитель **отложен решением Марка**: он пока не оплачивает BY Premium. Не покупать, не включать BY native canary и не делать восстановление entitlement условием спринта 2.
- Кнопка Copy клиента, Desktop и телефон не объявлены проверенными. Старый непроверенный clipboard не переоткрывать и не переносить как блокер медиа-спринта.

**Обязательная live-приёмка спринта 2: собственный RU Business Марка → его собственный BY как получатель.** Архитектура не должна хардкодить RU: BY проходит синтетическую изоляцию/validation, но native BY outbound сейчас не является обязательным live критерием. Scopes/counters/grants первого execution не переиспользуются и не обнуляются новым спринтом.

Перед реализацией повторно прочитать текущие состояния/receipts: перечисленные ревизии — доказательства на дату ТЗ, не приказ разрабатывать поверх устаревшего main. Интегрированные изменения других задач не откатывать при rollback media.

## 3. Пользовательский результат и границы

Пользователь передаёт Work готовые файлы. Work:
1. Подготавливает эти байты в защищённом storage Gate.
2. Собирает явный пакет: выбранный вид отправки, отправитель, адресат, фотографии, подписи и порядок.
3. Показывает **сами изображения** и весь текст, сообщает реальный Telegram-формат и число сообщений.
4. Получает точное согласование пакета.
5. Отправляет через Gate от выбранного Business аккаунта.
6. Возвращает квитанцию по пакету/элементам и truthful outcome; повтор команды не создаёт дубль.

| Режим | Что реально видит получатель | Требование |
| --- | --- | --- |
| photo | Одно обычное фото с подписью | Один файл; sendPhoto |
| album | Нативный media group с отдельными изображениями | 2–10 фото; sendMediaGroup; отдельные captions |
| rich_photo | Изображение внутри Rich-ответа | Один native photo block и минимальный текст |
| rich_slideshow | Листаемые фото внутри одного Rich-сообщения | Native slideshow/photo blocks, порядок и подписи |
| rich_collage | Несколько изображений внутри native Rich collage | Native collage/photo blocks, согласованная структура |

Обычный альбом не называть slideshow. Collage здесь — Telegram-блок с несколькими фото, **не рисование единого JPEG**. Если конкретный клиент меняет размещение плиток, обещать документированный порядок элементов, а не точную пиксельную сетку. Стрелки/жесты/листание проверить фактически в доступном клиенте.

Входят PNG/JPEG, подписи plain/markdown у обычных фото/альбомов и разрешённый RichText у rich-блоков, проверяемое staging/preview/approval/delivery, минимальная долговечная модель media package.

Не входят: Mermaid/рендерер/TAKE (спринт 3), новый AI-генератор изображений, ретушь/кадрирование/рисование коллажей, GIF/анимации/видео/audio, LaTeX/details (спринт 4), Mini App, прогресс/Stop (#730), автоматические серии, публикация в группах/каналах, сторонние адресаты в live тестах, массовая отправка. Подготовка технических synthetic fixtures обычной графической библиотекой допускается; LLM/imagegen/transcription calls=0.

Минимальный Rich-текст вокруг media (заголовок/абзац/существующая таблица или код) нужен для проверки интеграции. Полный редактор произвольных многочастных mixed reports не расширять из спринта 4 в этот.

## 4. Самостоятельное выполнение и вопросы

После прямого запуска Марком Work отвечает за исследование, код, тесты, независимую β/δ, исправления, CI, merge, штатную публикацию Gate, доступность MCP-параметров у реального Work, owner-only canaries, безопасный rollback и итог. Не останавливаться на ТЗ, draft PR или deploy.

Сначала самостоятельно получить всё доступное: source/runtime, способы staging, accepted protected Ops access, scoped own RU→BY route, состояние окна Business reply, доступный клиент. **Один начальный пакет вопросов — только для того, чего Work действительно не может получить сам.** Указать отсутствующий факт/доступ, причину и одно минимальное действие владельца. Не спрашивать выбор библиотек, схемы таблиц, routine merge/deploy или подтверждение уже разрешённых fixtures.

Прямой запуск этой issue владельцем разрешает описанный bounded цикл и тестовые действия раздела 13. Точная команда запуска приведена в конце. Авторизация привязывается к текущему ТЗ, execution, reviewed revision, target, сроку действующих grants и лимитам, а не к общей фразе «всё разрешено». Mere ссылка от другого автора не запускает задачу.

Для обычных будущих отправок пользовательское подтверждение exact account/recipient/content/media/mode сохраняется. Подготовка файла не означает разрешение отправить его человеку.

Не менять credentials/OAuth, платежи и BY entitlement. Использовать штатный login handoff, если он объективно нужен. Technical blockers, которые Work может устранить в собственном Gate scope, устранять самостоятельно. Непреодолимый blocker назвать точно, сохранить сделанное и продолжить независимое. Не запрашивать новые разрешения только из-за наличия старой общепроектной формулировки, если точный текущий запуск уже разрешает действие.

## 5. Bootstrap, владение и переиспользование

До repository mutation:
- актуальные Gate/Center AGENTS.md, WORK-PARENT.json и docs/work-bootstrap.md;
- текущие issues/PR/leases/executions/receipts/next pointers и duplicate check;
- one execution/lease/branch/PR; parent735 остаётся координирующим, эта issue — implementation;
- read-only подтверждение текущего source/runtime/состояния схемы.

Переиспользовать:
- app/application/telegram_rich.py и versioned draft snapshot/consent из #737;
- app/application/reply_drafts.py, существующие SqlReplyDraftWriter / SqlTelegramReplySender;
- app/services/telegram.py и все общие admission/budget/canary/process fences, в том числе multipart;
- app/api/mcp_routes.py / mcp_metadata_routes.py;
- app/application/reply_documents.py, app/infrastructure/reply_documents.py и cleanup worker как изученную существующую основу staging;
- docs/specs/ISSUE-737-RICH-SPRINT-1.md и текущие operator examples.

Фактическая документная основа на дату ТЗ: PDF/ZIP, структурированное content_base64, max8MiB, SHA256 и expiry24h. Это **не доказательство готовой поддержки изображений**. Расширять media staging минимально, не ломать документный контракт и не закрывать #700 только из-за reuse части кода. #700 касается файлов/PDF/ZIP; текущая задача не является его дубликатом.

Gate владеет транспортом, защищённой подготовкой к доставке, approval и receipts. Марк/Work предоставляет готовые media. Новый редактор/генератор и другой проект не запускаются.

## 6. Загрузка готовых файлов из реального Work

Выбрать и реализовать **реально доступный end-to-end путь** от локального/прикреплённого Work файла к Gate. URL/path sandbox Work не является автоматически доступным server path.

Предпочтительно один bounded prepare инструмент, например gate_prepare_reply_media, с task reference и items[]; bytes передаются существующим защищённым upload/reference протоколом либо структурированным content_base64, по аналогии с принятой документной основой. Конечное имя/аргументы выбрать α, отразить в final tools/list и рабочем JSON-примере.

Требования:
- приём только текущего явно разрешённого artifact или байтов; identity workspace/principal/account/task scope проверяется;
- ни server arbitrary filesystem path, ни произвольное скачивание по URL не принимаются;
- artifact_ref разрешён лишь при реально реализованной scoped resolver capability; не обещать универсальный доступ к Library/Dropbox/sandbox;
- если base64 используется, это structured upload field, не текстовый draft/RichText; не печатать его в сообщениях/логах/issue;
- для staging не использовать отправку в Telegram служебный чат как «бесплатную загрузку»; это внешняя отправка, не storage;
- все N файлов валидируются до готовности пакета. Ошибка последнего файла не оставляет approved полупакет;
- вернуться с opaque media references и metadata/preview, sent=false;
- размер/таймаут/retry staging ограничены; потерянный ответ staging не должен дублировать durable asset/package;
- input reference не переносит authority отправки; вложение из чужого workspace/отозванного доступа отказано.

## 7. Проверка и сохранение байтов

Для каждого media:
- filename без traversal/control characters; PNG/JPEG MIME, extension/signature и настоящий decode согласованы;
- reject malformed/truncated/polyglot where detectable, decompression bombs и oversized dimensions/pixels/memory;
- SHA256 исходных bytes и фактически отправляемых bytes фиксируются отдельно, если отличаются;
- bytes подготовленного артефакта immutable: изменение источника после preview не изменяет одобренный пакет;
- оригинальные изображения не ретушировать/обрезать/масштабировать ради вкуса;
- EXIF orientation учитывать для preview/provider: если требуется детерминированный поворот/удаление приватных EXIF/подготовка неподдерживаемого канала цвета, выполнить до approval, показать результат и записать transform/version/output hash. Не обещать byte-perfect raw file на стороне Telegram photo, который может сжиматься;
- unsupported изображение отклонить до отправки с точной причиной, а не незаметно отправить как document;
- лимиты per-file, total package, pixels/dimensions/aspect, N и memory/time согласованы между schema/runtime/документацией. Назначить конкретные application limits, не выдумывать provider limits;
- изучить текущие sendPhoto/sendMediaGroup/rich media provider constraints; применять меньшую границу;
- storage приватный и durable, не только tmp одного process; минимальная additive migration при реальной необходимости;
- expiry/cleanup сохраняет metadata/hash/receipts. Пока байты нужны in-flight/unknown reconciliation, не удалять их по гонке; просроченное до send отказать, не перезагружать скрыто;
- completed assets очищать по опубликованной retention policy без потери metadata. Не вводить обязательный новый scheduled heartbeat — использовать существующий cleanup механизм.

Telegram file_id cache, если нужен, привязать к credential/bot/storage scope и фактическим output bytes; нельзя заменять source identity одним непрозрачным file_id, переносить доступ между tenant или игнорировать user approval. Любая загрузка с пользовательски видимым эффектом учитывается в live бюджете.

## 8. Версионируемый контракт и captions

Существующий gate.rich.v1 спринта1 принимает строго шесть текстовых типов. **Не менять семантику сохранённых v1 drafts/hashes/receipts.** Media-rich расширить явной новой схемой gate.rich.v2 либо эквивалентным versioned совместимым контрактом с доказанным v1 reader; финальный выбор записать до реализации.

Новый content содержит только разрешённые photo / slideshow / collage и существующие текстовые блоки. Source блоки используют prepared media refs с version/hash. Provider builder формирует native InputRichBlockPhoto/Slideshow/Collage и multipart attachments из уже подготовленных bytes. Только один documented InputRichMessage representation; целевой blocks.

Caption ordinary photo/album:
- source plain/markdown явно выбран; parse/normalization/entities используют #727;
- UTF-16/emoji/newlines/links сохранены; проверяется длина **после** преобразования;
- caption каждого album item соответствует именно этому item;
- общая подпись и captions элементов не смешиваются/не теряются.

Caption Rich:
- использовать документированную RichBlockCaption структуру;
- у вложенного InputMediaPhoto caption игнорируется Telegram: показываемая подпись должна попасть в native block caption, а не только в media input;
- общую подпись slideshow/collage и подписи отдельных фото показать отдельно;
- неизвестные поля/недопустимые типы/вложенные slideshow/collage/чрезмерная глубина отказаны.

Минимальная cardinality:
- photo/rich_photo — один image;
- ordinary album — 2–10 images;
- slideshow/collage — 2–10 images как **application MVP limit**, если текущие provider constraints не требуют меньшего;
- >10 не разбивать автоматически; показать pre-send error.
Не обещать supports all Telegram media: в этом спринте только PNG/JPEG photos.

## 9. Предпросмотр и точное согласование

Preview — полный immutable пакет:
- resolved sender RU, recipient label и scoped references;
- mode, actual provider method, количество provider operations и ожидаемых Telegram messages;
- все реальные подготовленные изображения, позиции 1..N, имена, MIME, dimensions, byte sizes и hashes;
- полный общий текст и captions всех items с форматированием;
- source/prepared differences и transforms до approval;
- поддержка/неизвестность выбранного mode/account/client;
- ограничения display: не пиксельная гарантия grid и не скрытая обрезка.

Сам JSON metadata не заменяет просмотр изображений. Подключённый Work должен получить разрешённый renderable preview: через существующее image/artifact content, безопасную краткоживущую view route или другой действительно работающий механизм. Приватные signed URLs не публиковать; server не рассчитывает, что Марк вручную ищет файлы.

Canonical package digest включает content/version/converter, mode, порядок items, все captions, hashes фактически отправляемых bytes, transforms, route/account/recipient/connection/workspace binding. Approval immutable; смена любого пункта требует нового exact confirmation. Upload/staging != approval. Send использует saved snapshot, не пересобирает текст/media после согласования.

Для ordinary users нельзя молча заменить rich slideshow альбомом/коллажем, фото документом или RU ботом. Fallback — новый preview/mode/hash/confirmation. После unknown первой отправки альтернативу не отправлять.

## 10. Transport, долговечность и partial/unknown

Использовать общий TelegramClient, JSON/multipart admission, send switches, scoped authorization, canary budget и protected locks. Новый multipart путь не должен стать обходом safeguards.

Перед HTTP:
- exact draft/package approval и asset hashes/expiry/availability проверены;
- durable operation reservation/attempt с уникальным operation/package ключом закреплена;
- concurrent requests/restart/replay видят ту же операцию и не запускают новую;
- все bytes и request attachment mapping проверены до send;
- sends from RU текущим Business connection; recipient/rights/window перепроверены в существующем протоколе.

Операции:
- photo → один sendPhoto result;
- album → один sendMediaGroup POST, ожидаются N Message results с правильным order/chat/business/group linkage;
- native rich modes → один sendRichMessage multipart request с явным attachments mapping; receiver message содержит native rich blocks.

В MVP не добавлять цепочку отдельных сообщений вместо album/slideshow. **Один album HTTP request возвращает несколько message IDs** — квитанция обязана описывать все items. Не утверждать атомарность внешнего API по отсутствию локальной ошибки.

Receipt/result:
- overall sent только если expected N и все обязательные связи подтверждены;
- partial/unknown указывает известные delivered items/IDs отдельно, unresolved items отдельно;
- malformed/short/duplicated/mismatched response, timeout, crash after POST, SDK/body parse failure и DB finalize failure не означают «точно не отправлено»;
- unknown/partial запрещают повтор всего пакета. Durable fence сохраняется даже если receipt finalization упало;
- read-only reconciliation по точным собственным private references может уточнить outcome, но не создаёт новый POST;
- safe-resume уже известных unsent parts не реализовывать в MVP автоматически: explicit новый bounded approved operation только после доказанного статуса/согласованного плана. Не извлекать «остаток» из неизвестного album outcome;
- только доказанный до-HTTP отказ или подтверждённый provider rejection классифицировать failed; 400 не считать автоматически feature unsupported;
- 429/5xx следуют принятой no-blind-retry/unknown политике и фактическому outcome, не new generic retry loop;
- pending/unknown draft не превращать в новый operation через изменение ID;
- retained outward records по каждому album item не создают phantom tasks/дубли в inbox/projection. Sender/receiver direction semantics сохранены.

Минимальная квитанция: operation/package/draft refs, mode/version/digest, account+recipient labels, status, attempts/replay, expected/confirmed/unknown item counts, items[position,media hash,size,caption digest,outcome], private provider message/group evidence, original reservation runtime/time и безопасный error code. Raw IDs/bytes/captions не идут в общие логи/Git; owner-scoped result может содержать поля штатного защищённого контракта.

## 11. Provider/client ограничения без ложных блокеров

Раздельно фиксировать: implementation support, user Rich entitlement, Business send rights/window, delivery evidence, client rendering.

Обязательный sender — RU. BY native sender/pокупка Premium не включаются. Старый code Copy не является критерием медиа-функции. Доступный клиент получателя достаточен для live acceptance; остальные honest UNVERIFIED, без заочного PASS.

Методы photo/album и rich modes проверяются отдельно. Документация Telegram подтверждает shapes, но не фактическую возможность всех режимов на аккаунте/клиенте. Работоспособный RU text Rich не доказывает media Rich без canary.

Если конкретный mode действительно недоступен:
1. Проверить и исправить собственный adapter/attachment/caption defects.
2. Сохранить доказанную внешнюю причину, affected mode и supported варианты; unknown не кешировать как unsupported.
3. Остальные независимые modes завершить и публиковать в разрешённом scope.
4. Unsupported mode нельзя обозначить implemented-and-verified или скрыто заменить.
5. Полное закрытие заявленных native функций требует фактических обязательных критериев либо отдельного owner решения сократить scope. Work не создаёт новый обязательный BY/телефон/clipboard gate.

Матрица статусов/причин должна позволить Марку понять, что работает, без новых технических выборов по умолчанию.

## 12. Поставка под ключ

1. Bootstrap + claim, fresh main/runtime и existing safeguards.
2. Source map/contract/конкретные limits/retention и working Work upload route.
3. Staging + immutable package + visible preview + consent; затем common transport.
4. Local deterministic/schema/bytes checks + disposable PostgreSQL persistence/concurrency/restart/cleanup.
5. Independent β review и независимая δ exact candidate. δ выдаёт ACCEPT/REJECT, не чинит свой объект; β исправляет в том же PR, review successors по действующему protocol.
6. Необходимый exact-head CI и required checks; PG skipped != passed; pending/green не перезапускать без причины.
7. Merge проверенного head в актуальный main; конфликт с другой задачей решить изолированно, не merge чужих незавершённых PR.
8. Записать pre-release LKG и rollback boundaries. Additive migration если есть — принятый protected migration process, не считать обычный deploy receipt её доказательством.
9. Штатный /ops-deploy production target=gate через AQUB control issue72. Не создавать workflow и не ручной provider dashboard.
10. Fresh exact deployed SHA, redacted health/schema/storage readiness; прежний RU Rich v1 и plain/markdown/document behavior живы.
11. Server tools/list **и фактический connected Work catalogue** обновлены; реальные prepare/preview/send call patterns доступны. Server schema сама по себе не доказательство connected Work.
12. Выполнить ограниченные fixtures; replay/read receipts без нового send.
13. Actual receiver image evidence + captions/order/slideshow checks + final independent verdict.
14. Итог с критериями, commit/CI/Ops/runtime/media receipts; закрыть execution/lease. Parent735 остаётся open для спринтов3/4.

Rollback: при wrong sender/recipient, duplicate, approval bypass, corrupted media, lost receipt fence, deployed mismatch/health regression остановить новые media writes. Использовать существующий разрешённый kill switch либо reviewed revert/fix-forward preserving integrated accepted work + тот же Ops current-main route. Не обещать route deploy произвольного старого SHA; не делать DB downgrade/history deletion или откат voice runtime ради media. Ранее отправленные сообщения не удалять автоматически.

## 13. Точные synthetic fixtures и live budget

До live один раз подготовить **четыре статичных PNG**, детерминированно обычной графической библиотекой: 800×600, sRGB, без EXIF/личных данных; контрастный фон, крупный номер 1/2/3/4, русская надпись «Gate — тестовое изображение N», простая фигура. Цвета различимы; текст/номер не у края. Synthetic source code/files и hashes допустимы в Git. Это технические тесты, не заказ нового дизайна/imagegen.

После генерации α вычисляет exact source/prepared hashes и сохраняет immutable fixture manifest до approval/send. Разрешены эти images и следующие тексты при прямом запуске ТЗ Марком:

| Fixture | Mode / content | Provider writes | Ожидаемые messages |
| --- | --- | ---: | ---: |
| P | photo, image1, caption «Gate Media Sprint 2 — одиночное фото 1.» | 1 | 1 |
| A | album, images1..4, captions «Gate Media Sprint 2 — кадр N из 4.» | 1 | 4 |
| R | rich_photo, heading «Gate Media — фото в Rich», paragraph «Синтетическая проверка изображения внутри Rich.», image1 caption «Кадр 1.» | 1 | 1 |
| S | rich_slideshow, heading «Gate Media — слайд-шоу», images1..4 captions «Кадр N из 4.», overall caption «Синтетическая проверка листания и порядка.» | 1 | 1 |
| C | rich_collage, heading «Gate Media — коллаж», images1..4 captions «Кадр N из 4.», overall caption «Синтетическая проверка совместного отображения.» | 1 | 1 |
| T | text Rich v1 regression: heading «Gate Media — контроль текста» и paragraph «Синтетическая проверка прежнего текстового Rich.» | 1 | 1 |

Нативные подписи стилизовать только по указанному final fixture manifest; число/порядок/содержание не менять после approval. В обычном album проверить caption formatting синтетически; для live достаточно точных captions из таблицы.

**План: 6 provider write attempts, до 9 новых сообщений; жёсткий предел execution — 12 write attempts и 20 пользовательски видимых сообщений.** Album считается одним HTTP attempt и до4 сообщений; другие methods не «бесплатные». Все transport adapters/workers используют общий durable ledger. Резерв только для доказанного безопасного исправленного canary/явного отдельного fallback известных fixtures, не для повторного просмотра или unknown retry. Known failed и unknown attempts расходуют attempt cap; возможные unknown messages консервативно резервируют expected cardinality. Replay без POST не расходует writes/messages.

Контур **только own RU → own BY**. Дополнительные BY outbound seeds/messages не предусмотрены. Сначала reuse действующий owner-only reply context/follow-up механизм; если active-window отсутствует и Work не может открыть его безопасно штатно, сообщить точную prerequisite одним начальным пакетом вместо слепых provider probes. Не просить новый seed, когда подходящее собственное событие уже есть.

Новый execution/grant/lease/expiry по актуальному Gate policy; разрешение действует для этого bounded sprint run, не становится standing unlimited send. Истёкшие grants обновлять только доступным штатным механизмом в рамках прямой owner-команды; не выдумывать approval или source identity.

Выбрать один реально доступный own recipient client. Открыть уже отправленные сообщения без повторных POST. Проверить:
- номер/содержание изображения, отсутствие unexpected crop/distortion;
- все4 images/captions/order album;
- listable native slideshow: смена1→2→3→4 и правильные подписи;
- collage показывает все4 изображения, никто не потерян/подменён;
- rich_photo действительно находится внутри native Rich, не отдельным служебным фото.
Telegram photo compression не приравнивать к hash corruption: сравнивать исходный переданный файл по private request evidence, полученный display — по визуальному содержимому/порядку.

Cropped/redacted evidence только synthetic message, без чужой истории/sidebar/номеров. Browser — для visual checks, не альтернативный sender. Если login/2FA необходим, штатный protected handoff. Telegram Web/Desktop/телефон записать честно по доступности; отсутствие отдельного телефона не блокирует проверенный на другом клиенте результат.

LLM/imagegen/transcription/paid broadcast calls=0; покупки/credentials changes=0. Никаких canary третьим лицам. Обычные пользовательские отправки после выпуска остаются exact-approved.

## 14. Приёмка

| ID | Обязательное доказательство |
| --- | --- |
| B01 | Fresh main/runtime/scope/claim без duplicate execution |
| B02 | Реальная загрузка готового Work файла в Gate и preview работают end-to-end |
| B03 | PNG/JPEG decode/MIME/hash/dimensions/filename/size checks, corrupt/bomb cases отказаны |
| B04 | Cross-workspace/ref traversal/URL fetch/source mutation не дают доступа/отправки |
| B05 | Durable assets, expiry/in-flight cleanup/retention и restart целостность |
| B06 | Full photo/package preview содержит реальные изображения, captions и порядок |
| B07 | Любое изменение media/hash/текста/режима/account/target/version инвалидирует approval |
| B08 | photo/album exact method, files/attachments/captions сериализованы корректно |
| B09 | rich_photo/slideshow/collage нативные blocks; captions не потеряны в ignored media fields |
| B10 | v1 drafts/hashes/reader preserved; old plain/markdown/series/doc behavior без регрессии |
| B11 | Cap cardinality/byte/UTF-16/depth boundaries и invalid media-rich отказаны |
| B12 | Все send/admission/budget/canary/process fences сохранены на JSON/multipart |
| B13 | Scope/rights/expiry/unavailable bytes pre-send отказ, HTTP=0 |
| B14 | Replay/concurrent sessions/restart дают один POST и прежний operation evidence |
| B15 | Album N results сопоставлены с N items/message IDs; sender/receiver projection без дублей |
| B16 | Timeout/short response/finalization failure → truthful partial/unknown, без resend/fallback |
| B17 | Logs/Git/errors без private bytes/IDs/URL secrets; receipts содержат полную безопасную связь |
| B18 | Required exact-head CI/PG/migration-if-needed и independent technical verdict |
| B19 | Merge/Ops/deployed SHA/health/schema/storage readiness подтверждены |
| B20 | Actual connected Work prepare/preview/approved send доступны и реально вызваны |
| B21 | P/A/R fixtures доставлены из RU своим BY; реальные image/caption evidence |
| B22 | S native slideshow доставлен; фактическое листание/порядок/captions проверены |
| B23 | C native collage доставлен; все4 photos и captions/порядок проверены в пределах клиента |
| B24 | T regression и replay успешно проверены; shared attempts/messages caps соблюдены |
| B25 | Honest capability/client matrix; BY outbound deferred, old Copy не придуман PASS |
| B26 | Working operator JSON/manual, concrete limits/retention/fallback/rollback и финальный δ verdict |

Синтетические checks не заменяют B21–B23; HTTP200 не доказывает визуальное листание. Реальный пользовательский результат плюс независимый verdict закрывают implementation; личное принятие Марком фиксируется отдельно без выдумки. Provider/client ограничения фиксировать отдельно и не выдавать полный PASS, если native критерий не выполнен.

## 15. Итог, сохранение и связь с дальнейшими спринтами

В implementation branch сохранить спецификацию docs/specs/ISSUE-742-MEDIA-SPRINT-2.md, final schema/operator JSON, synthetic fixtures/manifest, deterministic tests и redacted receipts в актуальных Gate conventions. Private media/real receipts держать в защищённом storage, в Git только safe evidence/pointers.

Итог Марку:
- что можно отправить из Work, одним понятным рабочим примером;
- PR/head/merge/Production и ссылки CI/Ops/δ;
- B01–B26 PASS/FAIL/UNVERIFIED/N/A с evidence;
- actual RU→BY provider mode/receipts/preview/visual results;
- counts/caps, failed/unknown и limitations без ложного успеха;
- новый каталог Work и recovery baseline;
- следующий спринт3 Mermaid — только предложение, не автоматический запуск.

Issue и parent735 обновлять по существенному результату, без pointer-only heartbeat. Закрыть эту issue только по согласованным обязательным критериям и independent verdict; parent735 остаётся open.

## 16. Команда запуска

«Ты α Gate. Прими эту задачу и выполни второй спринт под ключ по её текущему ТЗ. Разрешаю описанные реализацию, β/независимую δ, исправления, merge, Production-публикацию Gate через Ops, rollback и ограниченные synthetic отправки только из моего RU в мой BY по разделу13. BY Premium не покупать и BY native outbound не включать в обязательную приёмку. Сначала сам получи доступное; отсутствующее спроси одним начальным пакетом. Затем выполняй разрешённые шаги самостоятельно без повторных согласований. Доведи до реального prepare/preview/send из подключённого Work, демонстрации фото/альбома/slideshow/collage и доказательного отчёта; не останавливайся на PR или deploy.»

## Первичные источники и материалы

- Общий план: https://github.com/AlphaCenter/gate/issues/735
- Основа и последний scope: https://github.com/AlphaCenter/gate/issues/737
- Независимая приёмка основы: https://github.com/AlphaCenter/delta/issues/269#issuecomment-6034114152
- Rich v1 контракт: https://github.com/AlphaCenter/gate/blob/main/docs/specs/ISSUE-737-RICH-SPRINT-1.md
- Файловая отдельная задача: https://github.com/AlphaCenter/gate/issues/700
- https://core.telegram.org/bots/api#sendphoto
- https://core.telegram.org/bots/api#sendmediagroup
- https://core.telegram.org/bots/api#sendrichmessage
- https://core.telegram.org/bots/api#inputrichblockphoto
- https://core.telegram.org/bots/api#inputrichblockslideshow
- https://core.telegram.org/bots/api#inputrichblockcollage
- https://core.telegram.org/bots/api#richblockcaption
- https://core.telegram.org/bots/api#sending-files

Официальные страницы просмотрены при подготовке 07.10.2026. Исполнитель перепроверяет актуальную документацию перед кодом/provider calls.

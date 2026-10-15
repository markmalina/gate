# Независимый read-only аудит Galaxy Watch runtime — 15.10.2026

15.10.26 13:30 МСК · δ → α Gate · Gate #736 / #731.

## Формальный статус: BLOCKED

Новая обязательная Delta issue не создана. После duplicate search по Gate736 (активного matching attempt не найдено) GitHub create_issue в AlphaCenter/delta вернул 403 `Repository was archived so is read-only`. Независимый GET repository подтвердил archived=true; поиск markmalina/delta не нашёл перенесённого репозитория. Репозиторий не разархивировался; маршрут не подменялся. Этот отчёт — независимые наблюдения, НЕ canonical Delta ACCEPT и НЕ приёмка автономного продукта.

Подготовленная identity: policy_revision=delta.review-attempt.new-issue.v1; review_series_id=gate-watch-runtime-20261006; attempt_number=3; predecessor_pointer=https://github.com/AlphaCenter/delta/issues/272; candidate_sha=86dbc0f429dce089bf733fc422f20f556535895b. Номер новой issue отсутствует. Terminal Delta272 остаётся неизменным.

Прочитаны Delta AGENTS/README, Center lifecycle и registry, Gate AGENTS, текущая передача и принятие: https://github.com/markmalina/gate/issues/736#issuecomment-6189801263 и https://github.com/markmalina/gate/issues/736#issuecomment-6189869154 . δ выполнял только GET/локальную проверку; STT/drain/owner commands/deploy/binding/credentials/Telegram не запускал. Единственная попытка внешней записи — отклонённый create_issue.

## Факты, подтверждённые δ

Gate current main свежим GET: 86dbc0f429dce089bf733fc422f20f556535895b. Production Gate M: afe0c6b8519381a0007c0dcc1c145207b5d54a3f, Alembic0115_watch_voice_post_payment. GET /ready: status=ready, database=ok, migrations=ok. Это действующая старая production-версия, а не выпуск исправления автономной очереди.

Обе сегодняшние owner записи: protected receipt прочитан из tool CONTENT JSON, original получен отдельно через read-only watch-voice-file. Base64 декодирован и SHA256 независимо пересчитан локальной JS реализацией с известным контрольным вектором abc. Размер/хэш каждой записи точно совпали с receipt; исходные значения и payload в этот отчёт не включены. Полные transcripts (897 и 1031 символ) точно совпали с полной единственной частью соответствующих bot-authored private Mark issues; source/revision/issue links совпали. recorded_at=null: время начала записи не подтверждено.

| Результат | Dropbox server time, МСК | Gate receipt, МСК | Transcript / GitHub, МСК | Dropbox → GitHub |
|---|---|---|---|---|
| [Private Mark58](https://github.com/markmalina/mark/issues/58) | 11:07:41 | 12:21:58.000331 | 12:22:09.141808 / 12:22:09 | 74 мин 28 с |
| [Private Mark59](https://github.com/markmalina/mark/issues/59) | 11:12:19 | 12:21:58.908298 | 13:24:53.532209 / 13:24:53 | 132 мин 34 с |

Полный all-state REST listing private Mark: 59 items, меньше per_page=100; ровно одна issue на каждый из двух exact event markers, номера58 и59. last_error=null, transcription=transcribed, delivery=delivered, fixed normal model=gpt-4o-mini-transcribe для обеих. Это не самостоятельная проверка качества речи по прослушиванию; неясные распознанные фразы должен обозначить α при показе владельцу. Содержание входящих не принимается этим аудитом к исполнению.

[Первый run38712370892](https://github.com/markmalina/mark/actions/runs/38712370892): event=schedule, owner23397294, attempt1, private main7b8aa3dd2b59a953c683e348bc71d0ce66eb5090, created_at=run_started_at=09:21:48Z; updated09:22:11Z. Job116346642514 SUCCESS, authenticate SUCCESS, exact-command step SKIPPED штатно для schedule. Actual safe receipt09:22:09.933Z: considered1/published_parts1/delivered1/reconciled0.

[Второй run38720065129](https://github.com/markmalina/mark/actions/runs/38720065129): event=issue_comment, тот же owner/attempt/main; created_at=run_started_at=10:24:40Z, updated10:24:56Z. Job116372141476 SUCCESS, strict command и authenticate SUCCESS. Actual safe receipt10:24:54.085Z: considered1/published_parts1/delivered1/reconciled0. Вторая доставка РУЧНАЯ по owner-command α; она не доказывает исправленную автономность.

Current private binding enabled=true, scope=future-only. Current caller .github/workflows/gate-watch-voice-drain.yml прочитан независимо:5901bytes, SHA2566a5b4db5e428ca13524eb2140e86298447bb520fa33e24a067aa8f8d7a026d34; совпадает с Gate Railway pin. Cron2-59/5; concurrency fixed/cancel-in-progress=false; один bounded effect per run. Gate endpoint enabled/pin/scope=true, ordinary_worker=false.

Последние шесть scheduled created_at/start, МСК:14.10 16:05:56,20:58:41;15.10 00:34:00,03:04:35,07:30:11,12:21:48. У каждого created_at=run_started_at. Интервалы составляют часы; первая реальная обработка занимает секунды. Наблюдаемая задержка локализована до создания scheduled run, а не многочасовым выполнением job. Точную внутреннюю причину scheduler GitHub эти evidence НЕ устанавливают; limits/quota/outage не объявляются причиной. Cron не является доказательством SLA.

Три новых replay выполнены α, δ проверил metadata и logs независимо:

| Run | Job | Receipt UTC | Результат |
|---|---|---|---|
| [38720536943](https://github.com/markmalina/mark/actions/runs/38720536943) |116373701919|10:28:49.629Z|SUCCESS/idle; все counters0|
| [38720611863](https://github.com/markmalina/mark/actions/runs/38720611863) |116373955322|10:29:30.037Z|SUCCESS/idle; все counters0|
| [38720667784](https://github.com/markmalina/mark/actions/runs/38720667784) |116374141251|10:29:57.993Z|SUCCESS/idle; все counters0|

Все eventissue_comment/owner23397294/attempt1/тот же private main. Strict command/authenticate SUCCESS. После replay3 full protected receipts обеих сегодняшних записей побайтно равны baseline до replay. Final Gate M: run_claim56, delivery_counts={delivered:7}, transcription_counts={transcribed:7}; received/pending watch states не возвращаются. Shared speech budget после replay: settled291734µUSD/reserved166800µUSD/settled_count78, равен исходному после второй доставки; unknown1 — существующий общий исторический speech debt, не zero-unknown claim для всей системы. Эти три повтора не создали новых STT расходов или публикаций в наблюдённом объёме; испытание reupload под новым Dropbox ID не выполнено.

## Блокеры и непроверенный объём

1. Автономный запуск/выборка нескольких pending записей не исправлены и не приняты. Новая физическая запись после будущего исправления, несколько подряд, автоматическое опустошение очереди, outage/restart и фактическая задержка нового механизма отсутствуют. Нынешняя пустая очередь получена с ручным run; это не достаточный product ACCEPT.
2. [Gate764](https://github.com/markmalina/gate/issues/764) и [Ops375](https://github.com/markmalina/ops/issues/375), свежие body: source repin merged, statuswaiting-owner, leases released10:13Z. Ops37513:13МСК фиксирует серверный Actions access=Not accessible, sharing option НЕ СОХРАНЕНА. Нужен точный owner шаг в существующей задаче; δ не читал admin setting напрямую и не менял sharing. Отсутствующий Public App private key — отдельный Public blocker, НЕ Gate blocker. Gate RAILWAY_TOKEN presence не доказывает пригодность credential. Private Mark server writer presence UNKNOWN, не доказанное отсутствие.
3. Политика требует отдельную Delta issue новой попытки, но архивный Delta403 блокирует формальную независимую приёмку. Нет нового issue URL; этот файл не заменяет его.
4. Изменения/CI/merge/новая production-версия для исправления autonomous queue отсутствуют. Исторические CI/Delta272 не переносятся на будущий runtime.
5. Durable future_cutover/cursor фактические значения и устойчивость read-only API не раскрывают. Нельзя просто включить ordinary_worker без отдельного future-only safety исправления: α обнаружил отсутствие futurecutover фильтра в старом process_watch_voice. Это сообщение α, а не independently reproduced finding δ; учитывается как stop condition при выборе решения.

## Допустимый следующий шаг

α Gate сохраняет эти evidence в существующих #736/#731 и делает минимальный bounded candidate для Gate-owned автономной очереди с durablefuture-only exclusion, caps и unchanged provider policy. Сначала разрешает release-route blocker через владельца Ops и определяет существующий verified writer без provisioning credentials. Перед исправлением/выпуском — действующие model-access/lease/exacthead/CI/Ops правила. Затем разрешается канонический маршрут новой Delta attempt, выпускается reviewed exact version и только после готовности Марк записывает физически новый owner canary и несколько записей подряд. Повторяется фактический пользовательский сценарий, outage/replay; terminal product ACCEPT только по этим evidence. Нельзя выдавать данный read-only audit или три ручных idle replay за завершение автоматизации.

# Watch → Mark: подготовленный выпуск ранних карточек

Программа: [#770](https://github.com/markmalina/gate/issues/770). Этот документ описывает кандидаты и проверки. Он не является квитанцией установки, разрешением на credentials или доказательством доставки.

## Компоненты и ограничения

- Gate PR845 готовит metadata-only маршрут и activation startup: persistent Watch/STT принудительно OFF, Mark Actions и early-card режим ON, до пяти карточек на authenticated run. Он рассчитан на exact workflow pin ниже и не должен быть установлен до OFF-first и подтверждения private Mark workflow.
- Gate PR846 — объединённый OFF-first и forward-recovery candidate: сохраняет новую схему и журналы, принудительно отключает persistent Watch, Mark Actions и early-card режим. Его startup действует только на новые процессы; старый процесс и его запросы он сам не завершает.
- Private [Mark PR134](https://github.com/markmalina/mark/pull/134) расширяет проверку фактических счётчиков receipt от 1 до 5. Exact workflow SHA256 `78dd74c5ea6e783e26f44bd50b1aafb53cca4e8586e033fa2f80fa16202a0512`. Нельзя соединить activation с другой версией workflow или ослабить проверку pin.
- Scheduler — отдельный, ещё не созданный Railway service с `railway.watch-scheduler.toml`: 15-минутный cron запускает `app.worker.watch_voice_scheduler`. По умолчанию scheduler выключен. Никакие токены или значения переменных в Git не входят. Приватная проверка/сборка действующего workflow и получение job OIDC/token остаются на стороне Mark Actions.

Максимум пять карточек за run. При серии больше пяти хвост остаётся в очереди для следующих запусков; условие 15–30 минут для каждого файла не гарантировано. Сохранённые pending и exact-body reconciliation выполняются до обращения к Dropbox. Если ранняя очередь не пуста, её обработка может отложить следующий scan. Состояния sending/unknown никогда не разрешают повторный POST по отсутствию результата поиска.

## Scheduler: точный маршрут и границы

Отдельному scheduler service потребуется `GATE_WATCH_SCHEDULER_DATABASE_URL`: доступ к текущей Gate database с ролью, ограниченной SELECT/INSERT/UPDATE только таблицы `watch_schedule_control` после migration 0117. Код не использует основной `DATABASE_URL` как запасной вариант. Права роли нужно фактически создать и проверить отрицательным тестом доступа к аудио и прочим таблицам. Также потребуется секретная переменная `GATE_WATCH_DISPATCH_TOKEN`: авторизация пользователя-владельца GitHub с доступом **только к private Mark** и минимумом repository Actions:write, без выдачи issue-write этому токену. Actions:write даёт и другие права на Actions в этом репозитории, не только вызов одного workflow; это остаточный риск. `GATE_WATCH_SCHEDULER_ENABLED` по умолчанию false. Создание/установка обоих credentials, DB grants и сервиса — отдельные защищённые шаги; этот пакет их не создаёт.

Один tick проверяет immutable owner ID, immutable private repo ID, owner, main и активный точный workflow; затем делает не более одного `workflow_dispatch` на main. Принятый GitHub HTTP 204 означает только принятие запроса, не завершение workflow или доставку карточки. Действующие Mark workflow и Gate OIDC дополнительно проверяют owner actor и workflow bytes. Локальный `GET /user` не доказывает фактический actor OIDC — это проверяется на одном согласованном run.

В PostgreSQL один control row допускает один dispatch на 15-минутный слот и не допускает параллельный tick. Отказ preflight до POST безопасно заканчивает слот; неясный POST сохраняет `unknown`, а прерванный процесс оставляет `inflight`. Оба состояния блокируют следующие dispatch до отдельной проверки. `python -m app.worker.watch_voice_scheduler --status` показывает только state, слот и безопасный outcome; токен и содержимое ответов не выводятся. Никакого слепого повторения, удаления control row или сброса unknown нет. Для сверки нужен точный интервал, GitHub workflow runs/actor/head и, где возможно, исходный provider receipt; неоднозначность не доказывает отсутствие запуска.

Railway может пропустить слот при незавершённом предыдущем cron. Частота проверяется фактически по запуску и latency, а не по строке cron. Проверить отдельную стоимость service и GitHub Actions; оценка из #770 не является фактическим счётом.

## Наблюдение очереди: подготовленный инструмент

`python -m app.worker.watch_voice_observe` читает только агрегаты: количество карточек по состояниям, возраст самой ранней обнаруженной карточки в каждом состоянии, число отклонённых метаданных и статус/слот scheduler. Ни название, ни тело, ни путь, ни ID исходника, ни текст ошибки не выводятся. Время `oldest_detected_seconds` — возраст с обнаружения в Gate, а не задержка Dropbox → GitHub. Сравнивать два снимка: рост pending/blocked/rejections, любое sending/unknown или unknown/inflight scheduler требуют расследования; это не готовое автоматическое оповещение. До миграции 0117 таблиц может не быть, команда выдаст обобщённую ошибку.

Инструмент требует отдельный `GATE_WATCH_MONITOR_DATABASE_URL` без fallback. Перед установкой необходима отдельная read-only роль с `SELECT` только на `watch_early_cards`, `watch_early_rejections`, `watch_schedule_control`; её ограничения и права PUBLIC/унаследованных ролей требуется фактически проверить. Scheduler role не пригодна для мониторинга очереди. В source инструмент не запускает автоматический мониторинг и не создаёт credentials. Для приёмки latency дополнительно сверять время Dropbox `server_modified` и время создания соответствующей issue в private Mark, считать для каждой записи серии; доступ к этим деталям сохранять приватным. В таблице карточек нет времени создания issue, поэтому текущий снимок не измеряет end-to-end latency.

## Дополнение к реальному переключению

Проверено чтением текущего Mark workflow: существуют триггеры schedule, owner workflow_dispatch, owner issue_comment и push изменённого workflow/binding. До переключения нужно перекрыть все четыре, а не только будущий Railway scheduler, затем проверить отсутствие активных/ожидающих job. Таймаут job 25 минут, таймаут клиентского POST 1300 секунд: истечение этих таймаутов само по себе не доказывает, что серверный обработчик или запрос GitHub завершился. Завершение старых Gate-инстансов и reconciliation подтверждаются отдельно; неизвестное состояние останавливает выпуск. Точный механизм выключения workflow, инвентаризации всех jobs и readback запросов для этой среды должен быть предварительно подтверждён оператором; API и права на это здесь не доказаны.

## Порядок подготовки и приёмки

1. Зафиксировать актуальные main и reviewed exact heads, target deployment/fleet, конфигурацию, активные и ожидающие jobs, состояние pending/sending/unknown и безопасную версию возврата. Не считать старый snapshot текущим.
2. Подготовить и независимо проверить OFF-first tree с фактическим parent и schema. Не допускать старый активный writer параллельно новому. После согласованной установки OFF проверить, что все старые экземпляры и запросы завершены; одно состояние нового процесса этого не доказывает. Обработать sending/unknown read-only сверкой; несовпадение или неизвестный исход — STOP, без повторного POST.
3. Только при подтверждённом OFF установить проверенный exact Mark workflow и сверить его bytes/SHA256. Не запускать старый Gate с новым workflow pin и не переключать flags вслепую.
4. Подготовить exact activation source поверх фактического main: обычный STT остаётся OFF, ранние карточки и Mark OIDC включены, cap=5, workflow pin соответствует прочитанным bytes. Сверить transition, migration и forward OFF recovery на фактическом parent. Изменения в этом порядке не доказывают готовность real scheduler.
5. Отдельно подготовить scheduler service и разрешённую identity; не печатать/копировать ключи в Git или чат. До enabling проверить условия доступа, пределы, расходы, одно соединение и stop conditions. Создание credential или изменения identity не следуют из самого source.
6. Для одной новой owner записи проверить metadata → одна private issue без STT, затем серию из нескольких записей, повторы, Dropbox outage и порядок. Измерить Dropbox server_modified → issue и фактические интервалы scheduler, указать размер выборки. Не объявлять 15–30min достигнутыми по одному run.

При повреждении/ошибке новый OFF source останавливает admission только после завершения переключения fleet; старый binary без журнала и downgrade не являются безопасным автоматическим rollback. Хранить originals, cursors, claims, receipts и unknown. Проверить фактический deployed revision/flags/health и отсутствие новых writers. При неизвестном исходе остановиться, зафиксировать его и не повторять эффект вслепую.

## Dropbox wake candidate (#770)

The existing `/dropbox/calls/webhook` verifies the installed Dropbox app's
signature and configured account. Watch uses that same account binding and
OAuth refresh/verification for metadata calls. It has an independent
nonrecursive `/Alpha/Voice` cursor. Calls keeps its independent intake state,
cursor and `/Alpha/Calls` path. The shared callback commits a Watch wake
generation before returning. The API process signals its writer immediately;
startup and a 60-second recovery interval drive the same writer if a signal
was missed. One PostgreSQL advisory lock serializes wake-writer passes across
Gate processes. The cursor writer fence distinguishes `app_webhook` from
`legacy_actions` and the standalone `app` runner. A lost GitHub response
follows the existing mark-and-reconcile contract.

Stage migration `0119_watch_webhook_wake` with
`GATE_WATCH_WEBHOOK_ENABLED=false`. Before the controlled transition verify the
callback registration and the account's metadata access to `/Alpha/Voice`.
Provision the existing GitHub App PEM through the Railway secret interface as
`GATE_WATCH_APP_PRIVATE_KEY` **on the Gate API service that serves the callback**,
alongside the App, installation and slug settings. The cron service does not
run this route. The PEM is used to request the installation token restricted
to private Mark with Issues: write. Verify the legacy writer is quiescent,
the Watch cursor exists, and the fence is switched deliberately to
`app_webhook` before enabling the webhook writer.

For controlled acceptance record Dropbox `server_modified`, the wake
timestamp on the early-card row, and GitHub issue `created_at` read back from
private Mark for each of two different input files. A wake timestamp identifies
the account notification that drove the scan. Correlate it with controlled
uploads, since a Dropbox callback carries no file ID. Verify batch, signed
repeat, restart and recovery without a wake, recording card counts and writer.

## Watch original and transcription queue (#736)

After migration `0120_watch_webhook_archive`, opt in on the owner Gate service
with `GATE_WATCH_WEBHOOK_ARCHIVE_ENABLED=true` while the webhook writer owns the
cursor. The same delta transaction records an archive job for each newly
discovered eligible source revision. A separate worker downloads that exact
revision into the protected Gate archive and leaves it in `received` for
transcription. Transient archive failures retain the job and are deferred in
the queue. GitHub availability does not block archiving, and archiving does
not change the timing or target of the early Mark card.

The protected `watch-voice:<event-id>` receipt and `watch-voice-file:<event-id>`
original use the same event ID as Mark. The feature performs no transcription
calls and does not sweep older cards. On rollback, turn off the archive flag;
the stored originals and pending jobs remain for later recovery.

# Watch → Mark: подготовленный выпуск ранних карточек

Программа: [#770](https://github.com/markmalina/gate/issues/770). Этот документ описывает кандидаты и проверки. Он не является квитанцией установки, разрешением на credentials или доказательством доставки.

## Компоненты и ограничения

- Gate PR845 готовит metadata-only маршрут и activation startup: persistent Watch/STT принудительно OFF, Mark Actions и early-card режим ON, до пяти карточек на authenticated run. Он рассчитан на exact workflow pin ниже и не должен быть установлен до OFF-first и подтверждения private Mark workflow.
- Gate PR846 — объединённый OFF-first и forward-recovery candidate: сохраняет новую схему и журналы, принудительно отключает persistent Watch, Mark Actions и early-card режим. Его startup действует только на новые процессы; старый процесс и его запросы он сам не завершает.
- Private [Mark PR134](https://github.com/markmalina/mark/pull/134) расширяет проверку фактических счётчиков receipt от 1 до 5. Exact workflow SHA256 `78dd74c5ea6e783e26f44bd50b1aafb53cca4e8586e033fa2f80fa16202a0512`. Нельзя соединить activation с другой версией workflow или ослабить проверку pin.
- Scheduler — отдельный, ещё не созданный Railway service с `railway.watch-scheduler.toml`: 15-минутный cron запускает `app.worker.watch_voice_scheduler`. По умолчанию scheduler выключен. Никакие токены или значения переменных в Git не входят. Приватная проверка/сборка действующего workflow и получение job OIDC/token остаются на стороне Mark Actions.

Максимум пять карточек за run. При серии больше пяти хвост остаётся в очереди для следующих запусков; условие 15–30 минут для каждого файла не гарантировано. Сохранённые pending и exact-body reconciliation выполняются до обращения к Dropbox. Если ранняя очередь не пуста, её обработка может отложить следующий scan. Состояния sending/unknown никогда не разрешают повторный POST по отсутствию результата поиска.

## Scheduler: точный маршрут и границы

Отдельному scheduler service потребуется ссылка на текущую Gate database с таблицей `watch_schedule_control` (после migration 0117) и секретная переменная `GATE_WATCH_DISPATCH_TOKEN`: авторизация пользователя-владельца GitHub с доступом **только к private Mark** и минимумом repository Actions:write, без выдачи issue-write этому токену. `GATE_WATCH_SCHEDULER_ENABLED` по умолчанию false. Создание/установка credential и сервиса — отдельный защищённый шаг; этот пакет их не создаёт.

Один tick проверяет immutable owner ID, immutable private repo ID, owner, main и активный точный workflow; затем делает не более одного `workflow_dispatch` на main. Принятый GitHub HTTP 204 означает только принятие запроса, не завершение workflow или доставку карточки. Действующие Mark workflow и Gate OIDC дополнительно проверяют owner actor и workflow bytes. Локальный `GET /user` не доказывает фактический actor OIDC — это проверяется на одном согласованном run.

В PostgreSQL один control row допускает один dispatch на 15-минутный слот и не допускает параллельный tick. Отказ preflight до POST безопасно заканчивает слот; неясный POST сохраняет `unknown`, а прерванный процесс оставляет `inflight`. Оба состояния блокируют следующие dispatch до отдельной проверки. `python -m app.worker.watch_voice_scheduler --status` показывает только state, слот и безопасный outcome; токен и содержимое ответов не выводятся. Никакого слепого повторения, удаления control row или сброса unknown нет. Для сверки нужен точный интервал, GitHub workflow runs/actor/head и, где возможно, исходный provider receipt; неоднозначность не доказывает отсутствие запуска.

Railway может пропустить слот при незавершённом предыдущем cron. Частота проверяется фактически по запуску и latency, а не по строке cron. Проверить отдельную стоимость service и GitHub Actions; оценка из #770 не является фактическим счётом.

## Порядок подготовки и приёмки

1. Зафиксировать актуальные main и reviewed exact heads, target deployment/fleet, конфигурацию, активные и ожидающие jobs, состояние pending/sending/unknown и безопасную версию возврата. Не считать старый snapshot текущим.
2. Подготовить и независимо проверить OFF-first tree с фактическим parent и schema. Не допускать старый активный writer параллельно новому. После согласованной установки OFF проверить, что все старые экземпляры и запросы завершены; одно состояние нового процесса этого не доказывает. Обработать sending/unknown read-only сверкой; несовпадение или неизвестный исход — STOP, без повторного POST.
3. Только при подтверждённом OFF установить проверенный exact Mark workflow и сверить его bytes/SHA256. Не запускать старый Gate с новым workflow pin и не переключать flags вслепую.
4. Подготовить exact activation source поверх фактического main: обычный STT остаётся OFF, ранние карточки и Mark OIDC включены, cap=5, workflow pin соответствует прочитанным bytes. Сверить transition, migration и forward OFF recovery на фактическом parent. Изменения в этом порядке не доказывают готовность real scheduler.
5. Отдельно подготовить scheduler service и разрешённую identity; не печатать/копировать ключи в Git или чат. До enabling проверить условия доступа, пределы, расходы, одно соединение и stop conditions. Создание credential или изменения identity не следуют из самого source.
6. Для одной новой owner записи проверить metadata → одна private issue без STT, затем серию из нескольких записей, повторы, Dropbox outage и порядок. Измерить Dropbox server_modified → issue и фактические интервалы scheduler, указать размер выборки. Не объявлять 15–30min достигнутыми по одному run.

При повреждении/ошибке новый OFF source останавливает admission только после завершения переключения fleet; старый binary без журнала и downgrade не являются безопасным автоматическим rollback. Хранить originals, cursors, claims, receipts и unknown. Проверить фактический deployed revision/flags/health и отсутствие новых writers. При неизвестном исходе остановиться, зафиксировать его и не повторять эффект вслепую.

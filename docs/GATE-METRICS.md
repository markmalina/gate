# Gate: метрики, исходный срез и учёт расходов

Программа [#787](https://github.com/markmalina/gate/issues/787), [текущая спецификация](GATE-CURRENT.md), [спринты](GATE-RECOVERY-PLAN.md). Дата исходного среза — 05.11.2026. Числа ниже — наблюдения с указанными границами, а не автоматически обновляемая панель.

## Единица учёта и время

Для аудио единица — разрешённая source revision, а не число webhook, CI или runs. Для отправки — один delivery intent с точным получателем и context revision. Повторное событие не увеличивает число новых пользовательских результатов.

Все сохранённые timestamps — UTC ISO 8601; видимые пульсы Марку — Europe/Moscow. Нельзя подставлять дату source-файла вместо времени фактической операции. Неизвестное время остаётся null/unknown; старые данные не заполняются текущим временем.

Целевой ряд timestamps для аудио:

1. Dropbox server_modified.
2. Webhook received; корреляция с конкретным файлом отдельно от webhook уровня account.
3. Delta/source discovered и metadata persisted.
4. First private issue created, если этот маршрут имеет карточку.
5. Original archived / integrity verified.
6. STT claim / start / provider dispatch.
7. STT terminal / complete.
8. Full text published, все части.
9. Independent external readback verified.

server_modified — время Dropbox, а не доказанное время Stop на часах или окончания звонка. client_modified→server_modified — промежуток между metadata timestamps; без дополнительной корреляции его нельзя назвать upload latency. Публичный отчёт хранит агрегаты; таблица соответствий — только в разрешённом private пространстве.

## Проверенный исходный срез

| Метрика | Значение | Источник и граница |
| --- | --- | --- |
| Watch delivered / pending | 67 / 3 | Scoped Gate M, 05 Nov 13:34–13:44 МСК |
| Watch transcription counts | received 1 / transcribed 67 / unknown 1 / rejected 1 | Только Watch, не Calls/Telegram |
| Selected Watch full-text issue | 0 | Protected selected receipt; private metadata не публикуются |
| Watch scheduled runs sample | 13 runs / 12 intervals, 02 Nov 01:57:18Z–05 Nov 10:05:43Z | GitHub private Mark schedule collection |
| Между scheduled starts | min 4 ч 08 м 01 с; P50 6 ч 58 м 36.5 с; max 10 ч 18 м 58 с | Это не Dropbox→issue latency |
| Последний Watch run | 42007811853, 05 Nov 10:05:43–53Z, SUCCESS/deferred | considered 0 / published_parts 0 / delivered 0 |
| Историческая Dropbox→issue latency | 25 Oct completed cohort n = 22: P50 2504 s / P95 9786 s / max 10710 s | Датированный source receipt; unfinished исключены, это не сегодняшний baseline |
| Selected long Call | rejected / duration_exceeded / transcript absent | Original identity подтверждена read-only; private details не публикуются |
| Selected long Call Dropbox→Gate received | 14.715 s | server_modified→received_at одного проверенного source; n = 1, не длительность STT |
| Long Call size/duration admission | Источник < 100 MiB; код: duration ≤ 3600 s, chunks ≤ 20 × 180 s | Отказ не вызван превышением 100 MiB; фактическая duration ещё не доступна через текущий context tool |
| Full reply freshness | Не установлен | PR747 source-only; production snapshot-at-send не равен Work-read revision |
| Open issues / PR inventory | 39 / 18 до создания #787 | Полная GitHub open collection 05 Nov; размер backlog, не число результатов |
| OpenAI balance/tier | unknown | Platform требует входа; данные account не прочитаны |
| Gate internal speech ledger | period 2026-11, hard limit $3, settled 0 / reserved 0 на срезе | Не OpenAI credit balance и не historical note entitlement |
| Исторические quota errors | Telegram voice: 7 quota_unavailable | Исторические outcomes, не свежий provider probe |

Последний run: [42007811853](https://github.com/markmalina/mark/actions/runs/42007811853). Источник исторической latency: [PR766 sprint results](https://github.com/markmalina/gate/blob/45ba499a5f1852208ba8a478a606358fd0fe3bd5/docs/watch-voice-sprint-results.md).

## Формулы приёмки

| Метрика | Как считать |
| --- | --- |
| Intake latency | metadata persisted − Dropbox server_modified; webhook→discovered отдельно при доказанной корреляции |
| Early visibility latency | first issue created − Dropbox server_modified; только для маршрутов с issue |
| Audio queue wait | STT started − original_ready; не смешивать с decode/provider duration |
| Processing latency | STT complete − STT started; рядом длительность аудио и real-time factor |
| Delivery latency | full text readback verified − STT complete; время первой части отдельно |
| Completeness | подтверждённые ordered text parts / ожидаемые parts; 100% только при complete receipt и readback |
| Terminal coverage | eligible revisions с явным terminal outcome / все eligible revisions в когорте |
| Success coverage | eligible revisions с доказанным полным результатом / все eligible revisions в когорте |
| Duplicate rate | лишние STT dispatch / логические revisions; лишние issues/parts считаются отдельно |
| Pending age | now − oldest eligible nonterminal received; число unknown отдельно |
| Stale-send protection | stale intents, отклонённые до dispatch / все stale intents в матрице; число фактических stale dispatch отдельно |
| Recovery time | verified recovery completed − detected failure; unknown settlement не считать restored |
| Product throughput | число независимых принятых пользовательских outcomes / период |
| Development lead time | product acceptance − начало bounded issue; source/CI/review/wait отдельно при наличии timestamps |

Каждый percentile сопровождается n, началом и концом периода, методом расчёта, числом unfinished и количеством deadline breaches. Незавершённые случаи не исчезают из отчёта или знаменателя coverage. P95 на маленьком n не служит гарантией SLA. Не переносить старую выборку на новую версию без подписи.

Предлагаемые engineering targets, ещё не runtime acceptance:

- Watch Dropbox server_modified→first Mark card: пилот 3/3, затем n ≥ 20, P95 ≤ 60 s / max ≤ 120 s при доступных внешних сервисах. Webhook→durable wake измеряется отдельно и не заменяет эту величину.
- Original-ready→STT-start: P95 ≤ 30 s; целевое время processing назначить после измерения по длине аудио.
- Calls webhook→metadata queued после разделения очереди: P95 ≤ 30 s.
- Completeness 100%; duplicate STT/issue/send 0; stale dispatch 0 для событий, durably принятых до final admission.
- Steady-state eligible terminal coverage ≥ 99% на 24–72 ч и достаточной выборке; ошибки и незавершённые случаи показываются отдельно.

## Расходы: три разных источника

1. **Estimate:** расчёт по фактическому объёму работы и датированной ставке. Это не списание.
2. **Provider-reported usage:** реальные usage units из API/ledger; при наличии цены получается оценка стоимости.
3. **Invoice / billed charge / credit balance:** только данные соответствующего billing surface. Недоступные поля — unknown, не $0.

Владелец 5 ноября снял прежний общий финансовый потолок программы. Накопленный ledger сохраняется; исполнитель сообщает цену этапа и необычный рост, не изобретая новый произвольный cap. Это не отменяет технические request/retry/input/output bounds и не утверждает, что server caps/provider billing уже изменены. Изменение model-access policy и прикладного monthly cap оформляется отдельным осмысленным reviewed diff, а не обходом ledger.

### Actions ledger

До новых jobs документационного этапа cumulative estimate — **$3.022**, в том числе:

- carried после ночного пакета: $3.004;
- два последующих коротких schedule jobs: +$0.012;
- [последний schedule 42007811853](https://github.com/markmalina/mark/actions/runs/42007811853): +$0.006.

Источники: [ночной ledger](https://github.com/markmalina/gate/issues/736#issuecomment-6641384983), [сверка 5 Nov](https://github.com/markmalina/gate/issues/736#issuecomment-6655106321). Новые jobs дописываются в текущую issue по фактическому terminal-результату; baseline не заменяет последний ledger.

[Official runner pricing](https://docs.github.com/en/enterprise-cloud%40latest/billing/reference/actions-runner-pricing), прочитано 5 ноября: standard Linux 2-core — $0.006/min, Windows — $0.010/min. Для каждого исполненного job:

- сохранить run URL, exact SHA, event, attempt, job ID и runner class;
- time-to-job-start = job started − run created: включает зависимости и очередь; чистое ожидание runner считать лишь при доступных соответствующих timestamps, иначе unknown;
- execution = job completed − job started;
- округлить billable минуты по актуальным правилам и умножить на ставку данного runner;
- сложить только реально исполненные attempts/jobs; skipped не имеет исполненных runner minutes;
- успешный Windows job, перенесённый в rerun failed backend, второй раз не считать;
- actual invoice, discounts и included allowance не угадывать по estimate.

Обязательный push-main CI после merge — отдельное событие; зелёный PR CI не подменяет его. Зелёные checks без новой причины повторно не запускать. Документ не изменяет workflow gates ради экономии.

### OpenAI и Railway

Текущий internal speech cap $3/month — состояние приложения, не баланс OpenAI. Historical note period берётся из received_at; snapshot текущего месяца не доказывает доступность historical entitlement/remaining. Известные quota_unavailable outcomes не означают, что квота всё ещё недоступна, и не подтверждают её восстановление.

Официальная оценка mini-transcribe на 5 ноября — $0.003/min; 120 min ≈ $0.36 только за STT compute. Реальные parts/usage, нормализация и другие расходы показываются отдельно. Shutdown модели 26 Feb 2027 требует заранее отдельной плановой миграции; модель не меняется попутно с unrelated исправлением.

OpenAI actual balance/rate tier и Railway invoice сейчас unknown. При необычном росте сравнить измеренную стоимость сценария или этапа с его оценкой и назвать источник расхождения до следующего расхода. Не маскировать неизвестный счёт старыми условными расчётами. Новые ресурсы, подписки и credentials не становятся необходимыми только потому, что общий бюджет больше не имеет жёсткого потолка.

## Шаблон содержательного этапного комментария

- Время МСК, issue/спринт, execution/claim и точный revision.
- Ожидали: baseline → target, предмет, период, n.
- Сделали и узнали: изменение, подтверждённая причина или ошибка.
- Проверили: команды, CI или реальный receipt с их собственными границами.
- Получилось: фактические counts, latency, completeness; что остаётся unknown.
- Расход: новая и накопленная оценка, provider usage/invoice отдельно; CI time-to-job-start и job execution.
- Дальше: ответственный и конкретный шаг; активные операции — только фактически подтверждённые.

История обновляется при материальном изменении. Чатовый пульс примерно раз в минуту не означает минутный GitHub comment. При готовом результате — финал; при неизменном блокере — никаких нулевых CI, коммитов или отчётов.

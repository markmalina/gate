# Gate: метрики, исходный срез и учёт расходов

Программа [#787](https://github.com/markmalina/gate/issues/787), [текущая спецификация](GATE-CURRENT.md), [спринты](GATE-RECOVERY-PLAN.md). Дата baseline05.11.2026. Числа ниже — наблюдения с указанным предметом, а не автоматически обновляемый dashboard.

## Единица учёта и время

Для аудио единица — разрешённая source revision, не число webhook/CI/run. Для отправки — один delivery intent с exact recipient/context revision. Повторное событие не увеличивает число новых пользовательских результатов.

Все сохранённые timestamps — UTC ISO8601; видимые пульсы Марку — Europe/Moscow. Нельзя подставлять дату source файла вместо времени фактической операции. Неизвестное время остаётся null/unknown; старые данные не заполняются текущим временем.

Целевой ряд audio timestamps:
1. Dropbox server_modified.
2. Webhook received (per-file correlation отдельно от account-level webhook).
3. Delta/source discovered и metadata persisted.
4. First private issue created, если этот маршрут имеет карточку.
5. Original archived/integrity verified.
6. STT claim/start/provider dispatch.
7. STT terminal/complete.
8. Full text published, все parts.
9. Independent external readback verified.

server_modified — время Dropbox, не доказанное время Stop на часах/окончания звонка. client_modified→server_modified — metadata gap; без дополнительной корреляции его нельзя назвать upload latency. Public report хранит агрегаты, private matching table — только в разрешённом private пространстве.

## Проверенный исходный срез

| Метрика | Значение | Источник/граница |
| --- | --- | --- |
| Watch delivered / pending |67 /3| Scoped GateM05Nov13:34–13:44MSK |
| Watch transcription counts |received1 /transcribed67 /unknown1 /rejected1| ТолькоWatch, неCalls/Telegram |
| Selected Watch full-text issue |0| Protected selected receipt; не опубликованные private metadata |
| Watch scheduled runs sample |13runs /12intervals,02Nov01:57:18Z–05Nov10:05:43Z| GitHub privateMark schedule collection |
| Между scheduled starts |min4ч08м01с; P50 6ч58м36.5с; max10ч18м58с| НЕDropbox→issue latency |
| Последний Watch run |42007811853,05Nov10:05:43–53Z, SUCCESS/deferred| considered0 /published_parts0 /delivered0 |
| Historical Dropbox→issue |25Oct completed cohort n22: P50 2504s /P95 9786s /max10710s| Датированныйsource receipt; unfinished excluded, не сегодняшнийbaseline |
| Selected long Call |rejected /duration_exceeded /transcript absent| Original identity привязана read-only; private details не публикуются |
| Selected long Call Dropbox→Gate received |14.715s| server_modified→received_at для одного verified source; n1, неSTTduration |
| Long Call size/duration admission |Источник<100MiB; code duration≤3600s, chunks≤20×180s| Отказ не вызван превышением100MiB; фактическая duration ещё не доступна через текущийcontexttool |
| Full reply freshness |Не установлен| PR747 source-only; production snapshot-at-send не равен Work-read revision |
| Open issues /PR inventory |39 /18 до создания#787| Полный GitHub open collection05Nov, это backlogcount, не progress |
| OpenAI balance/tier |unknown| Platform требует входа, account данные не прочитаны |
| Gate internal speech ledger |period2026-11, hardlimit$3, settled0/reserved0 насрезе| НеOpenAI credit balance и неhistorical note entitlement |
| Historical quota errors |Telegram voice7 quota_unavailable| Исторические outcomes; не свежий provider probe |

Последний run: [42007811853](https://github.com/markmalina/mark/actions/runs/42007811853). Исторический latency source: [PR766 sprint results](https://github.com/markmalina/gate/blob/45ba499a5f1852208ba8a478a606358fd0fe3bd5/docs/watch-voice-sprint-results.md).

## Формулы приёмки

| Метрика | Как считать |
| --- | --- |
| Intake latency |metadata persisted − Dropbox server_modified; webhook→discovered отдельно при доказаннойкорреляции |
| Early visibility latency |first issue created − Dropbox server_modified; толькомаршрутысissue |
| Queue wait |STT started − original_ready; не смешивать сdecode/providerduration |
| Processing latency |STT complete − STT started; рядомdurationaudio и real-time factor |
| Delivery latency |full text readback verified − STT complete; multipart firstpart отдельно |
| Completeness |подтверждённые ordered text parts /ожидаемые parts;100%толькоприcomplete receipt и readback |
| Terminal coverage |eligible revisions сявнымterminal outcome /всеeligible revisions вкохорте |
| Success coverage |eligible revisions сдоказаннымполнымрезультатом /всеeligible revisions вкохорте |
| Duplicate rate |лишние STT dispatch /логические revisions; лишниеissues/parts считаютсяотдельно |
| Pending age |now − oldest eligible nonterminal received; числоunknown отдельно |
| Stale-send protection |stale intents rejected beforedispatch /всеstale intents вматрице; actual stale dispatch count отдельно |
| Recovery time |verified recovery completed − detected failure;unknownsettlement не считатьrestored |
| Product throughput |число независимых принятых пользовательских outcomes /период |
| Development lead time |product acceptance −началоboundedissue; source/CI/review/wait отдельно при наличииtimestamps |

Каждый percentile сопровождается n, началом/концом периода, методом расчёта, числом unfinished и количеством deadline breaches. Для completed cohort незавершённые не исчезают из отчёта/знаменателя coverage. P95 на маленьком n не служит гарантией SLA. Не переносить старую выборку на новую версию без подписи.

Предлагаемые engineering targets, ещё не runtime acceptance:
- Watch webhook→first card: пилот3/3, затемn≥20, P95≤60s/max≤120s при доступных внешних сервисах.
- Original-ready→STT-start: P95≤30s; processingtarget назначить после измерения по длине.
- Calls webhook→metadata queued послеразделенияочереди: P95≤30s.
- Completeness100%; duplicate STT/issue/send0; stale dispatch0 дляevents durably accepted доfinaladmission.
- Steady-state eligible terminal coverage≥99% на24–72ч и достаточнойвыборке; ошибки/незавершённые показываютсяотдельно.

## Расходы: три разных источника

1. **Estimate:** расчёт по фактическому объёму работы и датированной ставке. Это не списание.
2. **Provider-reported usage:** реальные usage units из API/ledger; при наличии цены получается оценкастоимости.
3. **Invoice / billed charge / credit balance:** только данные соответствующего billing surface. Недоступные поля=unknown, не$0.

Owner05Nov снял прежний общий финансовый потолок программы. Накопленныйledger сохраняется; исполнитель сообщает ценуэтапа и необычныйрост, не изобретает новый произвольныйcap. Это не отменяет технические request/retry/input/output bounds и не утверждает, что server caps/provider billing уже изменены. Изменение model-access policy и прикладного monthlycap выполняется как отдельный осмысленный reviewed diff, а не обход ledger.

### Actions ledger

До новых jobs документационного этапа cumulative estimate **$3.022**, в томчисле:
- carried после ночного пакета$3.004;
- два последующих коротких schedulejobs +$0.012;
- [последнийschedule42007811853](https://github.com/markmalina/mark/actions/runs/42007811853) +$0.006.

Источники: [ночнойledger](https://github.com/markmalina/gate/issues/736#issuecomment-6641384983), [сверка5Nov](https://github.com/markmalina/gate/issues/736#issuecomment-6655106321). Новыеjobs дописываютсявтекущуюissue пофактическомуterminalрезультату; цифра в baseline не заменяет latestledger.

[Official runner pricing](https://docs.github.com/en/enterprise-cloud%40latest/billing/reference/actions-runner-pricing), прочитано05Nov: standard Linux2core$0.006/min, Windows$0.010/min. Для каждого исполненногоjob:
- сохранитьrun URL, exact SHA, event, attempt, job ID, runner class;
- queue wait=job started−run created; execution=job completed−started;
- округлить billable минуты согласно актуальным правилам, умножить на ставку данногоrunner;
- сложить только реально исполненныеattempts/jobs; skipped=нетисполненныхrunnerminutes;
- successful Windows, перенесённый в rerun failed backend, второйраз не считать;
- actualinvoice/discount/includedallowance не угадывать поestimate.

Нужный обязательный push-mainCI послеmerge — отдельное событие; зелёный PRCI не подменяет его. Повторные зелёныеchecks без новой причины не запускать. Документ не изменяетworkflowgates ради экономии.

### OpenAI и Railway

Текущая internal speech cap$3/month — состояние приложения, не баланс OpenAI. Historicalnoteperiod берётся из received_at; currentmonth snapshot не доказывает доступность historical entitlement/remaining. Известныеquota_unavailable outcomes не означают, что квота всёещё недоступна, и не подтверждают её восстановление.

Официальная оценкаmini-transcribe$0.003/min на05Nov;120min≈$0.36 толькоSTTcompute. Реальные parts/usage/нормализация и другие расходы показываютсяотдельно. Modelshutdown26Feb2027 требует заранее отдельнойплановоймиграции; не менятьмодельвunrelatedспринте.

OpenAI actualbalance/rate-tier и Railwayinvoice сейчасunknown. Длянеобычногороста сравнитьизмереннуюстоимостьсценария/этапасегооценкой, назватьисточникрасхождениядо следующего расхода; не маскироватьunknownсчётстарымиусловнымирасчётами. Новые ресурсы/подписки/credentials не считаютсянеобходимыми толькопотому,чтообщийбюджетбезжёсткогопотолка.

## Шаблон содержательного этапного комментария

- Время МСК, issue/спринт, execution/claim и точный revision.
- Ожидали: baseline → target, предмет/период/n.
- Сделали и узнали: изменение/подтверждённаяпричина/ошибка, а не подробнаявнутренняяцепочкамышления.
- Проверили: команды/CI/реальныйreceipt с их собственнымиграницами.
- Получилось: actualcounts/latency/completeness, чтоостаётсяunknown.
- Расход: newestimate +cumulative, providerusage/invoice отдельно; CIqueue/jobtime.
- Дальше: ответственный и конкретныйготовыйшаг; activeoperations толькофактическиподтверждённые.

History пишетсяпри материальномизменении. Чатовыйпульс примерноразвминутуне означает минутныйGitHubcomment. Приготовомрезультате — финал; при неизменномблокере — негенерироватьнулевыеCI/коммиты/отчёты.

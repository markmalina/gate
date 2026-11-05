# Gate: программа восстановления рабочих маршрутов

Единый управляющий контекст — [#787](https://github.com/markmalina/gate/issues/787), портфельный parent — [#277](https://github.com/markmalina/gate/issues/277). Перед работой читать [текущую спецификацию](GATE-CURRENT.md), [метрики](GATE-METRICS.md), [AI WORK-RULES](https://github.com/markmalina/ai/blob/main/WORK-RULES.md). Этот план от05.11.2026 заменяет очередь действий из старых аудитов, сохраняя их доказательства.

## Правило результата и последовательность

Спринт — один небольшой наблюдаемый результат для Марка. Его завершает реальная приёмка маршрута, а не число коммитов, SOURCE_ACCEPT, CI или merge. Source/CI/installation/product acceptance записываются отдельно. Подготовительный этап P0 необходим для общего контекста, но не считается работающей новой функцией.

Один активный functional sprint и один runtime writer. Независимый reviewer не автор. Read-only исследования независимых участков можно выполнять параллельно; они не получают право запустить второй writer или effect. Каждый спринт заранее имеет issue, baseline, target, scope, lease/исполнителя и ссылку на программу. Существующий совпадающий outcome продолжается, не копируется. Для следующего самостоятельного outcome — отдельная issue/подзадача; её создание не равно запуску агента.

Предлагаемый порядок: P0 → W1 → W2 → W3 → W4 → C1 → R1 → W5 → Q1 → Q2 → R2 → S. C1 или R1 можно поднять выше по готовности и явному решению в #787, сохранив один runtime lane. У каждого этапа есть свой результат; большой PR не становится единицей спринта только потому, что уже написан.

## Подготовительный P0 — установить общий контекст

**Задача:** [#767 / PR768](https://github.com/markmalina/gate/issues/767), общий parent#787; [AI#5](https://github.com/markmalina/ai/issues/5).

**Было:** спецификация19Oct и дальнейшие аудиты лежат в незавершённом docs PR; новый Work восстанавливает старые противоречивые очереди. **Приёмка:** current spec/plan/metrics и обязательные входы AGENTS/bootstrap/README находятся в main; ссылки и новый Work walkthrough проверены независимо. Runtime blobs равны base main. Исторические документы сохранены. AI WORK-RULES задаёт issue history, измеримые спринты, время/стоимость.

Это установка документационного контекста, не получение текста или ускорение доставки. Следующий активный функциональный outcome — W1 в#736.

## W1 — полный текст одной сохранённой Watch-записи

**Задача:** [#736](https://github.com/markmalina/gate/issues/736). **Было:** выбранная допустимая note received/pending, полный текст в private issue0. **Результат:** одна выбранная note → один полный текст в одной private Mark issue; completeness100%, duplicate STT0, новые original downloads0.

Предпочтительный путь — установить уже подготовленный queued-first из main через устранение deploy-блокера, сохранив текущий future-only. Eligible note обрабатывается под coordinator до Dropbox OAuth/listdelta; commit+return не меняет общий cursor. LKG initial-exact не READY: он пропускает чужие entries, но продвигает общий WatchVoiceCursor; после возврата future-only ещё не архивированные чужие записи могут потерять видимость. Даже snapshot без чужих entries не устраняет гонку: прямое переключение initial-exact остаётся NOT_READY. При пустой очереди старый scan — отдельный известный дефект. Проверить actual note/исходник/состояние, поведение push+schedule, historical budget, normal mini policy и old UNKNOWN. Изменение binding на main само запускает обработку; same-text publication после STT продолжает только ту же note.

Source checks: чужой oversized entry пропускается до validation; saved event не скачивается; только exact source pair выбран; UNKNOWN/уже transcribed не запускают STT; source revision mismatch и old-main run отвергаются; все части имеют одну issue и читаются обратно.

**Если LKG путь не готов:** внутри W1 сначала минимальная достаточная диагностика/ремонт выпуска или конкретной недостающей capability. Не заменять незнание причины новым deploy. Source-only изменение не закрывает W1. Нет универсальной зависимости от полного#781/новойfixture/поддержки.

**Остановка:** новый UNKNOWN, scope drift, integrity mismatch, бюджетный/provider отказ или иная note. Не rearm received и не менять модель по догадке. Критерий rollback/сохранения узкой binding входит в конкретный пакет до effect.

## W2 — ранняя карточка и пригодный оригинал

**Задача/требование:** [#770](https://github.com/markmalina/gate/issues/770), реализация связана с#736. До coding оформить отдельный bounded этап в этой задаче либо дочернюю issue с точным перенесённым критерием.

**Было:** в production issue создаётся при доставке текста. **Результат:** одна новая source revision получает одну private Mark карточку до первого STT; после архивирования защищённый original реально доступен; дубликатов0. metadata_received и original_ready — разные состояния.

Переиспользовать early-card/source journal изPR766, выделив минимальный согласованный diff. Если схема требует несколько добавлений, показать реальные зависимости и downgrade/recovery границу до выпуска; не переписывать исторические migrations.

**Проверки:** STT disabled/slow, повторный delta, GitHub lost ACK, рестарт до/после card create и archive. Реальная приёмка: карточка видна при отсутствии текста, ссылка проверена владельческим private reader после original_ready. Возможность прочесть original не называется распознаванием.

## W3 — Watch пробуждается webhook

**Задача:**#770/#736, bounded implementation issue перед coding.

**Было:** Watch GitHub schedule интервалы P50≈6ч59мин/max≈10ч19мин на датированной выборке; Dropbox webhook обслуживает Calls. **Результат:** существующий подписанный Dropbox webhook фиксирует durable Watch wake; один worker обнаруживает note без ожидания GitHubcron.

**Предлагаемый target:** пилот3/3 новых контрольных записей; затем n≥20, Dropbox server_modified→first Mark card P95≤60s, max≤120s при доступных внешних сервисах. Отдельно сообщать breaches и unavailable intervals; не объявлять target достигнутым по трём случаям.

Scope: webhook быстро подтверждает событие, тяжёлая обработка вне HTTP; distinct Call/Watch cursors, durable coalesced pending, один driver/lease, recovery polling для потерянного wake. Не запускать второй конкурирующий Actions/persistent worker.

**Проверки:** HMAC/account boundary, duplicate/out-of-order webhook, потеря события, рестарт между wake и scan, конкурентные drivers. Приёмка по correlated timestamps, не по одному HTTP200.

## W4 — автоматическая STT и полный текст в той же карточке

**Задача:**#736, отдельный bounded этап.

**Было:** ранний source candidate не установлен; один Actions run≤1note+1publication part, возможен зелёный deferred. **Результат:** eligible note проходит original_ready→STT→same issue complete автоматически; parts completeness100%, duplicate STT/issue0.

**Предлагаемый target:** original_ready→STT_started P95≤30s на n≥20; processing duration измерять отдельно по длине исходника, сначала получить фактический real-time factor. Полнота доказана числом/порядком/digests и чтением всей private issue, а не presence первой строки.

Сохранить серверный policy pin/key boundary, retry0, budget reservations и provider-error классификацию. Проверить timeout, quota denial, partial result, publication lost ACK, повторное событие после complete. Реальное provider evidence и стоимость обязательны.

## C1 — один длинный телефонный разговор

**Задача:** [#788](https://github.com/markmalina/gate/issues/788), planned, runtime writer ещё не назначен. Оригинал и private receipt уже найдены владельческим read-only путём; персональные metadata не копировать в программу.

**Было:** selected long call rejected/duration_exceeded, full transcript0; лимиты60min и20×180s. **Результат:** полный текст выбранного разговора доступен через Gate M; original preserved, gaps0, ordered parts complete, duplicate provider attempts0.

Сначала получить фактическую media duration и metadata-only стадии через безопасный reader, не требуя повторного скачивания оригинала в Work. Новый supported duration/part count рассчитывается по реальному исходнику; ориентир не менее120min, но это не заявление о длительности выбранного звонка. Согласованно изменить Calls admission, normalizer, maxparts, output и policy; shared provider-part limit сохраняется. Изменение только60min константы недостаточно.

Прежний отказ определён до STT; перенос к новой обработке всё равно должен иметь точную историю и at-most-once claim, без общего reset всех rejected/UNKNOWN. Проверить long synthetic media, границы duration/parts/size/output, decoder ошибки и сохранение полноты. Затем один selected real call по проверенной версии. Не превращать этот спринт в общий рефакторинг очереди.

**Доставка:** до отдельного решения Calls остаётся в protected Gate archive/ChatGPT. Автосоздание Mark issues для всех звонков не предполагается.

## R1 — устаревший ответ не уходит

**Задача:** [#745 / PR747](https://github.com/markmalina/gate/issues/745). **Было:** Work-read revision не закреплена в установленном draft; нет полного final admission guard. **Результат:** после нового принятого Gate события старый approved reply даёт context_changed/sent=false; stale provider attempts0. Неизменённый контроль —1 разрешённая отправка.

Переиспользовать sourcePR747. Выпустить совместимый migration/ingress/reply/draft/MCP контракт: snapshot чтения, immutable draft revision, общий raw-ingress lock, проверки при reservation и перед dispatch. Не оставить legacy route, обходящий guard. Text/document/sequence включены; хвост sequence останавливается.

Actual control case должен быть точным разрешённым private тестом; не отправлять реальные сообщения третьему лицу только ради теста. Гарантия ограничена durably received до final admission; уже отправленный HTTP не отзывается задним числом.

## W5 — Work/ChatGPT действительно обрабатывает тот же original

**Задача:**#770. **Было:** protected file selector есть; standalone STT capability в текущей среде не подтверждена, source claim/start/submit не установлен. **Результат:**1 реальный Work transcript принят в ту же issue; второй Gate STT после принятия0.

Сначала доказать существующий audio-read и распознающий инструмент в выбранной среде; если его нет, это точная capability gap, а не основание выдавать download за STT. Серверный OpenAI путь и Work-путь остаются явными альтернативами. Затем подключить проверенный adapter изPR766.

**Проверки:** Gate-first, Work-first, simultaneous claim, started lease expiry, поздний provider response, malformed/неполный submit, revision mismatch. Одна owner note не получает два независимых оплаченных процесса без явного отдельного решения.

## Q1 — плохая запись не останавливает соседнюю

**Задачи:** отдельные bounded Watch/Calls этапы, связанные с#736 иCalls outcome; перед coding issue обязательно.

**Было:** oversized/unreadable entry может прервать scan и advance cursor. **Результат:** смешанная серия даёт отдельный понятный отказ плохого файла и доставку следующего допустимого; lost originals0, duplicate STT0, stuck cursor0 в проверенных случаях.

Сохранить rejected-source receipt с безопасной причиной; не скачивать заведомо слишком большой файл ради проверки. Проверить malformed metadata, unsupported type, >limit, bad media, повторный webhook/delta и restart. Backfill не расширяется неявно.

## Q2 — discovery и длительная обработка разделены

**Задача:** bounded queue sprint после первого реального длинного transcript.

**Было:** Calls STT inline задерживает следующий scan; Watch зависит от driver scheduling. **Результат:** новая metadata записывается независимо от долгой предыдущей STT; очереди и oldest_pending видны.

Durable per-note queue/phase timestamps, ограниченная согласованная concurrency, отдельные leases и byte/storage bounds. Refactor принимается только с before/after: предложенный webhook→metadata queued P95≤30s, duplicate logical jobs0, сохранность после restart100% на контрольной серии. Не повышать concurrency/provider spend без измерения и обоснования.

## R2 — гонки, старые клиенты и восстановление send

**Задача:**#745 с#718/#716/#722 иPR720 по области покрытия.

**Было:** полный guard только source. **Результат:** A01–A12 и реальные разрешённые проверки подтверждают stale sends0 и duplicate outbounds0: входящее, owner-outgoing, edit/delete, same text, different chat, дваworker, legacydraft, sequence, UNKNOWN и restart.

Две независимые PostgreSQL sessions должны воспроизводить гонку raw-ingress/reservation. Mock dispatch counts не заменяют единственный разрешённый live control, а live control не доказывает все races. UNKNOWN не считается успехом или возможностью повторить send.

## S — устойчивость и плановая эксплуатация

**Задача:** один bounded acceptance sprint после функциональных результатов.

**Результат:**24–72ч наблюдений с полным учётом eligible/pending/rejected/unknown; p50/p95/max и размер выборки; silent loss0, duplicate STT/issue/send0, все failures имеют понятную причину/next action. Target eligible terminal outcomes≥99% — предложение, проверяемое по достаточной выборке; unfinished не исчезают из знаменателя.

Проверить один согласованный restart/recovery, актуальный runbook и минимальный budget/latency alert. Не создавать per-project heartbeat без необходимости: продуктовый webhook/worker и пользовательская отчётность — разные механизмы. Owner уведомление о необычном расходе не требует фиксированного нового произвольного cap.

До shutdown26Feb2027 подготовить отдельный model migration sprint по актуальным официальным правилам; deprecated статус текущей модели не повод менять её во время unrelated исправления.

## Вспомогательный выпуск и восстановление

Если конкретному спринту необходим deploy, он включает exact accepted source/tree, реальный currentmain, required CI, registered Gate-only executor, observed target/config/schema, конкретный recovery и health. Общий main может содержать другие ещё не установленные пути: выпуск не описывается как установка одного изменённого файла без проверки всего delta.

Старый UNKNOWN run41907078570 остаётся. Следующая допустимая диагностика при необходимости — ограниченный target-scoped READ source/trigger/eligibility с существующим token, safe enums, timeout/body bounds/retry0. Permission denial — полезный факт, отсутствие публичной ACL не требует автоматически многодневной поддержки. Такой read не доказывает full history/exactcommit visibility. Повторная mutation для воспроизведения ошибки не является диагностикой.

#781 остаётся NOT_ACCEPTED; accepted source не меняет этот статус. Ни funding, ни истёкшее окно, ни документ не создают новый operation slot. Новый общий owner budget от05Nov снимает денежный потолок программы, но конкретные side effects по-прежнему требуют проверенного scope; новые credentials/новые subscriptions не предполагаются.

## Карта существующих задач

| Объект | Роль в программе | Состояние переноса |
| --- | --- | --- |
| #787 | Единственный текущий управляющий parent | Ведёт очередь, active sprint, итоговые метрики |
| #277 | Широкая продуктовая платформа | Сохраняется над программой |
| #767 / PR768 | Спецификация/контекст | Переиспользовать, довести docs до main |
| #731 / #736 / #770 | История и требования Watch | Сохранить; новые slices явно связать |
| #788 | Один выбранный длинный Calls transcript | Planned, без runtime запуска |
| #745 / PR747 | Reply freshness | Сохранить source/verdict/history; runtime не принят |
| #718 / #716 / #722 / PR720 | Series/parallel send | Проверить фактическое покрытие, не объявлять полным snapshot guard |
| #781 | Отдельная инфраструктурная работа | Не принята; не универсальный critical path |
| #764 / #778 / #772 | История/условные инфраструктурные зависимости | Не терять незавершённые обязательства |
| #751 | Зарегистрированный control issue | Его closed state не означает остановку маршрута; не открывать ради новой программы |
| Прочие каналы/старыйAQUB | Вне первоочередной очереди | Не закрывать и не запускать массово |

Чистых дубликатов для безопасного массового закрытия не установлено. Superseded допускается только после явного переноса невыполненных критериев и двусторонней ссылки; это не done. Старые terminal Delta verdict неизменны; новая независимая terminal attempt оформляется новым Delta issue.

## История и рабочий ритм

В issue спринта обязательно сохраняются существенные шаги по мере появления: начало/claim, проверенная гипотеза или ошибка, решение, frozen candidate, CI/run/result/time/cost, installation, product acceptance/отказ. Не ждать10итераций для первого отчёта, но не писать heartbeat-only комментарии.

Каждый visible update начинается свежим временем МСК, говорит о новом факте/действии/блокере и заканчивается конкретным next actor. Счётчик прогресса — принятые пользовательские результаты. «CI идёт» сопровождается ссылкой и временем начала; длительность и деньги фиксируются после terminal run. Финал содержит было→стало, evidence и реальное состояние активных операций.

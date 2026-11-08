# Gate — пауза разработки и пакет закрытия Work

Дата решения и среза: **8 ноября 2026, 18:47–18:48 МСК**.
Управляющая задача: [#787](https://github.com/markmalina/gate/issues/787).
Регламент: https://github.com/markmalina/ai/blob/main/WORK-RULES.md .

## 1. Действующее решение Марка

Марк поручил: «заморозим всю эту работу, разработку по gate. Текущий статус объясни мне, собери, посмотри, что есть, сохрани в репозитории, чтобы вся информация была, чтобы я все ворки позакрывал, пока на паузу поставил gate».

Статус разработки: **PAUSED_BY_OWNER**. Разработка, исследовательские итерации, выдача следующих задач исполнителям, новые проверки/CI ради продвижения, source integration/release и экспериментальные provider/data/STT операции остановлены. Отсутствие завершённого W1 больше не служит основанием запускать следующий Work. Любая автоматизация или старый handoff сначала обязаны прочитать этот более поздний owner STOP в #787.

Это пауза инженерной работы. Работающий production, его обычные пользовательские интеграции, имеющийся Mark Watch caller/cron и данные сохранены в текущем состоянии. Сервер, production worker/caller, аккаунты, секреты, бюджеты, DB, originals/outbox/UNKNOWN этим сохранением не изменялись. Остановка сервера/действующего пользовательского маршрута не выполнялась.

Ни одна незавершённая продуктовая задача не объявлена выполненной. Открытые issues/PR/ветки сохраняются, без массового закрытия и удаления. Новая разработка и новый operational window не создаются. Возобновление — только после нового прямого поручения Марка, а не по истечении паузы/старому расписанию.

## 2. Краткий фактический итог

| Предмет | Наблюдение | Практический смысл |
| --- | --- | --- |
| GitHub main | `e7b3e32998706f7ef19053331466ea6c63105beb`, tree `e17b705e67747f52a937d9f2574b157600c0db9d` | Main с 06.11 не продвинулся; архив сохраняется отдельно |
| Production через Gate M | reported short SHA `afe0c6b85193` | На сервере прежний код; свежий полный SHA/fleet/schema этим инструментом не доказаны |
| Исторический полный LKG | `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` | Из предыдущего принятого контекста; не выдавать за новый full-SHA provider read |
| Watch | Actions enabled; ordinary worker disabled; delivered 67, pending 3 | Изменения не установлены; счётчики совпадают с проверкой 01.11 |
| Незавершённые Watch | received 1 / unknown 1 / rejected 1 | UNKNOWN не повторять и не сбрасывать |
| Продуктовый W1 | **NOT_ACHIEVED** | Полный текст выбранной сохранённой записи в одной private Mark issue и независимая полнота не приняты |
| Reply freshness | PR747 OPEN / UNMERGED | Подготовленная защита от устаревшего контекста не установлена |
| Telegram Business text parity | ready, 6361 matched, mismatch 0, accounts covered 2 | Только scoped read-only агрегат; не приёмка нового reply guard |
| Personal Telegram/MTProto | runtime_connected=false; local_session_not_bound | Личный MTProto маршрут не подключён; не смешивать с Business RU/BY |
| Звонки | В свежей выборке recent recordings есть rejected, включая unclassified_error и media_unreadable | Не заявлять исправность полной автоматической расшифровки Calls; C1 остаётся открытым |
| Open backlog | 41 issues + 35 PR | Полный список и exact heads сохранены ниже и в JSON |

Источники: fresh GitHub main/open issue/open PR/run reads, scoped Gate M status, automations readback.
В [pause-inventory.json](pause-inventory.json) — безопасная структурированная проекция с точными версиями и метаданными. Сырые ответы Gate и частные записи не сохранены.

## 3. Что подготовлено и где лежит

| Направление | Сохранённый результат | Что отсутствует |
| --- | --- | --- |
| W1 single-note guard/release checks | PR797 уже merged в main; #794–#799 source этапы приняты | Это не установка текущего main в production и не полный текст записи |
| Maintenance / WATCH_QUIESCE | Цепочка PR811/813/817/819/821/823/825/827/829/831/833; сводный PR835 | Source/mock/CI есть; реальная installation/activation/ledger/ACL/hold не выполнены |
| Сводный release | PR835 head `44531dc06f1a0b3438f70b83de110dffa525b3b1`, accepted tree `ff461b59716da761a1af55bf3a79f514d55f3965` | OPEN / UNMERGED; 10 commits / 24 paths по принятому receipt |
| Matching hard-OFF recovery | PR837 head `731d9bb03b2b8c791afea5c3a2d5210a76e1729c`, tree `0e833e0d8be6e1cbcbb4dee2a30829990b60d21e` | OPEN; base=PR835 branch; actual-new-main-parent recovery пока не существует |
| Старый recovery | PR802/808/784 | Другие bases/условия; не применять как взаимозаменяемый rollback. PR808 не SAFE_FOR_OFF_RESUME |
| Railway metadata reader | PR805 → PR840 → PR842; последний head `b5bcc065b2c0948bf63dee8cce5a45babfdca57d`, tree `c485e8ef1c0f446671227c82137e26042d71228d` | PR842 source accepted, не запускался; frozen окно уже истекло |
| Ранняя Watch карточка/Work journal | PR766 head `45ba499a5f1852208ba8a478a606358fd0fe3bd5` | OPEN / UNMERGED; миграции и новый product path не установлены |
| Reply snapshot guard | PR747 head `b975475c1c6c81035e38d9bd13a40d777933e83c` | OPEN / UNMERGED; migration/client activation и runtime приёмка не выполнены |
| Длинный Calls transcript | #788 | Текущая программа C1 остаётся planned/not achieved |
| Media, MTProto, sequences, old fixtures | Существующие PR и issues в инвентаре | Пауза распространяется на их инженерное продвижение; не реанимировать историческую очередь |

Наличие исходников/зелёного CI/закрытой source issue не означает функционального завершения родительской задачи.

Три важных CI заново прочитаны как metadata (тесты заново не запускались):
- [PR835 / CI42366773001](https://github.com/markmalina/gate/actions/runs/42366773001): exact head44531dc…, attempt1, completed/success.
- [PR837 / CI42369989568](https://github.com/markmalina/gate/actions/runs/42369989568): exact head731d9bb…, attempt1, completed/success.
- [PR842 / CI42415738305](https://github.com/markmalina/gate/actions/runs/42415738305): exact headb5bcc065…, attempt1, completed/success.

Точные подробные тестовые числа и независимые acceptance сохранены в [исходном управляющем контексте](pre-pause-context.md); текущий checkpoint не изображает повторную независимую проверку кода или raw logs.

## 4. Последняя остановка и почему результат не получен

Установленные факты:
1. Выпуск 04.11 run41907078570 остался UNKNOWN / BAD_USER_INPUT / UNCLASSIFIED, deploymentId не установлен. [Terminal](https://github.com/markmalina/gate/issues/736#issuecomment-6641384983), [Delta506](https://github.com/AlphaCenter/delta/issues/506#issuecomment-6641401287). Старый неизвестный эффект не очищен новым source анализом.
2. 07.11 в16:02 разрешённый Railway connector READ в #838 не вернул explicit autoDeploy. Premerge predicate остался UNKNOWN. Выполнен один из максимум трёх вызовов, остановка до merge; это недостаточность metadata, а не отсутствие owner approval. [Terminal](https://github.com/markmalina/gate/issues/838#issuecomment-6707293661).
3. α признал, что недостаточность такого MCP route уже была известна по #803/#804. Вместо повторных calls подготовлены новые source reader candidates. [Разбор](https://github.com/markmalina/gate/issues/838#issuecomment-6707427143).
4. Ночью #841/PR842 завершили source+CI и исправили неправильный parent плана. Operational grant оставался отдельным. Окно 08.11 00:45–01:30МСК, latest start01:20, истекло без запуска READ/merge/recovery. [STOP](https://github.com/markmalina/gate/issues/838#issuecomment-6713785097), [ночной итог](https://github.com/markmalina/gate/issues/787#issuecomment-6713786961).
5. Ночной контроллер запланирован на8часовых запусков, но его последняя фактическая run-запись: start01:11:39 / end01:21:34МСК / recorded_status=failed. В01:22:37МСК запись автоматизации обновлена; сейчас enabled=false. Причина failed/отключения UNKNOWN. Восемь выполненных циклов не подтверждены.

Интерпретация этого Work, а не новый runtime факт: работа раздробилась на source-компоненты/приёмки/допуски, осталась зависимость от ответа владельца внутри короткого ночного окна и не обеспечивалось сквозное исполнение до W1. Это сочетание технических дефектов и организации работы; нельзя объяснять весь застой отсутствием одного разрешения Марка. После решения о паузе этот вывод не разрешает новый аудит или новый helper.

## 5. Что реально осталось незавершённым — сохранено для будущего решения

Это перечень обязательств, **не активная очередь**:
- #838: authoritative fresh metadata, conditional source merge PR835, matching recovery на фактическом новом main; прежнее окно больше неисполняемо.
- Restricted [Mark131](https://github.com/markmalina/mark/issues/131): private caller/source pause; исходное private содержание не копировать в public Gate. Main merge не доказывает quiescence.
- Maintenance state seed, authentication, ACL/protection/durability и activation: NOT_PERFORMED. Accepted activation patch из [#832](https://github.com/markmalina/gate/issues/832#issuecomment-6705331250): 2150 UTF8 bytes, SHA256 `d60017be0c1f71041f9a56ad9f4d97ae820db47ed335fd5c005c79b2d1590382`, UNAPPLIED.
- Полная остановка Gate/hold: downtime consent и fresh fullSHA/deployment/fleet/target/executor/window пока не выполнены/не предоставлены; HOLD_NO_AUTORESTORE не обходить.
- Release/config/resume/cutover: actual forward/recovery pair, оба effective Watch OFF, writer/inflight/fingerprint/schema/health/fleet proof. Source defaultfalse сам по себе runtime OFF не доказывает.
- Выбранная запись: точный private selector, eligibility, исторический budget/UNKNOWN reconciliation, одна допустимая STT и доставка всех частей в одну private Mark issue с независимым readback полноты.
- Следом по последнему принятому порядку W1 → R1/R2 → W2/W3/W4 → C1 → W5 → Q1/Q2 → S. Весь порядок заморожен.
- PR766/747: совместимость migrations0116–0119 и reply0116 с фактически установленной схемой требует coherent join/recovery при будущем продолжении. Historical migrations/receipts не переписывать.
- #781/S1 NOT_ACCEPTED; старые fixture/инфраструктурные работы не превращать в универсальный prerequisite W1.
- AQUB остаётся frozen; прежние пути через его runtime, credentials, paired deploy не возобновляются.
- Старые UNKNOWN/reservations/outbox, consumed803/806/807, expired805/840/842 и завершённый838 не reset/replay/rearm.
- Historical Actions estimate$3.800 сохраняется историей; это не актуальный invoice/баланс. Provider billing/счёт Railway и новая полная стоимость оставшегося пути этим checkpoint не установлены.

## 6. Автоматизации, CI, агенты и возможность закрыть Work

Все **6** доступных Gate-задач уже disabled, next_run_time=null:
- «Проверить запуск Gate».
- «Продвигать Gate».
- «Ночной контроль Gate736».
- «Ночной контроль Gate».
- «Результат MTProto Gate».
- «Продолжить Gate #745».

Никакое новое расписание не создано, существующее не включалось/не продлевалось. В доступном списке включены только задачи других направлений, они не затронуты. Automation/conversation IDs в public snapshot не публикуются.

Свежие Gate Actions queries: in_progress0, waiting0; queued1 — [старый CI31127247901](https://github.com/markmalina/gate/actions/runs/31127247901) от06.08.2026. Он не принадлежит этому checkpoint и не доказывает выполнение runner сейчас. Отмена не выполнялась: операция cancel не обнаружена в доступном GitHub connector. Не объявлять «все GitHub runs terminal» или удалять историю по догадке.

В текущем дереве агентов есть только root, выполняющий сохранение паузы; дочерних агентов нет. Ночные прошлые claims RELEASED по сохранённым terminal receipts. Фактические процессы/статусы любых иных открытых Work за пределами этого дерева напрямую не наблюдаемы. Пауза опубликована в обязательных общих project pointers; это не имитация отправки/прочтения сообщения каждым чатом.

После сохранения checkpoint этот Work завершает ход, compute lease RELEASED. Хранение работы не зависит от сохранения текущего чата открытым. Код остаётся в Git commit/PR heads, история — в issues и pre-pause snapshot. Закрытие Work не является завершением продуктовых задач. Уже работающий production может продолжать свои пользовательские фоновые процессы; инфраструктура и подписки не удалены/не остановлены.

## 7. Где искать информацию без истории чатов

1. Текущий owner state — [#787](https://github.com/markmalina/gate/issues/787), зафиксирован PAUSED_BY_OWNER; прежний NEXT более не активен.
2. Этот документ — краткий и полный freeze handoff.
3. [pause-inventory.json](pause-inventory.json) — все 41 open issues, 35 open PR, exact branch/head/base, CI metadata, redacted runtime и schedules.
4. [pre-pause-context.md](pre-pause-context.md) — сохранённые до паузы полные тела #787, #838 и #277, до замены их верхних указателей.
5. Current spec/план/метрики и README сохранены в унаследованном дереве этого archive commit: [GATE-CURRENT](../../GATE-CURRENT.md), [GATE-RECOVERY-PLAN](../../GATE-RECOVERY-PLAN.md), [GATE-METRICS](../../GATE-METRICS.md). Их runtime baseline датирован05.11; свежие факты выше не меняют даты исторических наблюдений.
6. [Полный план07.11](https://github.com/markmalina/gate/blob/94f460c83dc5d466e070fbed4ea047d2057d8fc3/docs/operations/GATE-SPRINT-PLAN-2026-11-07.md) и [handoff828](https://github.com/markmalina/gate/blob/94f460c83dc5d466e070fbed4ea047d2057d8fc3/docs/operations/WORK828-HANDOFF.md): исторические материалы, не разрешение продолжать.
7. Исходные записи/аудио/тексты остаются в разрешённых private Mark/Gate/Dropbox местах. Этот checkpoint не переносит их и не публикует исходники в public Gate.

## 8. Возобновление в будущем

До нового поручения Марка — никаких действий по продвижению проекта и никаких напоминаний о получении старого допуска.

После нового поручения:
1. Начать с этого snapshot + актуального #787, подтвердить новый желаемый результат и границы.
2. Проверить только изменившиеся main/PR/runtime/claims/schedules; не повторять весь многонедельный аудит и уже зелёные CI без новой причины.
3. Восстановить один существующий outcome, собрать весь оставшийся исполнимый маршрут до выбранного пользовательского результата, включая необходимые данные, доступ, STOP и recovery. Не заставлять Марка вручную переводить каждую техническую подстадию.
4. Устаревшие window/source/grants не исполнять. Фактическую авторизацию определять по новому поручению и применимым правилам; пауза сама не предоставляет новых прав.
5. Исполнять только после явного снятия паузы. Этот checkpoint не запускает следующий Work.

## 9. Полный список открытых issues

Все перечисленные issues сохраняются OPEN и заморожены в части инженерного продвижения, не приняты как выполненные.

| Issue | Название |
| --- | --- |
| [#838](https://github.com/markmalina/gate/issues/838) | [W1 integration / WAITING_NEW_GRANT] PR842 READ → PR835 + recovery |
| [#788](https://github.com/markmalina/gate/issues/788) | [C1 / Gate787] Полный текст одной сохранённой длинной записи звонка |
| [#787](https://github.com/markmalina/gate/issues/787) | [GATE-RELIABILITY] Рабочие маршруты Марка: единый план, измеримые спринты и приёмка |
| [#781](https://github.com/markmalina/gate/issues/781) | [S1 BLOCKED] OFF integration source accepted; apply settlement and full budget pending |
| [#778](https://github.com/markmalina/gate/issues/778) | Дефект: неактивные label-workflow конкурируют за pending slot Watch scope |
| [#772](https://github.com/markmalina/gate/issues/772) | Дефект: metadata client допускает protected key input до проверки original human command |
| [#770](https://github.com/markmalina/gate/issues/770) | [WATCH-VOICE] Сразу создавать issue в Mark по аудио; добавлять текст позже от Gate или ChatGPT |
| [#764](https://github.com/markmalina/gate/issues/764) | Update Gate's pinned Ops caller after repository ownership transfer |
| [#745](https://github.com/markmalina/gate/issues/745) | Gate: запрет отправки ответа по устаревшему снимку диалога |
| [#742](https://github.com/markmalina/gate/issues/742) | [Rich Sprint 2] Готовые изображения, альбомы, native slideshow/collage через Gate M — под ключ из RU |
| [#736](https://github.com/markmalina/gate/issues/736) | [WATCH-VOICE] Завершить автономный Galaxy Watch → Gate → private Mark |
| [#735](https://github.com/markmalina/gate/issues/735) | Telegram Rich Messages: блоки кода, Mermaid, изображения и слайд-шоу |
| [#731](https://github.com/markmalina/gate/issues/731) | [WATCH-VOICE-MARK] Dropbox Voice → durable transcription → private Mark inbox |
| [#730](https://github.com/markmalina/gate/issues/730) | [План] Ход работы AI-агента в Telegram: статусы, анимации и Stop |
| [#726](https://github.com/markmalina/gate/issues/726) | [G1a→G2] Regression fixture never reaches local write-admission denial |
| [#722](https://github.com/markmalina/gate/issues/722) | [Release] Личный Gate: выпуск серий 2–50, inbound stop и постоянная работа |
| [#718](https://github.com/markmalina/gate/issues/718) | [Reply sequences] Stop pending messages when recipient sends an inbound message |
| [#716](https://github.com/markmalina/gate/issues/716) | [Reply sequences] Поддержать параллельную отправку серий разным адресатам |
| [#712](https://github.com/markmalina/gate/issues/712) | Русская транскрипция: разделение и опознание говорящих |
| [#696](https://github.com/markmalina/gate/issues/696) | [MTPROTO-RU-CLOUD] Полный маршрут AI → российский Telegram через Gate |
| [#689](https://github.com/markmalina/gate/issues/689) | Gate: скачивание Telegram-вложений больше 20 МБ по запросу Work |
| [#663](https://github.com/markmalina/gate/issues/663) | Food #273: emit evidence-only media envelopes for contextual sessions |
| [#629](https://github.com/markmalina/gate/issues/629) | Kakhovich General Cube: allow 30-second AQUB runtime budget |
| [#625](https://github.com/markmalina/gate/issues/625) | P1: reconcile failed Railway deploy and blocked Production verification |
| [#617](https://github.com/markmalina/gate/issues/617) | AQUB handoff: subject-bound Food export/delete lifecycle and terminal receipts |
| [#614](https://github.com/markmalina/gate/issues/614) | AQUB handoff: emit strict versioned Food receipt v1 |
| [#592](https://github.com/markmalina/gate/issues/592) | AQUB MVP: default-off owner photo byte archive |
| [#577](https://github.com/markmalina/gate/issues/577) | AQUB-FOOD-DELIVERY-V2-01: default-off food command acceptance readiness |
| [#570](https://github.com/markmalina/gate/issues/570) | [AQUB-CONSOLIDATION] Reduce Gate to transport and harden delivery |
| [#485](https://github.com/markmalina/gate/issues/485) | [BETA-KATYA-ACTIVATION-001] Activate the isolated “Зубы Кати” Telegram Space |
| [#478](https://github.com/markmalina/gate/issues/478) | Prove root cause of four historical missing reply tasks |
| [#476](https://github.com/markmalina/gate/issues/476) | Classify historical voice debt and text-parity mismatches |
| [#467](https://github.com/markmalina/gate/issues/467) | Independent production acceptance — Alfa transcription |
| [#431](https://github.com/markmalina/gate/issues/431) | BECOME controlled pilot: emit durable provider-disabled intake receipts |
| [#376](https://github.com/markmalina/gate/issues/376) | [BETA-BOT-001] Add isolated BetaViaBot connector |
| [#365](https://github.com/markmalina/gate/issues/365) | [BETA-CONTEXT-003] Serve OIDC-bound encrypted checkpoint batches |
| [#300](https://github.com/markmalina/gate/issues/300) | [GATE-MAYAK-DM-RUNTIME-001] Wire the default-off Oko Mayak Telegram Business runtime |
| [#277](https://github.com/markmalina/gate/issues/277) | [GATE-UNFREEZE-001] Зафиксировать продуктовую платформу Gate и план разморозки |
| [#272](https://github.com/markmalina/gate/issues/272) | [CENTER-CUTOVER] Канонический cross-project pointer — markmalina/center |
| [#261](https://github.com/markmalina/gate/issues/261) | [3.Gate][ALPHA-GROUP-CANARY-001] Make the owner test group answer through AlphaV |
| [#260](https://github.com/markmalina/gate/issues/260) | [3.Gate][TAKE-CAPTURE-CANARY-001] Run one private owner-media canary |

## 10. Полный список открытых PR

Все перечисленные PR остаются OPEN/unmerged. Exact heads — восстановимый исходный код; не нужно собирать его из чатов.

| PR | Название | Full head SHA | Base ref |
| --- | --- | --- | --- |
| [#842](https://github.com/markmalina/gate/pull/842) | Prepare proposed current-main reader window (#841) | `b5bcc065b2c0948bf63dee8cce5a45babfdca57d` | `main` |
| [#840](https://github.com/markmalina/gate/pull/840) | Prepare default-OFF current-main typed metadata reader (#839) | `528d381c617fc8303135e354df3dbc6f574f7137` | `main` |
| [#837](https://github.com/markmalina/gate/pull/837) | fix: matching hard-OFF Watch recovery for PR835 | `731d9bb03b2b8c791afea5c3a2d5210a76e1729c` | `agent/834-watch-off-release` |
| [#835](https://github.com/markmalina/gate/pull/835) | Consolidate OFF maintenance release and fail-closed Watch startup (#834) | `44531dc06f1a0b3438f70b83de110dffa525b3b1` | `main` |
| [#833](https://github.com/markmalina/gate/pull/833) | Wire default-OFF maintenance caller and completed-run verifier (#832) | `283791baa6c7a4c2ef11b3e2d8bd8b96315e1af8` | `agent/830-maintenance-artifact-binding` |
| [#831](https://github.com/markmalina/gate/pull/831) | Separate maintenance receipt preparation and artifact binding (#830) | `720726ceae6fcf27ff89a505d450336d0faf81ea` | `agent/828-maintenance-entry` |
| [#829](https://github.com/markmalina/gate/pull/829) | Add default-off maintenance caller gap candidate (#828) | `d654baa1405a9a0f2c6f0b2432ecf7c0b4725203` | `agent/826-maintenance-operation` |
| [#827](https://github.com/markmalina/gate/pull/827) | Integrate default-off maintenance operation with actual components | `5b730132b12196d7d28fbf1d0439770ddc09b639` | `agent/824-maintenance-ledger` |
| [#825](https://github.com/markmalina/gate/pull/825) | Add fixed single-slot maintenance intent ledger source | `6dca2490c2d579fd105ce4c037f8f0a4614b8871` | `agent/822-maintenance-receipt` |
| [#823](https://github.com/markmalina/gate/pull/823) | Strict pure maintenance receipt consumer | `ca3506ec8ece8a991709af44d19c2b36c8593944` | `agent/820-maintenance-admission` |
| [#821](https://github.com/markmalina/gate/pull/821) | Pure owner-request maintenance admission predicate | `9a4a0d18a3ddbcd538e5830e6723ebae4f8026c8` | `agent/818-recorded-gate-binding` |
| [#819](https://github.com/markmalina/gate/pull/819) | Bind historical Gate source only through explicit recorded identity (#818) | `70e5fff7577ea99539154a2bcb8750df388d2c38` | `agent/816-fixed-maintenance-transport` |
| [#817](https://github.com/markmalina/gate/pull/817) | Add fixed Gate maintenance transport and uncertain-stop tests (#816) | `7e2849144a348be2bfc05de3629a4864da37b2d3` | `main` |
| [#813](https://github.com/markmalina/gate/pull/813) | fix(watch): preserve explicit OIDC OFF at production startup | `c7d7f71013368cd2dbd14adb674cc119b72c7a33` | `main` |
| [#811](https://github.com/markmalina/gate/pull/811) | Prepare fixed Mark caller pause and complete queue component (#810) | `f8aab483af08a1d2a6dc941fc2a7c63a30d5cb2b` | `main` |
| [#808](https://github.com/markmalina/gate/pull/808) | Prepare accepted W1 recovery on actual merged main (#807) | `657b7581f4e7bde5638b93acb7607b702d106082` | `main` |
| [#805](https://github.com/markmalina/gate/pull/805) | Prepare default-OFF Gate-local READ preflight for public Gate (#804) | `42b8dbf110789c545c8b2137c02aaca197a00e92` | `main` |
| [#802](https://github.com/markmalina/gate/pull/802) | Publish accepted W1 forward recovery for source-only CI (#801) | `f9fc876a48952f9163a01ba6ec6a57f6ee0da95d` | `agent/796-w1-guard` |
| [#784](https://github.com/markmalina/gate/pull/784) | Prepare exact forward recovery for PR782 release | `3ef9c56d67bea51772c4960a756226a70d16e2c6` | `main` |
| [#783](https://github.com/markmalina/gate/pull/783) | Gate diagnostic helper: bounded main ref reader, default off | `bb1880a088e8d149b6de2056b415fafaa57cfbea` | `main` |
| [#771](https://github.com/markmalina/gate/pull/771) | Gate745: prepare canonical signed metadata v2 client | `a48344348350cafbac597a1890477f09a3fc57f0` | `main` |
| [#766](https://github.com/markmalina/gate/pull/766) | Watch Voice: early Inbox, fenced Work journal and safe writer boundary | `45ba499a5f1852208ba8a478a606358fd0fe3bd5` | `main` |
| [#762](https://github.com/markmalina/gate/pull/762) | Gate745: disabled fixed-source fixture caller | `e28b1db0776d0dd1f2a20b2247d6179938e4627f` | `main` |
| [#761](https://github.com/markmalina/gate/pull/761) | MTProto RU: durable Work commands and default-off cloud foundation | `cc874f16f328bdbe0143f8ff175e1bb941cbbbc6` | `main` |
| [#760](https://github.com/markmalina/gate/pull/760) | Gate745: default-off hosted fixture controller source | `7ec459467b606ac67ec2110289e9018fa322b8fa` | `main` |
| [#759](https://github.com/markmalina/gate/pull/759) | docs: MTProto RU — ТЗ v3, ревизия исходников и план выпуска (#696) | `d9a2866603d835983fd80e40688ce1c355e17a51` | `main` |
| [#758](https://github.com/markmalina/gate/pull/758) | Gate745: authenticated hosted fixture helper and durable operation ledger | `ac0e2dacefb258cff26095b1fdf58b85ee1c9169` | `main` |
| [#747](https://github.com/markmalina/gate/pull/747) | Reject stale-dialog replies and prepare default-off fixture contracts (#745) | `b975475c1c6c81035e38d9bd13a40d777933e83c` | `main` |
| [#744](https://github.com/markmalina/gate/pull/744) | Work image upload, albums and native Rich media (#742) | `08c499b943b432ab1ceca78c2d6df6bba57174c1` | `main` |
| [#724](https://github.com/markmalina/gate/pull/724) | feat: baseline process observation and legacy freeze before schema migration (#722) | `e1cb39dc7ae49b364787ccdb0a84303d7d9719ff` | `main` |
| [#721](https://github.com/markmalina/gate/pull/721) | docs: полное ТЗ выпуска личного Gate (#716, #718) | `460c27288de20d3a3b9199c816855ebde5b526de` | `main` |
| [#720](https://github.com/markmalina/gate/pull/720) | feat: durable inbound stop and bounded parallel reply sequences (#718, #716) | `af929dd6dd0bf28a9ff29f6945d1265600161c67` | `main` |
| [#719](https://github.com/markmalina/gate/pull/719) | docs(#718): полное ТЗ остановки серии при входящем сообщении | `a05f13658b25ae953c6c852baa7d0ea54eb95703` | `main` |
| [#578](https://github.com/markmalina/gate/pull/578) | Add default-off AQUB food v2 acceptance readiness | `8b1b1fa4f82edc03586898b4b6615da4855f0f51` | `main` |
| [#468](https://github.com/markmalina/gate/pull/468) | Sprint 2A: add default-off Gate downstream adapter | `ee09145a00030f70dfc93ce51ff5c32b214b5116` | `main` |

ДАЛЬШЕ: никто — сохранение паузы завершено; Gate development остаётся на паузе до нового поручения Марка.

# Управляющие тексты Gate до паузы

Сохранены 08.11.2026 18:48МСК. Это исторический контекст, не действующее разрешение продолжать. Актуальное решение — PAUSED_BY_OWNER в #787.

## #787 до паузы

## Актуальный NEXT — #838 / WAITING_NEW_EXACT_OWNER_GRANT
Срез: 08.11.2026 00:37 МСК. Регламент: https://github.com/markmalina/ai/blob/main/WORK-RULES.md .

**#841 завершена, принята α и закрыта.** [Приёмка](https://github.com/markmalina/gate/issues/841#issuecomment-6713303363), [terminal исполнителя](https://github.com/markmalina/gate/issues/841#issuecomment-6713283967), [независимый review](https://github.com/markmalina/gate/issues/841#issuecomment-6713202437).
[PR842](https://github.com/markmalina/gate/pull/842) OPEN / unmerged:
- Head b5bcc065b2c0948bf63dee8cce5a45babfdca57d; tree c485e8ef1c0f446671227c82137e26042d71228d.
- Единственный parent — фактический main e7b3e32998706f7ef19053331466ea6c63105beb. Ошибка старого плана с parent=PR840 исправлена без ослабления admission.
- Восемь added paths относительно main; относительно PR840 изменены только lock и инструкция, остальные шесть файлов byte-identical. α самостоятельно проверил original bytes/Git blobs/digests/pins/parent/tree и фактический CI merge tree.
- [Единственный CI42415738305](https://github.com/markmalina/gate/actions/runs/42415738305), exact head / attempt1 SUCCESS: Node159, Python2986 PASS / 1 SKIP, PostgreSQL17.11, migrations/restore, Docker/nonroot и Windows. Проверены оба raw logs и все45 step statuses. Повторных запусков нет.

**Ближайший шаг — существующая [#838](https://github.com/markmalina/gate/issues/838), новый точный пакет A+B+C.**
A: один hosted metadata READ через accepted PR842; B: только при полном fresh predicate один ordinary merge exact PR835; C: публикация существующего recovery tree с фактическим новым main-parent и необходимыми CI.
READ окно 08.11 00:45–01:30 МСК, latest start01:20. Это PROPOSED_NOT_GRANTED, не live permission. Предыдущий #838 execution terminal/BLOCKED; unused slots не переносятся. До нового owner decision не редактировать PR842 body командой и не запускать reader/merge. Недостаточный MCP route не повторять.
Точные source/target/caps/команда/STOP/readback/recovery находятся в #838; никаких выдуманных artifact ID и provenance. После upload нужны genuine metadata, original bytes и digest.

Принятые версии для B/C:
- PR835 head44531dc06f1a0b3438f70b83de110dffa525b3b1 / treeff461b59716da761a1af55bf3a79f514d55f3965, 10commits/24paths, CI42366773001 SUCCESS.
- PR837 head731d9bb03b2b8c791afea5c3a2d5210a76e1729c / tree0e833e0d8be6e1cbcbb4dee2a30829990b60d21e, matching recovery с обоими Watch hard OFF, CI42369989568 SUCCESS.
- Оба PR OPEN / unmerged. Main e7b3e32998706f7ef19053331466ea6c63105beb / treee17b705e67747f52a937d9f2574b157600c0db9d не изменён. Actual-main-parent recovery пока не существует.

**Реальная ночная оркестрация выполнена:** назначенный writer_841, отдельный frozen reviewer и read-only route_check со своим reader завершены по actual agent status. Source claim RELEASED, активных дочерних агентов/CI/операций0. Контроллер alpha-gate-night-20261107T211600Z COMPLETED / lease RELEASED; [terminal](https://github.com/markmalina/gate/issues/787#issuecomment-6713345713). Нового writer до нового разрешённого результата не создавать.
Существующая «Продвигать Gate» включена: exact schedule Europe/Minsk (UTC+3), 8 запусков 08.11 01:10,02:10,03:10,04:10,05:10,06:10,07:10,08:10. Readback schedule/prompt/enabled подтверждён. Одноразовый технический контроль01:25 проверяет реальный первый run/status; это не второй product controller. Новая серия пока не исполнилась; её успех заранее не заявлен.
Предыдущая серия фактически disabled07.11 в17:15:11МСК после единственного recorded17:10 run со status failed; причина UNKNOWN. Не выдавать предыдущую ночь за восемь выполненных циклов, не повторять этот отказ молча. Новую серию не выключать из-за WAITING_OWNER и не продлевать автоматически.

**Цель W1 NOT_ACHIEVED:** именно выбранная сохранённая Watch note → полный текст100% в одной private Mark issue, duplicate STT0, readback полноты. Затем R1/R2 — запрет устаревшего ответа после durably принятого изменения переписки. Source/CI/merge/delivered counter не заменяют результат. Отдельные расшифровки из других Work сами по себе не доказывают Gate W1.

После #838 остаются конкретные защищённые действия, а не новый core-аудит:
1. Private Mark source merge и конечный caller pause — отдельные точные решения по restricted [Mark131](https://github.com/markmalina/mark/issues/131). Source merge не доказывает quiescence. Gate811 merge не доказан как prerequisite: caller использует immutable executor.
2. Фактические state seed/ACL/protection/auth/durability и maintenance activation — NOT_PERFORMED. Accepted activation patch из [#832](https://github.com/markmalina/gate/issues/832#issuecomment-6705331250):2150 UTF8 bytes, SHA256 d60017be0c1f71041f9a56ad9f4d97ae820db47ed335fd5c005c79b2d1590382, UNAPPLIED.
3. Full Gate hold требует явного downtime consent, fresh runtime fullSHA/deploymentUUID/fleet/target, actual activated executor, window≤15min и maxstop1; затем HOLD_NO_AUTORESTORE. Сейчас NOT_GRANTED / runtime UNKNOWN.
4. Безопасный release/config/resume/cutover требует actual forward/recovery pair, оба effective Watch OFF, writer/inflight/fingerprint/schema/health/fleet proof. PR835 default false сохраняет explicit true: source не доказывает runtime OFF.
5. Exact selected-note selector/eligibility/parts/budget/window и одна STT — отдельный bounded data grant. Метаданные выбранной записи и исторический budget пока UNVERIFIED.

Общее ночное поручение разрешает реальную оркестрацию и безопасную source работу; прежние точные owner gates на main/live/config/credentials/state/DB/данные/STT/messages/payments не отменены. Старый UNKNOWN не сбрасывать/не повторять; grants803/806/807 использованы; PR805/840 окна истекли; S1 NOT_ACCEPTED и AQUB frozen сохраняются. Никакой внешней переписки/Telegram.
Не штамповать повторные аудиты/новые window candidates при отсутствии grant. Только независимый разрешённый результат; иначе no_ready_work и освобождение compute lease. Все private payloads остаются вне public Gate.
Provider READ/main merge/runtime/data/STT/messages из source841 execution =0. Historical Actions estimate$3.800 — история; public standard CI без нового charge estimate. Provider usage и invoice различать.

ДАЛЬШЕ: Марк — подтвердить или отклонить точный пакет #838; α после допуска назначает реального исполнителя. Очередной scheduled control01:10, техническая проверка01:25.
Ниже сохранены утверждённая программа и история; текущий NEXT выше имеет приоритет.

---

## Утверждённый план и роли — 07.11.2026
Марк утвердил [полную программу спринтов](https://github.com/markmalina/gate/blob/94f460c83dc5d466e070fbed4ea047d2057d8fc3/docs/operations/GATE-SPRINT-PLAN-2026-11-07.md) и [текст для Work-исполнителя](https://github.com/markmalina/gate/blob/94f460c83dc5d466e070fbed4ea047d2057d8fc3/docs/operations/WORK828-HANDOFF.md); сохранено в Git [commit94f460c83dc5d466e070fbed4ea047d2057d8fc3](https://github.com/markmalina/gate/commit/94f460c83dc5d466e070fbed4ea047d2057d8fc3), отдельная documentation branch, не main merge.
**Два результата:** полный текст выбранной Watch-записи → затем работающий аудиомаршрут; stale reply не отправляется после изменения durably принятого контекста.
**Утверждённый порядок:** W1 (ближайшая#828 и оставшийся bounded release/first-text маршрут) → R1/R2 защита переписки → W2/W3/W4 → C1 → W5 → Q1/Q2 → S. Прежний общий порядок полной интеграции PR766→PR747 пересмотрен; исторические migrations сохраняются, согласованные head/helper/cutover/recovery обязательны.
**Роли:** этот α Gate только контролирует, планирует и принимает; реализация — Work-исполнитель. [Передача#828](https://github.com/markmalina/gate/issues/828#issuecomment-6703937423) подготовлена; подготовка не claim/запуск. Существующая «Продвигать Gate» не отключена; перед ручным исполнением fresh claims/PR/CI/run/liveness check, без второго writer.
Текущие Gate standard Ubuntu/Windows CI минуты бесплатны; historical estimate$3.800 сохраняется как история, не новый charge/invoice. Утверждение плана не exact main/live/data/STT/message grant.
Ниже сохранён прежний технический receipt/next pointer; volatile состояние перечитывать перед стартом.

---

# Gate: рабочие маршруты Марка

## Исторический указатель — 7.11.26 09:46 МСК (заменён #830)

**NEXT: [#828 — default-OFF hosted caller source + owner installation packet](https://github.com/markmalina/gate/issues/828), READY / UNCLAIMED. Исполнитель ещё НЕ запущен.**
OneunmergedOFFcaller candidate/offlineexecutiontests + concreteownerpacket. LiteralOFF retained, noactivation/realstate/provider/credentials. Fixedcontrol815/owner23397294/repo1293746267/main/attempt1/exactsource+ownercommand+boundedcanonicalgrantcarrier; protectedGateenvironment/readinessownerprerequisites. Do not fabricateactualartifactId: truthfulproduction/postuploadprovenance mustresolvewithin6paths/25min/oneCI≤35min or ONEconcreterepro+STOP. Exactactivationdiff/maininstall/livegrants separate; futuremain/runtimeUUID/fleet/ACL notinvented. Fullscope/STOP828. Newisolatedstackedbase exactPR827head5b730…/tree39eb…; basebranchagent/826-maintenance-operation. No predecessorrewrites;freshclaims/base beforewrites.

**#826 завершён, принят SOURCE/mockintegration/CI и CLOSED/completed.**
[Приёмкаα](https://github.com/markmalina/gate/issues/826#issuecomment-6703537955); [terminal](https://github.com/markmalina/gate/issues/826#issuecomment-6703533561).
[PR827](https://github.com/markmalina/gate/pull/827) OPEN/unmerged/non-draft:
head5b730132b12196d7d28fbf1d0439770ddc09b639 / tree39eb78632873432c5a59482ab843c3e40e6b78c1 / soleparent6dca2490c2d579fd105ce4c037f8f0a4614b8871.
αactualremote6paths/diff/blobs/tree/parent verified, correctedpatch48914bytes/e0bfe05889deed28f0348aa1c64cd534bcec84437e8adaeef5711f65dc0aff26. Legacy18655bytes/prefixhash1609bcc6…unchanged. Actualcomponentscoordinator HELD14provider/10ledger, originalHELDreplayNOOP4/1/0stop, missing/priorUNKNOWN/FAILED/intent/conflictbeforeprovider, race≤1stop, owncap/prestopfingerprint/loss/expiry preserved onmocks.
Independentreviewer firstREJECT oversizedstopACKlostreceipt; [scopedamendment](https://github.com/markmalina/gate/issues/826#issuecomment-6703424889) same6paths/time; correctedACCEPTown157PASS+probes: truthful524289counter/UNKNOWNreceiptpresent/E_BODYTRANSPORT/http200/stop1; initialauthoversizeFAILED/nullobserved/0stop; invalidreason/counters/oversizedsuccessdenied/HTTP503preserved. Temporalnon-success≤15sinflightoverrun isretrospectiveevidenceonly, notnewIOpermission/NOOP. Positivesremainstrict.
[CI42347915031](https://github.com/markmalina/gate/actions/runs/42347915031) exacthead/attempt1SUCCESS: Node157PASS0FAIL0SKIP; Python2986PASS/1SKIP/12subtests/3warnings188.70s; Ruff/Mypy310/PG17.11/rehearsals/migrations/restore/Docker/nonroot/WindowsSUCCESS. αbothrawlogs/allsteps/cleanup/timing read. Windowsmerge6a281dec… actualtree=accepted39eb…/parentsbase+head verifiedα. Run352s/jobs347-66s/startdelays4seach,purequeueunknown. ProviderreportedUBUNTU/WINDOWSbillable0ms; carriedhistoricalActionsestimate$3.800preserved/invoice-localWorkcostunknown. Bothrealagentscompleted/claimRELEASED/ownedactive0; no dispatch/rerun.

**Acceptedsourcepredecessors(allunmerged/uninstalled):**
#824CLOSED [приёмка](https://github.com/markmalina/gate/issues/824#issuecomment-6702913938), PR8256dca2490…/tree0d7d3455a7b681c95b4944ba0b47882371cf7ff2/parentca3506ec…: one-slotCAS/readback/quarantine/oneuseintentcap.
#822CLOSED [приёмка](https://github.com/markmalina/gate/issues/822#issuecomment-6702405762), PR823ca3506ec…/tree1f5e8ca4…/parent9a4a0d18…: strictreceipt/HELD-NOOP-FAILED-UNKNOWN/truthfulnull.
#820CLOSED [приёмка](https://github.com/markmalina/gate/issues/820#issuecomment-6701827356), PR8219a4a0d18…/tree7d44dc8d…/parentJ70e5fff…: grant/context/exactownercommand/time/caps.
#818CLOSED [приёмка](https://github.com/markmalina/gate/issues/818#issuecomment-6701171767), PR819J70e5fff…/treeec0ba30a…/parentH7e284914…PR817: historicalexplicitbinding; rawAlphaCenter/gate/sourceMatches=false/notreleaseReady preserved.
Hashes/context/syntheticfixtures are not actualauthentication/consent/fleet/ACL/durability.

Mainfresh M=e7b3e32998706f7ef19053331466ea6c63105beb/treee17b705e67747f52a937d9f2574b157600c0db9d unchanged. RecoveryR657b7581…/PR808; PR811f8aab483…/PR813c7d7f710…/PR817/819/821/823/825/827 remainunmerged/notinstalled. PrivateMarkPR132124e8834…unmerged; **MARK_MAIN_MERGE WAITING_OWNER** [private131exactpacket](https://github.com/markmalina/mark/issues/131).

**Maintenance OWNER_DECISION/NOT_GRANTED; release HOLD/fullWATCH_QUIESCE NOT_READY.**
ops-maintenance.yml stillUNCREATED/commandunregistered. Statebranch/seedsourceproposal/uninitializedbythisrun, neveractuallyread/created/written; missingfailsclosed. ActualexclusivebranchACL/protection/auth/durability notprovedbymocks; initialization/contentswrite separateexactowner gates. CurrentcoordinatorartifactId suppliedformatonly; actualartifactorigin/ID afterupload mustauthenticate futurecaller. Runtimewriters/inflight/OFFresume/recovery notclosed. Stoppedfleet≠twoflagsfalse/completedexternaleffects; AFE0/currentRliteralOIDCtrue noautorestart/deploy. OldUNKNOWN notreset/retry;803/806/807consumed,805lockexpired/norearm;S1NOT_ACCEPTED/AQUBfrozen preserved.

**W1 product: exactly selectedsavedWatchnote -> onefulltext in ONEprivateMarkissue — NOT_ACHIEVED.**
Selectednote/budget/fulltext/writerinflight unknown; lastscopedruntimeAFE0/OIDCON/ordinaryOFF dated809, notrefreshedthisrun. No mainmerge/actualstate/providerREAD/stop/disable/restart/deploy/config/credentials/DB/realdata/audio/STT/messages/payment. Sourceprogressonly, notnewgeneral audit ofunchangedruntimeblocker.

Марк поручил α Gate повторно проверить спецификацию, аудит01.11 и исходные задачи, подготовить запуск восстановления выпуска и первого Watch-сценария и закончить этот ход планом.

**[Полный новый аудит, проверенный план и первый пакет A/B](https://github.com/markmalina/gate/issues/787#issuecomment-6683393921).**
Это собственный инкрементальный source/contract аудит α, не независимая Delta/runtime/product acceptance.
Регламент: https://github.com/markmalina/ai/blob/main/WORK-RULES.md .

### Подготовительные итерации и следующий шаг — 06.11.2026

По прямому поручению Марка зарегистрированы два отдельных Work-пакета:
- [A1 / #792 — READ-контракт Railway → exact commit](https://github.com/markmalina/gate/issues/792): один source-only вопрос, ≤10предметных источников, выход VERIFIED/NOT_PROVEN с одним следующим шагом.
- [B1 / #793 — source-пакет первого Watch → Mark](https://github.com/markmalina/gate/issues/793): проверка W1 dependency/delta/admission/readback, ≤16предметных источников, выход SOURCE_READY/SOURCE_GAP без STT и выпуска.

**A1 завершён и принят α:** READ_CONTRACT_NOT_PROVEN; [приёмка и подготовленный вопрос Railway](https://github.com/markmalina/gate/issues/792#issuecomment-6683754132), #792 CLOSED/completed. Reader не проверяет prospective exact commit fetch; глобальное отсутствие API/отказ credentials не утверждаются. Отдельный READ не становится автоматически универсальным блокером намеренного релиза. Вопрос подготовлен α без нового Work, внешнего обращения/ожидания/отправки нет.

**Б1–Б3 завершены и приняты α; #793 CLOSED/completed:** [приёмка Б3](https://github.com/markmalina/gate/issues/793#issuecomment-6684553603), [terminal W1_SOURCE_CHAIN_CLOSED](https://github.com/markmalina/gate/issues/793#issuecomment-6684186993), [manifest Б1](https://github.com/markmalina/gate/issues/793#issuecomment-6683682937). Нужный source-разбор закончен. Short-native passthrough сам bytes не усекает; intake даёт duration по исходному файлу, архив/StoredVoice сохраняют и проверяют bytes/hash. Полный decode ffprobe не обещается. Факты selected note и текущий runtime — следующий admission, не новый source gap.

**W1-R1/#794 завершён и принят:** [проект пакета](https://github.com/markmalina/gate/issues/794#issuecomment-6684779402), [приёмка α](https://github.com/markmalina/gate/issues/794#issuecomment-6685587737). ONE_RELEASE_SOURCE_PREREQUISITE: серверный guard exact archived note без scan после delivered/пустоты/expiry. Activation patch не принят к применению, release OFF. #794 CLOSED по подготовительному результату, #736 остаётся OPEN.

**W1-R2/#795 завершён и принят α как source:** [полная приёмка и corrected patch](https://github.com/markmalina/gate/issues/795#issuecomment-6687167835). Guard exact archived note без scan/recovery fallback, durable delivered no-effect, existing UNKNOWN/outbox/cursor сохранены; defaultOFF. α воспроизвёл original и corrected tree, исправил только PostgreSQL restart test, corrected128passed/1skipped16.52s; Ruff/mypy/compile/diffcheckPASS. Accepted tree71387a6754a6446310f7811d1601632114ca92fd; corrected local head2ba2354eea259d5d45061d829a69eb17bcf0d13d не remote. #795 CLOSED/completed по source критериям; PostgreSQL runtime/CI/merge/W1 не приняты.

**W1-R3/#796 завершён и принят α:** [приёмка](https://github.com/markmalina/gate/issues/796#issuecomment-6687969755), [terminal β](https://github.com/markmalina/gate/issues/796#issuecomment-6687778505). PR797 OPEN/not merged, remote headb0ac7825d529e3c4c82c5820f7be198d8105526c/tree71387a6754a6446310f7811d1601632114ca92fd — точный corrected candidate. Все5files/blobs совпали; публикация GitHub-коннектором, n8n operation в Work Б отсутствовала. Один CI42236167782 original attempt1 SUCCESS,2986passed/1skip/12subtests подPG17.11, WindowsSUCCESS; α metadata/steps/logs проверил. PG restart/schema/concurrent/replay покрыты exact source + полным PG набором; индивидуального verbose/JUnit нет. Atlas наPR не запускается. #796 CLOSED/completed, не merge/runtime acceptance.

**W1-R4/#798 source завершён и принят:** [приёмка α](https://github.com/markmalina/gate/issues/798#issuecomment-6689597728), [β + полный patch](https://github.com/markmalina/gate/issues/798#issuecomment-6688532144). Actual approved/checkout/executor/workflow SHA до action; strict local receipt/digest/provenance consumer. Baseb0ac7825, local-only headfc65b7bb3ea362d956704b873f5898387d57ffb3/treeba6613964a4d5de781386fb956957910dd629de8,23983bytes/SHA25652b98233b57f4859fb9f4a230b372b37ce35fd01508902c7b34754925331b1b2. α извлёк patch изissue, восстановил exact tree, прочитал4paths и повторил67tests/0fail/0skip1304ms Node24.22.1; syntax/diffPASS. Source без замечаний; provider transport/Watch/model/schema неизменны; literalOFF/S1OFF сохранены. #798 CLOSED/completed, новый hostedCI ещё не запускался.

**W1-R5/#799 конечный source/CI завершён и принят:** [α receipt](https://github.com/markmalina/gate/issues/799#issuecomment-6690558455), [β terminal](https://github.com/markmalina/gate/issues/799#issuecomment-6690255398). PR797 OPEN/not merged, exact approved heade319328af87ccb5444e6ea505697250d5661d57f/treee17b705e67747f52a937d9f2574b157600c0db9d,12paths. Guard+checks+снятие одного literalOFF только вPR; remainingguards/W1defaultOFF/4S1OFF unchanged. Один CI42254073671 original attempt1 SUCCESS: Node24.19.0 Ops68passed, PG17.11 Pytest2986passed/1skip/12subtests, backend/WindowsSUCCESS. α source/metadata/steps/logs проверил, ownNode68/0skip1826ms. #799 CLOSED/completed; main literalOFF, livegrant/recovery/runtime acceptance не созданы.

**W1-R6/#800 завершён и принят α:** [приёмка](https://github.com/markmalina/gate/issues/800#issuecomment-6691974997), [независимый review](https://github.com/markmalina/gate/issues/800#issuecomment-6691494960), [manifest/patch](https://github.com/markmalina/gate/issues/800#issuecomment-6690934787). Accepted recovery tree0b271d944ab5ad9d79d4826947a5f9e6d433fde1, sole parentfinale319328a;68461bytes/SHA256d511d81a35aeb45db5e7f99ea5f0fe20c39567d90624aef4d59d43116e318cec;14paths. Reviewer independently reproduced tree/app-LKG/protectedscopes; Node68PASS,Python175PASS/3PGSKIP, imports/compile/Ruff/narrowMypyPASS. #800 CLOSED/completed по source/review. Hosted recovery CI/fullsuite/Docker/PG/runtime ещё не приняты; это R(final-PR-tree), не actual merged main.

**Предыдущая review-only передача исполнена:** writer завершил20:20, свежий reviewer20:46–20:47; оба RELEASED. [Передача](https://github.com/markmalina/gate/issues/800#issuecomment-6691320240) и [terminal review](https://github.com/markmalina/gate/issues/800#issuecomment-6691494960). Повторная реализация/review не требуются; внутренний UI/OS state иных чатов не утверждается.

**W1-R7/#801 завершён и принят α:** [приёмка](https://github.com/markmalina/gate/issues/801#issuecomment-6692522732), [terminal Б](https://github.com/markmalina/gate/issues/801#issuecomment-6692409107), [PR802](https://github.com/markmalina/gate/pull/802). Remote headf9fc876a48952f9163a01ba6ec6a57f6ee0da95d/tree0b271d944ab5ad9d79d4826947a5f9e6d433fde1/sole parente319328a;14operations совпали. PR non-draft, baseagent/796-w1-guard. CI42270247802 originalattempt1SUCCESS,2/2jobsSUCCESS: Node68PASS, fullPython2899PASS/1SKIP/12subtestsPASS, PostgreSQL17.11/Docker/migrations/WindowsSUCCESS. α прочитал commit/tree/parent/files/run/steps/оба logs. Windows merge440123af имеет тотже tree; backend literalheadassertPASS. RemainingSKIP/individualPG names quiet log не доказывает. #801 CLOSED/completed; source/CI не заменяют production/actual-parent. Б RELEASED, CIcleanupSUCCESS, активных операций этого исполнения0.

**W1-R8/#803 завершён по согласованномуREAD:** [отчёт](https://github.com/markmalina/gate/issues/803#issuecomment-6693191830), [owner exception/start](https://github.com/markmalina/gate/issues/803#issuecomment-6693081015). Ровно3RailwayREAD22:05:50–22:06:37МСК, targetOK, mutations0. GateOnline/running1/crashed0/total1; activedeploymente4a8cf75-3d86-4443-acb9-15dfaa29b136 SUCCESS/fullSHAafe0c6b8519381a0007c0dcc1c145207b5d54a3f. Live sourceAlphaCenter/gate/main, servicestagednull. Environmentpending1stagedpatch2b950c8b… от18.09, changes[]summary/связьсGateunknown; не применялся/не очищался. autoDeployNOT_EXPOSED/UNKNOWN. Это platformmetadata, не app/schema/Watchquiescence acceptance. #803CLOSED по finiteREAD, claimRELEASED/триcallgrantисчерпан. ОстаточныеautoDeploy/stagedкритерии переданы#804; no merge/deploy/data/STT.

**W1-R9/#804 завершён и принят α поsourceCI:** [приёмка](https://github.com/markmalina/gate/issues/804#issuecomment-6694800871), [terminal Б](https://github.com/markmalina/gate/issues/804#issuecomment-6694252909), [PR805](https://github.com/markmalina/gate/pull/805). Head42b8dbf110789c545c8b2137c02aaca197a00e92/treeaa77dc139fed050c94ca94af4f51f9b55fd4d59b/soleparent285e2bc9, ровно8addedpaths. Reader/tests byte-identical pinnedOpsd1b47bd…, localadmission+provenance+originalbyteschecks принятывпроверенномобъёме. CI42283635367 originalattempt1SUCCESS: Node120PASS, fullPython2924PASS/1SKIP/12subtests, PostgreSQL17.11/Docker/migrations/WindowsSUCCESS; αsource/upstreamblobs/versions/run/steps/обаlogsпрочитал. #804CLOSED/completed, БRELEASED/cleanupSUCCESS. РеальногоhelperREAD0; sourceCIневыдаётruntime/effectgrant.

**W1-R10/#806 actualREAD завершён и принят:** [отчёт+originalreceipt](https://github.com/markmalina/gate/issues/806#issuecomment-6695093250), [ownergrant](https://github.com/markmalina/gate/issues/806#issuecomment-6694928084), [run42289788085](https://github.com/markmalina/gate/actions/runs/42289788085). Одинattempt1SUCCESS,23:36:30–23:36:48МСК, всеsource/reader/artifact/consumer/cleanupstepsSUCCESS. Receipt23:36:44МСК: autoDeployfalse/canEnablefalse/triggers0COMPLETE/pendingEMPTY_VERIFIED/configurationUnchangedtrue; activefullLKGafe0c6b8519381a0007c0dcc1c145207b5d54a3f,1SUCCESS/notstopped/latestMatchesActive. Историческийalias/sourceMatchesfalse/releaseReadyfalseнеисправлялись. OriginalJSON2120bytes/shad6889f3c…=detachedfile=readerlog, ZIPshaсовпалсGitHubmetadata; αactualworkflowGitobject/target/head/tree/run/bytesпроверил. #806CLOSED/completed, claimRELEASED; provider/config/data effects0. Это закрываетmetadataUNKNOWN, не принимаетdeploy/schema/Watchquiescence/STT. Main/PR797/802неизменены.

**W1-R11/#807 завершён/принят:** [отчёт](https://github.com/markmalina/gate/issues/807#issuecomment-6695712600), [ownergrant](https://github.com/markmalina/gate/issues/807#issuecomment-6695327220). ExactPR797e319328a mergedобычнымmergeбезbypass. Actualmaine7b3e32998706f7ef19053331466ea6c63105beb/treee17b705e67747f52a937d9f2574b157600c0db9d=acceptedrelease. Recovery657b7581f4e7bde5638b93acb7607b702d106082/tree0b271d944ab5ad9d79d4826947a5f9e6d433fde1/soleparent=actualmain, PR808OPEN/unmerged;14operationsравныстаромуPR802manifest. MainCI42292604898SUCCESS(Node68/Python2986PASS+1SKIP), recoveryCI42292848859SUCCESS(Node68/Python2899PASS+1SKIP), Atlas42292605030SUCCESS(8808nodes/22428edges), attempt1каждый;PG/Docker/migrations/restore/WindowsPASS. Все5jobs/cleanupSUCCESS, no rerun. #807CLOSED/claimRELEASED. LiteralreleaseOFFснятвsource; ownercontrol751неотправлялся/W1defaultOFF/4S1OFFсохранены. Provider/deploy/config/productionDB/data/STT0, последняяproductionREADдатирована23:36AFE0. Originalalias/sourceMatchesfalse/releaseReadyfalseнеподавлены.

**W1-R12/#809 завершён, gap принят α:** [приёмка](https://github.com/markmalina/gate/issues/809#issuecomment-6696708640), [terminal Б](https://github.com/markmalina/gate/issues/809#issuecomment-6696293473), [обновление794](https://github.com/markmalina/gate/issues/794#issuecomment-6696251163). Боколо00:28получилscopedAFE0/schema0115report, OIDCON/ordinaryOFF,67delivered/3pending, cumulativeclaims1200; последние100из1229Markrunscompleted. Полнаяочередь/inflight/selectednotemetadataUNKNOWN. SourceTOMLhardcodedOIDCtrueподтверждёнα; env-onlyfalseнедостаточен. Нет полногоregisteredquiescenceмаршрутавпровереннойповерхности, не глобальноеотсутствиеAPI. #809CLOSEDпоfinitegapрезультату, claimRELEASED/effects0/releaseHOLD.

**WATCH_QUIESCE/1 / #810 завершён и принят по source/CI:** [α receipt](https://github.com/markmalina/gate/issues/810#issuecomment-6697461497). PR811/CI приняты, Mark adapter остаётся proposal; actualpause0. Следующий startup prerequisite #812 также выполнен/принят — см. текущий верхний указатель. Актуальный next — #814, не старый manualrelay.

Новое поручениеМарка07.11заменяетручнойrelayмеждуWorkавтоматическимцикломдляGate. [Авторизация/настройка](https://github.com/markmalina/gate/issues/787#issuecomment-6697075297): actualsubagents, одинмалыйtechnicaloutcomeнаактивацию/45минмакс, boundedwriters/review,сохранениеиreadbackврепозитории. Не воскрешатьстарыеdisabledночныеgrants. Приowner-onlyprotectedgateготовитьодинконкретныйпакет/WAITING_OWNER; безновогоразрешенияне делатьliveeffects. Пристабильномblockerнеплодитьаудиты/пустыекоммиты. Другиеавтоматизации неизменны.

### Исторический срез 05.11 — актуальные версии см. верхний указатель и #807
- Gate PUBLIC; Ops PRIVATE. Открытие #789 завершено: https://github.com/markmalina/gate/issues/789#issuecomment-6682823809 .
- Current main285e2bc92d20f1b35d2dc7d6f8f77a8812fff9a8/tree794ae2ba700905a5a19fccd33c9d9ed52f90352a. Main CI42092299162/Atlas42092299147 SUCCESS; нового CI не было.
- Scoped production13:54–13:55: afe0c6b8519381a0007c0dcc1c145207b5d54a3f/schema0115; Watch ActionsON/persistentOFF,67delivered/3pending. Новая версия не установлена.
- Current deploy literalOFF; S1 quarantined/NOT_ACCEPTED. Старые UNKNOWN сохранены. Отказ credentials/необходимость reconnect не доказаны успешным read с HISTORICAL_GATE_ALIAS/sourceMatchesfalse.
- PR76645ba499 и PR747b975475 OPEN. Их union имеет2migrationheads; join/integration/helper/cutover требуют отдельного согласованного пакета.


### Свежий scoped read α при приёмке W1-R5
Gate M успешно ответил: production process commit prefixafe0c6b85193, WatchActionsON/persistentOFF,67delivered/3deliverypending, received1/unknown1/rejected1. Это scoped metadata; full deployed SHA/current schema/fleet/autoDeploy/selected note/grant не доказаны. Код новой версии не установлен этим Work. Не смешивать этот read с новым provider/deploy/STT effect.

### Подготовленный порядок, ещё не исполнение
**A/B параллельная подготовка:** конкретный source/commit/release contract (Gate736/Ops346) + один сохранённый Watch original→полный текст в одной private Mark issue. Первый реальный выпуск проверяется вместе с пользовательским результатом, без пустого testdeploy.
Затем ранняя карточка → быстрый Watch с отделённым discovery/STT → stale-reply guard → реальный Work fallback; Calls788 и устойчивость сохраняются отдельными результатами. Одна production mutation lane.

Два уточнения к датированному плану05.11:
1. Watch-часть Q2 включается в W3: текущий последовательный цикл PR766 не может гарантировать раннюю новую карточку во время долгой предыдущей STT.
2. Speech capability Work проверяется в ранней подготовке. claim/start/submit не являются распознаванием; in-flight/UNKNOWN takeover не реализован.

Первый пакет A/B и критерии завершения/остановки — в отчёте. Общий рефакторинг/S1/новые credentials не становятся универсальным prerequisite. Повтор неизменного чтения/зелёного CI не считается прогрессом. A1 и Б1–Б3 завершены/приняты в своих scope; source #793 закрыта. W1-R1/#794 завершён/принят; W1-R2/#795 source завершён/принят после test-only correction α; W1-R3/#796 remote source/CI завершён/принят; W1-R4/#798 source завершён/принят; W1-R5/#799 final source/CI завершён/принят; W1-R6/#800 source/review завершены и приняты α; W1-R7/#801 публикация/CI завершены и приняты α; W1-R8/#803 триowner-approvedmetadataREAD завершены с явнымautoDeployUNKNOWN; W1-R9/#804 source/CIhelper завершён/принят; W1-R10/#806 actualREAD завершён/принят; W1-R11/#807 mergedsource/actual-parentrecovery/CIзавершены/приняты; W1-R12/#809 metadata/livegapзавершён/принят; WATCH_QUIESCE/1 #810 component source/CIготовдляручногозапуска. Новые runtime/provider/DB/STT/message effects0. Накопленный Actions estimate$3.800 — прежний учёт, не invoice.

ДАЛЬШЕ: автоматический α Gate цикл — первыйзапуск01:20МСК, затемежечасно: проверить#810, принятьготовыйрезультат и назначитьследующийразрешённыйшагсубагенту. Маркнепереноситзадачивручную.

---

<details>
<summary>Предыдущий управляющий срез 5 ноября — история, не текущие public/release факты</summary>

# Gate: рабочие маршруты Марка

Единая текущая программа под #277. Поручение владельца от 05.11.2026: привести Gate в порядок через небольшие последовательные спринты с измеримой пользовательской приёмкой. Регламент: https://github.com/markmalina/ai/blob/main/WORK-RULES.md .

**Текущий указатель — 5.11.26 22:57 МСК: handoff новому Work для проверки подготовки открытия Gate / #789.**

[Актуальный полный handoff](https://github.com/markmalina/gate/issues/789#issuecomment-6667839352). Сохранение кода/ledger/CI повторно проверено через GitHub. Новый Work не запущен; Марк переоткрывает его сам. Первый этап — проверка подготовки и конкретного остатка, без public flip/source/CI/production effects до отдельного поручения.

PR790 и PR791 merged. Current main **285e2bc92d20f1b35d2dc7d6f8f77a8812fff9a8**, принятое полное дерево **794ae2ba700905a5a19fccd33c9d9ed52f90352a**. PR CI42089264706, новый main CI42092299162 и Atlas42092299147 — SUCCESS, original attempt1. Реальная установка аудиоинструментов прошла за **39/43 с** вместо прежнего незавершённого шага; оба backend: **2924 passed / 1 skipped / 12 subtests**. Main backend409с / Windows81с / Atlas68с; APT sandbox warning сохранён неблокирующим в517. Старый CANCELLED42075992455 остаётся историческим фактом, rerun не делали.

Два узких runtime-файла исполнителя теперь находятся в Gate; private Ops продолжает сопровождение через reviewed PR. Ops не менялся, deploy и S1 остаются literal OFF. Gate и Ops всё ещё private; public flip, rename/license, новые ресурсы/credentials, provider/deploy/STT/recovery не выполнялись.

[Git scan и долговечный ledger](https://github.com/markmalina/gate/issues/789#issuecomment-6666029141), [Delta515](https://github.com/AlphaCenter/delta/issues/515#issuecomment-6666171645): 632 refs / 4620 commits, 62 находки = **58 несекретных + 4 исторических HTTP-auth вхождения двух значений**. Соответствующие routes уже удалены в exact LKG/current; в коде не нашли действующих provider-key literals из этого набора. Связанный аудиофайл по metadata21:27 доступен только владельцу; [private evidence](https://github.com/markmalina/mark/issues/47#issuecomment-6665964780), аудио не скачивалось. Старые logs/artifacts/issues не прошли сплошной аудит; это не универсальный PUBLIC_READY.

Writer claim6666964366 **RELEASED**, reviewer517 **RELEASED**; фактические назначенные агенты закончили этот этап. Новых эффектов до следующего задания нет. Временная ночная автоматизация не возобновлялась. Накопленная оценка Actions **$3.800**, эта подготовка от3.456 добавила **$0.344** до льгот; CI-fix отдельно+$0.142. Это не invoice/подтверждённое списание.

Root scoped Gate M22:25 после merge: **production AFE0 / поле схемы0115**, Watch67delivered/3pending (received1/unknown1/rejected1), Calls440/326completed/55superseded/59incomplete. Новая версия приложения не установлена; W1/#736, C1/#788, freshness и S1 остаются открытыми по своим критериям. Исторический Railway UNKNOWN не закрыт; истёкший read helper PR783 не переarming.

**Следующий конкретный шаг:** Марк подтверждает открытие именно существующего markmalina/gate с сохранением истории и приватного Ops. Затем контроллер выполняет только разрешённый public flip и проверяет фактические visibility/тот же repo ID/main/эффективные правила GitHub и scoped Gate M. Это не разрешает production release или STT. После публичности следующий продуктовый результат — W1: один полный выбранный Watch текст в одной private Mark issue, через отдельный точный операционный допуск.

## Обязательный контекст
- [Текущее устройство и контракты](https://github.com/markmalina/gate/blob/main/docs/GATE-CURRENT.md).
- [Полный план спринтов и карта задач](https://github.com/markmalina/gate/blob/main/docs/GATE-RECOVERY-PLAN.md).
- [Метрики и стоимость](https://github.com/markmalina/gate/blob/main/docs/GATE-METRICS.md).

P0: Gate PR768 и AI PR6 уже merged; принятые trees совпали с resulting main. Независимый [Delta507](https://github.com/AlphaCenter/delta/issues/507#issuecomment-6656436735), PR CI42015841795 SUCCESS. Main Gate `062ffdd44bfc0780d5940203f8584a7edb117f97`, AI `c9476c488dce76a68edbe0c91b78346aa52110bd`. Push-main CI42017254010 и Atlas42017253809 SUCCESS. [Итог P0](https://github.com/markmalina/gate/issues/787#issuecomment-6656801042). Это подготовка контекста, **0 новых функциональных результатов**.

## Что установлено
Последний scoped срез16:56–17:00МСК подтвердил productionprefixAFE0/Watch67delivered/3pending и selectedreceived/pending безтекста/issue. Calls440 total/326 completed/55 superseded/59 incomplete; причины новых incomplete этим aggregate не установлены. Схема ниже — предыдущая проверка. Срез Gate M 05.11 в 14:08 МСК: production AFE0 / schema0115, Watch 67 delivered / 3 pending; новая версия приложения не установлена. Выбранный полный Watch текст пока отсутствует.

- Watch schedule: 13 runs / 12 интервалов за 02–05 ноября; P50 между запусками 6 ч 58 мин 36,5 с, max 10 ч 18 мин 58 с. Это не задержка конкретного файла. Webhook сейчас не пробуждает Watch.
- Попытка LKG initial-exact как обход **отвергнута**: общий cursor продвигается через пропущенные чужие entries. Queued-first из готового source обрабатывает сохранённую eligible note до scan и не меняет cursor. Предпочтительный W1 — устранить конкретный блокер выпуска, сохранив future-only.
- Дополнение15:04МСК: Calls438 total /326 completed /55 superseded /57 incomplete; новый latest снова без текста. Scoped selector15:12 вернул quota_unavailable; причину не переносить на все записи. Watch selected protected read: received/pending,transcript отсутствует,issue_url=null.
- Calls: 437 архивных записей, 326 completed, 55 superseded, 56 incomplete. В свежей выборке последних 20 — 0 текстов: 11 media_unreadable, 8 unclassified и 1 duration_exceeded. Для последней записи отдельный read-only selector установил quota_unavailable; не переносить эту причину на все остальные.
- Выбранный длинный звонок отклонён до STT по пределу 60 минут; одновременно есть предел 20 × 180 секунд. Intake Dropbox server_modified→Gate received = 14,715 с, n=1; это не STT latency. [Подробность C1](https://github.com/markmalina/gate/issues/788#issuecomment-6656516374).
- Полная защита от устаревшего ответа существует в PR747, не установлена. ChatGPT download не доказывает standalone STT capability.

## Последовательные пользовательские результаты
| Этап | Было → критерий результата | Исполнение |
|---|---|---|
| W1 | У выбранной сохранённой Watch note 0 → 1 полный текст в одной private issue; полнота 100%, повторная STT 0 | #736 — активный |
| W2 | Карточка только после STT → карточка до STT, рабочий защищённый original после archive, дубликаты 0 | #770 |
| W3 | Watch не пробуждается webhook → durable wake и один worker; пилот 3/3, затем n≥20 и предлагаемый Dropbox→card P95≤60 с / max≤120 с | #770 / отдельный bounded этап |
| W4 | Ручные/редкие части → автоматический полный текст в той же issue; полнота 100%, duplicate STT/issue 0 | #736 / bounded этап |
| C1 | Длинный разговор rejected → один полный доступный текст, исходник сохранён, gaps/duplicates 0 | #788 |
| R1 | Полный guard только source → устаревший одобренный ответ не уходит; stale dispatch 0, неизменённый контроль проходит | #745 / PR747 |
| W5 | ChatGPT STT capability не подтверждена → один реальный Work transcript через общий claim в ту же карточку, повторный Gate STT 0 | #770 |
| Q1 | Плохой файл может остановить scan → понятный отказ плохого и обработка следующего допустимого | Отдельные Watch/Calls этапы перед coding |
| Q2 | Discovery ждёт длительную STT → независимая durable очередь, измеренная задержка и восстановление | Отдельный bounded этап |
| R2 | Гонки/старые клиенты не приняты в production → матрица send paths, stale/duplicate отправок 0 | #745, #718/#716/#722 |
| S | Нет свежей общей приёмки → 24–72 ч наблюдений, n/coverage/p50/p95/max, silent loss 0, понятные failures | Итоговая приёмка программы |

Секундные targets — инженерное предложение, не достигнутый SLA. У каждого этапа до реализации появляется issue либо явно ограниченный этап существующей совпадающей задачи, с baseline, target, scope и claim. Порядок C1/R1 можно поднять по готовности, сохранив одного runtime writer.

## Деньги и история
Владелец снял прежний общий финансовый потолок. Расходы переносим между Work без обнуления; сообщаем стоимость и необычный рост, не придумываем новый cap. Это не означает, что технические limits, server policy или provider billing уже изменены.

Actions estimate carried **$3.456**: исходные$3.022 +P0$0.208 +diagnosticCI$0.110 +originalread$0.006 +ref-fixCI$0.104 +correctedread$0.006. Новая программа05.11добавила$0.434; последний read16с/+$0.006. Это estimate до льгот/включённыхминут, неinvoice. ПоследнийGateCI:backend7м06с,Windows1м22с,attempt1SUCCESS; OpsCI85/4с,Security20/39/3с. Обновление17:40МСК: OpenAI Pro linking/claim выполнены по прямой команде Марка; положительный баланс подтверждён в выбранной API organization; [private evidenceMark47](https://github.com/markmalina/mark/issues/47#issuecomment-6658179649). Runtime key→project association и успешная STT этим не доказаны; internal speech policy не менялась. Railwayinvoiceunknown. Provider deployment/config не менялись. [Оценка CI-вариантов и порядок дальнейших результатов17:49](https://github.com/markmalina/gate/issues/787#issuecomment-6661014225): CI перенос не prerequisite W1, новых runners/workflows/инфраструктуры нет.

В issue каждого спринта сохраняются материальные гипотезы, ошибки, решения, frozen SHA, run/attempt, time-to-job-start/execution, расход и фактическое было→стало. Не ждать много итераций для первого отчёта; не писать heartbeat-only comments. SOURCE/CI/merge/deploy не заменяют функциональную приёмку.

## История и нерешённые границы
#731/#736/#770 — Watch; #745 — freshness; #767 — документация; #781 — отдельная NOT_ACCEPTED инфраструктура, не универсальный critical path; #751 — зарегистрированный deployment control; #764/#778/#772 сохраняют свои остаточные обязательства. Не закрывать старые задачи косметически; superseded — только после точного переноса критериев. Старые terminal Delta verdict неизменны.

Ночная deploy attempt41907078570 остаётся UNKNOWN/UNCONFIRMED. Диагностика source/trigger/autoDeploy уже завершена OBSERVED; она не гарантирует exact commit visibility и не восстанавливает утраченный raw error. Старые UNKNOWN остаются открытыми. На17:01 in_progress/waitingGate/Ops/Mark0, Ops/Markqueued0, GateAPI показывает старый чужой queuedCI31127247901 от06.08; не отменяли/не запускали. Новые ресурсы, credentials, subscriptions, AQUB и внешняя переписка не предполагаются.

Продуктовый вопрос: нужны ли ранние Mark issues также для каждого Calls? Пока сохраняем существующий protected archive/Gate M, без автоматической публикации всех звонков. Это не блокирует C1 и текущий W1.


</details>


## #838 до паузы

# #838 — новый точный one-READ → conditional source integration
**WAITING_NEW_EXACT_OWNER_GRANT. Предыдущий execution838 TERMINAL/BLOCKED, не возобновляется.**
Регламент https://github.com/markmalina/ai/blob/main/WORK-RULES.md .
α Gate контролирует; один реальный исполнитель исполняет только после явного одобрения этого пакета.
Current источник: [αaccepted#841](https://github.com/markmalina/gate/issues/841#issuecomment-6713303363) / PR842, [документ](https://github.com/markmalina/gate/blob/b5bcc065b2c0948bf63dee8cce5a45babfdca57d/docs/operations/GATE-LOCAL-READ-PREFLIGHT.md).
Старый STOP https://github.com/markmalina/gate/issues/838#issuecomment-6707293661 сохраняется; unusedslots неcarry. Никаких повторов недостаточного MCProute.

## Зачем / один результат
Получить авторитетные metadata через уже принятыйfixedqueryreader и при доказанном отсутствии auto-release установить принятыйdefaultOFFsource в GitHubmain с actual-parent recovery/CI. Это подготовка W1selectedWatch→полныйтекстprivateMark, не самаSTT/установкаproduction.

## Точные версии
- Currentmain e7b3e32998706f7ef19053331466ea6c63105beb/treee17b705e67747f52a937d9f2574b157600c0db9d.
- READONLY source PR842/headb5bcc065b2c0948bf63dee8cce5a45babfdca57d/treec485e8ef1c0f446671227c82137e26042d71228d/soleparent=currentmain. SourceCI42415738305 долженбытьexactheadattempt1SUCCESSиαacceptance#841 доначала.
- ONLY sourcePRtoMERGE: PR835/head44531dc06f1a0b3438f70b83de110dffa525b3b1/treeff461b59716da761a1af55bf3a79f514d55f3965; полныйscope10commits/24paths; CI42366773001SUCCESS.
- Immutable accepted proposed-parent recovery PR837/head731d9bb03b2b8c791afea5c3a2d5210a76e1729c/tree0e833e0d8be6e1cbcbb4dee2a30829990b60d21e; CI42369989568SUCCESS. НЕmergeэтогоrecovery.
- R(actualmain) покаНЕСУЩЕСТВУЕТ; послеB толькоSAMEtree0e833e…/soleparent=фактическиmergedmain.

## Запрашиваемый новый допуск A+B+C
ОДИНновыйexecution/claim с deadline≤45min отстарта, необходимыеCI≤35min. Startтольковreadwindow сзапасомдля5minworkflow/10minфазыA; не позднее01:20МСК08.11. WindowпровайдерногоREAD08.11 **00:45≤t<01:30МСК**, fixedsource21:45Z..22:30Z07.11, неавтопродление.
Грант запрашивается отдельно к этимточным версиям, не выводится из общегоночногоавтономногоsourceпоручения. Доответа ownercommand/reader/merge0.

### A — один hosted metadataREAD безmerge reader
Послеrecordnewownerdecision/claim/freshchecks выполнить один **replacement PR842body**, точнооднастрока:
`/ops-gate-release-preflight expected-head-sha=b5bcc065b2c0948bf63dee8cce5a45babfdca57d`
Не issue751/815/comment/dispatch; не редактироватьPR805/840; PR842НЕmerge.
GitHub immutableowner23397294/repo1293746267/same-repononfork/openPR/mainbase/editedbody/attempt1/actualhead,parent,workflowtree/window/sourceadmission должныпройтиштатно. ТекущийmainE7/readerheadдрейф→STOPдокоманды.
Толькоодинновыйeffectworkflow Gate-local release read-only preflight, timeout5min/executionA≤10min, existingprotectedgate-production/RAILWAY_TOKENreference; значениясекретовне читать/не копировать/непровижинить. No retry/fallback/OAuth/browser/CLI/additionalMCP.
Неизменныйcore5HTTP:2GitHubmainrefGET+3RailwayGraphQL(auth+двabracketedstate), existing15sdeadlines/body/requestcaps/retry0. GitHubsourcefetch/setup/upload/download отдельно.
Fixedtarget project972b6ab7-988a-421c-abfa-425451ad714f/environmenta148b138-44b3-4349-9d4b-292880503055/service099cbf8c-1d94-43fe-8714-90725bf93998.
Никакихvariablesvalues/rawprovidererrors/commands/triggerIDs вevidence. Onlyredactedcanonicaloriginalreceipt.

ПослеterminalA: проверитьstepsвключаяstrictconsumer (continue-on-errorнеPASS), genuineartifactmetadata/ID/name/currentrun/head/workflow/tree, originalZIPbytes/digest и2originalmemberbytes/detachedhash/canonicalJSON/provenance. Toolartifactdownloadдоступен; настоящиеIDs появляютсятолькопослеupload. Не переписыватьreceipt, не считать CIartifactsоперационнымдоказательством.
Missing/authdenial/expiry/sourceconfig/state/provenanceUNKNOWN→STOP/no повторbodyedit.

### B — conditional ordinary merge ONLYPR835
Выполнятьтолькоприполномfreshpredicate изпроверенногоoriginalreceipt:
exacttarget, autoDeploy=false, deploymentTriggersCOMPLETE/count0/targetMatch, pendingChangesEMPTY_VERIFIED, configurationUnchanged=true, stableA/B,1activeSUCCESS/notstopped/latestMatchesActive иожидаемыйfullproduction/configfingerprint. НикакойtransitionalработыGate, unknown≠empty.
Для ЭТОГОrepo-onlymerge разрешён наблюдаемый HISTORICAL_GATE_ALIASсsourceMatches=false/releasePreflightReady=false, как в прежнем acceptedREAD806; этиполянеисправлять/ненормализовать. Это не futuredeploymenteligibility/identityfetchproof.
ВозрастobservedAtдоmerge≤120s; source/owner/targetcurrentconfig/time/refsfresh. Еслиистёк — STOP, не делатьвторойREAD.
Freshrepo metadata: ordinarymergeallowed/delete_branch_on_merge=false/repoIDowner/public; no conflicts/pendingrequiredchecks/rejectingreview/другойwriter. Не менять settings/bypass.
Один github_merge_pull_request PR835 expected_head_sha44531dc06f1a0b3438f70b83de110dffa525b3b1 merge_method=merge.
Readback PRmerged/actualmainSHA/tree/parents. **Требуемыйactualtreeff461b59716da761a1af55bf3a79f514d55f3965**. Differenttree/unknownmerge→STOPбезreplay/revert.

### C — механическаяactual-parentrecoverypublication
Толькопослеtreeequality: создатьcommit ссуществующимtree0e833e0d8be6e1cbcbb4dee2a30829990b60d21e иоднимparent=actualmergedmain. Ниодногоновогоfilebyte/tree, нификсовпопутно. Одинnewbranch/ordinarymain-targetrecoveryPR; existingmatchingreuse/noduplicate, source837/808branchesнепереписывать/неmerge.
Actualparent/tree/16paths/11originalbytes/digests/retainedmaintenanceOFF/readback. Толькоnecessaryautomaticruns actualmainCI+AtlasAST иnewrecoveryheadCI, attempt1. Старыеgreen842/840/835/837/808неrerun/dispatch. FirstFAIL/deadline/drift→STOP/receipt, безfixloop. Readsourceexpiresне даётправанаREADпосле01:30; repoCIможетзавершатьсятольковграницахобщего45minexecution.

## Recovery / пределы / остаток
В ЭТОМscope recovery приотказе — STOP дальнейшихдействий, сохранениеactualmain/tree/CI иunmergedrecovery. Никакогосамовольногоmainrevert/recoverymerge/productionrollback.
NO deploy/fullGatedowntime/stop/restart/config/sourcebinding/activation/statebranchseedACL/credentials/DB/schema/realdata/audio/STT/Markmerge/Markdispatch/Telegram/messages/payments. Этигейтыотдельно, новаяrealnoteневыбирается. FullWATCHQUIESCE/note/historicalbudget/fleet/ledgerACLостаютсянедоказанными. Толькоcurrentmetadata+GitHubsource.
OldUNKNOWN/reservations/outboxнеreset/retry; used803806807/expired805/840/old838/S1NOT_ACCEPTED/AQUBfrozen/HOLD_NO_AUTORESTOREсохраняются.
Существенныерезультаты/STOP/actualsource/run/clock/usage/cleanup/claimRELEASED здесь иприёмка/NEXTв787. PublicstandardCIбезновойchargeestimate; historical$3.800история,invoiceusageотдельно.
ДАЛЬШЕ: Марк — датьилиотклонить ОДИНновыйточныйдопуск A+B+C; доответа реальныедействия0. Послеответаαназначаетреальногоисполнителяэтогоexistingoutcome.


## #277 до паузы

## Решение владельца

Gate разморожен и становится канонической коммуникационной платформой Alpha V: безопасной омниканальной оболочкой между внешними каналами, личной Alfa Марка, пользовательскими Alfa и сетью Маяков.

Этот issue — один master pointer для новой продуктовой программы Gate. Он не заменяет и не дублирует текущие bounded execution:

- #261 — первый owner-group Alpha canary;
- #260 — private owner-media Gate → TAKE canary;
- #272 — durable Center cutover pointer.

## Зачем нужен Gate

Gate — транспортная и safety-система, а не второй интеллект, CRM, LMS или редактор контента.

```text
provider event
→ durable Gate fact
→ scoped identity / consent
→ Alfa or Mayak decision
→ exact proposal / authorized action
→ Gate delivery attempt
→ terminal receipt
```

## Каноническое владение

- **Gate:** connector identity, OAuth/session boundary, webhook, durable ingress, routing envelope, capability grant, idempotency, external delivery, terminal outcome and transport audit.
- **Alfa:** private intelligence, owner/user memory, life analysis and reasoning.
- **Relations:** Person, identities, consent, relationship truth, interaction meaning, commitments, attention and commercial relationship state.
- **MAYAKI / Oko:** expertise, program, practice, progress, publication workflow, funnel and analytics interpretation.
- **TAKE:** media analysis/edit/render/approval package.
- **Center / Flow:** portfolio, Work, execution, task and receipt control.

Gate may hold bounded compatibility projections, but must not create a second master for those domains.

## Target product tree

1. **Mark private communication**
   - Telegram Business / Secretary;
   - bounded MTProto for capabilities unavailable through the official bot/business surface;
   - voice/media;
   - exact confirmed replies;
   - scoped history projection to Relations and Alfa;
   - ChatGPT / Gate M as an alternative owner control surface.
2. **Mayak user channel**
   - private dialogs;
   - groups, communities, channels and bots;
   - explicit visible agent identity;
   - expert Mayak → explicit opt-in personal Alfa transition;
   - additional Mayaks as isolated expert modules;
   - human handoff.
3. **Instagram / Meta**
   - OAuth/token/webhook transport;
   - one confirmed Reel;
   - bounded insights and normalized comment/DM events;
   - MAYAKI owns content, funnel, CRM and analytics meaning.
4. **Other channels**
   - WhatsApp;
   - TikTok;
   - Messenger;
   - web chat and future connectors;
   - each through an explicit capability matrix, never assumed feature parity.
5. **Alpha Platform**
   - per-user workspace and private Alfa;
   - invitations and closed beta;
   - entitlements, quotas and provider cost;
   - export, correction and deletion;
   - commercial plans without shared account/API credentials.
6. **Shared Gate kernel**
   - workspace and installation registry;
   - channel account / connector / dialog identity;
   - canonical events;
   - capability grants and exact approval;
   - delivery ledger and reconciliation;
   - credential-store interface;
   - retention/deletion;
   - redacted observability;
   - connector SDK.

## Product invariants

1. Mark's private Alfa and private memory are never exposed to another user.
2. A Mayak never silently changes identity into Alfa; transition requires an explicit user-facing handoff and consent.
3. One user Alfa has an isolated workspace and memory scope.
4. Raw communication is not copied wholesale into prompts or Git; memory is structured, sourced, correctable and deletable.
5. Model output never selects a provider account, recipient or administrative permission.
6. Every external capability is default-deny and installation-scoped.
7. Unknown provider outcome is terminal until reconciliation; never retry blindly.
8. Commercial access is through the Alpha application, not shared OpenAI/Telegram/Meta credentials.
9. Channel capability sets are explicit; publishing support does not imply Direct messaging or administration.
10. Center stores metadata/pointers/receipts only, not Gate payload.

## Verified baseline · 2026-07-22

```yaml
gate_main: e3928b53a1d509ca524bcdd654506ae88feec3c4
railway_status: success
latest_exact_head_ci:
  pr: 282
  run: 29932970562
  conclusion: success
open_prs: 0
open_domain_issues:
  - 260
  - 261
center_pointer: 272
mcp_tools_in_code: 11
telegram_business: production_bounded
alpha_group_model: shadow_and_canary_ready_not_accepted_live
personal_mtproto:
  encrypted_owner_session: completed_locally
  runtime_wiring: false
  read: false
  send: false
instagram:
  meta_app: created
  account_oauth_connected: false
  transport_runtime: false
```

Operational counters in README are dated 2026-07-18 and must not be presented as a fresh live snapshot.

## Documentation conflicts to close first

- README points to an old 2026-07-20 commit/deployment.
- README says ten MCP tools; current code exposes eleven after PR #275.
- README says personal Telegram Stage B is not authorized; #269 records completed protected owner enrollment.
- README still presents closed Instagram #273 as a current open pointer.
- historical full technical spec excludes MTProto while the accepted current boundary permits it as a default-off bounded adapter.
- Center PR #14 currently describes Gate as standby/frozen and needs a later metadata-only successor after this Gate plan is accepted.

## Ordered program

### P0 — product truth and capability registry

Repository-only documents and machine-readable registry. No runtime behavior change.

### P1 — finish current Telegram control

Restore Gate M authorization, refresh the 11-tool schema and accept one exact #261 canary.

### P2 — personal Telegram read contour

Official capability audit #279 and provider-neutral bounded-read contract #281 are done. Live runtime remains disabled behind Telegram-terms/compliance and exact owner data gates.

### P3 — Alfa / Relations memory projection

Source-attributed facts, people, commitments, timeline, correction and deletion; no full-history prompt dump.

### P4 — reusable Mayak installation

Private/group/community/channel installation, visible identity, consent, program handoff and human takeover.

### P5 — Instagram

Repository connector contract → owner OAuth → read-only probe → one exact Reel → bounded events/insights.

### P6 — friends beta and commercial platform

Per-user auth/workspace/Alfa, quotas, entitlements, privacy center, support and measured unit economics.

### P7 — additional connectors

WhatsApp, TikTok, Messenger and web through the proven connector contract.

## Current execution claim

```yaml
status: done
execution_id: gate-unfreeze-product-truth-20260722T1356Z
stage: P0-product-truth
branch: agent/gate-unfreeze-product-truth
expected_pr: one repository-only architecture PR
external_effects: forbidden
provider_calls: forbidden
credential_oauth_actions: forbidden
message_delivery: forbidden
publication: forbidden
production_data: forbidden
real_person_data: forbidden
```

## P0 acceptance

- [x] one current product-definition document;
- [x] machine-readable capability registry with ownership and lifecycle;
- [x] root README and PROJECT_HANDOFF reflect current GitHub truth;
- [x] current / target / historical documents are clearly distinguished;
- [x] 11 MCP tools recorded without weakening scopes;
- [x] #260/#261/#272 preserved as pointers, not duplicated;
- [x] validation is repository-only;
- [x] no secret, raw identifier, message, personal payload or production mutation.

## Next pointer

P0/P2 repository contracts remain complete. P1 delivery controls landed through #284/#286; the first live owner-only send exposed a missing request commit, so both exact calls returned `sent, replay=false` and may have produced two identical test messages. The installation was immediately paused.

Safety hotfix #287 / PR #288 is complete: exact-head CI #1011 succeeded, merge `57c35211649be06dffa1b8063e04d9d2fdff7fa8` is live in production, and the regression proves one provider call across two independent request sessions. Railway connector receipt lookup is temporarily unavailable because that separate app connection requires reauthentication; the live Gate fingerprint verifies the deployed commit.

#261 is `waiting_user` only for visual reconciliation: report whether `Alpha Group` shows one or two identical «Альфа на связи.» messages. Keep the installation paused; the next live check requires a new bounded canary execution, not a retry of the incident turn.

#260 remains independently `ready` for its read-only receipt/health inspection. `Строй αV` and `Майя Альфа` remain undiscovered until each sends an explicit Alpha command.

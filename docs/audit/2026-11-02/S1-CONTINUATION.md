# S1 — продолжение диагностики Railway, 2 ноября 2026

Основание: [план из исходного документального коммита030c95bb](https://github.com/markmalina/gate/blob/030c95bb/docs/audit/2026-11-02/PLAN.md).
Предыдущий [S1-RESULT](https://github.com/markmalina/gate/blob/3dd9d3afef9a67d608cdb60aa639aba3aa6aab35/docs/audit/2026-11-02/S1-RESULT.md)
сохранён как историческое состояние до данного продолжения.

## Текущий итог

**S1 BLOCKED; S2 не готов и не начат.** Все **3/3** дополнительных Railway чтения израсходованы. Последнее чтение подтвердило **отказ авторизации `Not Authorized`** для environment; причина отсутствия доступа credential (тип, validity, scope или иная причина) не установлена. Target/token scope и pending changes остаются UNKNOWN. В этом продолжении Gate не выпускался; main остался `86dbc0f429dce089bf733fc422f20f556535895b`. Текущий клиент `6fc78006c5b209dc93ca2904ae349d6401eb922f`, установленный Ops `ec4e689a0fa24305672ef4047ff7c5fac0666982`.

## Граница поручения

Только S1. Новое прямое разрешение владельца — максимум три дополнительных
read-only обращения к прежнему Railway target, каждое с отдельной диагностической
целью. [Запись разрешения](https://github.com/markmalina/gate/issues/736#issuecomment-6575570385)
сделана агентом по текущему поручению; это не утверждение, что владелец лично
написал GitHub comment. Прежний grant6572700446 и original41425190543 израсходованы.
Никаких deploy приложения, config, migration, restart, audio/STT или сообщений.

Отправные рабочие записи: [Gate736](https://github.com/markmalina/gate/issues/736),
[PR766](https://github.com/markmalina/gate/pull/766),
[Ops399](https://github.com/markmalina/ops/issues/399),
[PR404](https://github.com/markmalina/ops/pull/404). К началу продолжения PR404 и
последующий [PR406](https://github.com/markmalina/ops/pull/406) уже объединены;
queue-eligibility исправление установлено, CLI-клиент исправлен. Пройденные source
проверки не выполнялись заново как общий аудит. Новые проверки ниже относятся к
изменённым диагностическим версиям, их установке и фактическим originals; rerun не использовался.

## Восстановленное состояние и установленный недостаток

Старый клиентb83eeffa04926343a9bbc5b2374444e1737a2b11, установленный
Opsf676942a22d1e35544b4463b0674281ea2673c52. [Старый запуск](https://github.com/markmalina/gate/actions/runs/41425190543)
выполнил один запрос и получил STOP/E_PROVIDER/facts:null. [Независимая432](https://github.com/AlphaCenter/delta/issues/432#issuecomment-6574366250)
сохраняется как BLOCKED/VERIFIED_STOP_ONLY, не переоформлена в успех.

Точный код boundedJson позволяет установить: ответ дошёл как HTTP200,
прошёл ограничения размера/UTF-8/JSON, затем содержал GraphQL ошибку
INTERNAL_SERVER_ERROR. HTTP500 этим не доказан. Сырой текст ответа тогда не
сохранялся, поэтому по сохранённым артефактам первопричину старой попытки установить нельзя.

[Официальная Railway API документация](https://docs.railway.com/integrations/api#errors)
поясняет, что этот код бывает также при отказе авторизации. [Официальная CLI
schema](https://github.com/railwayapp/cli/blob/b62467ef1b5303e3e340283ed96425dcf9612993/src/gql/schema.json)
содержит существующие поля и аргументы; [CLI ProjectToken query](https://github.com/railwayapp/cli/blob/b62467ef1b5303e3e340283ed96425dcf9612993/src/gql/queries/strings/ProjectToken.graphql)
использует nested project/environment. Несовместимость исходного query не подтверждена.

Подтверждённый недостаток диагностического кода: безопасная проекция удаляла
путь ошибки и не отличала документированный отказ авторизации от прочих ошибок.
Исправление сохраняет только фиксированные категории PROJECT_TOKEN/ENVIRONMENT/UNKNOWN
и AUTHORIZATION_DENIED/UNCLASSIFIED, HTTP200; ни raw message, ни ID/trace/token не
попадают в квитанции. Категории не превращают STOP в разрешение выпуска.

## Изменения и проверка

- [Ops PR407](https://github.com/markmalina/ops/pull/407), source
  4d023a0011cf154f65658757ff1da1ada9a7d55c: узкая локализация ошибок и допуск первого
  нового original; старые три original обязательны в истории.
- [Source433 ACCEPT](https://github.com/AlphaCenter/delta/issues/433#issuecomment-6575762898):
  31 целевой тест,574 независимых случая; точные CI41434819855/Security41434819887.
- Установленный Ops866bdf7ebaffafbb382d4e28228fecb7878412ea;
  [release434 ACCEPT](https://github.com/AlphaCenter/delta/issues/434#issuecomment-6575837971):
  22 runtime-файла сохранены,31+29 проверок; CI41435609029/Security41435608970.
- Original PUSH CI41436031050/Security41436031057 успешны. PR407 объединён
  обычным fast-forward, без force; это установка диагностического исполнителя,
  не deployment Gate.
- Клиент367654d6bfdca7c62e3151e11001ba2db44cdc0d в прежнемPR766: только диагностический
  workflow и lock,988 остальных blob/mode сохранены; приложение/миграции не менялись.
  [435 EXACT_DIAGNOSTIC_CLIENT_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/435#issuecomment-6576035939),
  [Gate CI](https://github.com/markmalina/gate/actions/runs/41436047343) и
  [Quest](https://github.com/markmalina/gate/actions/runs/41436047344) успешны.

## Первая новая попытка: локализация ошибки

[Original41437551667](https://github.com/markmalina/gate/actions/runs/41437551667), attempt1,
2026-11-02T11:31:49Z; чтение11:32:34.010–11:32:34.290Z.
[Точный запрос](evidence/s1-continuation/nested-query.json) прежний, endpoint/header/variables
сохранены. GitHub workflow завершился failure на semantic gate; observe и verifier
успешно сохранили STOP. Выполнено ровно одно чтение, без retry.

Установлено: HTTP200, data:null, один INTERNAL_SERVER_ERROR,
errorPaths:[PROJECT_TOKEN], errorMessages:[UNCLASSIFIED], facts:null.
Это локализует сбой к projectToken, но не доказывает неисправность nested resolver,
неверный token, отказ авторизации или недоступность environment. Текст ошибки не
равен точному документированному “Not Authorized”; остальные сообщения остаются
консервативно UNCLASSIFIED.

[Независимая436 BLOCKED/VERIFIED_STOP_ONLY](https://github.com/AlphaCenter/delta/issues/436#issuecomment-6576201175)
подтвердила RAW artifact digests/provenance, exact installed source, fresh native
verification и точный replay hosted verifier,8 отрицательных сценариев.
15 jobs пяти других control workflows SKIPPED; competing route не запускался.
[Сохранённые доказательства](evidence/s1-continuation/run-41437551667/provenance.json)
включают все три исходных RAW artifact и отдельный verifier; оригинальные байты
не переформатированы. Hash observation b8ccc3c847e78c9e2a20f8420f5a8834efefcb2ef0511528dfc5ce06d127b4d0.

## Второй диагностический пакет

Отдельная цель после локализации PROJECT_TOKEN: проверить прямые projectId/environmentId
из [официального API примера](https://docs.railway.com/integrations/api#using-a-project-token),
сняв зависимость от nested selections. Это диагностическая альтернатива, не заявление,
что прежний запрос не соответствует schema. [Точный scalar query](evidence/s1-continuation/scalar-query.json).

[Ops PR408](https://github.com/markmalina/ops/pull/408): source de0676dfb9cad5ec5424a0c051fb2a6239b69376,
[437 SOURCE_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/437#issuecomment-6576253687).
34 focused tests и108 независимых негативов прошли; CI41438332607/Security41438332609 успешны.
Только query/parser, учёт израсходованной первой новой попытки, exact second client binding
и связанные тесты/документ. Все906 остальных blobs/modes сохранены.
Новый packet допускает ordinal2 и требует пять original records включая текущий;
первый новый41437551667 обязателен как completed/failure/attempt1, не доступен для повтора.

Release22319196ae57843681d9f191d0c656b3f51ed9d4 установлен в Opsmain обычным FF;
[438 RELEASE_BINDING_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/438#issuecomment-6576338010).
22 runtime files unchanged;25 stage-sensitive tests+29 независимых негативов PASS.
[Release CI](https://github.com/markmalina/ops/actions/runs/41439133831) и
[Security](https://github.com/markmalina/ops/actions/runs/41439133026) успешны.
Клиент0dba814831889586a7141f79d4fbaeae71a54821 — sole two-file child367654d6;
workflow hash c616a328c5b7d22aad654ba52442a877e0c2e00ed907a18ae4122c18d9d0087b,
profile68180d801789ed8b02cdcf649cf8ac1d8ba8ed80601c6368cd657b647ee90b57,
bundlee3fc283ae49fa13097d0aac8f4d208927b3b8a1eab9b7a6eddd78a35efa1b674.

[439 EXACT_DIAGNOSTIC_CLIENT_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/439#issuecomment-6576535150)
проверил exact client,22 независимых negatives,988 неизменённых blobs/modes.
[Gate CI](https://github.com/markmalina/gate/actions/runs/41439589661),
[Quest](https://github.com/markmalina/gate/actions/runs/41439589748),
[Ops push CI](https://github.com/markmalina/ops/actions/runs/41439579177),
[Ops push Security](https://github.com/markmalina/ops/actions/runs/41439579116) успешны;
полные относящиеся логи прочитаны независимым reviewer.

[Второй original41440937308](https://github.com/markmalina/gate/actions/runs/41440937308),
attempt1,2026-11-02T11:58:21Z. Чтение11:59:08.802–11:59:08.945Z: одинPOST,
тот же HTTP200/INTERNAL_SERVER_ERROR/PROJECT_TOKEN/UNCLASSIFIED, data:null,facts:null.
[Независимая440 BLOCKED/VERIFIED_STOP_ONLY](https://github.com/AlphaCenter/delta/issues/440#issuecomment-6576646858)
подтвердила RAW digests/provenance/nativefresh+hostedreplay/8negativecases и15SKIPPEDjobs.
[Доказательства](evidence/s1-continuation/run-41440937308/provenance.json), observation hash
ddd87ae29ea96a2ee49b51a79804199c60beae59eef899d0fefd7ebe4c881452.

Установлено: nested selections не необходимы для воспроизведения отказа.
Не установлено: точная внутренняя причина Railway, ошибка token/auth или недоступность environment.
Одинаковый scalar retry не обоснован. Два новых обращения израсходованы отдельно
от старого41425190543; никаких reruns.

## Пакет для третьего чтения: environment isolation

[Ops PR409](https://github.com/markmalina/ops/pull/409) изолирует фиксированный
`environment(id,projectId){id projectId unmergedChangesCount}` без вызова projectToken.
[Точный query](evidence/s1-continuation/environment-query.json) и
[доказательство поля projectId из официальной схемы](evidence/s1-continuation/official-environment-projectid-evidence.json).
Диагностическая цель — отличить недоступность environment от отказа при вызове projectToken.
Ни один исход третьего чтения сам по себе не устанавливает token identity/exclusive scope.

Source39361b982eb92d99799c251f41e1f428effa1c23 принят
[441 SOURCE_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/441#issuecomment-6576789057):
35 focused tests +396 независимых негативных сценариев,903 прочих blobs/modes unchanged;
[CI](https://github.com/markmalina/ops/actions/runs/41442018208) и
[Security](https://github.com/markmalina/ops/actions/runs/41442018126) успешны.
Изменения ограничены query/parser, явной partial receipt/schema/verifier семантикой,
учётом двух израсходованных новых originals, exact client binding и связанными tests/docs.

Только partial успех: OBSERVED_ENVIRONMENT_ONLY → VERIFIED_ENVIRONMENT_ONLY,
`environmentIdentityMatches:true, countObservation:ZERO, pendingChanges:false,
tokenScopeVerified:false, releaseAuthorized:false`. Старый full-scope result отвергается.
Неизменённый финальный workflow gate отвергает partial как недостаточный для S1.
Шесть original records обязательны; ordinal3 — последний из разрешённых3 новых reads.
Эта версия допускала только третий original и запрещала следующий/rerun. После
её доказанного отказа до Railway учёт уточнён отдельно ниже; попытка не переиспользована.
Ошибки/неизвестность остаются STOP с facts:null.

Установленный Ops58dedb145849bb6015803ad40c04d7be642064bf и
[442 RELEASE_BINDING_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/442#issuecomment-6576870327):
22 runtime files unchanged,25 stage-sensitive tests+29 negatives PASS;
[CI](https://github.com/markmalina/ops/actions/runs/41442755043) и
[Security](https://github.com/markmalina/ops/actions/runs/41442755201) успешны.
Точный клиентdbddabd83784c52e45d938d1fc8026fdcb826998 — sole two-file child0dba8148;
workflow5200ca3ed6dfb6b021d273337f3d6e6d9f58ef01e36ffedb432be35451264715,
profile27dc2fa05485fdb4e46071743229d7dbdc6e0a772b5df451d48e6abb972cc45e,
bundlef72d85c40b2336b98406598e1b1e6d2de893274f41d7304bd84df78b9b6afa29.

[443 EXACT_DIAGNOSTIC_CLIENT_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/443#issuecomment-6577067453)
подтвердил два changed paths,988 остальных blobs/modes, native diff/lock+22 negatives;
[Gate CI](https://github.com/markmalina/gate/actions/runs/41443263845),
[Quest](https://github.com/markmalina/gate/actions/runs/41443263980),
[Ops push CI](https://github.com/markmalina/ops/actions/runs/41443252306) и
[Ops push Security](https://github.com/markmalina/ops/actions/runs/41443252359) успешны.

### Фактический отказ до третьего чтения

[Original41444775597](https://github.com/markmalina/gate/actions/runs/41444775597), attempt1,
создан2026-11-02T12:26:56Z, завершился **ADMISSION_FAILED/E_RESPONSE_SIZE**.
Admission job125275058845 сообщил ошибку12:27:19.8036088Z; observe125275247296 и
verify125275247409 SKIPPED, artifacts0. [444 BLOCKED](https://github.com/AlphaCenter/delta/issues/444#issuecomment-6577242999)
независимо подтвердил0Railway calls. Это не VERIFIED_STOP_ONLY: квитанции наблюдения
нет, native observation verifier неприменим. [Полные доказательства](evidence/s1-continuation/run-41444775597/outcome.json)
включают admission.log и jobs metadata. Неудачный original сохранён как состоявшаяся
попытка workflow; он не был повторён или переоформлен в неиспользованный.

Установлен E_RESPONSE_SIZE в GitHub metadata admission. Конкретный endpoint в логе
не указан. Отдельно воспроизведён структурный дефект: компактный JSON шести фактических
run records занимает74024bytes против общего лимита65536; допустимая растущая история
не помещается. Это восстановленный envelope из metadata, не сохранённый сырой ответ
отказавшего endpoint. Полный provider response limit не повышается.

### Узкое восстановление допуска без увеличения Railway бюджета

[Уточнение записи допуска6577269014](https://github.com/markmalina/gate/issues/736#issuecomment-6577269014)
явно исправляет добавленный агентом предел “3originals”: прямое поручение владельца
ограничивает **3 обращения к Railway**. Уже выполнено2, отказ41444775597 даёт0.
Уточнение не является новым разрешением владельца и не редактирует старую запись;
оно связывает только оставшееся read3 с новым workflow dispatch4. Старые41425190543
и все последующие попытки сохраняются. Никакого четвёртого provider read или rerun. Опубликованный локальный срок
2026-11-03T11:06:00Z сохранён; прежний код вычислял более ранний на4секунды
предел11:05:56Z из created_at старой записи. Это различие явно зафиксировано;
срок не назван заданным владельцем, лимит его3Railway requests не расширен.

[Ops PR410](https://github.com/markmalina/ops/pull/410), source8f3ba1cc16333e2a7d7f6777db0ab4ec6bf1f74e:
только фиксированный GitHub workflow-history GET получает ограниченный262144-byte
reader. Остальные GitHub metadata и Railway сохраняют65536bytes; query/partial semantics
не меняются. Source history теперь требует7records, включая independentlyverified
zero-provider attempt; immutable444 verdict закреплён в source до credential use.
39 локальных тестов прошли: старый64KiB отказ, приём допустимой большой истории,
сохранность других лимитов, header/stream/UTF8/redirect/deadline и запрет повторов.

[445 SOURCE_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/445#issuecomment-6577487640)
подтвердил exact8f3ba1cc,16 paths/904 остальных blobs,39 tests+97 независимых negatives;
[CI](https://github.com/markmalina/ops/actions/runs/41446586206) и
[Security](https://github.com/markmalina/ops/actions/runs/41446586219) успешны.
Reviewer независимо получил все6run records и воспроизвёл74024bytes.
Бюджетprovider2/3 и отдельный workflow0provider подтверждены; исправление границы
history не распространяется на Railway ответ или иной GitHub endpoint.

Release ec4e689a0fa24305672ef4047ff7c5fac0666982 установлен в Opsmain обычным FF;
[446 RELEASE_BINDING_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/446#issuecomment-6577551535).
24 runtime files сохранены,25 stage-sensitive tests+30 negatives PASS;
[CI](https://github.com/markmalina/ops/actions/runs/41447458459) и
[Security](https://github.com/markmalina/ops/actions/runs/41447458460) успешны.
Клиент6fc78006c5b209dc93ca2904ae349d6401eb922f — sole two-file childdbddabd8;
workflow72a8788e6f82d0ce926f7b9cc14170e19ba6b5d836a63150ec36baa64ebcb357,
profile8488b9c397f499c42fdfc8ae674676c1d53e31c39b56e164af3025ca3d41f493,
bundlee942bda0ad8958e4bbbff879473046a58500abfe6c10052bb4daf7577d11bd14.

[447 EXACT_DIAGNOSTIC_CLIENT_ONLY ACCEPT](https://github.com/AlphaCenter/delta/issues/447#issuecomment-6577805046)
подтвердил2paths/988 неизменённых blobs/modes, native diff/lock+22 negatives;
[Gate CI](https://github.com/markmalina/gate/actions/runs/41447982932),
[Quest](https://github.com/markmalina/gate/actions/runs/41447982959),
[Ops push CI](https://github.com/markmalina/ops/actions/runs/41447972056) и
[Ops push Security](https://github.com/markmalina/ops/actions/runs/41447972024) успешны;
все9 полных job logs прочитаны независимым reviewer.

## Последнее фактическое чтение и независимый итог

[Original41449717673](https://github.com/markmalina/gate/actions/runs/41449717673), attempt1, создан 2026-11-02T13:03:38Z; [request6577823952](https://github.com/markmalina/gate/issues/736#issuecomment-6577823952). Точный environment-only query выше, без изменения endpoint/header/target. Интервал единственного provider POST: **13:04:18.729–13:04:18.880Z**. Перед dispatch проверен exact код: GraphQL query, один attempted latch, ноль retry, никаких mutations или запрещённых операций.

Фактическая безопасная квитанция: **STOP/E_PROVIDER**, HTTP200, data:null, один INTERNAL_SERVER_ERROR, errorPaths:[ENVIRONMENT], errorMessages:[AUTHORIZATION_DENIED], facts:null, releaseAuthorized:false. Классификатор выдаёт AUTHORIZATION_DENIED только при точном сообщении **`Not Authorized`**. Поэтому отказ авторизации environment установлен; конкретный дефект credential и причина прежних PROJECT_TOKEN ошибок этим не установлены. Фактический тип credential не наблюдался: Project-Access-Token — контракт кода, не доказательство типа секрета.

Hosted verifier: **VERIFIED_STOP_ONLY**, 13:05:05.750Z; независимая свежая проверка 13:06:49.280Z и root 13:07:52.018Z, exact hosted replay PASS. Admission/observe/native verifier прошли, финальный full-scope semantic gate закономерно завершился failure. Другие15 jobs пяти control workflows SKIPPED.

[Независимая448 BLOCKED](https://github.com/AlphaCenter/delta/issues/448#issuecomment-6578062247) проверила реальные артефакты,68 exact source files, native verifier+8 негативов, полные job logs и241 run records: ровно7 исторических originals, все завершены. Это operational заключение; оно отдельно отметило недописанный тогда финальный отчёт. Этот недостаток документации исправлен здесь, терминал448 не изменяется.

[Архив последнего запуска](evidence/s1-continuation/run-41449717673/provenance.json) содержит исходные3 RAW artifacts, metadata, immutable request/review и отдельные проверки. Observation13143242653:1250bytes/SHA256 `0d10a898d4ca71d0ccfb2bb8c19c45dacc269a2a62a0f78355f167fbe5b405a9`; detached13144296731:72bytes/`2f73855dba4c90048e65061d1debe17f4af41055d66aa5a383b9402909fc28ef`; verification13144451956:676bytes/`be79b30c144153633c6d01407a8f7f9363b8eb26fe65ef57cdd6558af65281db`.

Итог учёта: три новых provider reads41437551667/41440937308/41449717673; дополнительный original41444775597 израсходован как workflow attempt с0provider reads. Старый41425190543 остаётся отдельно spent. Никаких reruns, четвёртого нового Railway чтения или переоформления истории. Конкретный необходимый пакет восстановления доступа — [S1-RAILWAY-DEBUG-PACKET](S1-RAILWAY-DEBUG-PACKET.md). Он подготовлен, но не исполнен.

## Независимый предел готовности S2

Принятый [план](PLAN.md) требует не только fixed target/pending clean, но и применимый
исполняемый маршрут следующего Watch выпуска. Узкая повторная проверка существующих
компонентов подтвердила отдельный пробел:

- [Gate short deploy](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.github/workflows/ops-deploy.yml#L69)
  закрепляет Ops512decbbd81d1d824563af995fc0df435fbf2429; его gate-deploy выполняет
  serviceInstanceDeployV2 и проверку deployment/SHA, но не locked backup/DB identity/schema transition.
- В кандидате railway.toml действительно есть alembic upgrade head. Это означает,
  что deploy может запустить миграцию, а не что безопасный backup/cutover путь существует.
- Gate745 snapshot backup-компоненты существуют, но закреплены на reply0116 и другой
  candidate; release OFF/fixturePENDING. Они не являются отдельной разрешённой
  Watch0119 backup-операцией.
- [Watch contract](https://github.com/markmalina/ops/blob/f676942a22d1e35544b4463b0674281ea2673c52/agent-ops/docs/gate-watch-release-preflight-v2.md#L181-L186)
  прямо требует отдельный контракт migration/apply и говорит, что observer и short
  deploy его не реализуют.

Недостаёт ограниченного Gate-owned Watch0119 release contract: проверенная physical
DB identity → completed locked backup → допустимый переход0115→Watch0119 с сохранением
Actions-режима/журналов → проверка точной установленной версии/схемы.

Подробные независимые source-only доказательства — в
[приложении о маршруте](S1-ROUTE-GAP.md). Файлы short deploy, snapshot profile/release,
Gate snapshot caller и railway.toml сохранены последующими узкими диагностическими
изменениями: сравнения source/client trees исключают изменения этих путей.

## Проверка критериев S1 и предел следующего действия

| Критерий плана | Проверяемый итог |
|---|---|
| Installed diagnostic executor/client | Выполнено; exact commits, original CI и независимые source/release/client verdicts приведены выше. |
| Нужный original доходит до jobs, другие control workflows не вытесняют его | Выполнено на реальных новых originals; admission/observe/verify выполнялись, другие15jobs SKIPPED. |
| Полная fixed-target/token-scope квитанция и pending clean | Все три новых запроса: STOP, facts:null; последний — ENVIRONMENT/AUTHORIZATION_DENIED. Target/token scope и pending clean не подтверждены. |
| Применимый исполняемый Gate-owned Watch0119 route | Не выполнено: отсутствует проверенная связка physicalDB/lockedbackup/Watchmigration/apply/postverify. |
| Независимая operational проверка | Фактически выполнена для каждого завершённого нового запроса, не заменена назначением issue. |
| Готовность/передача к S2 | BLOCKED; S2 не запущен. Последующее действие требует отдельного конкретного пакета, а не простого merge766. |

Последний отказ авторизации установлен по точному сообщению Not Authorized; его credential/account первопричина не установлена.
Если требуется дальнейшее выяснение после трёх разрешённых чтений, пакет для
авторизованного Railway оператора/support должен содержать exact target, header name
(без значения), три точных query documents, UTC observation timestamps и безопасные
error path/code receipts. Необходимо проверить resolver/project-token authorization
по серверной трассировке или сведениям аккаунта. Token rotation, service configuration
и любые дополнительные provider queries этим результатом не разрешаются и не выполняются.

## Конкретный следующий пакет по маршруту S2 — только предложение

Адаптировать имеющиеся Gate745 backup/cutover компоненты под Watch0119, не создавать
новую общую платформу. Зафиксировать exact candidate, target gate-production,
project972b6ab7-988a-421c-abfa-425451ad714f/environmenta148b138-44b3-4349-9d4b-292880503055,
Gate service099cbf8c-1d94-43fe-8714-90725bf93998, PostgreSQL service561d3da3-ddb5-4d3d-8fa2-c9454b0996bf
и volumeInstance7d2de9ed-c63e-454a-811e-4f206f3dfef3 из текущего профиля. Перед применением
проверить их фактическую идентичность и свежесть; это source bindings, не наблюдение БД.

Сначала подготовка disabled-контракта, локальные migration/backup/restore fixtures и
независимая проверка. Затем отдельный конкретный production-пакет с текущей схемой,
флагами, autodeploy, остановкой competing drivers/claims, locked backup, точным SHA,
окном работ и проверяемым возвратом без удаления журналов/автоматического schema downgrade.
Текущий S1 не разрешает выполнять эти операции. S2 не запущен.

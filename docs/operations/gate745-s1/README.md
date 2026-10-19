# Gate745 S1 — центральный SOURCE принят; transport BLOCKED

Срез продолжения 19.10.2026. Координатор — α Gate. SAME execution
`gate-745-snapshot-20261008T1458Z`, единственные #745 / PR747 /
`agent/745-dialog-snapshot`. Поручение Марка — продолжить от
[Ops handoff6265522437](https://github.com/markmalina/gate/issues/745#issuecomment-6265522437).

## Текущее состояние после передачи Ops

- Центральный код Ops принят только как SOURCE:
  `a441e184c98732db49ad5e02b17efa42cb30a5ef`,
  tree `4700a59e999a323ade1ba761bd1f55b4dffd50ad`.
  [Canonical handoff](https://github.com/markmalina/ops/issues/375#issuecomment-6265513072);
  [независимый δ348 SOURCE CODE ACCEPT](https://github.com/AlphaCenter/delta/issues/348#issuecomment-6265473225).
  Старое «Ops не принял зависимость» больше не является текущим блокером.
- PR381 OPEN / not merged; фактический Ops main
  `b2d0587126488cebb0ff48e16ba397cd7e97e27f`.
  Принятый C4 не установлен. Его release остаётся source PENDING /
  reviewedExecutor null / enabled false / fixture PENDING / allocation null.
- Существующий допустимый metadata transport для private Ops не подтверждён.
  Семь workflow Gate main проверены: явной source-read привязки нет.
  Состояние настроек secrets UNKNOWN; отсутствие секрета не утверждается.
- Поддерживающий C0 всё ещё содержит исторический mint для owner AlphaCenter
  с repositories gate/ops/delta и protected App key. Это не доказательство
  доступа к нынешним markmalina/gate и markmalina/ops; AlphaCenter/delta
  остаётся отдельным исходным репозиторием. До source admission App key
  не используется для устранения этого разрыва.
- Gate main проверен отдельно:
  `86dbc0f429dce089bf733fc422f20f556535895b`.
  У raw PR747 base.sha остаётся `461088d68d047e4cd08c7a4f581300d591af241e`;
  эти разные наблюдения не подменяются.
  Исходный head этого docs-этапа:
  `650faddbc9ee0a4b48130ff8ff9396a6697622c1`.
- Guard, workflows, client lock, H2 и source-policy этим docs-этапом не меняются.
  Принятый код нельзя выдать за установленный release pin.
  S1 / central removal criterion / live readonly остаются OPEN / BLOCKED /
  NOT_RUN. S2 packet существует отдельно и не расширяется этим продолжением.

Точный readset, ограничения доказательства, решение для владельца и порядок
снятия блокера: [READ-TRANSPORT.md](READ-TRANSPORT.md).
Машинный срез проверенных исходников: [read-transport-evidence.json](read-transport-evidence.json).
Источник bootstrap/права чтения агентского GitHub connector не передаются
в GitHub Actions и не считаются workflow credential.

## Исторический пакет β S1 до принятия Ops

Вся следующая часть — сохранённый авторский срез предшествующего этапа.
Его формулировки «не принято», «не реализовано» и следующий шаг описывают
то время, а не текущий Ops C4. Сохранённые proposed patch и tests не заменяют
нынешний принятый центральный код и не предназначены для повторного применения.

Срез 19.10.2026. Исполнитель β S1; координатор/следующий владелец — α Gate.
Execution `gate-745-snapshot-20261008T1458Z`; существующие #745, PR747,
ветка `agent/745-dialog-snapshot`. Только S1; S2 не начинался.

## Что готово и что не готово

Подготовлены реестр восьми действующих/подготовленных маршрутов, точные
source/blob/raw digests, воспроизводимые проверки и **частичный предлагаемый
Ops patch из шести файлов**. Патч хранится как материал для владельца зависимости;
он НЕ применён к Ops, Gate runtime, workflows или locks. Это не independently
accepted source и не готовый к запуску readonly маршрут. S1 НЕ завершён.

Причина остановки центральной записи: текущий владелец Ops375/PR380 прямо
сообщил, что пакет745 прочитан, но не принят:
[6264008931](https://github.com/markmalina/ops/issues/375#issuecomment-6264008931).
S1-only уточнение существующей зависимости опубликовано:
[6264132515](https://github.com/markmalina/ops/issues/375#issuecomment-6264132515).
Публикация не означает принятия. Исполнитель не получает роль α Ops.

## Подтверждённые дефекты S1

1. Установленный cutover caller и Ops main требуют `AlphaCenter/gate`/`AlphaCenter/ops`.
   Текущий `markmalina/gate` воспроизводимо получает E_CALLER до доступа к ключу.
2. `action-runner.mjs` после локального admission вызывает `gateGithubFactory`
   до `assertOwnerAuthorizationBody`. Синтетический вызов с неверным digest
   воспроизводит **App factory=1, provider authenticate=0**. После предлагаемого
   переноса admission: **App factory=0, provider authenticate=0**. Это mock proof,
   реальный ключ не читался и токен не выпускался.
3. Pure `assertArtifactProvenance` не проверяет immutable repository identity:
   foreign repository metadata с теми же run/workflow числами принимается.
   Новый отрицательный тест воспроизводит это на canonical source; предлагаемый
   checker отклоняет. Fixed API root сам по себе не заменяет проверку метаданных.
4. Namespace defects продолжаются в requester, protected entry/shared
   `emergency-deploy.makeGithub`, terminal verifier, fixture source admission,
   artifact loader, C0 embedded OIDC/bootstrap и H2 OIDC. Список ниже полный
   для изученного графа, но предложенный patch закрывает только cutover subset.

## Различие installed и current Ops main

Gate main `86dbc0f429dce089bf733fc422f20f556535895b`,
tree `7b5ddc0e6317fc475e2256953a69a07b109463dc`.
Guard head до этого пакета `605f136970a8334b73e2a670f45e42e2514befc8`,
tree `270bc16295496231e48097f4f812efb89983c64a`; PR747 OPEN/unmerged.
Ops patch base `a58d84798d0471792d391987c27a3cd2f6e7bb03`,
tree `889d8f00c861e311efb16d88a86f49006bee7aad`.
На повторной проверке12:42 Ops main продвинулся в
`b2d0587126488cebb0ff48e16ba397cd7e97e27f`,
tree `1441501e84bc4926cd99177bda655f1a59510ced` (отдельный PR380).
Рассмотренные S1 source blobs и шесть patch-base paths не изменились;
это не принятие зависимости745. Точная квитанция375:6264270820.

Установленный snapshot pin — `b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`.
Его `release.json` — blob `e5931366b63523825e521603745907efd15f97ea`:
source ACCEPT/reviewed `6957e465b905f3b26771db32fce48e83d12354a2`,
**enabled=false, fixture=PENDING, allocation=null**.
Текущий Ops main release отличается: blob
`d24afde6020a7f194166bc6c71167e8e398e2519`, source PENDING/reviewed null.
Нельзя описывать main release как фактически установленный и наоборот.
Caller, runner, profile и protected entry на этих двух источниках одинаковы.

У ordinary751 другой pin — `512decbbd81d1d824563af995fc0df435fbf2429`;
его current personal caller технически восстановлен в source. Этот маршрут
разрешает только DEPLOY_CURRENT_MAIN после собственных gates; readonly input
и snapshot/0116 authority отсутствуют. В S1 он не вызывался.

Gate snapshot lock не менялся. Побайтно проверены существующие bindings:

| Поле | Raw SHA256 | Результат |
|---|---|---|
| workflowSha256 | `d73c8eb3e258c2b67395e15a08ed8581b3ff7556090b649e724b4b0925d90684` | совпадает |
| profileSha256 | `6df45d551b0df67c51aa35c1a642fc6b1ad667397a4143fe40813fb590da8d6a` | совпадает с exact pinned profile |

## Реестр reachable путей и владельцы

Machine-readable сведения и точные blobs: [route-inventory.json](route-inventory.json).
Все 104 локально исследованные исходные файла сверены с canonical Git blob SHA;
их raw digests и шесть proposed before/after paths — в
[source-manifest.json](source-manifest.json). Проверка 104 файлов не является
проверкой всего Gate/Ops repository.

| Маршрут | Установка | Достижимый граф / остаток S1 |
|---|---|---|
| Ordinary751 | Gate main, Ops512 | owner23397294 → fixed main → ephemeral Gate token → fixed deploy; отдельный от745 |
| Snapshot745 | Gate main, Opsb099 | requester → caller/profile/lock → runner → protected entry → shared App source adapter → fixed provider; namespace и credential ordering требуют исправления |
| Pure terminal verifier | установлен в snapshot workflow | отдельный job без environment/provider key/App key; fixed metadata GET → pure content graph; metadata identity gap подтверждён |
| P / PR762 | OPEN, не main | old owner310893851; compiled E_CALLER_UNBOUND и `if:false` сохранены; permissions эффекта недостижимы |
| C0 / PR760 | OPEN, не main | embedded OIDC до checkout, source lock, source certificate, reservation/B, isolated fixture; old owner и namespace в нескольких readers |
| H2 / PR758 и guard747 | OPEN, не main | signed RS256 OIDC exact workflow/C0/audience/run → physical identity → durable ledger → synthetic helper; old owner bindings |
| Original artifact loader | Ops main; C0 не установлен | original attempt1 run/job/list + exact named artifacts + archive/raw digests; fixed old Gate root |
| Fixture source admission O | Ops main; B/lock UNBOUND | Ops/C0/H2/candidate source/CI/review → source certificate → отдельный owner grant → reservation; AlphaCenter/delta исторические источники не переписываются |

C0 lock остаётся UNBOUND/enabled=false/source PENDING. Ops fixture-controller
policy отдельно имеет SOURCE_READY/enabled=true/source ACCEPT на прежний
reviewed5652916, но fixture=PENDING/allocation=null; это НЕ native grant.
Не называть эти разные переключатели одним «всё выключено».

Fixed target и все физические IDs сохранены; максимум миграции0116,
раздельные requester316468440/App4578389 и owner23397294, no AQUB,
unknown/UNKNOWN, original-attempt1, finite reads и default-off release остаются.

## Owner-body mismatch и строгая совместимость

Legacy v2 требует digest `d8ae2e0e680c3876dd9baf872e5972e868e3b9b83aa7d2b449633f8935922d61`.
Current issue745 body raw UTF8 digest:
`da37bc133eab3aa0bd21bc3ab700490c9f9c82e9c1b04b50ec0b4f3b150721ae`.
Это воспроизведено заново, а не принято из старого отчёта.

Сохранённая исходная запись существует как immutable Git object:
`markmalina/ops@b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`,
`agent-ops/actions/gate-snapshot-cutover/owner-authorization.fixture.json`,
blob `40da201b018c81b58dc8e93ef829ed7e4ac1e3db`. Digest её body точно
равен прежнему `d8ae2e…`. На current Ops main тот же blob.
**Fixture сохраняет bytes/provenance, но не становится новым живым owner grant.**

В предлагаемом patch bodyDigest не заменён. Legacy reader не принимает current
mutable issue, другой issue или arbitrary comment; default denial сохранён.
Новый immutable-record contract пока НЕ реализован и НЕ согласован. Разработка
должна разделить status и authority явно, а не просто исключить issue-проверку.

Конкретный вариант для owning Gate/Ops review: новый строго версионированный
reader с единственным заранее закреплённым repository ID/commit/path/blob/body
digest исходной записи. Он проверяет точные raw bytes и историческую связь
с прежним принятым profile, а не выбирает произвольный issue/comment. Отдельно
проверяется новая точная effect authority для любого будущего запуска. Старый
v2 reader продолжает только прежний contract и честно отказывает; v2/v3,
неизвестные поля, edited records и смешанные bindings взаимно не принимаются.
Новые C/O/H/P source certificates/reviews требуются после изменения identities;
исторические ACCEPT не переносятся и исторические Delta URLs не заменяются.

**Один вопрос для владельца, только если owning review подтвердит необходимость
изменения authority:** «Согласуешь ли закрепление указанного exact Ops
commit/path/blob/body digest как неизменяемой исторической исходной записи745
для нового строгого reader, сохранив S1 source-only, migration ceiling0116,
fixed target и все отдельные будущие effect grants?»
Этот вопрос подготовлен; согласие не получено и в код не подставлено.

## Предлагаемое исправление и предел проверки

[ops-proposal.patch](ops-proposal.patch) меняет только:
`caller-context.mjs`, `contract.mjs`, `profile.json`, `profile-policy.mjs`,
`action-runner.mjs`, `artifact-verifier.mjs` в existing cutover namespace.
Текущие Gate/owner IDs, typed actor/App, exact workflow/environment/attempt1,
immutable artifact origin и порядок допуска проверены синтетически.
Физические target IDs, mutation ceilings, authorization digest и release bytes
сохранены. Никакого delete/restore operation не добавлено.

Пакет намеренно **частичный и не source-ready**: protected entry и его shared
source checker, requester до чтения private-key, artifact-verifier entry,
fixture/OIDC/admission и supporting readers ещё требуют owning implementation.
Применять patch как готовый инфраструктурный выпуск нельзя. Старые тесты на
AlphaCenter fixtures ещё требуют owning адаптации; full modified-source suite,
hosted Ops CI/Security и NEW independent exact-head verdict отсутствуют.

Проверки автора: **26 PASS / 0 FAIL / 0 SKIP** на Node24.19.0.
Отдельный canonical subset: **11 PASS / 0 FAIL / 0 SKIP** на исходном Ops source;
это baseline, а не full regression предлагаемого patch.
Raw log digests: [test-results.json](test-results.json).
Mock positive source admission не выдаёт real effect authority.

Воспроизведение: материализовать exact canonical paths из source-manifest в
`ops-source`, скопировать в `proposed-ops`, применить patch с `-p1` в этой
второй копии; отдельно материализовать supporting P caller в `supporting-source`.
Затем:

```text
S1_WORKSPACE=/absolute/path/to/materialized-packet node --test s1-source-tests.mjs
```

Harness использует только built-in Node modules и mocks; GitHub/provider fetch
не выполняет. Временный accepted release создаётся только внутри test tmpdir,
не меняет checked-in release и удаляется после сценария. Не использовать
test fixture как операционный grant.

## Условия продолжения и передачи

1. α Gate получает фактическое принятие S1-only зависимости текущим α Ops.
   Отдельная запись375 уже существует; новую product issue/ветку/PR не создавать.
2. Owning Ops Work завершает все связанные source seams и strict authorization
   compatibility в принятой существующей lane, сохраняя default-off gates.
3. Exact Ops head/tree → необходимые original CI/Security → NEW independent
   Delta attempt с predecessor. Ни315, ни исторические cutover ACCEPT не подходят.
4. Только после независимого принятия exact Ops source α Gate обновляет
   существующий client lock/workflow в747, проверяет raw profile/workflow digests
   и получает новую exact-head проверку изменённого Gate candidate.
5. Source-ready и live readonly указываются раздельно. S1 не даёт разрешения
   на новый operational запуск; без отдельного разрешённого запуска и receipt
   live readonly остаётся NOT_RUN/NOT_PROVEN.

**Следующий владелец — α Gate. S1 BLOCKED; S2 ждёт отдельного поручения Марка.**
Ни provider UI/API fallback, ни credentials, resources/costs, native fixture,
Production/migration/cutover/send, ни фоновое исполнение не запускались.

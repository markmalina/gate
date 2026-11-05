# Gate745 S1: допуск существующего metadata transport

Статус: **BLOCKED_EXISTING_BINDING_UNPROVEN**. Это технический пакет решения,
а не разрешение на credential action, protected run, установку или native.
Источник поручения — прямое продолжение Марка от Gate7456265522437.
Канонический исполнительский контекст — #745 / PR747 /
`gate-745-snapshot-20261008T1458Z`.

## Что проверено и что остаётся неизвестным

| Предмет | Наблюдение / предел |
| --- | --- |
| Ops C4 | SOURCE CODE ACCEPT δ348 на a441e184c98732db49ad5e02b17efa42cb30a5ef; PR381 OPEN, не installed |
| Ops actual main | b2d0587126488cebb0ff48e16ba397cd7e97e27f; accepted C4 не является этим main |
| Gate main | 86dbc0f429dce089bf733fc422f20f556535895b; все 7 workflow прочитаны |
| Gate snapshot workflow | Старый pin b0993f9, AlphaCenter namespace, Gate github.token, protected reviewer key; отдельный source-read input/env не wired |
| Gate committed workflows | Не содержат AGENT_OPS_SOURCE_READ_TOKEN / source-read-token / GATE745_SOURCE_READ_TOKEN binding |
| Secrets/settings | Не читались. Наличие подходящего существующего binding UNKNOWN, не ABSENT |
| Agent connector | Repository reads доступны; это не credential, который можно извлечь или передать workflow |
| C0 proposed source-reader | 7ec459467b606ac67ec2110289e9018fa322b8fa: owner AlphaCenter, repositories gate/ops/delta; mint из protected reviewer key |
| Поддерживающие P/C0/H2 | 762/760/758 OPEN; их старые ACCEPT не удостоверяют новые identities/readers |
| Live readonly | NOT_RUN / NOT_PROVEN; repository GET через connector не является запуском Gate control |

Raw PR747 base.sha461088d6 и current main86dbc0f4 получены разными exact GET.
Ни один не переименован в другой. Здесь не выполняется merge/rebase.

## Два разных потребителя capability

**A — snapshot requester/cutover source admission.**

C4 использует `source-read-token` → `AGENT_OPS_SOURCE_READ_TOKEN`.
Только этот отдельный token обслуживает фиксированные Ops
commit / original push CI / Security / release-only compare reads.
Gate `github.token` обслуживает только Gate.
Синтаксически непустой input не доказывает разрешения на чтение private Ops.

| Subject | Минимальные права чтения / фактический readset |
| --- | --- |
| markmalina/ops, ID1331657982 | contents:read, actions:read; exact commit, CI/Security workflow runs и release-only ancestry compare |
| markmalina/gate, ID1293746267 | Его job token; contents/actions/issues read в существующем workflow; exact source/current command и candidate proof |

**O — отдельный supporting fixture source admission.**

`makeSourceReader` принимает `GATE745_SOURCE_READ_TOKEN`. Его фиксированный
readset включает три репозитория:

| Subject | Читаемые API-семейства |
| --- | --- |
| markmalina/ops, ID1331657982 | git/commits, actions/runs, policy-only compare |
| markmalina/gate, ID1293746267 | git/commits, actions/runs, commits/main, pulls/747, issues/745 и её ограниченная история |
| AlphaCenter/delta, ID1345743768 | Только exact source-review issue и immutable comment, выбранные допущенным source packet |

O ограничен 37 уникальными GET и не более 5 страниц owner history;
ответ ≤1MiB, timeout15s, redirect:error. Эти ограничения взяты из C4 reader,
а не выданы за проведённый live тест.
Нельзя расширять issue history/реальные сообщения ради настройки доступа.

Для Gate `GET pulls/747` текущая официальная документация допускает
contents:read как альтернативу pull_requests:read; поэтому дополнительные
PR write/read права здесь не запрашиваются.
Delta требует issues:read; Gate O также требует issues:read.
Широкий PAT `repo`, все репозитории владельца или право записи не являются
предложенным решением.

Один token O должен доказуемо покрывать свой точный readset; иначе нужен
отдельный reviewed split transport. Существующий C0 action mint относится
к одной owner installation. Простая замена owner AlphaCenter на markmalina
не делает AlphaCenter/delta доступным. Это вывод из разных владельцев и
схемы pinned caller, не результат реального token403.

## Что нельзя считать устранением блокера

- Source ACCEPT, private action download или sharing не подтверждают API scope.
- Доступ connector к Ops/Delta не передаётся GitHub Actions.
- Наличие secret с подходящим названием не доказывает issuer/scope/expiry.
- Нельзя брать protected reviewer key раньше source admission, mint им token
  «для проверки» или использовать его как fallback.
- Нельзя подменять исторический AlphaCenter/delta другим owner.
- Нельзя provision/copy/rotate credential по общему «продолжай».
- Старый C0 mint не является уже допущенным нынешним transport.
- No-allocation/native/Production gates и v7 NOT_APPROVED сохраняются.

## Конкретный результат, нужный от capability-владельца

В существующей Ops375, без новой implementation issue/PR, требуется один
редактируемый только по фактам metadata receipt:

1. Название существующего binding и место его доступности; **без значения**.
2. Ссылка на уже действующее разрешение его issuer/use; точные repository IDs,
   owner/installation и read permissions, lifetime/revocation policy.
3. Раздельная совместимость с потребителем A и потребителем O; не переносить
   положительный результат A на O.
4. Доказательство доступности из exact calling workflow/environment и
   допустимости use до protected reviewer key.
5. Если binding не установлен или не подходит — честный UNKNOWN/UNAVAILABLE,
   затем отдельное решение Марка о credential этапе; никаких значений
   в issue, source, artifact или чате.

В этой сессии нет поддержанного API для инвентаризации/настройки Actions
secret bindings. Secret endpoint не вызывался, ключи не читались.
Source-only scope текущего handoff запрещает новые credentials.
Поэтому existing binding сейчас не может быть доказан ни по коду,
ни по историческому report. Это точный внешний prerequisite.

**Подготовленный вопрос Марку:** разрешить ли отдельный этап допуска и настройки
только readonly metadata transport для указанных A/O readsets, с приоритетом
существующего доступа, без получения значений credentials в чате?
Такое решение не разрешает native, resources, Production или отправку сообщений.
Способ фактической настройки, issuer и exact binding должны быть представлены
и приняты отдельно до credential mutation; этот пакет не является таким grant.

## Проверяемое продолжение после появления допустимого binding

1. Проверить receipt/срок/issuer/readset и exact Ops source/release gates.
   Нужны original push CI/Security для actual source и separate release
   admission; PR-CI C4 недостаточно.
2. Только после accepted installed release согласованно обновить три
   snapshot action refs и requester, lock repository/commit/v3 profile raw hash.
   Не ставить a441e184 как installed pin заранее.
3. Добавить exact-client-sha checkout перед requester,
   persist-credentials:false; wired explicit source-read input/env из
   реально принятого binding. Сохранить отдельный Gate job token.
4. Исправить active artifact repository bindings на markmalina/gate.
   Пересчитать workflow raw SHA256 после окончательных bytes.
5. Обновить P/C0/H2 current identity/OIDC и historical-v3 compatibility
   в их существующих lanes; O policy остаётся отдельным UNBOUND admission.
   Не сливать supporting source ради bootstrap управления.
6. Новые exact-head checks и независимая попытка δ. Старые SOURCE ACCEPT
   не переносятся. Actual readonly остаётся отдельным разрешённым запуском
   с его schema-valid receipt; S1/source не означают live readiness.

## Источники

- [Gate745 передача](https://github.com/markmalina/gate/issues/745#issuecomment-6265522437).
- [Ops canonical handoff](https://github.com/markmalina/ops/issues/375#issuecomment-6265513072).
- [Независимый δ348](https://github.com/AlphaCenter/delta/issues/348#issuecomment-6265473225).
- [C4 source reader A](https://github.com/markmalina/ops/blob/a441e184c98732db49ad5e02b17efa42cb30a5ef/agent-ops/actions/gate-snapshot-cutover/github-source.mjs).
- [C4 source reader O](https://github.com/markmalina/ops/blob/a441e184c98732db49ad5e02b17efa42cb30a5ef/agent-ops/actions/gate745-fixture-controller/source-admission.mjs).
- [C0 exact workflow](https://github.com/markmalina/gate/blob/7ec459467b606ac67ec2110289e9018fa322b8fa/.github/workflows/gate745-fixture-controller.yml).
- [GitHub GITHUB_TOKEN scope](https://docs.github.com/en/actions/concepts/security/github_token).
- [GitHub GET pull request permissions](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request).
- [GitHub App token owner/repositories contract](https://github.com/actions/create-github-app-token#inputs).

Полный canonical C4 и δ-аудит здесь не повторялись. Новый собственный результат —
точный committed-binding inventory, A/O readset и граница UNKNOWN/proposed
owner decision. Этот пакет не принимает S1 и не выпускает source.

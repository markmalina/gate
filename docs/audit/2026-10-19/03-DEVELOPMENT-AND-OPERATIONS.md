# Разработка, проверка и эксплуатация

Source baseline: `86dbc0f429dce089bf733fc422f20f556535895b`. Эти инструкции помогают подготовить изменение. Они не заменяют root AGENTS, текущий issue scope, exact owner grant или pinned Ops contract.

## 1. Первый вход разработчика

1. Проверить владельца и repository ID. После transfer текущий Gate — markmalina/gate.
2. Прочитать root AGENTS.md, WORK-PARENT.json и docs/work-bootstrap.md; текущую policy Center читать по действующему указателю, не менять её из Gate.
3. Проверить current main, relevant issue body **и новые comments**, PR head/changed files/CI, execution lease, latest receipt/next pointer.
4. Найти существующее исполнение этой цели. Один outcome — один issue/lease/branch/PR; новая документация аудита живёт в #767.
5. Для server-side model/transcription changes загрузить pinned use-model-access skill, model-access.lock и exact project policy до изменения provider кода.
6. Если процесс уже running, не начинать конкурентный writer. Просроченная lease не доказывает, что старый процесс остановлен.
7. Определить source-only scope, тесты, acceptance и отдельные effect gates.

Назначенная репозиторию роль α управляет требованиями/решениями; audit Work не принимает существующие задачи #736/#745/#696 вместо их владельцев и не выступает независимой δ для собственной работы.

## 2. Среда разработки

Основной подтверждённый CI runtime: Linux, Python3.12, PostgreSQL17, FFmpeg/ffprobe. На Windows есть отдельная DPAPI enrollment regression. Рекомендуемый source bootstrap для PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c constraints/ci.txt '.[dev,contact-sync]'
.\.venv\Scripts\python.exe -m pip check
```

Это установка зависимостей разработки; она не запускает provider/runtime. Требуется доступ к dependency index. Для personal Telegram optional extra применяется только в принятом scope. В Docker основной image устанавливает personal-telegram и добавляет src в PYTHONPATH; обычный wheel включает app*.

Тестовая DB должна быть отдельно созданной disposable PostgreSQL17. DATABASE_URL обязан указывать именно на неё. Нельзя подставлять production URL или автоматически подтягивать secrets для прохождения тестов.

```powershell
$env:GATE_ENV = 'ci'
$env:GATE_DEFAULT_MODE = 'manual'
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@localhost:5432/gate_disposable'
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\pytest.exe
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe check
```

Этот URL — синтетический локальный пример, не действующая credential. Убедиться в отсутствии реальной .env и provider credentials в тестовом процессе. Прямой старт app с настоящими credentials может запустить webhook reconciliation и credential-driven Alfa worker.

`alembic downgrade base` относится только к CI/disposable DB после проверки её identity. В real-use storage downgrade может уничтожить journals и не является процедурой rollback.

## 3. Как выбирать проверки

| Изменение | Достаточные предметные проверки перед общим gate |
| --- | --- |
| Документация | Ссылки, JSON, source refs, hashes, no sensitive payload, противоречия; runtime suite не доказывает prose |
| MCP/HTTP composition | ASGI create_app route order, tools/list/call, schemas/scopes, disabled feature, auth negative cases |
| SQL/store/lease | Реальные независимые PG sessions, concurrency/CAS, rollback, isolation, restart |
| Ingress | Secret/envelope, duplicate/mutated replay, receipt-before-ACK, each update class, poison/dead-letter |
| Send/document/sequence | Exact target/digest, duplicate/lost ACK, unknown, no extra I/O, inbound race, cross-account |
| STT/billing | Source/part/output bounds, budget before I/O, quota/transport unknown, usage settlement, no retry |
| Media | Реальный decoder fixture, magic/format limits, timeout/cancel/orphan/temp cleanup |
| Migration | Union heads, upgrade с принятой prior schema, alembic check, restore drill; downgrade disposable only |
| Enrollment/custody | POSIX permissions/DPAPI и exact source/peer identity без live credentials |
| Deployment/workflow | Immutable pins, issuer/actor/repo/environment/revision, exact admission, safe terminal receipts |

Pytest file count или green mock test не доказывает concurrency, real decoder или live product. Указывать skipped tests и доступность PG отдельно.

## 4. Действующий CI

generic `.github/workflows/ci.yml` запускается на PR open/synchronize/reopen/ready и push main. Backend пропускается для draft PR; Windows enrollment job не имеет того же draft gate. Workflow не содержит общего docs-only path filter, поэтому статус draft CI нельзя называть полным backend pass.

Backend проверяет:

- literal PR head checkout;
- runtime sensitive-literal guard;
- dependency install/pip check;
- Ruff; mypy app (strict с явными исключениями);
- pytest с PostgreSQL17 и FFmpeg;
- delivery-free Alpha rehearsals/incident/retention/admission evidence;
- Alembic upgrade, schema drift;
- disposable pg_dump/pg_restore evidence;
- downgrade disposable schema;
- web и contact-sync Docker builds, UID!=0.

Есть отдельные BECOME I4/Quest/W9 и Atlas workflows. У каждого свои triggers/gates; отдельный зеленый subset не заменяет required full checks. CI не подтверждает Production fingerprint и не является owner product ACCEPT.

Для исходного main [run 38718360230](https://github.com/markmalina/gate/actions/runs/38718360230) успешен. Backend job 116366494513: 2914 passed,1 skipped,12 subtests; mypy309 files. Это исторический проверенный baseline от 15.10.2026.

## 5. Изменение исходников

- Писать в принятую branch, сохраняя current main ancestry.
- До изменения storage определить actual migration head и конкурирующие PR; числовое имя migration не является unique authority.
- Не переиспользовать canonical model/route/status только из-за совпадения имени.
- Прочитать вызывающий entrypoint, SQL store, последний HTTP/provider boundary и соответствующие negative tests.
- Ограниченный diff не должен включать namespace/policy cleanup, secrets provisioning или активацию соседней функции.
- Сохранить exact base/head/tree, scope, tests, skips, non-claims и next pointer.
- Повторять проверки после нового изменения или обнаруженного failure, а не ради ещё одного green run.

В этом пакете [verify-audit.mjs](verify-audit.mjs) проверяет документные индексы и immutable source snapshot без Python dependencies. Запуск из checkout audit branch:

```powershell
node docs/audit/2026-10-19/verify-audit.mjs
```

Скрипт использует git show на закреплённом baseline, поэтому не сравнивает новую README с её прежним blob. Он не вызывает провайдеры и не запускает приложение. В аудите эквивалентные in-memory checks выполнены; сам CLI файл не заявляется локально запущенным в текущей среде.

## 6. Выпуск

Новейший root AGENTS описывает независимый Gate-only requester в `.github/workflows/ops-deploy.yml`, control issue#751, pinned action из markmalina/ops. Предыдущий TEAM_WORKFLOW с automatic deploy-on-main — исторический противоречащий слой, не основание обойти текущие gates.

Форма точной команды, **только после принятого release scope**:

`/ops-deploy production target=gate expected-source-sha=<полный проверенный текущий main SHA>`

Workflow проверяет immutable owner identity, original attempt, main/control issue, точную форму команды и использует ephemeral GITHUB_TOKEN для source read. Target AQUB/all запрещён. Простой deploy не разрешает config/credential provisioning, migrations вне принятого plan или #745 cutover.

Квитанции 19.10 по #731/#736 сообщают Production afe0c6b8519381a0007c0dcc1c145207b5d54a3f и autoDeploy=false. AutoDeploy/config остаются чужим датированным observation. Позднее собственный scoped Gate M status подтвердил fingerprintafe0c6b85193 и ordinary Watch worker off; он не проверяет autoDeploy/secret bindings. Их нельзя превращать ни в обещание автоматического выпуска, ни в доказательство сегодняшних secrets/startup/migrations.

## 7. Приёмка runtime

После отдельно разрешённого выпуска подтверждать раздельно:

1. Exact deployed source revision и accepted schema/effective config.
2. /live, /ready и redacted worker health.
3. Нужный MCP tools/list и настоящие поля клиента.
4. Default-off/paused boundaries остальных функций.
5. Один exact bounded user outcome с разрешёнными caps, durable receipt и replay.
6. Unknown/rollback condition и фактически завершённый rollback при срабатывании.

Без актуальных owner-only activation facts standing autonomy не позволяет выводить release authority из фразы «полный аудит». Поэтому #767 не merge/deploy и не запускает live smoke.

## 8. Инциденты

| Наблюдение | Следующий технический шаг |
| --- | --- |
| /ready503 | Разделить DB unavailable, migrations mismatch, timeout; сверить exact source/schema через approved readonly route |
| /ready200, нужная очередь не движется | Проверить именно worker/task/lease/progress и её enabled mode; не утверждать product healthy |
| Send unknown | Сохранить attempt; read-only reconcile точной операции; не пересоздавать отправку |
| STT quota/rate rejection | Typed code и budget reservation state; новый provider attempt только по принятому recovery scope |
| Watch GitHub unknown | Сверить exact writer/body/receipt across all pages; отсутствие receipt не разрешает POST |
| Migration failure | Сохранить failed/source/deployed schema; не делать destructive downgrade реальных journals |
| Namespace/secret binding unknown | Подготовить safe identity/presence receipt через owning operation; не копировать credential из другого проекта |
| AQUB path в старой инструкции | Применить frozen boundary; не запускать старый operational workflow |

Rollback — заранее записанный forward-compatible application/config plan с сохранением данных. После voice recovery migrations возможен forward application revert, а не слепой deploy старого image, который не знает DB head.

## 9. Передача следующему Work

В текущем issue: автор/адресат/роль, цель, границы, exact source/PR/tests, execution/lease, source/runtime/acceptance отдельно, blocker и next action. Ссылка должна быть самодостаточной. Issue publication не доказывает доставку и принятие другим Work. Не создавать фон/автоматизацию без отдельной задачи и проверки дублей.

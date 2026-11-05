# Доказательства, метод и охват

## Зафиксированная основа

| Предмет | Evidence |
| --- | --- |
| Repository | markmalina/gate, ID1293746267, private; owner markmalina; main |
| Main |86dbc0f429dce089bf733fc422f20f556535895b от 2026-10-15T10:10:25Z |
| Tree |7b5ddc0e6317fc475e2256953a69a07b109463dc, recursive=true, truncated=false |
| Blobs |943, суммарно 8637975 bytes |
| Проверка | Git SHA-1(header "blob N\\0"+UTF8 bytes) и size каждого файла;0 mismatch |
| Snapshot tasks |34 issues без PR,14 openPR до собственного#767 |
| Комментарии |461 issue comments; дополнительно 14 PR issue-comments и 2 submitted review records |
| Branch enumeration |7 страниц:100+100+100+100+100+100+16=616 до audit branch |
| Recent history |30 commit records от закреплённого main; полная Git history не аудировалась |
| Main branch protection |REST branch snapshot protected=false |
| Rulesets |API403; полнота account/org policy не установлена |
| Automations |Read-only inventory запрошен; новые задачи/расписания не создавались |
| Ownership |Root AGENTS, WORK-PARENT, bootstrap, текущие Center AGENTS/rules/review lifecycle прочитаны |
| Локальная среда |git clone: Could not resolve host: github.com; command/file execution недоступно; source получен через GitHub |

SHA-1 здесь — проверка соответствия Git object identity, а не выбор нового алгоритма безопасности для содержимого пользователя. Для payload новые контракты используют SHA-256, где это предписано.

## Что исследовано подробно

- ASGI composition, nested routers и фактический MCP dispatcher.
- OAuth principal/scopes и workspace resolver; temporary bearer ограничен read scopes.
- Durable Business ingress и legacy delegation.
- Reply preparation/reservation/provider/finalize, sequence discovery/stop boundary.
- Voice/Calls/Watch/Alfa jobs и модельные policy/budget paths.
- Worker startup, interrupted reconciliation, readiness и supervision.
- ORM/migration graph, proposed PR topology.
- Docker/CI/install constraints, literal sensitive-data guard.
- Полный перечень source paths, tests, contracts, config aliases.
- Все открытые issues, latest comments, PR bodies/files/head/CI/review metadata; исторические claims классифицированы.

Структурно проиндексированы все 943 файла. Семантическое ручное внимание сосредоточено на перечисленных границах и соответствующих тестах; это не заявление о построчном ручном ревью каждого файла.

## Подтверждённый CI baseline

[Run38718360230](https://github.com/markmalina/gate/actions/runs/38718360230), event push, exact86dbc0f, conclusion success. [Backend job 116366494513](https://github.com/markmalina/gate/actions/runs/38718360230/job/116366494513):

- Ruff: all checks passed.
- Mypy: no issues in309 source files; исключения перечислены в pyproject.toml.
- Pytest:2914 passed,1 skipped,3 warnings,12 subtests passed.
- Дополнительные reheasal tests:39,90,4,6 passed; их нельзя складывать с основным pytest как уникальное покрытие.
- Upgrade/schema drift/backup-restore/downgrade и web/contact image builds прошли.
- Windows enrollment job116366494076 успешен.

Сообщения ERROR в логе disposable PostgreSQL встречаются в отрицательных проверках constraints. Само наличие строки ERROR не делает terminal-success workflow упавшим.

Это проверки 15.10; в данном Work они не перезапускались как будто новые. Production flow test не выполнялся; позднее добавлено одно прямое read-only Gate M status observation ниже.

## Open PR checks

Полные доступные run summaries для exact heads сохранены в backlog.json. Среди 14PR последний общий CI:12 success,1 failure(PR744),1 без найденного exact-head run(PR759). Это агрегат API, **не** вывод о полном backend pass всех 12: draft job может быть skipped; scopes и freshness различаются.

PR747 последний exacte5c2220 содержит CI39254953108 / Quest39254953136 / I439254953058 success. PR766 exact4aa388b — CI39231843887 и scoped observation39233448643 success. Ни один такой source/diagnostic run не принят за product release.

## Runtime: что известно только из квитанций

[Watch latest receipt](https://github.com/markmalina/gate/issues/736#issuecomment-6264607138) и [linked731](https://github.com/markmalina/gate/issues/731#issuecomment-6264614897) датированы 19.10:

- diagnostic observation only;
- reported Productionafe0c6b8519381a0007c0dcc1c145207b5d54a3f;
- reported autoDeploy=false, staged patch{}, pending count NULL_UNKNOWN;
- actual predeploy/startup/migration/writer не подтверждены этим observation;
- autonomous delivery остаётся OPEN/BLOCKED.

[Gate745 latest receipt](https://github.com/markmalina/gate/issues/745#issuecomment-6267718741) говорит SOURCE_CODE_ACCEPTED_NOT_INSTALLED для внешнего Ops этапа; source/read metadata binding и весь S1 остаются заблокированы.

Эти отчёты — источники исторически наблюдённого состояния и ограничений. Аудит проверил их наличие/содержание, но не повторил provider/runtime наблюдение и не присвоил себе их independent ACCEPT.

## Что не доказано

- Текущее число реплик/всех Gate product users, cloud session validity.
- Actual secrets/credential bindings, effective Railway start/predeploy/schema.
- Новая provider квота, текущие цены/retention terms, актуальные CVE.
- Fresh end-to-end доставка Watch, MTProto или snapshot protection.
- Full secret scan всего Git history; actual privacy leakage не утверждается.
- Full subject export/delete всех storage/backup классов.
- Полнота inline review-thread acceptance для будущего merge.
- Работоспособность внешних проектов по их переписанным Gate ссылкам.

## Проверка пакета

Машинные JSON проверены на parse/shape/count/ref consistency; все source evidence coordinates существуют и привязаны к immutable blob; все 34issues и 14PR перечислены по одному разу; найдены и отдельно описаны main/proposed schema branches и MCP route shadow. Проверены относительные Markdown links и fenced blocks, sensitive-pattern candidates без публикации matched content.

Точный результат и последующие remote readback — [validation.json](validation.json). Окончательный receipt коммита/PR публикуется в[Gate#767](https://github.com/markmalina/gate/issues/767), не самообъявляется в этом статическом отчёте.

## Срок пригодности

Source facts действительны для закреплённого SHA. Backlog/runtime evidence действительны для своих timestamps. Перед продолжением делать fresh query; старый snapshot не исправлять молча так, будто он всегда отражал новое состояние.


## Дополнение: прямое scoped Gate M observation19.10

После 2026-10-19T16:41:49Z и до 2026-10-19T16:42:51.938Z успешно выполнен один gate_get_status, без чтения сообщений, отправки или модели.

- environmentproduction, commitafe0c6b85193, manual, send_enabled=true;
- main остаётся 86dbc0f; service fingerprint отстаёт от текущего source;
- MCP authoauth2_enforced; status.mcp.tools — legacy subset18, а не exhaustive tools/list;
- MTProto runtime_connected=false, configured/authenticated=null, send/create_group=false;
- Watch Actionsenabled=true, workflow pin configured=true, ordinary_worker_enabled=false; aggregate18transcribed/18delivered;
- Telegram voice state=reconciliation_required; hard ceiling3000000microUSD, settled443531, unknown_count1;
- RU/BY stored connection state ready/can_reply; last_checked timestamps RU19.10 08:59Z, BY06.10 20:22Z. Это не повторный provider probe;
- private record payload, raw IDs/группы/контакты в audit evidence не копируются.

Наличие 18 доставленных Watch notes подтверждает работу отдельных исторических Actions runs, **но не** постоянно работающий worker, будущую автономную очередь или приёмку#736. Current ordinary worker off подтверждён напрямую.

Безопасная выдержка: [runtime-observation.json](runtime-observation.json). Scope — один авторизованный workspace. Global user census, actual provider configuration/secret presence/predeploy/migrations и все продуктовые flows этим не доказаны.

Предыдущие строки о runtime «только из квитанций» относятся к более раннему этапу аудита. Это дополнение заменяет отсутствие прямого status observation; ограничения старых Ops receipts сохраняются.

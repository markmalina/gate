# Независимое приложение: применимость существующего маршрута к S2

Дата: 2 ноября 2026. Supporting source-only fact check; это не повторная приёмка неизменённых исходников и не runtime-проверка. Проверяющий отдельно от автора/оператора текущего исправления S1. Провайдерные обращения, запуски workflow и изменения репозиториев не выполнялись.

## Вывод и предел утверждения

Ранее заявленный предел готовности S2 подтверждён для нижеуказанных точных версий: существующий short deploy умеет запустить deployment, а компоненты Gate745 содержат backup/cutover primitives; **применимый исполнимый Gate-owned маршрут production backup → Watch0119 migration/deploy → проверка в них отсутствует**. Это не утверждение об отсутствии внешних резервных копий или любых других нерассмотренных реализаций.

Этот вывод не диагностирует причину Railway error, не доказывает token scope/pending state и не объявляет готовность к S2. Более поздние диагностические версии не исследовались повторно: факт относится к точно перечисленным baseline-файлам; перенос на другой SHA требует лишь проверки сохранности соответствующих файлов, а не общего аудита.

## Точные источники и доказательства

| Источник | Что установлено |
|---|---|
| [PLAN.md:114–119](https://github.com/markmalina/gate/blob/3dd9d3afef9a67d608cdb60aa639aba3aa6aab35/docs/audit/2026-11-02/PLAN.md#L114-L119) | Исполнимый следующий Gate-owned release route — самостоятельный критерий S1. Строка118 прямо оставляет S1 BLOCKED, если маршрут отсутствует или требует неизвестной capability. Успех scope observation сам по себе не доказывает возможность backup/migrate/deploy. |
| [Gate ops-deploy.yml:67–76](https://github.com/markmalina/gate/blob/86dbc0f429dce089bf733fc422f20f556535895b/.github/workflows/ops-deploy.yml#L67-L76) | Реальный short deploy закреплён на Ops `512decbbd81d1d824563af995fc0df435fbf2429`. Он передаёт только target=gate и exact source SHA. |
| [Закреплённый gate-deploy.mjs:157–165](https://github.com/markmalina/ops/blob/512decbbd81d1d824563af995fc0df435fbf2429/agent-ops/actions/gate-deploy/gate-deploy.mjs#L157-L165) и [211–229](https://github.com/markmalina/ops/blob/512decbbd81d1d824563af995fc0df435fbf2429/agent-ops/actions/gate-deploy/gate-deploy.mjs#L211-L229) | Выполняет authentication → current main check → `serviceInstanceDeployV2` → ожидание deployment/SHA. В этой последовательности нет backup/lock, physical DB identity, допустимого schema transition или drain competing drivers. |
| [PR766 railway.toml:5–7](https://github.com/markmalina/gate/blob/b83eeffa04926343a9bbc5b2374444e1737a2b11/railway.toml#L5-L7) | `preDeployCommand = "alembic upgrade head"` существует. Deployment потенциально запускает миграцию; наличие команды не заменяет безопасный backup/release contract. |
| [Watch preflight contract:181–186](https://github.com/markmalina/ops/blob/f676942a22d1e35544b4463b0674281ea2673c52/agent-ops/docs/gate-watch-release-preflight-v2.md#L181-L186) | Требует отдельный reviewed operation contract, physical DB identity, completed locked backup, predeploy/schema, drain, immutable plan, distinct approval и post-apply verification. Прямо сообщает, что observer и short deploy не реализуют эту capability. |
| [Gate snapshot lock:1–9](https://github.com/markmalina/gate/blob/b83eeffa04926343a9bbc5b2374444e1737a2b11/agent-ops.gate-snapshot.lock.json#L1-L9) и [snapshot workflow:109–115](https://github.com/markmalina/gate/blob/b83eeffa04926343a9bbc5b2374444e1737a2b11/.github/workflows/gate-snapshot-cutover.yml#L109-L115) | Существующий Gate745 caller закрепляет Ops `b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`; он не использует более свежий Ops profile автоматически. |
| [Закреплённый snapshot profile:33–67](https://github.com/markmalina/ops/blob/b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85/agent-ops/actions/gate-snapshot-cutover/profile.json#L33-L67) | Scope `Gate745-only`, backup-before0116 и migration `0116_reply_dialog_versions`. Backup primitive существует: фиксированный volume, максимум одно создание и lock, completed/locked required. Это часть ограниченного reply cutover, не разрешённая standalone Watch0119 операция. |
| [Закреплённый snapshot release:1–16](https://github.com/markmalina/ops/blob/b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85/agent-ops/actions/gate-snapshot-cutover/release.json#L1-L16) | `enabled:false`, fixture `PENDING`, allocationPolicy `null`, кандидат `303a7aca19513070235a943bd5c798e9f4bf1b84`, migration reply0116. Source ACCEPT не разрешает применение. |
| [Ops baseline snapshot profile:33–67](https://github.com/markmalina/ops/blob/f676942a22d1e35544b4463b0674281ea2673c52/agent-ops/actions/gate-snapshot-cutover/profile.json#L33-L67) и [release:1–16](https://github.com/markmalina/ops/blob/f676942a22d1e35544b4463b0674281ea2673c52/agent-ops/actions/gate-snapshot-cutover/release.json#L1-L16) | Более свежая заготовка сохраняет Gate745/0116 предел; release выключен, fixture/source review `PENDING`, reviewed executor `null`. Простое переключение caller на этот SHA не создаёт применимого Watch0119 release route. |

## Конкретное недостающее действие и предлагаемый пакет

Недостающая операция: принятый Gate-owned Watch0119 release contract, связывающий проверенную физическую БД и завершённый locked backup с переходом `0115 → Watch0119`, сохранением выбранного Actions режима и журналов, а затем проверкой exact установленного SHA/schema/flags.

Предложение для отдельного решения владельца, **не реализованное и не запущенное в S1**:

1. Подготовить узкую адаптацию уже существующих backup/cutover компонентов к Watch0119, без нового общего инфраструктурного framework. Закрепить exact candidate, target/DB/volume, допустимый исходный и конечный schema head; не присваивать старый Gate745 grant.
2. В конкретном пакете определить backup create/lock/completion, применимый путь восстановления, drain/fence текущих drivers, сохранение Actions режима и выключенных новых функций, exact deployment и post-apply verification. Указать условия остановки и совместимый rollback без автоматического downgrade/удаления журналов.
3. Провести целевые offline/fixture проверки и независимую проверку этого изменённого capability-пакета; отдельно получить разрешение на его точные production backup/migration/deploy действия. Текущий пользовательский запрет этих действий сохраняется.

Заключение предыдущего supporting review не пересматривается. Уточнение формулировки: **компоненты backup уже есть; отсутствует их применимый разрешённый и проверенный исполнимый контракт для Watch0119**, а не любой код резервного копирования вообще. S2 не запускался.

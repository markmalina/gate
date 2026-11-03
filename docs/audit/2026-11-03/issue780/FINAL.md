# Gate780 — проверенный source-пакет границы живых writers

Родитель: [Gate736](https://github.com/markmalina/gate/issues/736). Поручение: [Gate780](https://github.com/markmalina/gate/issues/780). Исполнитель: `gate780-boundary-20261103T0605Z`.

**Итог: ACCEPT_SOURCE_BOUNDARY_METHOD.** [Независимое заключение](review/independent-source-assessment.md). Задача780 выполнена в разрешённой source/test области; весь S1 остаётся BLOCKED до native/current runtime evidence.

## Результат и пределы

Реализован producer проверяемой **локальной границы writers**, который не требует полной истории Actions и не выдаёт остановку процесса за завершение удалённого эффекта. Mark подтверждает только запрет новых admissions; Ops отдельно подтверждает остановку точного старого deployment и пустой fleet/allocation перед backup. Gate candidate и совместимый fallback проверяют live workflow до любого DB claim, поэтому старые queued jobs после запуска нового приложения не обходят fence.

Старый baseline сохраняет durable claim до внешнего эффекта. После подтверждённой гибели всех старых процессов согласованный snapshot содержит либо атомарно сохранённый результат, либо прежний processing/sending/UNKNOWN. Эти состояния не возвращаются в очередь повторного внешнего вызова. Cursor сохраняется после originals. Завершение удалённого эффекта остаётся неизвестным; возможна потеря результата, но повтор эффекта не допускается.

Предварительная установка server-side fence на работающий baseline не нужна. Новый guard впервые устанавливается в candidate/fallback после остановки старого fleet и полного locked backup. Нет круговой зависимости. Конкретные target, порядок установки, пределы простоя, бюджеты и rollback: [README](README.md). Весь порядок является подготовленным решением, а не выполненной операцией.

## Точные версии

| Компонент | Проверенная версия | Состояние |
| --- | --- | --- |
| Gate PR766 | `897b2f1b882326cdc77777f4c600d8087cfbcdb8` | OPEN, UNMERGED |
| Ops PR414 | `0079fc2f3f5048cb54fc0907364945779d89a6ca` | DRAFT, OPEN, UNMERGED |
| Mark PR126 | `f448bdb07efd97192e4a1b19117179d4abc3ef71` | DRAFT, OPEN, UNMERGED |
| Guarded fallback | `4827624955c9543bfae1b1377de8497956720ab9` | Immutable artifact; не установлен |
| Исторический baseline | `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` | Только source reference, не текущий runtime факт |

Gate main остаётся `86dbc0f429dce089bf733fc422f20f556535895b`; Ops main `d66dece8939398d5a149f9174c4b2fb4b98684f6`; Mark main `9fc3a9402a75632d2e9a8d0f08f3697bfa82583b`. Все три PR имеют `auto_merge=null`. Ops/Mark release остаются OFF/PENDING. Исторический fallback7fd сохранён, но новая ветка его не допускает: в нём отсутствует admission guard.

Ops фиксирует весь candidate `app` tree `da554d37f4d89d336037273146b3d9fd92b6aea8` и `alembic` tree `c3793c393b1c73dc19ce16a35063ef6aa520593d`, а также exact fallback и фактический digest restore proof. Изменение runtime/migrations требует нового review и policy; docs/client-only successor может сохранить эти деревья. Отдельный будущий release successor должен связать фактически установленные reviewed версии, актуальный Gate main и действующее разрешение S2.

## Проверки

Окончательные CI metadata и независимое заключение сохраняются рядом с этим файлом. Source head указан выше; более поздний архивный docs commit не объявляется проверенным runtime source.

- Mark: 44 локальных теста, независимо повторены.
- Ops: 154 локальных snapshot-теста, независимо повторены; полный [CI41591294947](https://github.com/markmalina/ops/actions/runs/41591294947) — SUCCESS, 2551 Node tests + 2 Vitest tests passed / 0 failed; [Security41591294948](https://github.com/markmalina/ops/actions/runs/41591294948) — SUCCESS.
- Gate: окончательные local admission/client tests — 10 passed; 2 PG-only tests пропущены локально и выполнены в disposable PostgreSQL CI.
- Guarded fallback: [CI41590282789](https://github.com/markmalina/gate/actions/runs/41590282789), job125765478098 — 11 passed, затем 1 restored-database test passed. Проверены populated0115→0119, actual drain/readiness с HTTP stubs, abrupt process death с открытой транзакцией, originals/hash/cursor/journals, processing/sending/UNKNOWN и отсутствие повторного эффекта после dump/restore. PostgreSQL17.11, production_authorized=false.
- Фактический artifact13209747366 скачан, ZIP SHA256 `ea36cf5c20d075a7f388a01fc7111b4a4722ae4ffd010dcf71762633f7efeb92` независимо проверен. Сохранённые raw JSON/JUnit побайтно совпадают с ZIP. Ops использует raw restore SHA256 `1b9e28f7100ae56acc37d3dbaf5f5b7346c9c1550425ec11a7b3fd787432d399`.
- Предыдущие Gate6232 full CI41590282789 и Quest41590282890 — SUCCESS. Окончательный source897b2f1: [CI41591466943](https://github.com/markmalina/gate/actions/runs/41591466943) и [Quest41591466908](https://github.com/markmalina/gate/actions/runs/41591466908) — оба SUCCESS, все jobs SUCCESS. Backend3217 passed /2 skipped; synthetic merge3210 passed /9 skipped; в обоих12 subtests passed. Ruff/mypy, migrations/drift, dump/restore, Docker build и unprivileged checks прошли. Metadata: [ci-final.json](ci-final.json).

Реальные provider/production вызовы, STT, личные аудио и публикации не использовались. Начальная ошибка fixture import была исправлена в6232; ранний run41589660885 не выдаётся за зелёный. Локально потребовалась установка PyYAML для существующего client test; после неё проверки прошли.

## Что не выполнено

Production не менялся и не наблюдался этой работой; auth не повторялся. Merge, установка workflow/control, live disable/enable, backup/restore/migration/deploy/restart и S2 не выполнялись. Старые budgets/grants не возобновлены.

Для всего S1 всё ещё нужны независимо принятые native evidence: pinned stopped+empty-fleet действительно означает гибель **всех** старых процессов; backup действительно transactionally consistent, completed и locked; свежие protected факты точного allocation/source/pending/flags/DB/volume. Тестовый адаптер, фиксированная задержка или принятый stop request этих фактов не заменяют. Настоящие внешние эффекты могут закончиться позднее, поэтому `remoteSettlementProven=false` обязателен.

Следующий ограниченный результат подготовлен в [Gate781](https://github.com/markmalina/gate/issues/781): native disposable stop-all/consistent-backup/lock proof с фактическим disposable restore на уже существующей Ops архитектуре. Это отдельная подготовленная задача, не поручение на автоматический запуск provider операций. Родитель736 остаётся OPEN, S1 не DONE, S2 не STARTED.


Финальный fallback job125768931323 на Gate897b2f1 также завершён SUCCESS: 11 passed + restoredDB1 passed. Artifact13210546134, ZIP SHA256 `bbdff8dad07bd02732419ae457f38ecd71fbcfd88a555cd4278031f9a8c96999`. Два raw JSON побайтно совпадают с ранее закреплёнными доказательствами, поэтому Ops proof pin остаётся точным. Новый JUnit SHA256 `f186feadbfe4cb6fec6eb62566bde4faafe28782f5d9f6c8ed5923947e446a79`; файлы сохранены с префиксом `final-`.

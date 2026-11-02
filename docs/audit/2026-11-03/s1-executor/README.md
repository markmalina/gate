# Gate736 / Watch0119 — итог S1: BLOCKED

Это результат нового прямого поручения Марка Малиновского от 02.11.2026. Прежний USER_STOP сохранён как история; он не использовался как разрешение старых действий. S2 не запускался. Этот документ не разрешает merge, установку исходников или production effects.

## Результат и основной блокер

Fallback фактически проверен на PostgreSQL17.11, включая populated0115→0119 и отдельную восстановленную тестовую БД. Подготовлены и изолированно проверены Mark-owned control и типизированная ветка Watch существующего Ops snapshot/requester/verifier. **Полный production-путь не принят:** отсутствует авторитетное доказательство полной границы и завершения ранее отправленных drain-запросов. Сохранившиеся GitHub runs/логи не исключают удалённый последний запуск или истёкший лог. Старый Gate хранит факт claim, но не durable completion каждого запроса. Cancelled/failed и `replayed` завершение не доказывают.

Поэтому фиксированный Mark production adapter намеренно отвечает `E_SETTLED_CHECKPOINT_REQUIRED` до любого API и disable. Это безопасный отказ, а не реализованная остановка production. Подменять его `ACCEPT`, временным окном или выдуманным checkpoint нельзя. Проверяющий принял только `ACCEPT_SAFE_BLOCKED_DRAFT`. Пока этот обязательный пробел не закрыт, запуск backup запрещён и S1 не может быть DONE.

## Точные версии и доказательства

| Компонент | Версия / доказательство | Состояние |
|---|---|---|
| Исходный baseline приложения | `afe0c6b8519381a0007c0dcc1c145207b5d54a3f` | Исторический ориентир; сейчас production не наблюдался |
| Совместимый fallback | [`7fd322c958cccf6828a3e8fcb00a075b2d4f1f46`](https://github.com/markmalina/gate/commit/7fd322c958cccf6828a3e8fcb00a075b2d4f1f46) | Старое приложение + только миграции0116–0119; не установлено и не объединено |
| Исправление populated fixture | [`3329b95b9c6c048a593c871d1fbc2ac16bf15d52`](https://github.com/markmalina/gate/commit/3329b95b9c6c048a593c871d1fbc2ac16bf15d52), Rufffix [`ea92973c579de3f802323f9811ca32fed070118f`](https://github.com/markmalina/gate/commit/ea92973c579de3f802323f9811ca32fed070118f) | Реально запускалось, не только source review |
| Gate fixture + полный CI | [CI41525798415](https://github.com/markmalina/gate/actions/runs/41525798415), [Quest41525798407](https://github.com/markmalina/gate/actions/runs/41525798407) | SUCCESS на ea929; backend3204passed, synthetic3197passed; Windows/Ruff/mypy/migrations/drift/Docker PASS |
| Mark control | [PR126](https://github.com/markmalina/mark/pull/126), [`071d4657f3a3222278bf0499cffcaf5b2bfd542e`](https://github.com/markmalina/mark/commit/071d4657f3a3222278bf0499cffcaf5b2bfd542e) | Draft; release OFF; существующие drain/binding без изменений; 33/33 tests |
| Ops Watch | [PR414](https://github.com/markmalina/ops/pull/414), [`5c86c479f6581c5922519382ffcd953d63629084`](https://github.com/markmalina/ops/commit/5c86c479f6581c5922519382ffcd953d63629084) | Draft; release OFF; 142/142 snapshot tests, 9/9 lifecycle tests |
| Единый Gate поток | [issue736](https://github.com/markmalina/gate/issues/736), [PR766](https://github.com/markmalina/gate/pull/766) | Открыт; merge не выполнен |

Ops final [CI41530880282](https://github.com/markmalina/ops/actions/runs/41530880282) и [Security41530880330](https://github.com/markmalina/ops/actions/runs/41530880330) — SUCCESS на5c86. [Независимый клиентский review](client-independent-review.md) — ACCEPT_SOURCE_BLOCKED. Полные безопасные журналы fixture/CI сохранены с gzip; raw SHA256 перечислены в [manifest](log-manifest.json).

Последующие CI клиента и финальные SHA фиксируются в итоговом комментарии issue736 вместе с этим документом. Запуск CI не считается успехом до conclusion=success. Исходный полный Ops CI41530543012 имел2537PASS/2FAIL: fixture lifecycle не копировал новый inert импорт; исправление5c86 добавляет только этот файл в изолированную копию, отрицательные gates сохранены.

Независимые проверки: [fallback](fallback-independent-review.md), [Mark](https://github.com/markmalina/mark/blob/071d4657f3a3222278bf0499cffcaf5b2bfd542e/docs/gate736-s1-independent-review.md), [Ops](https://github.com/markmalina/ops/blob/5c86c479f6581c5922519382ffcd953d63629084/agent-ops/docs/gate736-watch0119-independent-review.md). Их приёмка ограничена прочитанными файлами/хешами и не является приёмкой всей S1 или production.

## Что фактически доказано

**Реальная изолированная БД:** [fixture](ea929-watch-baseline-fallback.json), [dump/restore](ea929-watch-baseline-fallback-restore.json), [JUnit](ea929-watch-baseline.xml). Artifact13180056569, ZIP SHA256 `84b73892fa7e7f704a6c28d2aef6849581a732da4a4dfd57e772d457f8ac37f3`. Raw restore proof SHA256 `5826b13f2b27586680579a0b42d4d431ce37097ecbdb0a4a18dc1219c421e42a`. Подтверждены readiness старого совместимого приложения, реальный drain с HTTP stubs, originals/hash, cursor/watermark, journals, сохранение UNKNOWN, ноль повторных эффектов после replay и восстановления. STT, личные записи и external provider не использовались.

**Исходники и synthetic transport:** точный target/source/schema, запрет pending/mixed fleet/неверного DB/volume/flags, ожидание queued/waiting/in-progress, обязательные безопасные HTTP receipts, UNKNOWN и one-attempt backup/deploy. Ops требует stopped baseline → completed+locked backup → повторную сверку → один candidate → loaded schema0119/flags/fleet. При подтверждённом отказе — один совместимый fallback с независимым результатом `RECOVERY_VERIFIED_DRIVER_FENCED`. Safe stop отдельно обозначен `BASELINE_STOPPED_NOT_RESTORED`. Production restore/downgrade/delete не добавлены. Driver restore возвращает только prior enabled; прежний disabled остаётся disabled. Неполная/просроченная/изменённая квитанция запрещает переход.

**Клиент Gate:** Watch jobs добавлены в существующий workflow; прежние reply745 jobs и action pins сохранены, пересчитан только общий workflow digest старого lock. Новый Watch lock фиксирует точный Ops commit/profile. Terminal verifier не имеет provider environment/credentials. Опциональный `baseline_flags=true` в существующем read-only admin endpoint показывает загруженные early/Work flags; обычный ответ сохраняет прежнюю схему.

**Реальное production-наблюдение этой работой:** отсутствует. Ранее подтверждённая Railway авторизация принята как факт истории и не повторялась. Она не доказывает current fleet, pending changes, настройки, backup readiness, границу drain или разрешение выпуска. Неизвестны текущие allocation/source/instances, DB/volume binding, native backup contract и фактическая возможность lock; native disposable provider fixture не выполнен. Зелёные source tests этого не заменяют.

## Пакет будущего S2 — подготовлен условно, к запуску НЕ готов

Точный физический target: project `972b6ab7-988a-421c-abfa-425451ad714f`; environment `a148b138-44b3-4349-9d4b-292880503055`; Gate service `099cbf8c-1d94-43fe-8714-90725bf93998`; Postgres service `561d3da3-ddb5-4d3d-8fa2-c9454b0996bf`; volume `7d2de9ed-c63e-454a-811e-4f206f3dfef3`. Source repos markmalina/gate, markmalina/mark, markmalina/ops; Actions baseline, persistent/early-card/Work admission OFF.

Перед S2 обязательны: доказанный producer полной Mark quiescence; его независимая приёмка; разрешённое отдельно защищённое наблюдение allocation/pending/fleet/DB/volume/settings; настоящий native disposable backup/lock fixture и его независимый verifier; точные reviewed installed Ops/Mark versions; текущий полный Gate candidateSHA с зелёным push CI; отдельная команда владельца на S2. Текущие draft SHA нельзя объявить installed. Пересборка release-only successor требует новой фиксации pins; это не выполнялось.

Будущий порядок и лимиты: Mark disable≤1 с сохранением prior enabled, доказать outcomes; stop только подтверждённых baseline instances; backup create≤1, дождаться complete, lock≤1 и reread; повторить условия; candidate deploy≤1; normal budget20min, total recovery28min. PlanTTL15min, fenceTTL30min, отдельная свежая verification. Неизвестный backup/deploy исход сначала выясняется по точному run/ID/source; повтор запрещён. Только подтверждённый отказ candidate допускает stop этого candidate и fallback deploy≤1 на `7fd322…`; schema0119 и история остаются, готовность fallback проверяется, drain остаётся fenced до отдельной приёмки. Затем restore только прежнего enabled. Отсутствие доказательства восстановления — UNKNOWN, не работающая система.

Новое Railway наблюдение сейчас не запрашивается: оно не закроет первичный пробел completion/history Mark. Не нужны перенос токена или повтор авторизации. Один необходимый следующий шаг контроллера — согласовать и предоставить исполнимый авторитетный способ доказать полную границу и завершение уже принятых запросов на старом baseline. Если для этого требуется установка серверного completion-control или другая production операция, это отдельное решение владельца за пределами данного S1; скрыто выполнять его нельзя. После закрытия этой границы подготовленный маршрут требует указанного native evidence, а не автоматического запуска.

Все рабочие изменения остались в PR/ветках. В production ничего не установлено; live Mark-drain не переключался; секреты и права не менялись; приложения не выпускались. S1 — BLOCKED. S2 остановлен до отдельного поручения.

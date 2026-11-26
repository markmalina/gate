# R2 — единый отказ серии с актуальным scoped контекстом

Дата: 27.10.2026 МСК. Задача745/PR747; существующий execution gate-745-snapshot-20261008T1458Z.
Source base: bb1c552bd28ca6fa4f7ae941a0ed54ecce5ada99. Реализация разрешена Марком26октября; R2 продолжен27октября.

## Поведение

До R2 save draft, document preparation, single/document send уже направляли freshness ошибки в общий _dialog_context_error_result. Queue ловил тот же ReplySequenceError, но возвращал толькоerror, поэтому Work не получал sent=false/current_context и должен был отдельно восстанавливать ситуацию.

Теперь очередь использует общий обработчик для context_changed/context_snapshot_required/context_unavailable. Он сначала откатывает transaction, затем проверяет gate:inbox:read, находит task только через draft/account/project grants и возвращает bounded текущий контекст. При отсутствии read scope или невозможности безопасно прочитать контекст возвращаются sent=false/current_context=null. Ошибка чтения не скрывает исходную причину и не позволяет отправку. Прочие ошибки очереди не вызывают дополнительное чтение.

Исходные drafts не обновляются и не ставятся в очередь при отказе. Work читает возвращённые изменения, готовит новые drafts с полученным snapshot и точными payload hashes, получает требуемое новое точное approval и создаёт новую очередь. Freshness error сам по себе не является одобрением нового текста.

## Матрица контракта

| Путь | Snapshot / отказ |
| --- | --- |
| get_reply_context | context_snapshot/context_ready, scoped projected history и accepted events |
| save_reply_draft | expected_context_snapshot required; общий контекстный отказ |
| prepare_reply_document | expected_context_snapshot required; тот же draft writer/guard |
| send_approved_reply / send_approved_document | stored draft snapshot, неизменные payload/account/recipient, общий контекстный отказ |
| queue_approved_reply_sequence | stored draft snapshots; после R2 тот же scoped отказ |
| sequence worker | shared sender admission и durable собственное продвижение, остановка хвоста при изменениях |

Существующий error schema уже допускает sent=false/current_context, изменение schema enum или новый обязательный queue parameter не нужно. Snapshot хранится на draft; queue не принимает свежий snapshot для автоматического обновления старого согласования. Descriptor очереди теперь объясняет этот контракт и необходимость нового точного approval.

Сохранены2–50items, interval60, ordered payload hashes, неизменные captions/entities, отсутствие автоповтора послеunknown. Connected Gate M catalogue не устанавливался; его актуальная схема и пользовательский roundtrip проверяются после выпуска в R8.

## Проверки

tests/test_reply_sequence_context_mcp.py добавляет22сценария:
- 6combinations трёх freshness errors и read permission;
- 2safe fallback при недоступном/чужом task lookup или сбое context read;
- 2nonfreshness errors без чтения;
- 4realPG cases inbound/outbound с/без inbox read, без изменения drafts/queue/attempts;
- 2legacy drafts без snapshot, без auto-backfill;
- 2foreign account/project cases, без раскрытия контекста;
- 2realPG MCP recovery read→new exact plain drafts/hashes→confirmed queue→first mock delivery;
- 1explicit raw-event truncation flag;
- 1server catalogue/source contract.

Локально Ruff из корня Gate PASS, compile PASS,10exact-handler диагностических случаев PASS. Последние извлекают неизменённые AST обработчиков и проверяют их с actual QueueApprovedReplySequenceService и mocked DB/reader; это не полный API import или PG acceptance. Локальный checkout частичный, PostgreSQL по-прежнему не запускается из-за UIDограничений. Полный настоящий API/PG и strict Mypy выполняются в original exact-head CI после публикации; terminal результат записывается отдельной issue receipt.

Контекст сохраняет существующий bounded raw page20 и dialog_events_has_more; тест проверяет честное обозначение усечения. Новый raw-event pagination API этим bounded исправлением не вводится. Полнота readsets/пограничные ситуации проекции остаются в целостной матрице R3; пользовательская приёмка не объявляется по одному этому source patch.

## Публикация и остаток

Изменяется один runtime module app/api/mcp_routes.py (descriptor и freshness catch); добавляется один test module и этот source report. Миграции/workflows/locks/pins и результатыR1 сохраняются. Source SHA и проверяемые CI/PG counts см. в последней R2 receipt существующей #745.

Production/credentials/resources/native/Telegram/model/STT effects0; тестовая mock delivery не является реальной отправкой. Старые4/12drafts не использовались. #745/PR747 остаются открыты до выпуска и A01–A12.

Следующий source этап после R2 — R3: собрать целостную матрицу гонок/идемпотентности/медиа/миграций и сформировать exact independent review packet. R4/R5 инфраструктурные зависимости и отдельные release/live gates сохраняются.

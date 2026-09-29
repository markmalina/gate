# PR #720: доработка ревью 5332137616

Поручение Марка: 28.09.2026. Автор исправлений: α Gate.
Исходное ревью: https://github.com/AlphaCenter/gate/pull/720#pullrequestreview-5332137616 .
Исходный head: `acbd4347ab232a360e26c1b152c9ebc0d9b4de36`.
Execution: `gate-716-718-review-fixes-20260928T0622Z`.

## R1 — ревизия только для показанного, спроецированного контекста

До и после чтения истории MCP получает scalar snapshot task/conversation/guard/revision.
ORM identity map не используется для чтения счётчика. Проверяется завершение всех известных
inbound receipts этого guard, включая более ранние receipts при обработке не по порядку.
Последняя revision должна иметь ровно одну нормализованную inbound Message в точных
account/project/chat/business connection. Она должна быть trigger текущей task и присутствовать
в реально возвращённой странице истории.

Если снимки различаются, consumer ещё работает, receipt находится в dead letter, проекция
отсутствует, task устарела или показана старая страница, ответ содержит
`sequence_approval.ready=false`, безопасный `blocked_reason` и
`dialog_inbound_revision=null`. Историю и состояние серий можно прочитать, но этот ответ
не предоставляет ревизию для подтверждения. Клиент должен обновить актуальную task/контекст.

Queue под существующим dialog advisory barrier проверяет revision и готовность проекции ещё
до записи серии. Финальный sender admission повторяет проверку. Поэтому знание нового числа
revision при задержанном consumer не позволяет поставить старые drafts в очередь. Входящее,
принятое после завершающего чтения MCP, по-прежнему отвергает старую revision при queue либо
атомарно останавливает уже созданную серию. Нового lock-order или внешнего запроса не добавлено.

## R2 — оставшиеся тексты

Detail читает ReplyDraft.draft_text в том же SQL snapshot, что и header/items, с точной связкой
draft/account/project/conversation/task. Все 50 элементов доступны через next_cursor; counts
сохраняют смысл полной серии. Изменившийся статус исходной task не скрывает сохранённые drafts.
Чужой либо неправильно привязанный draft не раскрывается: текст и match flag равны null.
`draft_text_matches_approval` сравнивает текущий сохранённый текст с прежним approved hash.
Это позволяет явно отличить восстановленный черновик от текста прежнего подтверждения.

## R3 — адресат в detail и уведомлении

Один resolver использует точную conversation/account/project/chat и имя из разрешённого
контекста: импортированное имя контакта, display name identity или conversation title.
Имя ограничено 120 символами; переносы, управляющие и bidi-control символы удаляются.
При отсутствии имени используется стабильная непрозрачная метка диалога, различная для разных
account/chat. Уведомление владельцу содержит это имя и прежние account/sequence/count/reason;
входящий текст не копируется. Проверка owner route остаётся обязательной.

## Regression evidence

- `tests/test_reply_sequence_review_postgres.py`: реальное чтение MCP и SQL ingress между
  чтениями; committed receipt без Message для received/processing/dead_letter/processed;
  положительный путь после проекции и нового подтверждения; отказ для старой страницы,
  старой task, другого project/connection; out-of-order consumer и sender recheck;
  все 50 текстов по страницам, изменённый текст и scope denial; два stop одного аккаунта
  с разными именами либо fallback и только owner destination.
- Проекция consumer в этих тестах задаётся синтетическими SQL-записями Message/task/receipt/outbox;
  Telegram/model providers не вызываются. Сам production consumer этим тестом не запускается.
- `tests/test_reply_sequence_context.py`: неизменившийся/изменившийся snapshot, missing/pending/
  blocked context, обязательная видимость trigger и ограничение безопасного имени/fallback.
- Прежний тест stale revision исправлен: одно только новое число revision больше не считается
  готовым контекстом до обработки receipt.

Точная окончательная квитанция head/CI хранится в PR #720. Локальная PostgreSQL-служба в этой
среде недоступна (доступен только uid 0); реальные PG17 regression/migration проверки выполняет
CI. Skipped не считаются passed. Независимая повторная приёмка остаётся отдельным шагом.
Production/настройки/реальные сообщения/модельные вызовы/merge/deploy этим этапом не меняются.

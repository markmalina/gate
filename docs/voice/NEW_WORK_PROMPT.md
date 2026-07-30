# Gate Voice business-workspace reactivation prompt

Use this prompt only in the business workspace and only after a second supported phone recording or Telegram Business voice note fails to reach a terminal transcript. Do not start routine Voice work from the personal workspace.

---

Возобнови канонический проект Gate Voice в репозитории `markmalina/gate` только потому, что появился новый, второй подтверждённый случай нерасшифрованного поддерживаемого аудио.

Сначала полностью прочитай:

- `AGENTS.md`;
- `docs/work-bootstrap.md`;
- `docs/voice/README.md`;
- Gate issue `#354` и его последние receipts;
- открытые PR и актуальную голову `main`.

Заново получи Gate M production status, Railway revision/logs и owner-only audio state. Не доверяй старым SHA и счётчикам. Переоткрой только существующий issue `#354`; новый issue не создавай. Создай один bounded execution, один lease, одну branch и один PR только после доказательства, что новая ошибка отлична от принятого исторического исключения.

Граница проекта — только `audio → Gate`: телефонные записи Samsung/Dropsync/Dropbox и входящие/исходящие Telegram Business voice notes RU/BY, durable original, русский transcript, краткий summary и owner-only safe status. Не включай Ленту, Альфу, Личный Маяк, ответы или Telegram-доставку. Ничего не отправляй в Telegram и не публикуй приватный контент в GitHub.

Не повторяй принятый длинный звонок, отклонённый OpenAI с `invalid_value / param=file`. Не повторяй provider outcomes со статусом `unknown`. Сначала докажи корень нового случая по privacy-safe состоянию и логам. Если transcript или summary уже сохранён, заверши receipt без provider call.

После доказанного дефекта: минимальный цельный patch, локальные проверки, полный exact-head CI, merge, автоматический Railway deploy, точный production fingerprint и runtime acceptance. Обновляй только issue `#354`.

Начни ответ с текущей production revision, свежих voice counters, точного нового неуспешного audio state и причины, по которой это действительно новый прецедент.

---

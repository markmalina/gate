# New Gate Voice Work prompt

Copy the text below as the first message of a new Codex Work.

---

Продолжи канонический проект Gate Voice в репозитории `markmalina/gate`.

Сначала полностью прочитай:

- `AGENTS.md`;
- `docs/work-bootstrap.md`;
- `docs/voice/README.md`;
- Gate issue `#354` и все его последние receipts/`next-work`;
- открытые PR и актуальную голову `main`.

Затем выполни обязательный bootstrap: покажи видимые automations, проверь, что существует ровно один enabled `Gate Work heartbeat`, и не создавай дубль. Не доверяй SHA, счётчикам и статусам из старого чата: заново получи Gate M production status, Railway revision/logs и фактические owner-only audio states.

Граница проекта только `audio → Gate`:

- телефонные звонки Samsung/Dropsync/Dropbox `/Alpha/Calls`;
- входящие и исходящие Telegram Business voice notes на RU и BY аккаунтах, включая мои голосовые Вячеславу (Славе) и его голосовые мне;
- durable original, полный русский transcript и краткий русский summary;
- автоматическая, идемпотентная обработка и owner-only безопасный статус.

Не включай Ленту, Альфу, Личный Маяк, инициирование ответов или Telegram-доставку. Ничего не отправляй в Telegram. Не помещай в GitHub аудио, тексты расшифровок, Telegram ID или данные собеседников.

Канонический issue — только `gate#354`; новый issue не создавай. Старые execution/lease истекли. Если нет свежего активного claim, запиши один новый bounded execution с одним lease, branch и PR, точным rollback revision, лимитом provider calls и stop conditions. Пользователь явно разрешил обработку существующих и новых голосовых в настроенных аккаунтах и телефонных записей в рамках этого проекта; никаких иных внешних эффектов это не разрешает.

Первая P0-задача: выяснить, почему production принимает новые Telegram voice, но они остаются в `processing`. На снимке 2026-07-29 было 82 received / 30 processing / 41 delivered / 11 dead letter, 71 settled и 3 unknown. Сначала докажи корень по Railway logs и privacy-safe состоянию, затем исправляй. Не делай слепых повторных платных запросов: если transcript/summary уже сохранён, заверши receipt без провайдера; unknown outcome сохраняй до безопасной сверки.

Далее:

1. Докажи и исправь автоматическую обработку обеих направлений Telegram voice на реальных update shapes RU/BY.
2. Разбери 9 `telegram_voice_not_inbound` и 2 `openai_transcription_transport_unknown` по точным безопасным исходам.
3. Проверь summary для всех успешных Telegram voice и phone calls; сделай точечный backfill пропусков.
4. Проверь длинный звонок с Ириной после chunking и один новый полный звонок без ручного копирования.
5. Исправь owner-only audio day/status и latest-transcript selectors: на прошлом снимке RU/BY selectors ошибочно возвращали `telegram_voice_archive_empty`.
6. Проведи новый реальный smoke: одно моё исходящее голосовое, одно входящее голосовое и один телефонный звонок должны получить original + transcript + summary + terminal delivered.

Работай автономно до завершения: минимальный цельный patch → локальные проверки → полный exact-head CI → merge → автоматический Railway deploy → проверка точного production fingerprint и реального результата. Следуй правилу `PR → полный exact-head CI → merge → автоматический production deploy`. Не называй проект готовым по CI/merge/deploy без runtime evidence.

После каждого материального этапа обновляй только существующий issue `#354`. В конце обнови `docs/voice/README.md`, запиши terminal redacted receipt и закрой `#354` только когда выполнен весь Definition of Done. От меня ничего не проси, пока не возникнет реальный human gate, credentials/payment/legal/destructive blocker или неоднозначность внешнего пользователя.

Начни ответ с короткого фактического статуса: текущий production revision, свежие voice counters, активный execution/lease и точная первая причина, которую проверяешь.

---

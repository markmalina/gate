# API и MCP: каталог фактической сборки

Baseline 86dbc0f429dce089bf733fc422f20f556535895b. Полные machine entries: [api-catalog.json](api-catalog.json).

## 1. Важное ограничение MCP

Определены 19 core tools и 5 transcription tools. В текущем create_app первым POST/mcp является communication_feed router, включённый через metadata router; он делегирует core напрямую. Второй transcription endpoint перекрыт. Поэтому **не описывать публичный baseline как 24 работающих tools**. Статическая модель показывает 19; dynamic runtime discovery в аудите не выполнялась.

Цепочка: main.py → mcp_metadata_routes.router → communication_feed_mcp → mcp_routes.mcp_endpoint. Feed — selector существующего gate_get_reply_context, не 20-й tool.

## 2. Достижимые core definitions

| Tool | Scope | Эффект / ограничение |
| --- | --- | --- |
|gate_get_status|gate:status:read|Scoped DB/status; не global census|
|gate_list_unanswered|gate:inbox:read|Scoped inbox read|
|gate_get_reply_context|gate:inbox:read|Context / attachments / voice; отдельные selectors могут читать media; feed использует stored facts|
|gate_get_owner_reply_readiness|gate:inbox:read|Readiness projection, не отправка|
|gate_save_reply_draft|gate:drafts:write|DB draft write|
|gate_prepare_reply_document|gate:drafts:write|Document staging/write, не send|
|gate_send_approved_reply|gate:messages:send|Telegram send после exact approval|
|gate_send_approved_document|gate:messages:send|Telegram document send после exact approval|
|gate_queue_approved_reply_sequence|gate:messages:send|Durable scheduling будущих sends|
|gate_alpha_list_installations|gate:status:read|Scoped installation state|
|gate_alpha_get_webhook_status|gate:status:read|Confirmed live Telegram webhook read|
|gate_alpha_control_installation|gate:agents:manage|Lifecycle mutation; approval/pause/resume/revoke по allowed state|
|gate_alpha_run_model_preflight|gate:agents:manage|Confirmed provider preflight; отдельный effect/data/cost scope|
|gate_alpha_list_shadow_reviews|gate:agents:manage|Scoped review read; может содержать private candidate|
|gate_alpha_get_test_group_presence|gate:agents:manage|Confirmed live presence probe, не send|
|gate_alpha_send_review|gate:messages:send|Exact canary/review Telegram send|
|gate_alpha_verify_review_replay|gate:status:read|Durable replay verification; не новый send|
|gate_beta_export_space_checkpoint|gate:agents:manage|Confirmed encrypted checkpoint export; сверить durable side effects|
|gate_beta_get_provider_probe|gate:agents:manage|Confirmed live Telegram identity/webhook diagnostic|

Режим temporary bearer даёт только gate:status:read и gate:inbox:read при explicit grants; его нельзя использовать для write/send/agents manage. OAuth discovery и tools/list в разрешённой OAuth конфигурации не выдают payload доступа; tools/call отдельно проверяется.

## 3. Определены, но перекрыты

| Tool | Scope | Назначение после исправления composition |
| --- | --- | --- |
|gate_alpha_create_transcription|gate:agents:manage|Dropbox stat и job write|
|gate_alpha_get_transcription|gate:status:read|Workspace job read|
|gate_alpha_list_transcriptions|gate:status:read|Workspace jobs read|
|gate_alpha_get_transcription_artifact|gate:status:read|Private artifact read/download|
|gate_alpha_retry_transcription|gate:agents:manage|Explicit failed-job successor attempt; потенциальный paid effect|

Их descriptors тестируются напрямую, что не доказывает HTTP достижимость. Перед подключением устранить availability/data/budget/lifecycle gaps A02–A04/A08. Upload route существует отдельно и не становится безопасным только из-за недостижимости этих пяти tools.

## 4. HTTP routes

Статически найдено 49 route declarations в app/api;46 входят в показанную main composition,34 уникальные method/path pairs. Дубли/ordering перечислены в JSON. Built-in FastAPI docs/openapi routes и отдельный I4 service сюда не включены.

| Группа | Paths | Основная защита / смысл |
| --- | --- | --- |
| Health |/live, /ready, /health, /deployment/fingerprint|Redacted public process/schema/revision; не product acceptance |
| Protected ops |/admin/ops/health|Admin bearer; aggregate two-worker health |
| MCP |/mcp|Feature/auth/scope; first-match ordering существенно |
| Discovery |/.well-known/oauth-protected-resource и /mcp; /mcp/metadata|Public configured resource/scopes; forwarded origin доверяется reverse proxy boundary |
| Business |/telegram/webhook|Secret + durable seam + legacy delegation |
| Isolated groups |/telegram/alpha/webhook, /telegram/beta/webhook, /telegram/become/webhook|Connector enablement, own secret/binding and installation checks |
| Watch |/internal/watch-voice/drain|Exact private Mark OIDC/run/main/workflow pin + request-local job token; empty body |
| Beta |/internal/beta/checkpoints/next, /ack|Pinned GitHub OIDC + exact batch/ack scope |
| Contacts |/admin/contact-snapshots|Protected bounded import; персональные данные, не unrestricted audit call |
| Dropbox Calls |GET/POST /dropbox/calls/webhook|Challenge/notification/HMAC/feature contract; source poll отдельно |
| Alfa upload |/alpha/transcriptions/upload|OAuth principal + bounded upload; common feature/budget gate gaps documented |
| Alpha admin |/admin/alpha/*|Admin bearer/lifecycle/preflight; POST может делать effects |
| Legacy admin |/admin/events/recent, /audit/recent, /status, /inbox/unanswered|Admin bearer; результаты могут быть private |
| Draft HTML |/admin/drafts/{draft_ref}/edit и /web-edit|Отдельный token/approval contract; не публичная shared ссылка |
| Historical one-off |/ops/owner-one-off/transcription/2026-08-03-chunks|Exact historical controls; не общий STT API |
| AQUB |Source module aqub_telegram.py|Не монтируется текущим main; frozen |

Здесь описаны route families. Для изменения конкретной функции читать её dependencies и final effect boundary; таблица не выдаёт её credential или право вызова.

## 5. MCP envelope и ошибки

JSON-RPC2.0: initialize, tools/list, tools/call с params.name и arguments. Текущий protocol2025-06-18. Core выдаёт schema/securitySchemes/_meta annotations; schemas в mcp_schemas.py. Metadata/core schemas сейчас поддерживают 19 core definitions и отдельный legacy status subset18.

Ошибки transport/auth и application tool result различаются. HTTP200/JSON-RPC result с content.error не является successful send. OAuth challenge/insufficient_scope не «исправлять» удалением требуемого scope.

Вызов с unknown tool возвращает JSON-RPC method-not-found; этим же путём сейчас заканчиваются shadowed transcription actions.

## 6. Selectors контекста

- Reply task UUID и предусмотренные exact attachment/voice/call selectors.
- `feed:day:YYYY-MM-DD` / `lenta:day:YYYY-MM-DD`: stored communication projection, current boundary Europe/Berlin, limit до 500.
- Watch receipt/original selectors защищены private workspace и UNCLASSIFIED scope; raw audio не публичная ссылка.
- Provider refs и rawTelegram IDs не заменяют друг друга; source time и Gate received time различаются.

Europe/Berlin — фактическая текущая константа feed, а не персональная timezone политика Марка. При смене на Europe/Minsk нужен явный контракт/проверка date-boundary/DST без пересчёта исторических timestamp.

## 7. Как тестировать API изменение

Запускать настоящий create_app с synthetic lifespan/dependency overrides и ASGI client. Проверять tools/list **и tools/call через HTTP**, feature-off, missing/wrong scope, foreign workspace, mutated replay, schema и exact side-effect count. Прямой вызов builder/helper недостаточен.

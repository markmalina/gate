# Telegram Business document delivery · Gate #700

Status: repository implementation on the `agent/gate-700-document-send`
branch. `GATE_DOCUMENT_SEND_ENABLED=false` is the default. No live document
send, recipient receipt or Production activation is claimed by the branch.

## Provider and local bounds

Telegram Bot API `sendDocument` supports `business_connection_id` and a
multipart document upload. Telegram's current general limit is 50 MB. Gate's
initial transport bound is 8 MiB for PDF and ZIP only, with a filename of at
most 255 UTF-8 bytes and an optional literal caption of at most 1024 characters.
The document call uses a 30-second write/read timeout; an upload timeout is
an **unknown** delivery outcome, never a safe automatic retry.
The separate inbound `getFile` >20 MB issue does not govern this upload.

## Required Work-facing path

1. Work selects and locally hashes a PDF or ZIP. Its bytes enter Gate only
   through the dedicated authenticated `gate_prepare_reply_document` tool's
   `content_base64` upload argument. The 8 MiB limit is checked before
   decoding. Neither text draft nor tool response contains the bytes. The
   24-hour staging record has a unique draft id and scoped principal, and an
   independent worker erases expired staged bytes hourly, including after
   sends are disabled. No GitHub file or
   external URL is involved. **The Work connector's practical payload limit
   for this argument is not yet measured**; a binary/chunked client adapter
   may be needed for larger files.
2. Preparation binds SHA-256, actual size, original name/MIME, exact caption,
   task, conversation, account, recipient and authenticated principal. The
   tool returns only those facts for display to Mark. It does not send.
3. `gate_send_approved_document` requires the exact draft id and explicit
   confirmation of RU/BY account, recipient, filename, digest, size, caption.
   It rechecks the scoped task, Business connection and `can_reply` rights,
   current window, document bytes and digest under a conversation fence.
4. Commit one unique attempt before provider I/O; invoke
   `TelegramClient.send_business_document` outside the transaction.
   Record the provider message id and returned document metadata. On
   timeout or ambiguous failure, retain a terminal `unknown` attempt.
   Repeated tool calls return the existing attempt and cannot resend.
5. Observe the outgoing attachment in scoped `get_reply_context` and compare
   recipient/account, provider message id, filename and MIME. The staged
   SHA-256 describes the bytes Gate uploaded. The provider result alone does
   not prove that the recipient downloaded or viewed the bytes.

The existing text draft and send tools must remain independent; confirming a
text draft never confirms a file or a different recipient. A shared
conversation fence must prevent ambiguous text/file overlap.

The default-off switch applies to preparation and fresh sends. Failed or
unknown attempts retain metadata for replay but clear staged bytes; attempts
never automatically retry after an ambiguous provider result.

## Acceptance matrix

| Scenario | Required outcome |
| --- | --- |
| RU ZIP and RU PDF; BY PDF | Exact account, filename, MIME, bytes, one receipt and scoped outgoing attachment |
| Wrong account, recipient or task | No upload to Telegram |
| Changed bytes, filename, MIME or caption after preparation | Fail closed before reservation |
| File over Gate bound; unsafe path or empty file | Reject before provider call |
| Disabled connection, expired reply window or missing rights | No send |
| Timeout/5xx or lost reply after provider call | Terminal unknown; retry returns same attempt |
| Definite 4xx | Failed receipt, no false `sent` |
| Existing text send | Same behavior and tests as before |

Before live acceptance, record one owner-approved test dialog and exact message
cap without publishing chat, user, or Business connection identifiers.

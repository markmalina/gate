# Telegram Business document delivery · Gate #700

Status: transport primitive implemented on the `agent/gate-700-document-send`
branch. The Work-facing preparation, durable binding, and send tools remain
unimplemented. No live document send is enabled by this branch alone.

## Provider and local bounds

Telegram Bot API `sendDocument` supports `business_connection_id` and a
multipart document upload. Telegram's current general limit is 50 MB. Gate's
initial transport bound is 8 MiB for PDF and ZIP only, with a filename of at
most 255 UTF-8 bytes and an optional literal caption of at most 1024 characters.
The current shared HTTP client's write timeout is five seconds; an upload
timeout is an **unknown** delivery outcome, never a safe automatic retry.
The separate inbound `getFile` >20 MB issue does not govern this upload.

## Required Work-facing path

1. An authenticated binary upload capability accepts a selected local artifact
   into short-lived private storage, producing an opaque immutable `file_ref`.
   The file never travels in the text draft, tool response, URL, GitHub, or
   audit log. Upload alone grants no external-send permission.
2. `prepare_document_send` binds `file_ref`, SHA-256, actual size, original
   name/MIME, exact caption, task, conversation, account, recipient, and
   authenticated principal. It returns those facts for display to Mark and an
   opaque preparation id. Preparation is not delivery.
3. `send_approved_document` requires the exact preparation id and explicit
   confirmation of RU/BY account, recipient, filename, digest, size, caption.
   It rechecks the scoped task, Business connection and `can_reply` rights,
   current window, document bytes and digest under a conversation fence.
4. Commit one unique attempt before provider I/O; invoke
   `TelegramClient.send_business_document` outside the transaction.
   Record the provider message id and returned document metadata. On
   timeout or ambiguous failure, retain a terminal `unknown` attempt.
   Repeated tool calls return the existing attempt and cannot resend.
5. Observe the outgoing attachment in scoped `get_reply_context` and compare
   recipient/account, provider id, filename and MIME. The provider result
   alone does not prove that the recipient downloaded or viewed the bytes.

The existing text draft and send tools must remain independent; confirming a
text draft never confirms a file or a different recipient. A shared
conversation fence must prevent ambiguous text/file overlap.

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

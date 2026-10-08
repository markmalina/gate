# Telegram Business document delivery · Gate #700

Current contract: PDF/ZIP plus original Office and image documents. The original
PDF/ZIP canary is historical evidence in #700; the 08 October extension has its
own implementation/CI/runtime/connected-Work/receiver checks. This document does
not claim that extension live acceptance has occurred.

## Provider and local bounds

Telegram Bot API `sendDocument` supports `business_connection_id` and a
multipart document upload. Telegram's current general limit is 50 MB. Gate's
transport bound is 8 MiB for every supported original document, with a filename of at
most 255 UTF-8 bytes and an optional literal caption of at most 1024 characters.
The document call uses a 30-second write/read timeout; an upload timeout is
an **unknown** delivery outcome, never a safe automatic retry.
The separate inbound `getFile` >20 MB issue does not govern this upload.

## Required Work-facing path

Read `gate_get_reply_context` first and review the current dialog. Require
`context_ready=true` and pass its exact `context_snapshot` as
`expected_context_snapshot` when preparing the document. If Gate returns
`context_changed`, review `current_context` and prepare/approve a new draft.
Never attach a refreshed token to an answer whose context was not reviewed.

1. Work selects and locally hashes one supported original file. Its bytes enter Gate only
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


## Original-format allowlist · 08 October extension

| Extensions | Canonical MIME |
| --- | --- |
| `.pdf` | `application/pdf` |
| `.zip` | `application/zip` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.xls` | `application/vnd.ms-excel` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.doc` | `application/msword` |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| `.ppt` | `application/vnd.ms-powerpoint` |
| `.jpg`, `.jpeg` | `image/jpeg` |
| `.png` | `image/png` |
| `.webp` | `image/webp` |
| `.gif` | `image/gif` |

The registry is `app/application/document_formats.py::DOCUMENT_TYPES`. Extending
it also requires a bounded parser, matching input/output MCP enums and tests.
Unknown types return `unsupported_document_type` with supported formats.
Office names/MIMEs are never changed to ZIP just because OOXML is a ZIP container.
No automatic ZIP, PDF conversion, image compression or format switching occurs.
The preview explicitly says `mode document (original file)`. Photo/album/native
Rich media belong to #742/PR744; this change reuses the existing document staging,
SqlTelegramReplySender, durable attempt, scope fences and Telegram HTTP client.

### Parser and resource bounds

All new-format validation runs in a disposable Linux subprocess, up to two
active validators per ASGI process, queue wait 100 ms, wall deadline 5 s,
CPU 3 s, address space 384 MiB. Timeout/cancellation kills and waits for the
child before releasing the slot. Bytes, filenames and parser exceptions are
not logged. Busy validation returns a clear preparation error, never a send.

OOXML: at most 2048 ZIP entries, total declared inflation 64 MiB, inspected XML
parts at most 2 MiB each; no encryption, duplicate/traversing names or unsupported
compression. Main part content type, package relationship and document root must
match the chosen extension. Defused XML refuses DTD/entities/external resolution
including UTF-16. Macro-enabled/encrypted OOXML is unsupported; `.xlsm/.docm/.pptm`
are outside the allowlist. Legacy OLE uses the bounded read-only olefile parser
and requires the corresponding Workbook/WordDocument/PowerPoint Document stream.
Legacy macros remain bytes and are never executed. These are format integrity
checks, not antivirus or proof that every application will open arbitrary content.

Images: Pillow verifies and decodes in the same bounded subprocess solely for
validation. Original bytes stay unchanged. At most 64 frames and 16 million
aggregate decoded pixels; decompression bombs/corruption/MIME mismatch fail closed.
A GIF remains a document, not an animation; WEBP is not silently converted to PNG.

PDF/ZIP preserve their accepted legacy signature checks and response shape.
Every pre-send still verifies bytes/hash/size/expiry/scope; canonical MIME is
rederived from the unchanged filename to reject metadata substitution before I/O.
Provider acknowledgement must match file name, MIME, size and bound route.

### Concrete local Work bridge

A `sandbox:` link is not a Gate server file. Use the selected absolute local
path with `scripts/gate_document_upload.py::upload_arguments`, or its CLI:

```bash
python scripts/gate_document_upload.py /absolute/selected/report.xlsx \
  --task-id VERIFIED_TASK_UUID --expected-context-snapshot REVIEWED_CONTEXT_SNAPSHOT \
  --caption 'Согласованный отчёт' --follow-up
```

Consume its JSON stdout privately in code and pass that object directly to the
connected `gate_prepare_reply_document` tool. Do not print the returned upload
JSON/base64 to the chat, store it in logs/Git, or send it as a text draft. The bridge
refuses URLs, relative paths, leaf symlinks, non-regular/oversized files and changes
during reading. Gate separately validates its SHA-256, content and authenticated
workspace/task/account/project scope. Staging returns metadata only and does not
create permission to send. Show the exact returned preview, then call
`gate_send_approved_document` with its draft id, exact account/recipient/file
name/size/SHA-256/caption and formatting payload SHA-256 after owner approval.

The staging bytes expire after 24 hours (hourly cleanup); unresolved provider
attempts never authorize a second upload/send. The actual connected Work catalogue
must advertise the new MIME enums, not merely the server schema. Refresh the
installed Gate M tools through the supported connector control if the Work still
shows PDF/ZIP only. No provider credential or parallel sender is needed.

### Extension acceptance evidence to record

- Literal PR head/green CI, including PostgreSQL byte/replay/scope/concurrency cases.
- Current-main Ops receipt and scoped deployed fingerprint/health.
- Actual connected Work upload and preview calls for Office and original images.
- Exact synthetic owner-only approval manifest and hard message/attempt caps before live.
- Provider receipt + scoped outbound attachment + recipient download hash/openability.

No synthetic legacy CFB container fixture alone proves Word/Excel/PowerPoint can
open it. No provider HTTP 200 alone proves recipient download/hash equality.
Keep #700 open until those remaining product checks are evidenced.


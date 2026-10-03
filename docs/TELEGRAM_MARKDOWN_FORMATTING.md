# Telegram formatting for approved replies (#727)

Gate accepts an explicit `plain` or `markdown` mode when preparing text.
The default is `plain`; stars, underscores, backticks, HTML-looking text and
other characters remain literal in that mode. Gate never guesses the mode.
The Markdown converter is `gate.telegram-markdown.v1`, a bounded documented
dialect rather than unrestricted CommonMark or Telegram MarkdownV2.

Telegram receives normalized text with explicit `entities`, or a document
caption with `caption_entities`. No `parse_mode` accompanies these payloads.
Official references: [MessageEntity](https://core.telegram.org/bots/api#messageentity),
[formatting](https://core.telegram.org/bots/api#formatting-options),
[sendMessage](https://core.telegram.org/bots/api#sendmessage), and
[sendDocument](https://core.telegram.org/bots/api#senddocument).

## Supported Markdown v1

| Input | Prepared Telegram presentation |
| --- | --- |
| `**bold**` | Bold entity |
| `*italic*` or `_italic_` | Italic entity |
| `~~strike~~` | Strikethrough entity |
| `__underline__` | Underline entity; deliberately differs from CommonMark bold |
| `||spoiler||` | Spoiler entity |
| Single-backtick inline code | Code entity; literal contents, nonempty and on one line |
| Standalone matching backtick fences, optionally followed by a language | Preformatted code entity with optional language |
| `[label](https://example.com/docs?a=1&b=2)` | Text-link entity; query strings and balanced parentheses preserved |
| ATX headings: one to six `#` followed by whitespace | Heading markers removed and heading text made bold |
| `-`, `+`, `*`, `1.`, or `1)` followed by whitespace | Literal list marker, with formatted item text |
| A run of lines beginning with `>` | Blockquote entity; quote prefix removed |
| Backslash followed by punctuation | Literal escaped punctuation |

Cyrillic, emoji, punctuation and line endings are preserved in the prepared
text. Entity offsets and lengths use UTF-16 units; an entity cannot split an
emoji or another Unicode character. Underscores inside an alphanumeric word
are literal, so `sample_value` remains a word. Valid nested styles and styles
inside link labels are supported. Crossing ranges, nested links, overlapping
code/styles and unsupported combinations fail before any send.
Inline styles and links must fit on one line; close and reopen a style on each
line rather than spanning a newline. Fenced code and blockquotes may span lines.

Tables, images, HTML/angle-bracket links, reference links, horizontal rules,
nested blockquotes, tilde fences and malformed or unclosed delimiters are
rejected with a safe error. Use a shown plain-text version or separate approved
messages for such content. Code fences require standalone opening and matching
closing lines. This converter does not claim full CommonMark compatibility.

The converter accepts at most 65,536 source characters, 100 entities and
16 levels of nesting. Prepared text uses a conservative limit of 4,096 UTF-16
units; document captions use 1,024. These limits apply **after** conversion.
Text replies cannot be empty or whitespace-only. A document caption may be
exactly empty; whitespace-only captions are rejected. Gate rejects oversized
output; it does not split messages automatically. For a longer text, prepare
and show each separate draft and confirm the ordered sequence explicitly.

## Preview, storage and approval

1. Call `gate_save_reply_draft` with `text_format="markdown"` or `"plain"`.
   For a document, call `gate_prepare_reply_document` with
   `caption_format="markdown"` or `"plain"`.
2. Show the returned normalized final `text`/`caption`, formatting preview,
   account and recipient to Mark. A formatting preview contains source text,
   normalized text, mode, converter version, entities and `payload_sha256`.
   For a document also show filename, MIME, byte count and file SHA-256.
3. Confirm that exact preview. Pass its `payload_sha256` as
   `expected_payload_sha256` to `gate_send_approved_reply` or
   `gate_send_approved_document`, alongside the existing account/recipient
   confirmation and exact file metadata when applicable.
4. For `gate_queue_approved_reply_sequence`, pass the `draft_ids` in displayed
   order and `expected_payload_sha256s` in the same order. Confirm all drafts,
   the account and recipient together. Existing 2–50 bounds and the minimum
   60 seconds after a preceding confirmed delivery remain unchanged.

Every newly prepared draft, including plain mode and an empty document
caption, has a formatting snapshot. Text snapshots are stored under
`telegram_formatting`; document caption snapshots under
`telegram_document_caption_formatting` in the existing draft JSON metadata.
The file bytes and file SHA-256 retain their existing independent protection.
No database migration or new schema head is introduced.

Send validates the saved snapshot and approved digest, then uses the saved
normalized text/entities without running the Markdown converter again.
The digest binds the source, normalized text, entities, mode and converter
version. An entities-only edit, changed URL, changed body, replaced snapshot
or mismatched approval fails closed. The queued item freezes the digest and
the worker carries it into the send command after restart/discovery; a changed
item stops the remaining sequence without an HTTP send for that item.

Legacy drafts with no formatting metadata continue their existing literal
text or caption behavior. A new snapshot cannot be sent using a legacy
approval that omits the payload digest. A legacy-only sequence may still omit
digests. Mixed legacy/new sequences are rejected: prepare fresh explicit
previews for all items before confirming one ordered digest list.

Authorization, scope, account/recipient binding, document confirmation,
delivery fencing and idempotent replay remain in force. A provider timeout or
unknown outcome never triggers an automatic plain-text retry or fallback.
Telegram refusal is recorded as failure according to the existing transport
contract. A successful delivery receipt proves provider acceptance and stored
delivery, not the font rendering in a Telegram client.

## Registered Work client and live acceptance

After a reviewed release, check the actual Production fingerprint and server
tool schema, then the tools available in the current Work. Confirm that the
registered `Gate M` client exposes the new preparation modes, preview and
send/sequence digest parameters; a backend deployment alone does not refresh
the registered client. If the client remains stale, stop formatted sends and
request its supported refresh/reconnection. Do not assume an unavailable
refresh API or silently bypass the registered client.

Repository tests use synthetic inputs and disposable PostgreSQL only.
Production rollout and live sends are separate gates. The proposed owner-only
visual check uses the existing direct RU-to-BY self-chat, freshly scoped to an
approved test task, synthetic content, no AI provider calls and an exact
outbound-attempt cap. No trainer, contact history, health information or other
third-party payload is a test fixture.

Before live acceptance record exact source/deployed revision, target, shown
previews/hashes, caps, expiry and stop/rollback conditions. Evidence must
include Telegram client rendering of bold, italic, code, links and line breaks,
plus literal plain mode and the checked reply/sequence/caption surfaces.
Preserve an unknown outcome without resend. Stop on unexpected participants,
payload, recipient, source drift, health regression or duplicate delivery.
Issue #727 remains open until independent verification and the visual/runtime
acceptance criteria are completed; CI does not establish rendering.

## Integration with the separate #722 release

This change is based on schema `0111_reply_documents` and its existing legacy
sequence worker. It does not merge, activate or accept PR #720, PR #724, the
0112–0115 migration chain, inbound-stop/parallel mode or #722 release controls.
When that separate candidate is integrated later, its v2 approval records,
dispatch/readback paths and worker snapshots must retain the exact formatting
digest binding and run these formatting regressions. Do not transfer this
legacy worker acceptance to the v2 sequence implementation automatically.

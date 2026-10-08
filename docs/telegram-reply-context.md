# Telegram replies in Gate M

`gate_get_reply_context` adds optional `messages[].reply_to` when an exact
stored update contains a reply or quote. No new input parameter or provider
call is required. Existing task, pagination, voice and attachment selectors
remain unchanged. This change needs no migration or history rewrite.

The projection includes the immediate source message ID, author display name,
source date, full bounded text/caption, attachment filename/type/size and the
selected quote text. `quote.position` is the sender's approximate UTF-16
offset; it is not recomputed from Python character indexes. The nested
Telegram snapshot works even when the original message is outside the current
history page. It describes the stored snapshot, not a newly fetched live copy.

Only a source in the selected chat is projected. The parent update, event,
message, chat and Business connection must match the scoped stored message.
The nested chat, message ID and any supplied Business connection are checked
separately. Text reads use the existing account/project/conversation scope.
Voice reads additionally retain their verified-identity requirement and exact
archive/receipt update identity. The reply survives legacy-to-voice deduplication.

`status=snapshot` means source text, caption or supported media metadata is
present. `status=unavailable` explicitly reports missing content, mismatched
source identity, or an external reply. A message without reply evidence has
no `reply_to` field. External-reply content is deliberately not projected.
Ephemeral sources omitted by Telegram cannot be reconstructed by this change.
No filename similarity or chronological-neighbor guess is used.

This is metadata discovery, not a download grant: quoted attachment summaries
do not contain a download selector. Existing explicit attachment selectors
remain restricted to their original message and allowed page. Provider file
IDs, sender IDs, vCards, thumbnails, forward origins and nested reply chains
are excluded. No source payload is logged or copied into fixtures.

Official source checked 2026-10-08:
- https://core.telegram.org/bots/api#message (`reply_to_message`, `external_reply`, `quote`)
- https://core.telegram.org/bots/api#textquote (`position`, `is_manual`)

Regression coverage: `tests/test_mcp_reply_context.py` exercises old-document,
text/partial-quote, missing/external source, identity mismatches, all stored
payload sources, standalone and merged voice, schema and readable MCP content.
Tests are synthetic; they do not call Telegram, AI providers or Production.
Repository/CI evidence is separate from merge, deployed revision and live
product acceptance. Tracking issue: AlphaCenter/gate#748.

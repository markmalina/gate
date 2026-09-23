# Telegram Business contact readback

Issue: [Gate #694](https://github.com/AlphaCenter/gate/issues/694).

## Boundary

Telegram Bot API may put a `contact` object on a `message` or
`business_message`. Gate's authenticated webhook already stores the complete
update in its durable receipt and, on the legacy route, the Telegram update.
The human inbox record identifies the account, conversation, update and message.
This change does not import an address book, call MTProto, send a message, or
change Relations person facts.

`gate_get_reply_context` exposes a `contact` message only under the existing
account/project/conversation scope and only when a stored update matches the
message's update ID, event type, Telegram message ID, chat ID and Business
connection. The contact object returns bounded first/last name, phone and
optional Telegram user ID, plus `vcard_present`. The vCard body is not parsed
or returned. The opaque source reference and provider message ID retain
provenance. The existing message/update uniqueness rules still deduplicate
replayed delivery.

The day feed returns only the generic semantic `Получен контакт`. It never
returns the card's name, phone or vCard. A document/photo remains a separate
attachment; neither a missing nor a malformed contact is invented.

## Historical data and retention

An old contact can be read back only if its exact typed update remains in the
existing `telegram_updates`, `gate_events`, message payload or canonical
receipt. No migration or retroactive provider fetch is performed. A blank
legacy message without such payload remains blank. The change does not add a
new persistent copy or extend the existing raw-update retention period. A
provider vCard may already exist inside a pre-existing raw receipt; the
readback intentionally does not reveal it.

## Release and verification

Run the deterministic synthetic contact, schema, feed and inbox tests and the
normal exact-head CI. A production deployment and live third-party data
readback require the current exact owner gate. After deployment, inspect one
already-authorized scoped conversation for a typed `contact`; record only a
redacted status (not personal fields) in the issue. A fresh canary must be an
owner-authorized contact in an existing Business dialog. Roll back application
code to the recorded previous release if scope, schema or privacy checks fail;
no data migration or deletion is needed.

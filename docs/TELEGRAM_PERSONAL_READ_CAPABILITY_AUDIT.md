# Personal Telegram read capability audit

> Status: P2 repository audit complete; runtime remains disabled  
> Issue: [Gate #279](https://github.com/markmalina/gate/issues/279)  
> Captured: 2026-07-22  
> Repository baseline: `d30d63ab598faae9ede4b3199caa3e31482aeb10`

## Executive decision

Gate must use the least-powerful surface that satisfies each use case:

1. **Telegram Business / Bot API** for future events and actions in already connected
   business dialogs and for Mayak-owned bots, groups and channels.
2. **Owner-controlled export** for a one-time, explicitly selected historical bootstrap
   when a bounded offline import is acceptable.
3. **MTProto user API** only for a residual requirement that cannot be met by the first
   two surfaces, after separate owner data authorization and a Telegram-terms/compliance
   decision.
4. **Reject** secret-chat ingestion, bulk account surveillance, silent read-state
   mutation and unrestricted history/contact replication.

The first MTProto read canary is **not ready to activate**. The repository has protected
owner enrollment and default-off flags, but no read port, runtime adapter, opaque dialog
allowlist, retention/deletion implementation or Relations projection. More importantly,
Telegram's current API Terms prohibit using API-derived data to train, improve or deploy
AI/ML technologies. Gate must fail closed before routing personal Telegram content to
Alfa or any model until an owner-approved compliance interpretation and permitted design
are recorded.

This audit performs no provider call, credential or OAuth action, dialog discovery,
message/media read, send, publication, production/data mutation or real-person-data
operation.

## Official capability sources

| Surface | Official source | Relevant documented capability |
| --- | --- | --- |
| Bot API updates | [Bot API](https://core.telegram.org/bots/api) | Business connection, new/edited/deleted business-message updates; webhook or polling update stream; pending updates retained for no longer than 24 hours |
| Bot visibility | [Bot FAQ](https://core.telegram.org/bots/faq) | Private bot chats; channel posts where the bot is a member; group visibility constrained by admin/privacy mode; bots do not see other bots' messages |
| Business evolution | [Bot API changelog](https://core.telegram.org/bots/api-changelog) | Business connection/message events and later business rights/read methods |
| Dialog discovery | [messages.getDialogs](https://core.telegram.org/method/messages.getDialogs) | Current user's dialog list; user accounts only |
| Cloud history | [messages.getHistory](https://core.telegram.org/method/messages.getHistory) | Peer history in reverse chronological order; user accounts only |
| Search | [messages.search](https://core.telegram.org/method/messages.search) | User-account message search |
| Contacts | [contacts.getContacts](https://core.telegram.org/method/contacts.getContacts) | Current user's Telegram contacts; user accounts only |
| Offline export | [Telegram export](https://core.telegram.org/import-export) and [takeout API](https://core.telegram.org/api/takeout) | Owner-selected JSON/HTML export and scoped takeout categories |
| Read state | [messages.readHistory](https://core.telegram.org/method/messages.readHistory) | Marks history read; this is a state mutation, not a passive read |
| Files | [upload.getFile](https://core.telegram.org/method/upload.getFile) | User-API file retrieval for a known location |
| Secret chats | [End-to-end encryption](https://core.telegram.org/api/end-to-end) | Device/authorization-key-specific E2E protocol distinct from cloud chats |
| API obligations | [Telegram API Terms](https://core.telegram.org/api/terms) and [API application guidance](https://core.telegram.org/api/obtaining_api_id) | Privacy, transparency, consent and anti-abuse duties; current AI-use restriction; own API application required |

The documented Bot API is an update/action surface. It has no documented equivalent of
`messages.getDialogs`, `messages.getHistory` or `contacts.getContacts` for arbitrary
owner-account backlog and address-book discovery. That is a capability-gap finding, not
a claim about undocumented provider behavior.

## Capability matrix

| Product need | Business / Bot API | Owner export | MTProto user API | Gate decision |
| --- | --- | --- | --- | --- |
| New connected private-business messages | Primary: business-message updates | Not continuous | Possible but over-privileged | Use Business API |
| Edited/deleted connected messages | Future edit/delete updates for known business dialogs | Point-in-time only | Possible for cloud history/updates | Use Business API; retain deletion tombstone, never reconstruct deleted content |
| Arbitrary personal dialog discovery | No documented account-wide dialog-list method | Exported chats only | `messages.getDialogs` | MTProto only if an exact selected-dialog workflow cannot avoid discovery |
| Historical cloud messages | No arbitrary backlog method | Preferred bounded one-time source | `messages.getHistory` / search | Export first; MTProto only for justified incremental residual |
| Mark's saved contact names | Bot sees provider profile/chat data, not private saved labels | Existing Desktop-export path is preferred | `contacts.getContacts` | Keep export primary; contact API separately gated and excluded from first canary |
| Mayak private chats | Primary through the installed bot/business connection | Not applicable | Not needed | Use official bot/business surface |
| Mayak groups/channels | Bot membership, admin rights and privacy mode define visibility | Not applicable | Personal account inventory is broader than needed | Use official bot surface and explicit installation capabilities |
| Messages from other bots | Bot API says bots do not receive other bots' messages | Point-in-time export may contain owner-visible material | Owner account may see cloud dialog content | Do not add MTProto merely to bypass bot-to-bot visibility |
| Media in new events | Message metadata/file identifiers available when visible to bot | Preferred for selected historical media | Known-file retrieval possible | No media download in first read canary |
| Mark messages read | Business read method can mutate read state when granted | No mutation | `messages.readHistory` mutates state | Passive canary must never mark read |
| Personal-account send | Business send only in connected/authorized context | Impossible | Existing Stage D contract can support one exact attempt | Keep send separately default-off and exact-text gated |
| Secret chats | Not available | Not cloud-exported as normal history | Separate device-bound E2E implementation | Reject and keep out of scope |
| Alfa life analysis | Carries scoped facts only | Potential bounded bootstrap source | Potential residual source | No raw history dump; blocked pending Telegram-terms/compliance and explicit owner data gate |

## Current Gate implementation

| Layer | Implemented | Contract-only / missing |
| --- | --- | --- |
| Business transport | Durable RU/BY ingress, bounded context, draft, exact-confirmed send and receipts | Account-wide arbitrary history and saved owner labels |
| Personal policy | Independent `read_dialogs_enabled`, `contact_sync_enabled`, `send_enabled`; all default false | No capability grant persistence or dialog allowlist |
| Personal provider port | One `send_exact` protocol and fake synthetic adapter | No read/list/history provider port or real runtime adapter |
| Session custody | Protected Telethon 1.44 enrollment; Fernet ciphertext; Windows CurrentUser DPAPI-wrapped key; redacted receipt | Runtime does not load or wire the session; `EncryptedSessionSecretStore` is only a protocol |
| Read safety | Stage C checklist: max three selected dialogs, fixed window, minimum projection, no bulk history/secret/deleted/contact import | No code enforcing peer scope, limit, window, data class, retention or deletion |
| Relations / Alfa | Domain boundaries and source-fact contracts exist | No accepted personal-Telegram projection; AI use blocked pending terms/compliance decision |
| Audit / rollback | Default-off policy, no-send-without-approval contract, setup revocation procedure | No read-canary terminal receipt or automatic capability expiry |

## Residual MTProto requirements

Only two candidate residuals survive this audit:

1. Resolve an owner-selected personal cloud dialog to an opaque `peer_ref` when the
   official Business surface and an owner export cannot identify it safely.
2. Fetch a very small incremental cloud-history window for that exact allowlisted peer
   when a continuous product need cannot be served by future Business events.

Full contact sync, full dialog inventory, bulk history, secret chats, media backfill,
read-state mutation and send are not part of the read canary. A saved-name requirement
uses the existing Desktop-export path unless a new separately approved contact-sync
decision proves it insufficient.

## Minimum future read-only transport canary

This contract may be implemented and tested synthetically in a repository-only PR.
Activation requires both the compliance gate and an exact owner data authorization.

| Control | Required value |
| --- | --- |
| Account | One safe account label bound to one opaque `session_ref` |
| Dialog | One owner-selected cloud dialog bound to one opaque `peer_ref`; no unrestricted listing returned to Gate logs or GitHub |
| Window | One shot; at most the five most recent messages from the preceding 24 hours |
| Data | Envelope metadata only for the transport canary; no message text routed to Alfa/model |
| Excluded | Contacts, secret chats, search, media bytes, deleted content, read-state mutation, typing, send |
| Persistence | Raw provider payload remains in memory and is discarded after validation |
| Receipt | Counts, time bounds, policy version and digests only; no names, text, provider IDs or credentials |
| End state | Read capability returns to disabled automatically |
| Failure | Fail closed; no retry on scope mismatch, flood/rate response, unknown outcome or terms uncertainty |

A later content-projection experiment is a different stage. It requires exact data
classes, purpose, recipients/processors, retention/deletion, source provenance,
correction, model-use compatibility and owner approval. Passing the transport canary
does not authorize that experiment.

## Human gates

Before any live read, the issue must record all of the following:

1. The permitted Telegram-terms/compliance basis for the exact use; absent a recorded
   basis, personal content must not be passed to Alfa or another AI system.
2. One safe account label, one selected dialog label/opaque reference, the exact time
   window, data classes and purpose.
3. The retention/deletion behavior and whether any content may leave protected local
   memory.
4. A separate explicit authorization for the provider call and real-person-data read.

Credentials, login codes, API hash, phone, session material, raw peer identifiers,
message text and names never belong in chat, GitHub, CI or receipts.

Read authorization never authorizes contacts, media, read-state changes or send.
Send remains Stage D and requires the exact account, recipient and complete text.

## Rollback and next work

Repository-only rollback is removal of the audit/registry changes. A future live canary
must disable the read flag automatically, discard transient payloads and retain only the
redacted terminal receipt. Revoking a real session or deleting its encrypted artifacts is
an owner-controlled external action and needs an exact command.

The next safe P2 unit is a repository-only, provider-neutral read contract with fake
adapter tests that enforce the limits above. It must not import Telethon at runtime or
read any dialog. Live activation remains blocked behind the two human gates.

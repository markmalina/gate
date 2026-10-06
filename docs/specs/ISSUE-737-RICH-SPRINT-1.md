# Gate #737 — native Rich replies, sprint 1

Execution: `gate-rich-737-20261006-155742`; parent #735, implementation #737.
Owner launch on 2026-10-06 permits the bounded implementation, independent
verification, exact-head merge, protected Ops publication/rollback and only the
specified synthetic own-account RU↔BY fixtures. Implementation readiness does
not prove live capabilities, delivered messages or client rendering.

## Contract and current path

Existing `gate_save_reply_draft` → `SaveReplyDraftService` →
`SqlReplyDraftWriter` → existing JSON `ReplyDraft.prompt_context`;
`gate_send_approved_reply` → existing admission/scope/confirmation →
`SqlTelegramReplySender` preflight/probe/reserve → shared `TelegramClient`
HTTP transport → durable `SendAttempt`, private outbound Message and AuditLog.
No migration, provider SDK replacement, direct Work HTTP send or model call.

Explicit `text_format=rich` requires `rich_content` and omitted or empty `text`.
Any nonempty legacy text conflicts. `rich_content` on plain/markdown/omitted mode
is rejected, including an explicitly supplied null. Legacy omission,
plain/markdown, their series and document captions retain #727 behavior.

`rich_content` has exactly `schema_version=gate.rich.v1` and `blocks`.
Every object rejects unknown fields. Inline RichText is a nonempty string, a
nonempty array, or `{type,text}` with bold/italic/underline/strikethrough/code.
Links use `{type:"url",text,url}` with an HTTP(S) URL. Code inline content is
literal text; links cannot be nested. Invalid Unicode/NUL, unsupported types,
media, formula, details, nested block lists/quotes and surplus fields fail before
sending. A table requires a nonempty header and data rows of equal width;
blank cells are rejected, including styled whitespace.

| Gate block | Input | Exact native provider mapping |
| --- | --- | --- |
| heading | text, size integer 1–3 | heading/text/size |
| paragraph | text RichText | paragraph/text |
| list | items RichText[], optional ordered boolean and start 1–1000 | list/items[] each containing paragraph blocks; ordered items additionally value and type="1" |
| blockquote | text RichText | blockquote/blocks containing one paragraph |
| pre | literal text, optional language | pre/text/language, whitespace and terminal newline unchanged |
| table | header RichText[], rows RichText[][] | table/cells, is_bordered=true; cells have text, align=left, valign=top; header cells alone is_header=true |

Provider method is `sendRichMessage` with `rich_message={blocks:[...],
skip_entity_detection:true}`. Exactly blocks is used, never html/markdown/media,
parse-mode guessing, automatic splitting, paid broadcast or retries.
Language is supplied to the client; highlighting is not guaranteed.

## Limits (application limits, not claims about Telegram)

| Bound | Limit |
| --- | ---: |
| Top-level blocks | 32 |
| Total textual leaf UTF-16 units | 16,000 |
| Source JSON UTF-8 bytes and native provider rich JSON bytes | 65,536 each |
| Inline nesting depth | 4 |
| Inline nodes across the message, including arrays/styles | 512 |
| List items per list | 32 |
| Ordered start | 1–1,000 |
| Table data rows, excluding header | 20 |
| Columns | 6 |
| Visible text UTF-16 units per cell | 512 |
| Literal pre code UTF-16 units | 8,192 |
| URL UTF-16 units | 2,048 |
| Language | 0–32 ASCII letters/digits/underscore/plus/dot/hyphen |

All bounds apply together. The finite tools/list schema advertises the typed
six-block contract; runtime additionally enforces Unicode, nesting combinations,
ragged/blank cells and shared byte/text/node bounds. Rejection never splits.

## Saved preview, consent and truthful receipt

The existing `telegram_formatting` JSON key contains a versioned rich snapshot:
original `source_content` and canonical `source_text`, saved native `blocks`,
complete semantic `text`, `schema_version`, `converter_version`, `revision=1`,
limits and scoped workspace/account/project/recipient/connection/draft binding.
Each save creates a new draft and hash; revision 1 identifies that immutable
snapshot, rather than a mutable document counter. Source strings/code remain
exact. Arrays, styling, table cells, numbering, links and language are all visible
in the full structured preview. It is a semantic preview, not a pixel promise.

SHA-256 covers all saved fields except itself, including converter version,
revision, delivery mode, native blocks and exact scope. Scope is resolved inside
the existing conversation lock by the SQL writer. Any changed sender account,
recipient, connection, workspace, payload, version or hash invalidates consent.
The send confirms `expected_account`, `expected_recipient_chat_id`,
`expected_payload_sha256` and `confirmed=true`. Approval is the explicit exact
send command; creating a draft does not approve or send it.

Send validates the saved hash/source JSON/native blocks and consumes the stored
blocks. It never calls the rich converter again. Rich probes only the exact
saved Business connection; a replacement connection cannot inherit approval.
Rich in queue/series or captions fails explicitly with
`rich_surface_unsupported`. Legacy admin/callback/text edit and personal sends
retain their saved-formatting fail-closed fences.

Durable reservation precedes HTTP under existing conversation locks and unique
per-draft SendAttempt. Two sessions, replay and restart reuse the reservation.
A returned provider message must include a positive integer message ID,
matching chat and Business connection, and a nonempty native rich_message.
Malformed success, timeout/transport/5xx are unknown; there is no resend or
fallback. A verified provider 4xx rejection is failed and likewise never
blindly reposted. Rich provider errors expose only safe codes, never the raw
provider description or source content.

If receipt finalization fails after provider success, Gate rolls back that
transaction and attempts to save `unknown/rich_receipt_pending` plus available
private provider message-ID evidence. If storage is unavailable, the existing
SENDING reservation remains a durable no-resend fence; a later rich replay
reports unknown rather than implying safe failure. Existing recovery reconciles
stale reservations; no new automatic writes are introduced.

The rich receipt includes operation/draft references, exact payload hash,
`delivery_mode=rich`, `draft_revision`, original reservation `runtime_revision`
and `reservation_time` from durable private reservation AuditLog, status,
replay flag and protected provider evidence. The original runtime metadata is
read on replay, not replaced by the runtime handling that later query.
Private MCP recipient/provider IDs stay inside the scoped connection; public
Git/report artifacts contain only synthetic IDs or redacted labels.

## Operator example and actual steps

[Complete valid JSON-RPC save call](ISSUE-737-RICH-OPERATOR-EXAMPLE.json) uses
synthetic task UUID `00000000-0000-4000-8000-000000000737`. Replace only that task
with the authorized scoped task supplied by Gate; choose the account by the
existing scoped task, never by guessing. Exact P/D/L/M content is in
`tests/fixtures/gate_rich_sprint1.json`.

1. Call the example through the connected Work catalogue.
2. Display the entire `structuredContent.formatting`, sender label and recipient.
3. Receive ordinary-user exact consent (or use only the already authorized
   current #737 owner fixture scope).
4. Call `gate_send_approved_reply` with the returned draft_id,
   confirmed=true, and all three returned confirmation fields unchanged.
5. Record the operation reference, digest, runtime revision/time and delivery
   outcome; replay that same command to obtain `idempotent_replay=true` without
   another provider write. Unknown means stop, bounded reconciliation only.

Example send envelope (substitute saved result fields, not new content):

```json
{"jsonrpc":"2.0","id":738,"method":"tools/call","params":{"name":"gate_send_approved_reply","arguments":{"draft_id":"00000000-0000-4000-8000-000000000737","confirmed":true,"expected_account":"RU","expected_recipient_chat_id":"synthetic-recipient","expected_payload_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}}}
```

The send example is structurally valid and intentionally synthetic; it grants
no authority or real destination. Only a real saved hash/binding can pass.

## Error, fallback and activation guide

`invalid_arguments` rejects conflicting text/mode; `invalid_rich_content` rejects
shape/limits; `formatting_changed` rejects snapshot mutations;
`rich_scope_changed` and `rich_connection_changed` reject target transfer;
`payload_confirmation_required/mismatch` reject missing/stale consent;
`rich_send_disabled` is the rich kill switch. Existing global/per-account,
permission, Business rights, dispatch admission and canary fences remain.

Settings defaults rich off. The existing source-controlled Production
`railway.toml` start command activates rich using
`GATE_RICH_SEND_ENABLED=${GATE_RICH_SEND_ENABLED:-true}`. An explicit existing
environment false remains a kill switch; staging has no activation export and
stays default-off. Ordinary drafts opt in only via explicit `text_format=rich`;
no existing text is automatically upgraded. Publication still uses the accepted
`/ops-deploy production target=gate` route, with no Ops/AQUB code/config change.

A generic `telegram_api_400` proves a failed write outcome only; it does not
prove stable account/method Rich unavailability. Official Bot API documentation
does not enumerate stable Rich-specific rejection tokens, so no guessed error
string whitelist or unsupported-account cache is introduced. Temporary and
unknown outcomes likewise are not classified as unsupported. Reliable independent
pre-send/account capability denial may lead to a separate #727 plain/markdown
fallback draft and hash. An ordinary user must approve that new
preview. The exact #737 fixtures permit fallback only when rich unavailability
is established before its provider write. A failed/unknown rich write does not
authorize sending fallback. A preformatted table is a documented fallback and
never evidence of native table rendering. Capability confirmed, delivery
confirmed and visual confirmed are separate states.

The registered Ops fast lane publishes only current main; it cannot publish an
arbitrary historical SHA or execute a later provider rollback. Recovery therefore
uses a reviewed Gate revert/fix-forward commit restoring the recorded LKG
behavior and prior railway command, then the same current-main Ops deployment.
The resulting source SHA is new and must never be represented as literal LKG
publication. Existing JSON data remains compatible with the prior runtime
(unknown rich snapshots fail closed); no DB downgrade/delete is required. On wrong sender/recipient, duplicate, approval bypass, wrong SHA
or health regression: stop new rich writes, use the available kill/global send
switch or authorized LKG rollback; do not delete history/messages automatically.

## Acceptance evidence and live bounds

Repository tests cover native schema, exact HTTP/semantic payload,
Unicode/code/query preservation, boundary/excess limits, scope mutations,
consent, no provider before denial, two-session and restart replay, timeout,
malformed success, safe error metadata, post-success finalization ambiguity and
plain/markdown/legacy/series/caption regression. PostgreSQL skips are
UNVERIFIED, never PASS; required hosted CI starts disposable PostgreSQL17.
No migration A15 is N/A, with legacy JSON fail-closed rollback compatibility.

The live execution remains owned by α. Before live calls record private own
RU/BY bindings, exact fixture/draft hashes, protected grant/48h expiry and a
restart-safe shared attempt ledger: P/D/L/M in each direction (8 planned), total
hard cap12 and cap6 per direction. Success, denial and unknown each consume a
provider attempt; read-only probes/replays consume no write attempt. No model,
image/transcription or paid broadcast call. Do not send another delivered
fixture merely for a screenshot. Unknown forbids retransmission/fallback.

Server tools/list and the actual connected Work catalogue must both expose and
apply rich. Independent δ validates the exact implementation head and live
outcome separately. A19/A20 require real RU↔BY delivery and cropped synthetic
client evidence with copied code compared to exact fixture bytes. Web/Desktop/
phone versions and unverified surfaces are recorded honestly. Merge/deploy or
synthetic tests alone do not close #737; #735 remains open for later sprints.

## Official API verification

Verified 2026-10-06, Telegram Bot API10.3 (2026-08-24 release). Gate uses its
existing raw httpx JSON client, so there is no SDK hydration layer to upgrade.
On successful `sendRichMessage`, native rich_message is retained in the private
Message provider payload, not coerced into a legacy text-only SDK model.

- https://core.telegram.org/bots/api#sendrichmessage
- https://core.telegram.org/bots/api#inputrichmessage
- https://core.telegram.org/bots/api#richtext
- https://core.telegram.org/bots/api#inputrichblock
- https://core.telegram.org/bots/api#inputrichblocklistitem
- https://core.telegram.org/bots/api#inputrichblocktable
- https://core.telegram.org/bots/api#richblocktablecell

Business sendRichMessage is conditional on the corresponding user's native
ability to send rich messages. Documentation proves method/shape availability,
not either account's live capability or a particular client's rendering.


## Independently observed Ops route and LKG

Read-only α Ops inspection identified the owner/main/first-attempt protected
requester on [AQUB control issue72](https://github.com/AlphaCenter/aqub/issues/72).
Exact command is `/ops-deploy production target=gate`, no extra comment body.
The executor is pinned to
[`0bbb4bbc7cc258d98c94d3169ff4ea268c9f9029`](https://github.com/AlphaCenter/ops/blob/0bbb4bbc7cc258d98c94d3169ff4ea268c9f9029/agent-ops/actions/fast-deploy/fast-deploy.mjs).
It resolves Gate current main and verifies matching provider SHA plus terminal
SUCCESS/deploymentStopped=false; variables and standalone rollback are absent.

Recorded last-known-good deployment is
[Ops run37425099393](https://github.com/AlphaCenter/aqub/actions/runs/37425099393),
finished 2026-10-06T06:42:55.577Z, Gate source
`d99cd943749a3c4953c7f07ff2ba0f1126f08f0c`. Downloaded
`agent-ops.fast-deploy/v1` receipt is SUCCEEDED; its independent SHA-256 matches
the detached digest:
`c3e880908a615800e42d03beba31aac7a198279e3d3c4aec274b2e098848dfac`.
Fresh scoped Gate status corroborated Production source and Alembic
`0113_watch_voice_drain_runs`; this does not prove Rich capability/rendering.

The emergency route refuses railway.toml diffs and is not selected here. Routine
Release remains frozen. LKG evidence establishes the recovery baseline only;
new candidate publication, health and actual client acceptance require their own
receipts. α owns the single publication and bounded live ledger.

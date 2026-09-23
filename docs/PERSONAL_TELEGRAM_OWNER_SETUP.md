# Personal Telegram owner setup checklist

Status: Stage A contract is merged and default-off. Issue #269 records the
separate owner authorization for Stage B. Issue #270 owns the protected local
enrollment utility; its existence never activates a runtime capability.

## Stage A acceptance

- Repository contract and tests are green at one exact PR head.
- No production import, dependency wiring, database migration or configuration
  flag activates the transport.
- Read dialogs, contact sync and sending are all default-off.
- Fixtures contain only synthetic opaque references.
- Unknown provider outcome cannot be retried blindly.
- The selected client boundary is the already pinned optional Telethon 1.44
  dependency; no second client stack is introduced.

## Stage B — application and login gate

Only after a separate owner approval:

1. Confirm the exact owner-controlled Telegram account and one Telegram API
   application.
2. Enter `api_id`, `api_hash`, phone, login code and optional 2FA only in the
   non-echoing local enrollment prompts. Never enter them in chat, GitHub, CI,
   logs or a Flow payload.
3. Encrypt the resulting session before it is persisted; expose only an opaque
   `session_ref` to Gate.
4. Record a redacted capability receipt: safe account/application labels,
   created time, secret-store class and disabled flags. Do not record phone,
   code, password, API hash, session string or raw provider identifiers.
5. Keep read, contact sync and send disabled.

### Telegram application form

The current personal application is for Alfa itself, not AlphaV and not Gate.
Use non-secret labels only:

```text
App title: Alfa Personal
Short name: alfapersonal26
URL: leave blank
Platform: Desktop
Description: Personal desktop client for Alfa.
```

If Telegram returns only `ERROR`, do not keep changing architecture names and
do not paste credentials into an issue. Retry the same minimal ASCII values
later from the owner account; application creation remains an external Telegram
action, not a Gate repository action.

### Protected local enrollment (Windows PowerShell)

Install the already pinned optional client dependency in a protected local
checkout:

```powershell
py -m pip install -e ".[contact-sync]"
```

Create the encryption key once. On Windows the command wraps the generated
Fernet key with native CurrentUser DPAPI before the key reaches disk. The file
can be unwrapped only under the same Windows user profile. The command fails
instead of overwriting an existing key:

```powershell
gate-personal-telegram-enroll init-key `
  --key-file "$env:USERPROFILE\.gate\personal-telegram.fernet.key.dpapi"
```

After the Telegram application exists, perform one enrollment under that same
Windows account:

```powershell
gate-personal-telegram-enroll enroll `
  --key-file "$env:USERPROFILE\.gate\personal-telegram.fernet.key.dpapi" `
  --output-directory "$env:USERPROFILE\.gate\personal-telegram" `
  --account-label "Alfa owner" `
  --application-label "Alfa Personal"
```

The utility rejects an unwrapped legacy Fernet key on Windows. It asks for
`api_id`, `api_hash`, phone, login code and optional 2FA through non-echoing
terminal prompts. Normal output is only the redacted receipt. The Telegram
session is encrypted with Fernet, while the corresponding Fernet key remains
bound to the current Windows account by DPAPI. Neither artifact belongs in Git.

The receipt remains non-secret and records
`windows_current_user_dpapi_wrapped_fernet`; all capability flags remain false.
The enrollment command never downloads or selects cryptography code
dynamically.

Keep the DPAPI-wrapped key file separate from the session ciphertext before any
later runtime wiring. Deleting either one makes the stored session unusable. Do
not run dialog reads, contact sync, runtime wiring or a send after enrollment.

## Stage C — read-only canary gate

The repository-only contract in #281 is limited to a fake adapter, one opaque
dialog, five content-free metadata envelopes and a 24-hour maximum window. It
does not load the enrolled session and cannot call Telegram. A live adapter is
not authorized.

Before any live read, record both a permitted Telegram-terms/compliance basis
for the exact use and a separate owner approval naming the scope:

1. Select exactly one cloud dialog in the owner authorization surface.
2. Convert the selection to an opaque Gate `peer_ref`.
3. Bind one one-use grant to that peer, at most five envelopes and at most the
   preceding 24 hours.
4. Keep message text and media out of the transport canary and out of Alfa/model.
5. Do not import bulk history, secret chats, deleted messages or contacts, and
   do not mark messages read.
6. Consume the grant, disable read and retain only the redacted terminal receipt.

## Stage D — one exact send gate

Only after a separate exact-text approval:

1. Show Mark the account label, recipient label and complete text version.
2. Bind one short-lived approval token to actor_ref, peer_ref and the exact
   UTF-8 text digest.
3. Perform one send attempt with the deterministic random/idempotency id.
4. Record sent, rejected or unknown with an opaque receipt.
5. Never retry unknown automatically.
6. Consume the approval and return send to disabled.

## Stop and rollback

Immediate stop conditions:

- selected account or recipient differs from the approval;
- text digest differs;
- any flag is enabled without its stage receipt;
- a session secret or raw Telegram identifier appears in an issue, log or
  payload;
- flood/rate errors exceed the one-attempt boundary;
- provider outcome is unknown;
- owner revokes or pauses the session.

Rollback sequence:

1. Disable send, contact sync and dialog read.
2. Revoke the Telegram user authorization/session from the owner-controlled
   Telegram security surface.
3. Delete the encrypted session ciphertext and DPAPI-wrapped key; rotate the
   application secret if exposure is suspected.
4. Preserve only opaque audit receipts and the unknown-outcome marker.
5. Do not delete evidence required to prevent a duplicate retry.
6. Confirm Gate has no usable session and no queued personal Telegram attempt.

Stage A rollback is repository-only deletion of its module, tests, ADR and this
checklist; it has no provider or data cleanup.

## Offline local integrity preflight (default-off)

Use this under the same Windows user profile as enrollment, with the existing
DPAPI key and receipt. Do not copy either artifact to Git, chat or a server.
The command checks receipt binding and decryptability locally without calling
Telegram and prints only redacted booleans and an allowlisted error code:

```powershell
gate-personal-telegram-preflight `
  --key-file "$env:USERPROFILE\.gate\personal-telegram.fernet.key.dpapi" `
  --receipt-file "$env:USERPROFILE\.gate\personal-telegram\<session_ref>.receipt.json"
```

The local `configured: true` means only that the encrypted file can be
decrypted on this host. `authenticated: null` means Telegram authorization has
not been checked; `runtime_connected: false`, `send_enabled: false` and
`create_group_enabled: false` remain unchanged. The live Gate status reports
`configured: null` and `error_code: local_session_not_bound` until a
separate protected runtime binding is implemented. The preflight does not
transfer a session, create a group, read a dialog or send a message.

## Local send foundation (Gate #696)

`app/personal_telegram/local_runtime.py` can now load the existing ciphertext
under its original Windows CurrentUser DPAPI profile and make **one** raw
Telethon `messages.SendMessageRequest`. It requires an owner-provisioned exact
`session_ref`, API application credentials held only in process memory, the
expected owner user ID and an account-specific pinned `InputPeerUser` mapping
for an opaque `peer_ref`. It does not discover contacts, open dialogs, read
message history, import a session to the cloud or select a recipient itself.
The constructor does not connect to Telegram. Install the pinned optional
dependency locally with `py -m pip install -e ".[personal-telegram]"` when a
separately authorized local runtime is provisioned.

`SqlitePersonalTelegramSendJournal` in `app/personal_telegram/durable_send.py`
stores only token/text digests, opaque refs, expiration, a deterministic
Telegram random ID and redacted outcome. Its path must be absolute and under
an owner-only directory. It commits `unknown` **before** an external attempt;
parallel requests and a restart replay the stored result without sending
again. A confirmed Telegram random-ID mapping records `sent`; a missing
confirmation, timeout, invalid reply or crash remains `unknown`. Telethon's
automatic request retry/reconnect/flood sleep are disabled. A one-use approval
binds actor, session, peer and exact text, and can be revoked before use.

This is a local library foundation, not an activated Gate capability or a
command to send. `send_enabled` remains false in server status. No group
creation is implemented by this stage.

## Shared Business and personal draft reservation (default-off)

`SqlPersonalTelegramSendFence` stores a personal attempt against an existing
Gate reply draft in PostgreSQL before any local provider call. It records
opaque actor/session/peer refs, exact text and approval digests, and an
ambiguous outcome, never the Telegram session, token or message text. The
local service requires both this shared reservation and its local SQLite
journal. If the shared reservation fails, it cannot call Telegram. A replay
cannot trigger another attempt. Failure to finalize leaves the shared
attempt `unknown`, which blocks another draft in the same conversation until
operator reconciliation.

Business and personal reservations both lock the same conversation row.
Business refuses the draft owned by a personal attempt and refuses other
drafts in the conversation while that outcome is unknown. Personal refuses a
draft with any Business attempt and refuses the conversation while an active
or ambiguous Business attempt exists. A completed draft stays reserved for
its original channel. A new draft may be sent after a confirmed outcome only
through its existing explicit confirmation flow.

The PostgreSQL adapter is disabled by default and there is still no protected
server-to-owner-host binding, authenticated fence transport, live session
binding or owner account/recipient pin. Do not set the local send policy to
enabled until those are provisioned, the exact owner-approved recipient and
text are bound to the Gate draft, and a separate one-send acceptance is
recorded. The cloud has no Telegram user session; local credentials must
remain on the original Windows profile.

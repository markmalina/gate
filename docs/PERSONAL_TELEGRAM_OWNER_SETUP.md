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

Only after a separate owner approval naming the scope:

1. Select no more than three dialogs in the owner authorization surface.
2. Convert each selection to an opaque Gate `peer_ref`.
3. Enable read-only access for that exact allowlist and a fixed observation
   window.
4. Return only the minimum Relations projection needed for the approved purpose.
5. Do not import bulk history, secret chats, deleted messages or contacts.
6. Disable the read flag and record the redacted receipt after the canary.

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

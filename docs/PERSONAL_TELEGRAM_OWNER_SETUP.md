# Personal Telegram owner setup checklist

Status: Stage A documentation only. Do not perform this checklist until the
matching later stage has an exact owner authorization.

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
2. Put api_id and api_hash directly into the approved secret store. Never enter
   them in chat, GitHub, CI, logs or a Flow payload.
3. Complete phone code and optional 2FA directly in the authorization surface.
4. Store the resulting session encrypted; expose only an opaque session_ref to
   Gate.
5. Record a redacted capability receipt: account label, application label,
   created time, secret-store reference class and enabled flags. Do not record
   phone, code, password, api hash, session string or raw provider identifiers.
6. Keep read, contact sync and send disabled.

## Stage C — read-only canary gate

Only after a separate owner approval naming the scope:

1. Select no more than three dialogs in the owner authorization surface.
2. Convert each selection to an opaque Gate peer_ref.
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
3. Delete the encrypted session ciphertext and rotate the application secret if
   exposure is suspected.
4. Preserve only opaque audit receipts and the unknown-outcome marker.
5. Do not delete evidence required to prevent a duplicate retry.
6. Confirm Gate has no usable session and no queued personal Telegram attempt.

Stage A rollback is repository-only deletion of its module, tests, ADR and this
checklist; it has no provider or data cleanup.

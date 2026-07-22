# ADR 0012: Gate owns the personal Telegram user-session transport

Status: proposed; repository-only; runtime disabled.

## Context

AlphaV needs a path in which Relations can prepare a contextual draft and Mark
can approve the exact account, recipient and text before one personal Telegram
attempt. Telegram Bot API cannot act as the owner's personal account. A
user-session adapter therefore belongs to Gate, the provider-transport boundary,
not to MAYAKI, Relations or Flow.

The repository already pins Telethon 1.44 as an optional, narrowly scoped
contact-sync dependency. Stage A chooses that reviewed Python MTProto client
boundary instead of adding a second Telegram client stack. No Telethon runtime
is imported or wired by this slice.

## Decision

The personal Telegram application module defines:

- a narrow PersonalTelegramTransport port for one exact-text attempt;
- an EncryptedSessionSecretStore port that addresses ciphertext only through
  an opaque session_ref;
- independent read-dialog, contact-sync and send flags, all default-off;
- a digest-only approval store binding one short-lived token to actor, opaque
  peer reference and exact UTF-8 text;
- a deterministic provider random id derived from the idempotency key, opaque
  peer reference and exact-text digest;
- one-use approval and replay semantics;
- unknown outcome capture with no blind retry;
- a fake adapter that has no account, network or external effect.

Relations remains the Person/context/draft owner. Flow owns follow-up Work and
execution state. Gate owns authentication, session custody, peer resolution,
MTProto errors and the eventual delivery fact. MAYAKI receives no automatic
access to this transport.

## Threat model and controls

| Threat | Stage A control |
| --- | --- |
| Session theft | only an encrypted-secret-store port; no session value in code, fixtures, logs or receipts |
| Recipient confusion | exact opaque peer reference is bound into the approval |
| Draft changed after approval | exact-text SHA-256 must match |
| Duplicate send | deterministic random id plus one idempotency record |
| Timeout followed by duplicate retry | timeout becomes unknown; identical replay returns the stored result without a provider call |
| Token disclosure | only SHA-256 approval-token digests are retained |
| Background surveillance | read-dialog and contact-sync capabilities are independently default-off |
| Autonomous outreach | send is default-off and requires one exact short-lived approval |
| Ownership drift | Relations owns Person/context; Gate stores only opaque transport references |
| Sensitive identifiers in Git | phone, codes, passwords, api hash, session strings and provider ids are excluded from the contract |

## P2 bounded-read contract

Issue #281 adds a provider-neutral metadata-only read boundary with no real
adapter. It enforces one opaque session, one opaque peer, one-use digest-only
grant, at most five envelopes from at most the preceding 24 hours, redacted
digest receipt, automatic grant consumption and idempotent unknown-outcome
capture. The fake adapter has no account, network, content or external effect.

The contract deliberately has no message-text field, contact surface, media
bytes, search, dialog discovery, read-state method or Telethon import. Synthetic
success proves only repository behavior. It grants no provider or data access.

Telegram API-derived personal content must not be routed to Alfa or another AI
system without a recorded terms/compliance basis for the exact use. A live read
also requires exact owner authorization for account, dialog, window, data
classes, purpose and retention.

## Activation boundary

This Stage A PR authorizes no Telegram API application, login, phone or 2FA
step, credential/OAuth action, session creation, dialog/contact read, message
send, production configuration, real-person data, provider call, publication,
Relations mutation or manual deployment.

Stage B requires a separate exact owner decision and an approved secret store.
Stage C requires an owner-selected maximum of three dialogs and a read-only
receipt. Stage D requires Mark to see and approve the exact account, recipient
and text version before one attempt.

## Rollback

Before activation, rollback is deletion of this module, tests, ADR and owner
setup checklist. No database row, provider application, session, message,
credential or real-person record exists to reverse.

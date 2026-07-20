# ADR 0011: Gate owns a default-off TAKE shadow intake boundary

Status: proposed; repository-only; runtime disabled.

## Decision

Gate remains the only Telegram transport owner. TAKE owns capture facts,
validation, canonical media assets, review and approved packages. The adapter in
`app/application/take_shadow_intake.py` is a deterministic shadow only: it is not
wired into the webhook, worker, dependency graph, configuration or production
database.

The boundary stores only SHA-256 token digests, requires scoped explicit consent
and adult confirmation, and consumes a token after one accepted submission.
Revoked and expired tokens fail closed. A minor stops at `guardian_required`;
this slice cannot grant guardian authority.

Photos are accepted only with document delivery semantics. Media above the
20 MiB Telegram boundary produces a per-submission
`gate.dropbox-upload-request@1.0.0` descriptor with
`external_request_created=false`. Only a later synthetic opaque Dropbox file
reference can complete the shadow rehearsal.

Successful rehearsals emit an idempotent `gate.media-received@1.0.0` event bound
to `take.capture-manifest@1`. The event contains opaque references, consent scope
and evidence references, MIME/byte/checksum facts, and explicit false values for
publication authority and personal-identifier inclusion. Raw Telegram IDs and
contact fields are rejected recursively.

## Activation and effects

This ADR authorizes no live Telegram update, message, file download, Dropbox
request, OAuth or credential action, provider call, production migration or
deployment, real-person payload, publication, or Relations mutation. Runtime
activation requires a separate issue and exact owner authorization.

## Rollback

Before activation, rollback is deletion of this ADR, the shadow application
module, its JSON Schema and its tests. No migration, row, secret, provider
resource, Telegram state or Dropbox state exists to reverse.

## Next pointer

After this repository-only PR is green and merged under separate production
deployment authorization, the next work may define a private owner-media canary.
That canary remains `waiting_user` until exact authorization names the owner
payload, transport, storage action and production effect.

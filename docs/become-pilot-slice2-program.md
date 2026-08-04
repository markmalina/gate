# BECOME Pilot Slice 2 — controlled PROGRAM draft

As of: **2026-08-04 MSK**

## Product boundary

Golden Path:

```text
approved transcript version
→ one controlled provider reservation/call
→ internal PROGRAM draft
→ owner-only Telegram review
→ optional append-only correction
→ exact latest-version approval
```

The canonical business artifact is the existing Quest `PROGRAM`. Slice 2 does not
create a publication, message to another person, learner, exercise, payment, Relations,
MAYAKI, Oko or TAKE operation.

## Exact source contract

Only a Slice 1 `become_pilot_transcript_reviews.state = approved` row may be selected.
The selected plaintext must be the exact version and digest named by
`approved_version/approved_digest`; it must remain unpurged and unexpired. Every
PROGRAM draft stores the immutable source review id, version and digest.

A later approved transcript version invalidates the prior PROGRAM draft before a new
reservation is created. The invalidated row and versions remain append-only evidence
until independent plaintext purge.

## State and idempotency

```text
reserved → calling → pending_display → presented
                                  ↘ awaiting_correction → pending_display
                                  ↘ approved
calling → unknown
any nonterminal old-source draft → invalidated
```

The unique source tuple is
`(transcript_review_id, source_transcript_version, source_transcript_digest)`.
The transition to `calling` is committed before the provider boundary. A restart while
`calling`, provider timeout, transport failure or non-completed provider response ends
at `unknown`; no automatic or manual blind retry exists in Slice 2. Exact replay of a
completed source does not reserve or call again.

PROGRAM versions are append-only. Approval binds the exact latest version and digest.
The `b2` callback namespace is separate from Slice 1 `b1`. Actor and Telegram dialog
must match the persisted owner-only binding.

## Configuration contract

All new gates default closed:

- `GATE_BECOME_PILOT_SLICE2_ENABLED=false`;
- `GATE_BECOME_PILOT_SLICE2_DATA_GATE_APPROVED=false`;
- `GATE_BECOME_PILOT_SLICE2_MAX_OPERATIONS=0`;
- `GATE_BECOME_PILOT_SLICE2_PROVIDER_MAX_CALLS=0`.

Provider execution additionally requires the existing OpenAI credential and approved
API base. The provider receives only the exact approved transcript body, is configured
with `store=false`, no tools, a strict JSON schema and a bounded output limit.

## Threat model

| Threat | Fail-closed control |
|---|---|
| unapproved/stale/purged transcript | exact approved version/digest join, expiry predicate |
| duplicate worker/restart | unique source tuple plus committed `reserved→calling` claim |
| unknown provider result | terminal `unknown`; no blind retry |
| prompt injection inside transcript | transcript declared untrusted; tools/delivery absent |
| wrong user/chat callback | persisted subject and dialog binding |
| stale/mutated callback | unique callback UUID and latest state/version checks |
| correction after approval | prior approval cleared; append-only higher version |
| transcript changes later | old PROGRAM becomes `invalidated` before replacement |
| plaintext retention | independent idempotent 30-day purge; digest and timestamps remain |
| duplicate Telegram delivery | unique request digest and reserved/sent/unknown ledger |
| external effect expansion | provider and owner-only Telegram are the only Slice 2 boundaries |

## Mandatory PostgreSQL regression matrix

- default-off and provider cap zero create no draft/call/delivery;
- only exact approved transcript version/digest is selected;
- wrong workspace, actor, dialog and callback fail closed;
- concurrent/restarted reservation produces one source row and one provider claim;
- `calling` and ambiguous provider outcome never call twice;
- completion creates one provider version and one presentation operation;
- delivery ambiguity becomes `unknown` and is not auto-retried;
- correction appends version 2 and invalidates prior approval;
- duplicate approval is a no-op on the exact latest digest;
- a newly approved transcript invalidates the old PROGRAM and permits one replacement;
- 30-day purge removes only bodies and preserves digests/receipts;
- migration upgrade, convergence, populated dump/restore and exact-head CI have zero
  mandatory skips.

## Rollback

Rollback target remains Gate production
`d7f144668bd2d5f43b9764c51e21bcff6484a22c`.

1. Freeze BECOME Pilot worker intake.
2. Set the Slice 2 enabled/data gates false and both caps to zero.
3. Require zero `reserved` drafts.
4. Treat every `calling` draft as `unknown`; never retry it.
5. Reconcile every reserved/unknown PROGRAM delivery without automatic resend.
6. Run plaintext purge when due and require no expired body remains.
7. Deploy the rollback application SHA without Alembic downgrade.
8. Preserve additive Slice 2 tables as content-free/TTL-governed evidence.
9. Record source SHA, counts of reserved/calling/unknown/deliveries, and zero
   downstream/publication/payment effects.

No merge, deployment, activation or live action is authorized by this document.

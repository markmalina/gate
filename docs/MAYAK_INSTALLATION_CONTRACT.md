# Reusable Mayak installation

Status: current contract, runtime default-off  
Source: Gate issue #293, parent roadmap #277  
Contract: `gate.mayak-installation@1.0.0`

## Outcome

Gate can describe one visible Mayak installation for a private dialog, group,
community, or channel. The contract separates transport authority from Mayak
expertise, program state, personal Alfa, relationship truth, LMS state, and
human responsibility.

```text
opaque workspace + connector + dialog
  -> visible Mayak identity and controller disclosure
  -> explicit installation-scoped capability matrix
  -> explicit invocation or separately authorized transition
  -> terminal digest/reference receipt
```

This stage is repository-only. It is not wired to a connector, provider,
database, worker, Telegram group, LMS, prompt, or production installation.

## Canonical ownership

| Concern | Canonical owner | Gate owns | Gate must not become |
| --- | --- | --- | --- |
| Connector and dialog binding | Gate | opaque installation scope and transport receipts | provider identity directory |
| Mayak identity and expertise | MAYAKI | visible label and opaque Mayak reference | expertise or content master |
| Program and practice | MAYAKI / LMS | explicit handoff reference and receipt | LMS, progress or funnel master |
| Person and consent | Relations | opaque consent-basis reference | Person, CRM or consent master |
| Personal intelligence | Alfa | explicit opt-in transition receipt | private Alfa or memory master |
| Human responsibility | Relations / operations | explicit takeover receipt | staffing or responsibility truth |
| Work control | Center | execution pointer and terminal receipt | communication payload store |

## Installation descriptor

- A single descriptor binds one opaque workspace, connector, dialog and dialog
  kind. Raw provider account, chat, message, member and user IDs are forbidden.
- Supported dialog kinds are `private`, `group`, `community`, and `channel`.
  This list describes installation shape, not guaranteed provider parity.
- The agent must be visibly identified as a Mayak. Public label, controller
  label and short disclosure are mandatory; `identityVisible` is always true.
- Every capability is represented by an explicit boolean. Missing and unknown
  capabilities fail closed.
- Discovered, pending, paused, revoked and deleted installations have every
  capability off. Only an active installation may carry separately granted
  capabilities.
- Even an active repository descriptor grants no delivery, provider action,
  publication or LMS mutation. Those remain false and require later,
  action-specific runtime authority.

## Explicit transitions

Program handoff, personal-Alfa opt-in and human takeover are distinct terminal
receipts. They cannot be inferred from a normal chat message or from installing
a Mayak.

- `program-handoff` records an explicit user-facing program transition request.
  It never enrolls a user or mutates an LMS.
- `personal-alfa-opt-in` records explicit consent and an explicit, visible
  identity transition. A Mayak never silently turns into Alfa.
- `human-takeover` records that the visible controller changed to a human. It
  does not identify the person or create staffing truth in Gate.
- `pause`, `revoke`, `delete`, and `re-consent` are separately auditable
  lifecycle transitions.

Accepted or replayed consent-bearing transitions require both explicit consent
and visible disclosure. Accepted or replayed identity transitions require
`identityTransitionExplicit: true`.

## Idempotency and reconciliation

Each descriptor and receipt has an immutable idempotency key. Identical replay
returns `replayed`; the same key with a different digest is rejected.

Receipt states are `accepted`, `rejected`, `replayed`, or
`reconciliation-required`. An unknown provider or consumer outcome maps to
`reconciliation-required` and is terminal until read-only reconciliation. Gate
must never retry an externally visible action blindly.

## Privacy, isolation and deletion

Unknown fields fail schema validation. Alternate workspace fields, provider
IDs, member lists, raw histories, prompt dumps, credentials, recipient
selection and canonical Person/program/memory payloads are not representable.

Every future runtime adapter must resolve all opaque references inside the
authenticated workspace. Cross-workspace installation, target, handoff or
receipt resolution fails closed. Consent revocation and workspace deletion
propagate deletion according to the required retention flags.

## Runtime gate

Runtime wiring requires a separate issue, provider-specific capability audit,
data-flow review, migrations, consumer contracts, deletion implementation,
synthetic replay proof and exact owner authorization for any provider call,
message, publication, credential/OAuth, LMS mutation or real-person data.
This contract authorizes none of those actions.

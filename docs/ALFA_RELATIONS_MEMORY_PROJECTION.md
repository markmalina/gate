# Alfa / Relations memory projection

Status: current contract, runtime default-off  
Source: Gate issue #291, parent roadmap #277  
Contract: `gate.alfa-relations.memory-projection@1.0.0`

## Outcome

Gate can hand Alfa or Relations a bounded, source-attributed projection of a
canonical communication fact. The projection is not a prompt transcript,
Person record, memory record, CRM record, consent decision, or delivery grant.

```text
canonical Gate event
  -> explicit workspace + subject + dialog scope
  -> minimum-necessary structured facts
  -> Alfa and/or Relations candidate intake
  -> terminal digest-only receipt
```

This stage defines a repository contract only. It is not wired to ingress,
workers, providers, databases, prompts, or production.

## Ownership

| Concern | Canonical owner | Gate may emit | Gate must not do |
| --- | --- | --- | --- |
| Transport evidence | Gate | opaque event/dialog references, transport class, timestamp, digest | expose raw provider IDs or credentials |
| Private reasoning and memory | Alfa | sourced candidates for Alfa intake | decide personal truth or persist Alfa memory |
| People and relationships | Relations | identity, commitment and timeline candidates | create a second Person, consent, CRM or relationship master |
| Programs and funnels | MAYAKI | source pointers through a later explicit contract | infer program state or operate an LMS |
| Media meaning | TAKE | media receipt pointers through a later explicit contract | copy raw media or perform analysis |
| Work receipts | Center | execution metadata and terminal references | store communication payloads |

## Envelope rules

- `scope` is singular and mandatory. An envelope cannot contain a second
  workspace, subject or dialog scope.
- Every source uses an opaque canonical Gate event reference and a SHA-256
  digest. Provider chat, account, message and user identifiers are forbidden.
- `basis.projectionAllowed` must be exactly `true`; absence or revocation fails
  closed. Capability, consent-basis and purpose are opaque references owned by
  their canonical systems.
- Facts are minimum-necessary scalars. A string is capped at 1,024 characters,
  an envelope at 32 facts, and full-history arrays are not representable.
- Gate labels facts only as source observations or candidates. Alfa and
  Relations decide whether a candidate becomes canonical domain knowledge.
- Correction, supersession, deletion and retention address an opaque prior
  projection. Consumers must resolve that reference inside the envelope's
  workspace; a cross-workspace target must fail closed.
- Source deletion propagates deletion. Every retention object therefore has
  `deleteOnSourceDeletion: true`.
- The envelope hard-codes `deliveryAuthorized`,
  `providerActionAuthorized`, and `externalEffectsPerformed` to `false`.

## Idempotency and receipts

`idempotencyKey` identifies one immutable projection. The consumer records a
digest before returning one terminal state:

- `accepted`: the bounded candidate was durably accepted for domain review;
- `rejected`: validation or policy rejected it;
- `replayed`: the same key and digest already reached a terminal state.

The same idempotency key with a different digest is a mutation and must be
rejected. An unknown consumer outcome is terminal until reconciliation; Gate
must not blindly emit a second projection.

The receipt contains only opaque references, consumer, terminal state, reason
code, digest and timestamp. It carries no content and grants no external
authority.

## Privacy and adversarial boundary

The machine schema rejects unknown properties. Consequently raw provider IDs,
full histories, prompt dumps, alternate workspace fields, recipient selection,
credentials, delivery grants, and canonical Person or memory payloads cannot
be smuggled into either envelope or receipt.

Schema validation is necessary but not sufficient. A future runtime adapter
must additionally resolve every opaque reference within the authenticated
workspace and verify capability, consent basis, purpose, retention, and
idempotency against their canonical stores before any durable projection.

## Runtime gate

Future wiring requires its own issue, execution, data-flow review, migrations,
retention/deletion implementation, consumer acceptance contract, synthetic
replay proof, and exact owner authorization for any real-person data. This
document authorizes none of those actions.

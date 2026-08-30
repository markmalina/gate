# Gate message contract v1 — repository preflight

This product-neutral contract is a provider-free GATE-31 preparation artifact.
It does not claim GATE-31 or G4 acceptance.

The contract carries only opaque installation, conversation, actor, destination,
message and object references; route generation; bounded text or immutable media
metadata; idempotency; and delivery facts. Media bytes, signed URLs, provider
payloads, product policy and semantic memory are outside Gate's ownership.

`app/application/gate_message_contract_v1.py` is a pure strict parser/serializer.
Its adapter defaults to disabled and accepts only the code-owned
`SYNTHETIC_REHEARSAL` enum. `app/infrastructure/gate_message_preflight_store.py`
can create only a disposable `gate31_rehearsal_*` PostgreSQL schema and is imported
by no endpoint, configuration, worker or application composition.

The rehearsal proves durable ingress before acknowledgement, one outbox command
per idempotency key, exact route-generation fencing, bounded retries, consumer
commit ordering, and reconciliation-only treatment of terminal `unknown` facts.
It performs no provider delivery and cannot block the synthetic legacy result.

Runtime wiring, route activation, database migration, provider access, deploy and
operational acceptance require later independently reviewed Works.

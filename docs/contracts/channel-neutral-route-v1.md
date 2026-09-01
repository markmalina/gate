# AQUB channel-neutral route v1

Within the upper Alpha Center company contour, this repository-only boundary
accepts synthetic source events for the user-facing AQUB ecosystem,
application, platform, and main brain without binding the contract to any
channel or provider. Cubes, OMARK, MARK, and third-party methods remain methods
inside AQUB; they are not transport identities. Today is an interface and Deal
is a confirmed commitment, neither of which changes this route contract.

The exact routing key is `route_ref + space_ref + actor_ref`, and event
idempotency is bound to `space_ref + actor_ref + source_event_id`. Only `test`,
`pilot-a`, and `pilot-b` are valid synthetic spaces. Every route has an explicit
actor allowlist. Unknown, ambiguous, cross-space, or defaulted input is refused.
The allowlist is validated and snapshotted as an immutable set at registration;
later mutation of a caller-owned collection cannot change admission.

The local journal keeps four distinct facts: `accepted`, `committed`,
`response_prepared`, and `delivered`. A prepared response is not delivery.
Delivery exists only after an explicit synthetic delivery fact is recorded.
Commit accepts only the sealed local outcome enum. It never executes a supplied
callback, so provider-shaped or network-shaped callables are refused without
invocation and cannot advance the journal.

This slice has no runtime wiring, network client, provider call, environment
lookup, deployment step, secret, real-person identifier, or real payload.

# Baseline observation → verified candidate process admission

This contract joins the separately reviewed G1a baseline package and G2/G4
candidate. It does not make candidate schema a baseline proof, and does not
perform migration, provider inventory, deployment or a real message test.

## Startup and identity

The actual runtime session factory reads the single `alembic_version` row before
workers or provider writes. No caller parameter, JSON document or flag can
select a more permissive schema interpretation.

| Actual ledger | Process admission |
| --- | --- |
| `0111_reply_documents` | Strict G1a physical-session observer; original marker/config fixture and reader remain unchanged. No v2 runtime may claim this mode. |
| `0115_reply_canary_binding` | Current verified G2 process/epoch admission for the personal bot. No baseline observer or `0111` marker is emitted. |
| Missing, multiple, another revision, unavailable DB | Closed; startup does not yield a writable process. |

`GATE_SEND_ENABLED=false` and the synchronous in-flight HTTP counter apply to
all `TelegramClient` writes as in G1a. Candidate health is required for the
actual personal bot ID from the client's credential, including independently
constructed clients with that same bot ID. Only explicitly configured distinct
Alpha/Beta/BECOME/AQUB bot IDs retain their separate product admission contracts;
they are not attached to the personal epoch. Unknown IDs and aliases fail closed.
The startup primary/secondary identity predicate is retained. No token is logged.

Both JSON and multipart common HTTP boundaries perform admission before HTTP.
The three explicit read methods (`getBusinessConnection`, `getChatMember`,
`getChatAdministrators`) retain their read-only exemption. Canary scope, quota,
execution binding, allowed method, retry policy and final dispatch start window
remain additional independent checks; process health never overrides them.

## Candidate bootstrap and loss

A candidate starts with no send authority. All v2 flags off, missing callback,
missing/current-source-unverified epoch, disabled dispatch, expired evidence,
wrong bot/source/config/boot, missing fleet, unhealthy worker, binding drift,
receipt/projection gap or DB error denies a personal write before HTTP.

Completed idle DB work remains allowed while admission is disabled. Existing
worker health and G2 process observations can therefore bootstrap without
sending. Ops first proves the new exact source/config/provider process inventory,
installs the protected initial epoch with dispatch disabled, arms the exact
canary scope when required, and explicitly selects serial/parallel. All startup
flags (including PARALLEL) are configured before the epoch; a restart or rebind
requires new current-source evidence and approval, never inheritance of an old
epoch or baseline marker.

Every primary write uses a read-only check of the actual ledger, current
control, epoch, loaded process membership and the existing full G2 durable
health predicates. A final DB-clock/control-generation/epoch check rejects a
concurrent stop, rebind or expiry during the scan. It does not renew evidence,
change generation, create observations or arm anything. Existing G2 continuity
renewal and terminal gap stop remain the only runtime renewal path.

## Ownership and outcomes

One process fence is installed before constructing workers. ASGI Telegram worker
iterations borrow the same `DatabaseRuntime` and process owner; they do not
create a new engine, observer, boot identity or transport owner on each tick.
Standalone Telegram worker and BECOME maintenance construct one runtime, enter
the same process admission context, and close it once. A nested borrow cannot
replace a different runtime/settings owner. Producers are cancelled before the
owning process fence/observer and database are closed.

The synchronous in-flight section begins immediately before the actual HTTP
await and ends after its completion/cancellation. A `BaselineSendClosed` raised
by these local guards proves that this attempt never reached HTTP: a reserved
sequence attempt becomes `cancelled`, a pending notice becomes `failed`, with
fixed `process_admission_closed` code. A real transport timeout/cancellation or
unconfirmed provider response retains its existing unknown classification.
Neither path refunds the canary budget or restarts stopped series.

## Ops handoff evidence

The release journal must keep these separate:

1. Exact accepted baseline source, strict schema-0111 physical identity/fleet
   observation, provider inventory, frozen sends/workers, drain and backup.
2. The separately authorized migration to exact candidate schema/source.
3. Fresh candidate source/config/boot inventory and protected initial G2 epoch.
4. Exact canary scope and explicit dispatch mode, then bounded product checks.

`read_frozen_fleet` remains an 0111-only reader. Its historically completed
unknown outcomes remain counted separately; they are not deleted or converted
to sent. Reserved, sending and incomplete unknown outcomes still block drain.
No 0115 health result can be presented as an 0111 baseline attestation.

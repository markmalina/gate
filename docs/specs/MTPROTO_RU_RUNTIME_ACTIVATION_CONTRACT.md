# Independent Gate RU runtime: activation contract proposal

Status: **proposed, no executable authority granted**. Owner: Gate #696.
This is an actionable handoff for the remaining V0/V1 boundary. It is not a new
provider control route and must not be used as deployment permission.

## Verified current route

Gate's `.github/workflows/ops-deploy.yml` uses Ops executor
`7bd47fe1c646f30c87207dc513cd1e5990d23a78`. Its action and `gate-deploy.mjs` were
read at that exact revision. It accepts `AlphaCenter/gate`, Mark's immutable
GitHub identity, current main, and only the allowlisted Gate service. It
authenticates the fixed Railway project/environment and invokes only
`serviceInstanceDeployV2`, checking the observed deployment SHA and writing an
Ops receipt. It has no custody inspection/import, secret provisioning, schema
migration, dedicated worker selection, drain/termination or session recovery.

The root [AGENTS.md](../../AGENTS.md) explicitly says: "Other Gate operations,
including Gate745, need a reviewed independent Gate-owned Ops contract; this
short route does not authorize cutover." The MTProto runtime cannot use that
simple deploy route as permission for these missing operations. Existing Gate745
and historical AQUB routes do not expand it. No provider UI/API fallback.

## Required reviewed operations

Each operation pins caller repository/workflow/owner identity, expected source,
Ops executor SHA, one workspace/RU binding, fixed provider target, installation,
schema revision and hard caps. It rejects source/target/binding drift before
mutation and writes an atomic redacted receipt, including UNKNOWN on uncertainty.

| Operation | Concrete result | Required stop condition |
| --- | --- | --- |
| `INSPECT_RU_CUSTODY` | Presence/shape/decryptability of existing protected application/session; no values returned | Unknown target/binding or missing protected read authority |
| `IMPORT_EXISTING_RU_SESSION` | Encrypt into reviewed DB envelope, preserve old recovery material, pin provider owner, verify CAS | Wrong owner/schema/key generation, prior owner not stopped |
| `PREPARE_RU_LOGIN` | Protected owner form and session persistence before code/QR/2FA is requested | No owner-bound IdP session, secret logging or browser-to-AI leakage |
| `MIGRATE_RU_RUNTIME` | Backup receipt, exact additive migration, schema fingerprint, real restore/downgrade proof | Pending conflicting migration, active operation incompatible with rollback |
| `DEPLOY_RU_WORKER_DISABLED` | Reviewed build/run command, one dedicated process, default-off effects, source fingerprint | Unknown/new resource cost, replicated web startup or wrong artifact |
| `DRAIN_AND_STOP_RU_OWNER` | Pause admission, finish/report current attempt, disconnect; provider confirms old deployment stopped | Any dispatch/termination uncertainty; lease remains latched |
| `VERIFY_RU_CONNECTION` | Bounded authorization/actual-owner check and disconnect/status receipt | Wrong RU identity, missing session, duplicate owner, exceeded read-call cap |
| `ACTIVATE_RU_COMMANDS` | Exact accepted capabilities/delegations, writer guard proof, fresh runtime/Work proof | Any unknown writer, unresolved peer attempt, missing grant or Work boundary |
| `RESTORE_OR_ROLLBACK_RU` | Compatible schema/source/credential generation; terminate old owner before restore | Rollback would erase unresolved/idempotency facts or revive duplicate TCP owner |
| `ROTATE_RU_CUSTODY_KEY` | Re-encrypt CAS under exclusive ownership, key version pin and recovery proof | Concurrent use, missing prior-key recovery or failed persistence |

`VERIFY_RU_CONNECTION` is its own reviewed test, with a concrete API-call budget
chosen from the implemented client's measured handshake. It never reads history,
contacts, participants, BY or sends messages. No arbitrary live check follows
merely because a connection succeeds. New resource/cost and login gates remain
exactly scoped; existing protected values must be inspected before requesting
new ones. No secrets, phone numbers, OTP, QR tokens or 2FA values in Work/repo.

## Deployment and custody package to prepare

1. Pin the actual Gate-owned worker service/deployment identifiers from an
   accepted read-only Ops inventory. Reuse an existing suitable resource where
   verified; do not invent that one exists or create a new service implicitly.
2. Establish reviewed monthly incremental CPU/memory/storage/egress estimates,
   a hard ceiling and stop policy before a new resource is created. This package
   made no provider inventory calls, so it cannot supply a truthful cost today.
3. Deliver the protected setup/status form with authentication from Gate's
   existing IdP. MCP/AI can receive setup state and a safe URL, never login values.
4. Pin session and application to the actual RU user, workspace, binding and
   generation. Separate the encryption key from ciphertext; make it available
   only to the authorized setup/worker process. No whole-account entity cache.
5. Add the reviewed migration to the current linear Alembic chain and register
   active metadata. Prove clean/populated upgrade, downgrade, backup and restore.
6. Add a dedicated worker entry point/container, signal handling, heartbeat/
   health, timeout limits and redacted diagnostics. Enforce DB ownership before
   any TCP connection; replica count alone is insufficient during deploy overlap.
7. Prove drain plus old-deployment termination before takeover. A crash recovery
   needs an independently admitted termination receipt; lease expiry is not one.
8. Admit only the source/runtime capabilities proven through actual Work. Record
   current owner-only installations evidence, LKG source/schema/session revision,
   rollback trigger and zero-third-party-payload check scope for each release.

## Work and Business acceptance still required

- Actual Work OAuth discovery and refresh see versioned tools/scopes. Owner
  delegation is bound to the authenticated client and current durable registry;
  a caller's `confirmed=true` or DTO cannot substitute for that proof.
- `prepare`, `execute`, `status`, `result`, `cancel` operate on opaque job/peer/
  message references with strict RU scope. Discovery distinguishes planned,
  unavailable, rights-required, partial and UNKNOWN states.
- File input and private download are proven with an actual Work file, rather
  than assuming arbitrary local paths, public URLs or remote MCP file support.
- Shared admission covers every active Business/MTProto writer at dispatch;
  frozen routes prove exclusion. Typed `(account, peer kind/id, message id)`
  mapping prevents collision between user/chat/channel numeric IDs.
- A correlated acknowledgement/outbound echo closes only exact covered tasks.
  First outgoing and on-demand history do not synthesize incoming tasks.
- Reads name a selected context/window and approved use/retention basis. No
  account-wide mirror, ambient subscription or automatic paid Stars flags.

Until these proofs exist, this source foundation remains unbound/default-off.
The original V0-V7 acceptance matrix is retained; none of V2-V7 is closed by
library method presence, synthetic tests, source merge or a successful build.

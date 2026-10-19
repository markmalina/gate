# MTProto RU: V0-B source contract

Execution: `gate-696-v0b-work-contract-20261012T0557Z`. Product issue: #696.
Architecture: PR #759 at `d9a2866603d835983fd80e40688ce1c355e17a51`.
Baseline implementation: PR #761 at `3955eb0539c1b30bd0816a958ffc7127f768fcb3`.

## Deliverable and boundaries

This package implements the durable Work-facing command boundary in source.
It does not complete actual-host V0-B acceptance or restore MTProto.
The six personal MCP tools are default-off (`GATE_PERSONAL_COMMAND_TOOLS_ENABLED=false`).
No startup hook binds the service factory, creates tables, imports credentials or starts a worker.
No production migration, deployment, session custody change, Telegram request, file transfer or model call belongs to this execution.

| Tool | Required verified OAuth scope | Effect of this source implementation |
|---|---|---|
| `gate_personal_command_readiness` | `gate:personal:jobs:read` | Reports unbound dispatcher |
| `gate_personal_command_prepare` | `gate:personal:send_text` | Journals an approved bounded text intent |
| `gate_personal_command_execute` | `gate:personal:send_text` | Queues the existing operation |
| `gate_personal_command_status` | `gate:personal:jobs:read` | Returns the caller's operation state |
| `gate_personal_command_result` | `gate:personal:jobs:read` | Returns stored state/receipt after revocation too |
| `gate_personal_command_cancel` | `gate:personal:jobs:cancel` | Cancels pending intent; dispatched state becomes unknown |

`sent=false` means this tool invocation performed no Telegram send. A stored `succeeded`
state or receipt, if later produced by a separately reviewed dispatcher, remains historical
operation evidence. `runtime_connected=false` describes this unbound command contract.
The contract must be updated before a real dispatcher can be attached.

## Authorization and retry contract

Personal tools validate signed issuer/audience/expiry and an unambiguous `azp`/`client_id`.
They never use Business workspace fallback. RU account, workspace and owner must be explicit.
The immutable client namespace is SHA256 of canonical JSON `[verified issuer, verified client]`.
Caller arguments cannot choose that namespace, owner, account, capabilities or raw provider peer.

Grants are durable, encrypted immutable policies bound to installation/owner/client/expiry,
key version and approval reference. Provisioning is an internal reviewed setup port, never
an MCP self-grant. Revocation uses compare-and-swap and binding-before-grant row locks.
Prepare, execute and reservation re-read durable policy in their own transactions.
The legacy DTO is data, not authority; changed DTO policy is rejected.
Status/result/cancel are owner and verified-client bound; a revoked sending grant does not
prevent its owner/client from inspecting a prior receipt or cancelling pending work.

Peer references are encrypted server-issued typed peers bound to the full installation,
owner, client and grant, expiring after at most five minutes. Public references are opaque.
`request_key` identifies one intent per installation/owner/client. A retry with unchanged
intent returns the same operation; changed payload/grant/peer/capability conflicts; a new key
creates a distinct intent even when text matches. Execute reuses that operation, with no
second attempt after a durable outcome. UNKNOWN remains quarantined per typed peer.

## Reproduction

Install the pinned CI dependencies, then run:

```sh
python -m pip install -c constraints/ci.txt '.[dev,personal-telegram]'
ruff check .
mypy app
pytest tests/test_mcp_oauth.py tests/test_mcp_surface_consistency.py \
  tests/test_personal_command_grants.py tests/test_personal_command_service.py \
  tests/test_personal_command_mcp.py tests/test_personal_command_runtime.py
```

Real contention tests use the disposable localhost PostgreSQL 17 CI service.
`PERSONAL_COMMAND_TEST_DATABASE_URL` must explicitly name a disposable local database
for grant tests; no production URL fallback is allowed. Existing runtime PostgreSQL tests
use the CI `DATABASE_URL`. Both use unique temporary schemas, removed after the run.
The CI workflow sets both URLs to its service and runs the complete suite on literal head SHA.
A local PostgreSQL skip is not acceptance evidence.

## Remaining actual-host acceptance

Independent source acceptance covers exact candidate code, OAuth/schema boundaries,
end-to-end ASGI journal flow, durable replay/revocation and PostgreSQL locking evidence.
It must not be promoted to actual Work acceptance.

The next host package needs an independently accepted Gate Ops activation contract for
staged migration and service binding, an authorization-server contract issuing the new
personal scopes plus signed client identity, and exact deployed-head evidence. Then the
actual Work host must prove catalog refresh, reauthorization, same-key retry after response
loss and operation status/result/cancel. Record each host result and observed request key.
No Telegram send is needed for this host-only acceptance; file transfer is a separate V0-D
proof. Sending still depends on V1 custody/runtime and V2 all-writer admission.

Sequence after this source package: actual-host V0-B, independent custody/stop-proof V0-C,
then bounded V1-S connection package; do not reopen the completed architecture research.

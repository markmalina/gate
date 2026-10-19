# Watch voice driver selection

Gate #736 / #731. Source preparation only; not activation authority.

The production start command preserves explicit server flags instead of
overwriting them. With flags unset or empty, the defaults remain persistent
worker **off**, private Mark Actions **on**. The reviewed private workflow pin
is unchanged. No credential, model, budget, schema or durable watermark changes.

| Persistent worker | Mark OIDC | Behavior |
| --- | --- | --- |
| false | true | Existing private Actions driver |
| true | false | Persistent driver, after separate release/config/writer gates |
| false | false | Both drivers disabled |
| true | true | Both processing entry points reject before external I/O |

The conflict code is `watch_voice_driver_conflict`. It does not stop unrelated
Gate services, reset a cursor, retry an unknown provider operation, or publish a
note. HTTP OIDC authentication, including a JWKS read, may precede the Actions
processing entry point; the conflict prevents data processing, not authentication.
The Actions request-local settings may still enable internal processing
after authenticating the Actions-only mode; they do not enable the persistent
loop. Existing PostgreSQL coordination and durable per-effect claims remain.

Before switching to the persistent driver, independently verify the deployed
source, effective startup/configuration, owner scope, existing server writer and
private target, durable future cutover, approved narrow setting delta and rollback.
Disable Mark OIDC in the same reviewed mode change. Requests already in flight
and process overlap still require the existing concurrency/replay checks and
actual runtime acceptance; a flag table is not evidence of a completed cutover.

Rollback stops the persistent driver and restores only the accepted driver
configuration through the approved Gate-owned route. Preserve originals,
transcripts, source cursor, future watermark and all effect journals. Do not
downgrade the database or copy an Actions job token into server configuration.

Release remains blocked on the pending-state semantics described in
[Railway CLI #1305](https://github.com/railwayapp/cli/issues/1305). This source
change grants no provider read, deployment, configuration write or real note test.

# Beta data retention and recovery policy

Status: approved engineering policy for the pre-provisioned Beta Space.

## Scope and ownership

The Gate service owner owns operational retention. The Beta repository owner owns encrypted
checkpoint retention. Product policy does not override either owner through runtime configuration:
changes require a reviewed, versioned change in the owning repository.

A legal or security hold suspends deletion for the affected Space. It never permits cross-Space
reads, plaintext export, or identifier exposure.

## Retention classes

| Data class | Retention clock | Deletion preconditions | Maximum retention after the clock starts |
| --- | --- | --- | --- |
| Raw Telegram receipt payload | Event accepted | Related work is terminal and the containing encrypted checkpoint is acknowledged | 30 days |
| Extracted plaintext and lexical projection | Binding revoked or explicitly deleted by the owner | Final encrypted checkpoint is acknowledged and restore verification succeeds | 30 days |
| Provider message/file identifiers | Delivery or material job becomes terminal | The terminal result is checkpointed and acknowledged | 30 days |
| Abandoned reply/material candidate | Operator resolves `unknown` or terminal failure | Resolution is checkpointed and acknowledged | 7 days |
| Operational hashes, counters, and sequence metadata | Binding revoked or explicitly deleted by the owner | No pending dispatch/export and final checkpoint is acknowledged | 90 days |
| Encrypted checkpoint container and manifest | Owner deletion request | Restore verification succeeds; no legal/security hold | Until explicit owner deletion |

“Terminal” means `completed`, `sent`, `failed`, or operator-resolved `unknown`, as
applicable. A mere lease expiry is not terminal. A purge must abort closed if any precondition
cannot be proved.

## Recovery and observability contract

The owner-only status surface reports only aggregate, exact-Space metrics:

- queue depth for nonterminal work and dispatch intents;
- checkpoint lag as accepted event sequence minus acknowledged export sequence;
- age in seconds of the oldest nonterminal work;
- total worker and dispatch attempts;
- stale recovery count, defined as durable rows with more than one recorded attempt;
- terminal failures grouped into redacted categories.

Raw error codes, Telegram identifiers, provider identifiers, candidate text, filenames, payloads,
and job identifiers are never returned. Error categories are deliberately coarse:
`external_transient`, `transport`, `provider`, `validation`, and `internal`.

## Enforcement requirements

A retention worker must apply the table with exact `workspace_id` and `binding_id` predicates,
bounded batches, an audit receipt containing counts only, and a dry-run mode. Until that worker is
deployed, the conservative safe behavior is no automatic deletion. This prevents unverified data
loss while making the retention deadlines and implementation owner explicit.

# 0015 — Default-off AQUB v2 delivery lifecycle capability

Status: proposed

## Decision

Migration 0102 introduces a synthetic-only policy gate and a fixed-search-path `SECURITY DEFINER` redaction capability. Its owner and executor are separate `NOLOGIN`, `NOINHERIT` roles; `gate_aqub_v2_runtime` receives neither table writes nor function execution.

The same migration adds a separate lifecycle-only consent-cancellation capability. It accepts the closed `aqub.consent-cancellation.v1` envelope only after transport authentication and binds it to the exact command UUID, canonical command SHA-256, opaque revoked-consent reference, positive authority epoch and millisecond UTC observation time. Under the ledger row lock, a committed command becomes rejected with a failed/do-not-retry receipt before the provider boundary. If the send boundary was already crossed, the row becomes or remains unknown/reconcile-only and preserves any existing provider reconciliation handle. The opaque cancellation receipt is durable and exact-replay idempotent; changed epoch, timestamp or command evidence conflicts.

Redaction is allowed only for an exact terminal or reconcile-only record (`sent`, `failed`, `rejected`, or `unknown`) after the exact retention deadline. The caller supplies policy version, consent reference, command hash, state and request ID. The function serializes per record, removes `command_json`, canonical bytes, conversation/cube references, ACK and delivery receipt, then records an idempotent content-free receipt.

The candidate tombstone retains installation reference, command UUID, idempotency key, command hash, state and any opaque cancellation evidence so the uniqueness key stays occupied. Re-accepting the same delivery is rejected and dispatch remains impossible. Route-binding retirement is deliberately separate.

## Safety boundary

The approved tombstone composition, controller/operator, processors/readers, retention, backup/log handling, delete SLA and assisted-mode access remain Mark's decisions. Their absence keeps real-data mode disabled. This draft does not claim backup or log erasure and authorizes no migration apply, deploy, provider call, Telegram or real Food data.

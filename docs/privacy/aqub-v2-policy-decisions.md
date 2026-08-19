# AQUB v2 Gate lifecycle — decisions required from Mark

Status: **default-off**. Migration 0102 permits enabling only an explicit synthetic policy. It does not authorize real Food data, production, Telegram or provider calls.

| Decision | Required value | Current value | Default-off effect |
|---|---|---|---|
| Controller/operator | Identity, contact and responsibility boundary | Unresolved | No approved real-data lifecycle policy |
| Processors/readers | Readers/processors of command JSON, canonical bytes, receipts, route references and lifecycle evidence | Unresolved | No approved real-data policy |
| Retention | Deadlines for command JSON, canonical bytes, receipts, route authority, tombstone and lifecycle receipt | Unresolved | Real-data lifecycle cannot be enabled |
| Backup/log policy | Database backups, application/access logs, CI artifacts, caches and restore behavior | Unresolved | No full-erasure claim |
| Delete SLA | Completion deadline and failure/escalation behavior | Unresolved | No delete promise for real data |
| Assisted-mode access | Who may receive or inspect assisted-mode delivery data and required disclosure | Unresolved | No assisted real-data authorization |
| Tombstone composition | Whether installation reference, command UUID, idempotency key, command hash, state and timestamps may remain | Unresolved | Migration's candidate tombstone is synthetic-only |
| Erasure method | Hard deletion, irreversible redaction or cryptographic erasure per class | Unresolved | 0102 only rehearses one-way redaction |
| Route retirement | Separate policy and authority for conversation/route binding retirement | Unresolved | Route authority is not changed by 0102 |

An approved policy must be introduced as a new reviewed version. Enabling it or retiring a route is a separate owner-controlled action outside this draft.

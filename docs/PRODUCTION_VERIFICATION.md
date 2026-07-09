# Gate Production Verification

This document defines manual production verification commands for Gate.

## Rules

- Mark's local terminal is Windows PowerShell.
- Use PowerShell commands by default.
- Use `Invoke-RestMethod` for HTTP endpoint checks.
- Pipe nested responses through `ConvertTo-Json -Depth 5` so PowerShell does not collapse objects into table cells.
- Do not paste real secret values into chat.
- If `$env:ADMIN_BEARER_TOKEN` is not already set in the current PowerShell session, get the value from Railway → Gate project → service `gate` → Variables → `ADMIN_BEARER_TOKEN` and set it locally without sharing it.

## Admin status

Run this after deploying a PR that changes operational endpoints or deployment metadata:

```powershell
Invoke-RestMethod -Uri "https://gate-production-bab1.up.railway.app/admin/status" -Headers @{ Authorization = "Bearer $env:ADMIN_BEARER_TOKEN" } | ConvertTo-Json -Depth 5
```

Expected contract:

```json
{
  "service": {
    "status": "ok",
    "name": "gate",
    "environment": "production",
    "default_mode": "manual"
  },
  "deployment": {
    "service": "gate",
    "commit_sha": "<current deployed commit>",
    "bot_username": "gate_m_bot"
  },
  "events": {
    "count": 0,
    "last_received_at": null
  },
  "audit_logs": {
    "count": 0,
    "last_created_at": null
  }
}
```

Counts and timestamps may differ in production. They must be valid JSON values and must not expose raw Telegram payloads or secrets.

## Recent events

```powershell
Invoke-RestMethod -Uri "https://gate-production-bab1.up.railway.app/admin/events/recent" -Headers @{ Authorization = "Bearer $env:ADMIN_BEARER_TOKEN" } | ConvertTo-Json -Depth 5
```

The response must not include the raw `payload` field.

## Recent audit logs

```powershell
Invoke-RestMethod -Uri "https://gate-production-bab1.up.railway.app/admin/audit/recent" -Headers @{ Authorization = "Bearer $env:ADMIN_BEARER_TOKEN" } | ConvertTo-Json -Depth 5
```

The response may include safe audit metadata, but must not include real secrets.

## Action ownership wording

When a next step depends on a future event, name the owner of the wait and the follow-up action.

Good:

```text
I wait for CI to finish. When CI becomes 🟢, I check the merge gate and merge using the already-given conditional merge command.
```

Bad:

```text
We wait for CI.
```

A next step must be an action with an owner, not a status summary.

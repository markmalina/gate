# Gate MCP: Auth0 + ChatGPT activation runbook

This runbook activates the implemented OAuth resource-server boundary and the explicit,
idempotent reply-send MVP. It does not add a custom authorization server, expose secrets in
Git, or permit autonomous message sending.

## Fixed production identifiers

- Gate MCP resource / Auth0 API Identifier: `https://gate-production-bab1.up.railway.app/mcp`
- Protected-resource metadata:
  `https://gate-production-bab1.up.railway.app/.well-known/oauth-protected-resource/mcp`
- Gate MCP endpoint: `https://gate-production-bab1.up.railway.app/mcp`
- Access-token algorithm: `RS256`

OAuth issuer identifiers are exact strings. Preserve the trailing slash used by Auth0, for
example `https://example.eu.auth0.com/`.

## 1. Create the Auth0 API

In an Auth0 tenant, create an API with:

- Name: `Gate MCP`
- Identifier: `https://gate-production-bab1.up.railway.app/mcp`
- Signing algorithm: `RS256`
- Permissions:
  - `gate:status:read`
  - `gate:inbox:read`
  - `gate:drafts:write`
  - `gate:messages:send`

Enable RBAC and include permissions in access tokens. Assign only these permissions to Mark's
user for the private MVP. Existing ChatGPT connections must be reconnected after the fourth
permission is granted so that the new access token contains it.

## 2. Add explicit Gate grants to Mark's Auth0 user

Store this structure in the user's Auth0 `app_metadata`, using the real Gate UUID values:

```json
{
  "gate_access": {
    "workspace_id": "mark-personal",
    "account_ids": ["<allowed-account-uuid>"],
    "project_ids": ["<allowed-project-uuid>"]
  }
}
```

Do not put these authorization grants in `user_metadata`, which the user can edit.

## 3. Add namespaced access-token claims

Create and deploy an Auth0 Post Login Action, then attach it to the Login flow:

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const access = event.user.app_metadata?.gate_access;
  if (!event.authorization || !access) return;

  const namespace = "https://gate-production-bab1.up.railway.app/claims";
  api.accessToken.setCustomClaim(`${namespace}/workspace_id`, access.workspace_id);
  api.accessToken.setCustomClaim(`${namespace}/account_ids`, access.account_ids);
  api.accessToken.setCustomClaim(`${namespace}/project_ids`, access.project_ids);
};
```

The Action must add claims to the access token, not only the ID token.

## 4. Create the ChatGPT OAuth application

Create an Auth0 application for ChatGPT using Authorization Code with PKCE. Use the exact
callback URL displayed by ChatGPT when the Gate developer-mode app is created. Do not invent
or shorten the callback URL.

Keep the Auth0 client secret outside GitHub. Enter the client credentials only in the ChatGPT
developer-mode OAuth configuration when the UI requests them.

## 5. Configure Railway

Set these variables in the existing Gate production service:

```text
GATE_MCP_ENABLED=true
MCP_AUTH_MODE=oauth
MCP_AUTHORIZATION_SERVER=https://<tenant>.<region>.auth0.com/
MCP_PUBLIC_RESOURCE_URL=https://gate-production-bab1.up.railway.app/mcp
MCP_OAUTH_JWKS_URL=https://<tenant>.<region>.auth0.com/.well-known/jwks.json
MCP_OAUTH_ALLOWED_ALGORITHMS=RS256
MCP_OAUTH_WORKSPACE_CLAIM=https://gate-production-bab1.up.railway.app/claims/workspace_id
MCP_OAUTH_ACCOUNT_IDS_CLAIM=https://gate-production-bab1.up.railway.app/claims/account_ids
MCP_OAUTH_PROJECT_IDS_CLAIM=https://gate-production-bab1.up.railway.app/claims/project_ids
```

No Auth0 client secret is needed by Gate. Gate only verifies access tokens using the public
JWKS endpoint.

## 6. Connect ChatGPT and verify

In ChatGPT Developer Mode, create a private app pointing to the Gate MCP endpoint and choose
OAuth with the Auth0 application credentials. Then verify, in order:

1. ChatGPT discovers all five tools.
2. Login requests only `gate:status:read`, `gate:inbox:read`, `gate:drafts:write`, and `gate:messages:send`.
3. `gate_get_status` succeeds.
4. `gate_list_unanswered` returns only explicitly granted account/project data and labels every item `RU` or `BY`.
5. `gate_get_reply_context` returns the same account label, recipient and recent chronological history.
6. `gate_save_reply_draft` saves a draft, reports `sent=false`, and returns the account and recipient confirmation fields.
7. Before `gate_send_approved_reply`, ChatGPT shows Mark the final text, `RU`/`BY` account and recipient, then waits for explicit confirmation.
8. The first confirmed send creates one `send_attempts` record and one Telegram call. Repeating the same tool call returns the existing attempt without another Telegram call.
9. A Telegram timeout returns `status=unknown`; do not retry that draft automatically.

## Rollback

Set `GATE_MCP_ENABLED=false` in Railway. Gate immediately returns 404 from `/mcp`; Telegram
ingress and the existing Telegram-admin workflow remain independent.

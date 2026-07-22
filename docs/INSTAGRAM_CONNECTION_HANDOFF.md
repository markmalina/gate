# Instagram connection handoff

> Status: `waiting_user`  
> Recorded: 2026-07-22  
> Tracking issue: [#273](https://github.com/markmalina/gate/issues/273)

## 1. Intended outcome

Connect the owner-controlled professional Instagram account `mark.mayak.oko` to the Meta Developer app `gate-IG` through the official Instagram API with Instagram Login.

The first product result after connection is deliberately narrow:

```text
owner selects one connected account
+ supplies one ready video
+ supplies one caption
+ explicitly confirms the account, video and final caption
→ publish one Reel
→ return the Instagram media identifier and permalink
```

This document records connection state and safety boundaries only. It does not authorize a Reel publication, a message, a comment reply, a provider call or real-person-data processing.

## 2. Product boundary

- **Gate owns:** the Instagram transport adapter, OAuth callback and state validation, token lifecycle, account connection health, webhook authenticity, idempotent API calls, delivery audit and fail-closed transport errors.
- **Mayaki owns:** content scheduling, publication workflows, analytics interpretation, comment-keyword funnels, CRM attribution and business rules.
- **TAKE owns:** video analysis and editing. Gate must never acquire editing, transcription, subtitle or rendering behavior.

Gate may expose bounded transport operations to Mayaki or ChatGPT, but it must not become a second CRM or video-production service.

## 3. Current verified setup state

- Meta Developer app `gate-IG` has been created.
- The selected use case is Instagram content and messaging management.
- The dashboard is configured for Instagram API with Instagram Login.
- The Instagram mobile application is still authenticated for `mark.mayak.oko`.
- The owner set a new account password from that trusted mobile session.
- Direct login at `instagram.com` on desktop enters a repeated login/password/CAPTCHA loop in multiple browsers.
- The Instagram account has therefore not yet been added to `gate-IG`.
- Gate has not received or stored an OAuth authorization code, access token or refresh result.
- No Instagram webhook, publication, analytics poll, comment reply or Direct message is active.

The repeated CAPTCHA across browsers is treated as an Instagram web-login risk challenge, not as a Gate application failure. Recreating the Meta app or changing the password repeatedly is not a remedy.

## 4. Secrets and prohibited data

Never commit, paste into an issue, or emit in logs:

- the Instagram password;
- the Meta App Secret;
- OAuth authorization codes;
- short- or long-lived access tokens;
- webhook verification secrets;
- raw user profile, comment or Direct-message payloads;
- screenshots that reveal a secret.

The Instagram App ID is not a secret, but it should still come from environment configuration rather than being hard-coded. Secret values belong only in the approved production secret store. Repository files may contain variable names and safe defaults only.

## 5. Owner recovery sequence

Preserve the working mobile session. Do not log out of the Instagram application or remove the account.

1. Stop desktop login attempts for 12–24 hours. Repeated CAPTCHA attempts can extend the risk challenge.
2. Do not change the password again during the cooldown.
3. In the authenticated mobile application, open Accounts Center → Password and security → Two-factor authentication → `mark.mayak.oko`.
4. Enable an authenticator application or SMS and save backup codes privately.
5. Check Password and security → Where you're logged in / Login activity. If the failed desktop attempt is shown, approve it only when its time and location match the owner's attempt.
6. After the cooldown, make exactly one normal-browser login attempt:
   - no incognito mode;
   - VPN and browser traffic blockers disabled;
   - cookies and pop-ups allowed;
   - stable usual network, preferably the phone hotspot;
   - exact username `mark.mayak.oko` and the new password.
7. Immediately approve any matching login request on the authenticated phone and complete 2FA.
8. If the CAPTCHA loop returns, stop further attempts. Try `instagram.com` once in the phone browser. If that also loops, report the login problem from the authenticated Instagram application instead of logging out.

Official recovery references:

- [Instagram two-factor authentication](https://www.facebook.com/help/566810106808145/)
- [Instagram login requests](https://www.facebook.com/help/154776793259282/)
- [Instagram backup codes](https://www.facebook.com/help/1006568999411025)

## 6. Meta connection sequence after web login succeeds

1. Open the existing `gate-IG` application; do not create another application.
2. Open Instagram API → API setup with Instagram Login.
3. Choose **Add Instagram account**.
4. Authenticate as `mark.mayak.oko`, complete 2FA and approve only the permissions required for the planned stage.
5. Confirm that Meta lists the expected Instagram professional account under the app.
6. Do not send the password, App Secret, authorization code or token through chat or GitHub.
7. Return only non-secret evidence:
   - account username;
   - connection status;
   - granted permission names;
   - redacted expiry/health state;
   - any exact Meta error text with secrets removed.

Planned permissions must be verified against the live Meta dashboard before authorization. The expected families are basic business identity, content publishing, comments, messages and insights. Request the minimum needed for the active sprint; do not grant advertising permissions.

## 7. Gate implementation sequence

All capabilities remain default-off until their individual sprint and owner gate.

### Stage A — repository-only connector contract

- configuration names for App ID, App Secret reference, redirect URI and webhook verification secret;
- OAuth `state`/nonce validation and one-time callback handling;
- server-side code exchange;
- encrypted token persistence behind a credential-store interface;
- account registry keyed by workspace and Instagram scoped account identifier;
- permission and expiry health projection with all tokens redacted;
- deterministic errors and synthetic tests;
- no live Meta call in CI.

### Stage B — one owner account connection

- execute one authorized OAuth flow for `mark.mayak.oko`;
- verify account identity and granted permissions;
- store credentials outside Git;
- perform one read-only identity/permission probe;
- record a redacted connection receipt;
- no content publication or messaging.

### Stage C — first product: one Reel from ChatGPT

- accept one ready video from the owner; editing remains in TAKE;
- validate account, media format, caption and explicit final confirmation;
- create the official Meta media container;
- wait for bounded processing status;
- publish exactly once with an idempotency record;
- return media identifier, permalink and terminal result;
- timeout or ambiguous provider result becomes `unknown` and is never blindly republished.

### Stage D — analytics and events

- collect supported media insights at bounded checkpoints;
- accept authenticated comment/message webhooks;
- retain minimal redacted transport facts;
- pass normalized events to Mayaki for analytics, funnels and CRM;
- never let an inbound comment silently authorize a Direct message or external link.

## 8. Required environment-variable names

Names are provisional until the connector contract PR is accepted:

```text
INSTAGRAM_APP_ID
INSTAGRAM_APP_SECRET
INSTAGRAM_OAUTH_REDIRECT_URI
INSTAGRAM_WEBHOOK_VERIFY_TOKEN
INSTAGRAM_GRAPH_API_VERSION
INSTAGRAM_CONNECTOR_ENABLED
INSTAGRAM_PUBLISH_ENABLED
INSTAGRAM_COMMENTS_ENABLED
INSTAGRAM_MESSAGES_ENABLED
INSTAGRAM_INSIGHTS_ENABLED
```

Every feature flag defaults to `false`. Real values are forbidden in `.env.example`, tests, GitHub issues and documentation.

## 9. Acceptance and current blocker

Connection is accepted only when:

- `mark.mayak.oko` is visibly attached to the existing `gate-IG` app;
- the OAuth callback succeeds once with valid state;
- Gate can identify the exact account and granted permission names;
- no credential appears in Git, chat, logs or screenshots;
- a read-only health check succeeds;
- publication, messages, comments and analytics remain disabled until their own stages.

Current blocker:

```yaml
status: waiting_user
blocker: Instagram web login loops through CAPTCHA on desktop
owner_action: after a 12–24 hour cooldown, complete one trusted web login and add mark.mayak.oko to gate-IG
safe_mobile_session: preserve
recreate_meta_app: false
change_password_again: false
external_effects_authorized: false
```

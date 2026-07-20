# Gate Alpha operator runbook

Status: production fixture baseline; model path awaiting provider billing.

This runbook contains no credentials, Telegram identifiers, message content, or
payment data. It complements ALPHA_OPERATING_POLICY.md; the operating policy is
authoritative when the documents differ.

## Current safe baseline

- Alpha reacts only to a command, exact mention, or reply in an approved group.
- One fixed fixture deployment is the rollback baseline.
- Model proposals are shadow-only and cannot create Telegram delivery grants.
- At most one Telegram send attempt exists for one accepted invocation.
- OpenAI is the only approved provider and the hard UTC calendar-month ceiling is
  USD 50.
- Provider status reads, CI, product evals, and operational drills make no network
  provider call.

## Owner control in Gate M

Gate discovers group membership automatically. The owner never supplies a Telegram
group ID.

1. gate_alpha_list_installations shows group labels, opaque installation
   references, current state, limits, usage, and allowed actions.
2. A newly discovered group remains pending_approval and cannot run Alpha.
3. gate_alpha_control_installation applies one explicit approve, pause, or
   resume action with confirmed=true and the state the owner reviewed.
4. Approval binds only the reviewed fixture deployment and revision. A concurrent
   state change blocks the action.
5. gate_alpha_list_shadow_reviews is read-only. It shows bounded request/candidate
   evidence for approved groups and always reports sent_to_telegram=false.
6. gate_alpha_run_model_preflight is a one-shot synthetic provider check. Do not
   invoke it until billing is confirmed ready.

The management and review tools require the separate OAuth permission
gate:agents:manage. Add that permission to the Gate Auth0 API and the ChatGPT
User-Delegated Access grant, then reconnect Gate M once. Do not grant the permission
by weakening Gate tool scopes.

## Release sequence after billing works

1. Confirm production still reports the exact current Git commit, one staged model
   deployment, fixture binding active, zero model delivery grants, and budget
   remaining.
2. Invoke gate_alpha_run_model_preflight once with confirmed=true. It sends a
   synthetic prompt only, reserves budget first, never reads group content, and
   never calls Telegram.
3. Require provider_preflight_verified=true. A failed or ambiguously reserved
   preflight key is terminal; do not retry it automatically.
4. Use a reviewed code release to enable delivery-disabled model shadow for the
   approved installation. Do not rebind through a production database edit.
5. Collect at least ten clean proposals and review them through
   gate_alpha_list_shadow_reviews.
6. Require zero model delivery grants, zero failed/stuck turns, exact deployment
   binding, passing offline adversarial/product evals, passing multi-group isolation,
   and remaining monthly budget.
7. Promote only through another reviewed code release. Fixture remains the rollback
   target.

## Automatic stop drills

| Observed condition | Expected state | Required safe result |
|---|---|---|
| Any model delivery grant during shadow | safety_invariant_violated | no promotion; fixture rollback target |
| Any failed model shadow turn | provider_preflight_failed or blocked shadow | no promotion; inspect bounded error category |
| Reserved preflight becomes stale | provider_preflight_stuck | no automatic retry |
| Reserved spend reaches USD 50 | budget_exhausted | block before provider call |
| Staged deployment count is not exactly one | deployment_not_exact | no preflight or promotion |

CI executes all five drills as pure state evaluation. Every result must keep
mutation_allowed=false, limited_canary_review_ready=false, and
rollback_target=fixture_canary.

## Incident boundaries

- Pause is the normal reversible installation kill switch.
- Revocation is intentionally destructive to the installation lifecycle and is not
  exposed in Gate M.
- A provider timeout, unknown response, or missing usage is not retried.
- A Telegram unknown send result is terminal and is not retried.
- Do not manually redeploy, retry, roll back, edit production data, or change Railway
  configuration without direct owner authorization.
- Never move context across groups, expose provider identifiers, choose a recipient
  from model output, or add administrative Telegram capabilities.

## Production verification after every merge

1. Railway/Gate M commit equals the exact GitHub merge SHA.
2. RU and BY Business connections remain ready and can_reply=true.
3. MCP lists the expected tools and OAuth remains enforced.
4. Alpha fixture installation remains exact and connector/webhook readiness is true.
5. Model delivery grants stay zero; model budget and bounded error state are visible.
6. No live message is manufactured for a smoke test.


## Persistent ChatGPT permission warning recovery

An orange ChatGPT warning that not all requested permissions were granted is an
authorization-server grant mismatch. Reconnecting cannot add a scope that the Auth0
user-delegated client grant does not allow.

Recovery order:

1. Stop repeating reconnect attempts and keep all live Alpha actions blocked.
2. In Auth0, open **Applications → APIs → Gate API → Application Access**.
3. Edit the ChatGPT/Gate M application's **User-Delegated Access** grant and allow
   all five Gate scopes:
   `gate:status:read`, `gate:inbox:read`, `gate:drafts:write`,
   `gate:messages:send`, and `gate:agents:manage`.
4. For a dynamically registered ChatGPT client without a stable application row,
   configure the same set under **Settings → Default Permissions for Third Party
   Apps → User-Delegated Access**. Do not select future or unrelated scopes.
5. Save the Auth0 grant first, then disconnect/reconnect Gate M exactly once.
6. Verify read-only calls in order: `gate_get_status`,
   `gate_alpha_list_installations`, and `gate_alpha_list_shadow_reviews`.
7. If any call still reports a missing scope or refresh token, stop. Record only the
   bounded error category; never paste a token, client secret, authorization code or
   raw group identifier.

Do not fix the warning by moving `gate:agents:manage` tools to a weaker scope or by
removing protected tools from the connector.

## Owner test-group limited-canary boundary

Repository readiness may proceed while OAuth is blocked, but the live canary may not.

Before the first owner test-group delivery:

- production commit and schema equal the accepted GitHub/Railway receipt;
- Gate M read-only status and installation list succeed;
- exactly one existing owner-controlled installation is selected by label and opaque
  reference, never by a Telegram numeric ID;
- the installation is approved or resumed through the reviewed-state fence;
- invocation is explicit: `/alpha`, exact mention, or reply;
- one model turn is bound to the reviewed deployment and budget ceiling;
- one invocation can create at most one Telegram delivery attempt;
- fixture remains the rollback target and pause remains the immediate kill switch;
- audit receipt records bounded installation label, turn state, delivery state and
  service health without tokens or Telegram identifiers.

The first canary ends after one verified answer. No proactive message, broadcast,
second group, Alexander group, Meta path or cross-group memory is part of this release.
Any ambiguous provider or Telegram result is terminal and is not retried.

# Gate Alpha operating policy

Status: standing owner authorization recorded on 2026-07-15.

This document is the repository-side release contract for current and future Alpha
sprints. It does not contain credentials, Telegram group identifiers, message content,
or payment data.

## Authorized product scope

Gate may develop, test, review, merge, deploy, and operate Alpha in Telegram groups
that the owner added to Alpha and explicitly approved in Gate. Alpha may process only
an invocation addressed to it by command, exact mention, or reply, together with
supported attachments and bounded context from the same approved group.

The release path is:

1. fixture regression;
2. model shadow with no Telegram delivery;
3. limited canary;
4. live delivery in approved installations.

Promotion requires green CI, automated evaluations, group-isolation checks, rate and
token limits, rollback evidence, and no critical errors. One inbound invocation may
create at most one Telegram send attempt. Alpha must not initiate messages, select a
recipient, use cross-group context, or acquire group-administration capability.

## Provider and budget boundary

OpenAI is the only authorized external model provider for this policy. Model requests
must use the data-minimized, non-stored Responses runtime and must remain inside the
approved installation and dialog scope.

The hard Gate budget is **USD 50 per UTC calendar month**. Gate reserves the maximum
possible cost before a provider call and blocks the call when the projected reservation
would exceed the ceiling. The budget cannot be raised by runtime configuration; an
increase requires a reviewed code change and new owner authorization.

A configured API key and data gate do not prove that provider billing is active. Until
billing works and the staged deployment is deliberately activated, the production
fixture remains bound and Gate makes no model call.

## Code-owned delivery barrier

Every model-backed proposal is marked `shadow_only`. The canary orchestrator rejects
such a proposal before creating a delivery grant, even if the general Alpha delivery
switch is enabled. The existing network-free fixture alone remains canary-deliverable.

Moving a model deployment from shadow to canary or live therefore requires a separate
reviewed release change with evidence. No new Railway variable or manual group test is
needed for ordinary development iterations.

## Explicit exclusions

This standing authorization does not cover Oko, personal Telegram Business
conversations, another AI provider, cross-group memory, proactive broadcasts,
administrative group actions, secret disclosure, irreversible data deletion, manual
production rollback or redeploy, or spending above USD 50 per month. Those actions
require separate owner authorization.


## Provider fault isolation

CI must execute every reviewed model-bundle case through the Responses contract without
network access. The required suite covers useful replies, unavailable current data,
prompt-injection refusal, external-action and medical handoff, and an empty-request
no-reply outcome. Removing or changing a required case blocks release evidence.

The runtime makes one provider attempt per claimed turn and never retries an ambiguous
outcome. Authentication rejection, rate limiting, provider unavailability, timeout,
invalid output, and missing token usage become separate bounded error codes. Provider
response bodies are never copied into Gate errors. A nominally successful response
without trustworthy token usage fails closed so accounting cannot silently undercount.

## Operational readiness status

Gate M and the scoped admin status derive model readiness from database evidence and
boolean configuration checks. Reading status never contacts OpenAI or Telegram and
never returns a credential, group identifier, or message content.

The state is closed and fail-safe: configuration incomplete, deployment not exact,
budget exhausted, awaiting provider preflight, provider preflight failed, or shadow
observed. It also reports the exact staged-deployment count, content-free turn counts,
and conservative reserved and remaining monthly budget.

`awaiting_provider_preflight` is the expected healthy state while provider billing is
unavailable. Provider verification and shadow observation require a separately
controlled model turn; neither status inspection nor deployment performs that call.
Telegram delivery remains blocked for every model-backed proposal.


## One-shot provider preflight

Provider billing is verified through one protected, idempotent preflight release key. The preflight
uses the exact staged deployment and reviewed instruction bundle but sends only a synthetic request;
it never reads group content, constructs a recipient, creates a delivery grant or calls Telegram.

Gate reserves conservative monthly budget before the provider boundary under a workspace-scoped
database lock. A succeeded, failed or ambiguously reserved release key is terminal and is never
retried automatically. Only content-free evidence and a candidate SHA-256 digest are persisted.
The preflight endpoint may be deployed while billing is unavailable, but it must not be invoked
until billing readiness is confirmed.

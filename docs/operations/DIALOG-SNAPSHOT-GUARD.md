# Reply context freshness (#745)

Gate refuses a reply prepared against an older dialog. The prerequisite is a
scope-bound `context_snapshot` returned by `gate_get_reply_context`. It covers
stored messages and durable Business ingress revisions across the account's
connections, including incoming and outgoing text, voice, files, edits and
deletions. An unrelated chat and duplicate webhook delivery do not invalidate it.

## Work contract

1. Read `gate_get_reply_context` without a historical cursor. Review its current
   history and `dialog_events` (accepted events awaiting projection are included).
   Require `context_ready=true` and a non-null `context_snapshot`.
2. Pass that exact token as `expected_context_snapshot` to
   `gate_save_reply_draft` or `gate_prepare_reply_document`. Never obtain a newer
   token merely to attach it to an unchanged, unreviewed answer.
3. Show the resulting text/file/caption and recipient for approval and use the
   existing approved-send tool. The saved token is immutable send context; the
   send call cannot replace it. Formatting hashes and confirmation remain required.
4. On `context_changed`, nothing is dispatched. Read `current_context`, review
   the new messages, compose a new answer, save a new draft, and request approval
   of the new content. Gate does not rewrite or resend the old answer itself.

Refusals return `sent=false`, an error code, and `current_context` when the caller
also has `gate:inbox:read` and the context can be read. Otherwise it is null.
Raw event text is withheld when the dialog has a conversation in an ungranted
project. The snapshot is a concurrency precondition, not an authorization grant.
Such a redacted read is not ready for composition and returns no usable token.
Scope completeness is checked against both conversation and projected-message
rows even when the recent raw-event page is empty. A hidden message without a
conversation row also prevents a sendable snapshot; its content remains hidden.
Historical pages and reads that change while being assembled return no usable
snapshot. `dialog_events_has_more` flags bounded raw-event history.

The Office/image upload bridge accepts `--expected-context-snapshot`; pass the
token from the context actually reviewed. Synthetic snapshot values in operator
examples are placeholders and must be replaced with that token.

## Admission and boundary

Business ingress commits its revision increment with the deduplicated receipt,
before projection or voice transcription. Send reservation locks the same
connection/chat revision rows and checks the saved snapshot. It increments the
revision so another Work cannot send from the same old review. A second check
immediately before provider dispatch catches events committed after reservation.
A refusal there finalizes the attempt as failed with `context_changed`, without
provider send I/O. Exact retries of an existing sent/unknown/failed attempt return
its receipt and never redispatch it.

No SQL transaction spans provider HTTP. The linearization point is the final
admission transaction: this protects events durably received before admission.
Events that arrive after admission, while HTTP is in flight, or have not reached
Gate yet cannot retroactively cancel that send. Gate cannot atomically compare
its database with Telegram's remote dialog state.

Text, original documents (including #700 Office/images), and queued sequences
share the sender. A sequence advances only from its own confirmed send effect;
an intervening accepted event stops subsequent items. An own outbound webhook
echo arriving in flight conservatively stops the remainder too. Queueing never
refreshes a saved baseline. Legacy admin send routes refuse delivery and direct
the operator to the guarded MCP flow; legacy snapshot-less drafts must be
reviewed and recreated. Personal Telegram delivery remains dormant and needs
this guard wired before activation. Future photo/album senders (#742) must use
the same admission path.

## Release and verification

### Independent Gate release boundary (9 October 2026)

Owner decision [Center #152](https://github.com/AlphaCenter/center/issues/152)
freezes AQUB and supersedes the earlier AQUB-based #745 release route.
Do not use AQUB issue #72, its archived workflows, its credentials, or the
historical `aqub-c1c-production` requester to perform this cutover. Historical
source and review receipts remain evidence of those exact revisions only.

Gate owns its requester and control issue #751. The current Gate-only simple
deploy action does **not** implement the snapshot cutover below: it does not
prove automatic deployment disabled, freeze/drain the entire fleet, create the
bound backup, or perform the two verified deployments of one guarded SHA.
Do not substitute it for these requirements. Before merging this migration,
obtain a reviewed, independently verified Gate-owned Ops cutover contract and
a protected observation proving automatic deployment disabled. Its fixed
caller, target, credential scope, source binding, receipt verifier, disposable
fixture, rollback and recovery requirements must be established independently
of AQUB. Missing credentials or target evidence are blockers, not permission
to copy secrets, re-enable AQUB, or use a provider dashboard.

The source integration retains Gate's frozen-AQUB startup/routes/CI and its
own requester while retaining the bounded `reply_cutover` predeploy helper.
A merged source or successful CI does not prove this boundary is deployed.
The independent Gate release observed on 9 October failed before provider
access with `E_GITHUB_AUTH / github-app-key`; exact Gate-owned protected
credential provisioning is an operator prerequisite and is outside this
source change. Re-observe current source/runtime and prerequisites before
any release, rather than repeating that unchanged failed request.

Apply migration `0116_reply_dialog_versions` after `0115_watch_voice_post_payment`.
Disable sends while migrating and replacing all Business ingress and send
processes; enable only after every process uses the guarded code. A mixed fleet
with an old ingress writer cannot provide the new guarantee. Do not downgrade
the table while guarded processes are running. To roll back, disable delivery
first, stop guarded processes, then restore code/schema deliberately. Merge
concurrent migrations into one Alembic head before release.

`tests/test_dialog_snapshots_postgres.py` exercises real database locks with
synthetic ingress and recording providers: incoming/outgoing media before
projection, duplicates, other chats, edits/deletions, stale prepare, probe and
dispatch races, exact replay, fresh MCP context, and concurrent Works. The
normal PostgreSQL send suite also exercises document delivery and sequences.
These tests require `DATABASE_URL`; skips are not database race evidence.
Code/CI verification does not constitute deployment or live acceptance. Joint
acceptance should cover both a stale text answer and an original Office/image
document after another incoming/outgoing message, plus an unchanged-context send.

### Fixed migration admission and redacted observations

The #745 Railway predeploy command is `python -m app.worker.reply_cutover`.
It reads only Alembic revisions and aggregate `send_attempts.status` counts,
works on the pre-guard schema, and permits only the single baseline
`0115_watch_voice_post_payment` or already-applied `0116_reply_dialog_versions`.
On 0115, `GATE_SEND_ENABLED=false` is also required before migration. An
ordinary deploy with delivery still enabled fails before applying 0116. On
already-applied 0116, enabled delivery is permitted for the guarded activation
deploy; active-attempt and exact-schema checks still apply.
Any other or multiple revision heads, an unavailable database, or any
`reserved` / `sending` attempt stops deployment before migration. It runs only
`upgrade 0116_reply_dialog_versions` and verifies the resulting revision and
active counts. A future migration must deliberately update this bounded
admission command; an unknown future head never passes by default.

This observation does **not** prove the old deployment is stopped, that there
are no requests in flight, or that Telegram delivered an uncertain attempt.
Ops must prove all old instances/processes are stopped before invoking the new
predeploy command and bind the backup and migration to the exact Gate database.
A zero count before shutdown is not a fleet freeze. Preserve attempt journals:
`unknown` is reported unchanged and is never relabeled, retried, or erased by
this command. Existing stale recovery is an explicit, default-disabled one-shot
operation; it is not automatic redelivery. Active attempts require a separate
reviewed resolution and another observation, never forged zero counts.

After deployment, authenticated `GET /admin/ops/reply-cutover` reports only the
process deployment SHA, schema revisions, `reserved` / `sending` / `unknown`
counts, the process's configured send flag, and the `dialog-v1` guard contract.
It returns 503 with a safe code if observation is unavailable. This endpoint
does not enable delivery and cannot attest other instances. Ops must correlate
it with exact new deployment identities and the absence of old instances before
acceptance or enabling reviewed sends. The release uses two stages: set the
fixed service send variable false without starting a deploy; prove all old
instances stopped; create and bind the backup; deploy the guarded SHA with
delivery disabled; verify 0116, that SHA, the disabled flag, and only guarded
instances; then set the send variable true and redeploy the exact same SHA.
Verify the enabled flag and only guarded instances again. The same fixed
predeploy check accepts 0116 idempotently on activation. The flag requirement
fences migration with enabled delivery; it does not prove old fleet shutdown.
Before merge, Ops must separately prove automatic deployment is disabled so
the reviewed stop-and-backup sequence cannot be bypassed.

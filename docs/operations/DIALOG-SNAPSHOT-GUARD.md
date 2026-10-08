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

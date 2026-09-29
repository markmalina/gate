# Gate M: owner approval and readback contract

This is the Gate-side MCP contract for #716/#718 and release #722. It does not
assert that the already connected ChatGPT app has refreshed its tool declarations.

1. Read the **latest** task with `gate_get_reply_context`. If
   `sequence_approval.ready` is not true, or `dialog_inbound_revision` is null,
   wait for projection/recovery and read the latest task again. An older history
   page or a sequence-detail revision is not approval authority.
2. Save 2–50 distinct drafts for one task and show Mark the exact ordered text,
   RU/BY account and recipient. Get one explicit approval for those values.
3. Queue once with those draft IDs, `confirmed=true`, the approved account and
   recipient, `interval_seconds=60`, and `expected_inbound_revision` from step 1.
   A changed task/revision/text requires fresh context and approval. A queued
   response means `sent=false`, not delivery.
4. Call `gate_get_reply_sequence` using its `sequence_id`; follow every
   `next_cursor` until null to display all remaining saved draft texts. Interpret
   `draft_text_matches_approval=false` as changed and null as unavailable; the
   current saved body is not an immutable approval archive. Show distinct
   `sending`, `sent`, `failed`, `unknown`, `cancelled` counts and the notification
   `pending`, `blocked_route`, `failed`, `unknown`, or `sent` state.
5. After stop or unknown, never auto-resume. Mark's new approval is required.

## Connected app gate

Read-only tool metadata inspection on 28 September 2026 found the connected
Gate M `gate_queue_approved_reply_sequence` still declares 2–20 and omits
`expected_inbound_revision`. Its `gate_get_reply_context` declaration lacks the
new approval fields and `gate_get_reply_sequence` is not exposed. No real
conversation or provider call was used. The backend PR cannot establish the
user path until the registered app refreshes its MCP discovery/schema. The
supported mechanism and owner of that refresh must be established before live
testing. Acceptance requires a newly observed connected declaration for all
three tools and a scoped synthetic interaction covering page traversal; a
backend JSON schema or CI result alone does not satisfy it.

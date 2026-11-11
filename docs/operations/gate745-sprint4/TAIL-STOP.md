# S4: durable series tail-stop and exact own progress

Scope: #745 / existing PR747, baseline
`2de172ff7d34448dd7047663f5aff927ab4d9f74` /
`dc69d2b7d46d05d8a00317a1fd32bf3cc7c9b913`, main
`8d17d0b40a86c856bec1072633c721212a34a9dc`.
[Owner plan](https://github.com/markmalina/gate/issues/745#issuecomment-6789115422),
[S3 receipt](https://github.com/markmalina/gate/issues/745#issuecomment-6793658880),
[claim](https://github.com/markmalina/gate/issues/745#issuecomment-6795320936),
[regulation](https://github.com/markmalina/ai/blob/main/WORK-RULES.md).
Source/check evidence is author evidence, not independent acceptance or production.

## Before and after

S3 checked the next item at admission, but accepted ingress did not persist an
immediate series stop. Discovery had a global 500-row LIMIT, which could split a
series. Progress used an audit cursor with a next snapshot; a webhook echo or its
later projection could invalidate it.

S4 uses the existing draft JSON and attempt/receipt/audit journal. No tables or
columns are added: ReplyDraft.prompt_context already persists series metadata;
SendAttempt already records account/project/conversation/draft/task/connection/chat,
status, provider ID and confirmed finish time. TelegramReceipt is already unique
by connector/update ID. These are the necessary storage capabilities, so there is
no DDL, new migration, history rewrite or graph change (single source head 0120).
No PR720 schema/G2/scheduler dependency is imported.

## Serialization and durable stop

The existing global ingress version row is acquired before context/draft locks in
queue, reservation, success finalization and worker outcome. Authenticated Business
ingress takes the same fence before raising its scoped revision and stopping all
matching series in the same receipt/outbox transaction. Unavailable producers use
the same order. HTTP remains outside SQL transactions. This uses S3 serialization;
it is not a new scheduler or a claim of #716 parallel-performance acceptance.

`stop_sequences` has no page limit. Proven account/chat stops only that account's
matching series across routed projects. An unmapped event uses the existing sticky
connector-wide unavailable contract. A known disabled connection stops its account;
ordinary successful receipts/enabling do not clear sticky fences or stop latches.

The item metadata receives state=stopped, stop_reason, stopped_at, stop_event_ref,
error_code=context_changed and auto_resume=false. Existing sent items are preserved.
A stop does **not** update SendAttempt, erase reservations, cancel an admitted HTTP,
or turn SENDING/UNKNOWN into a claimed cancellation. Readback uses attempt truth in
preference to the metadata display state. An already started write may finish and
retain its SENT receipt while the remaining tail stays stopped. Worker completion
never removes a stop_reason. Restart, expiry, failure, duplicate command and later
own echo cannot reactivate it. There is no resume/clear endpoint or backfill.

A separately approved Work reservation is itself a new outgoing fact and stops
other series conservatively even if that HTTP later fails. Own reservation excludes
only the exact originating series; it does not exempt other approved packages.

## Own-progress proof, not refreshed approval

Every draft retains its original dialog_snapshot. Sequence admission reconstructs
the same baseline by subtracting **only** proven own effects from the current
fingerprint. It never writes a fresh fingerprint into an old draft and never adopts
an arbitrary current state as its expected snapshot.

Proof chain: durable account/connection/chat -> sequence ID and ordered item ->
exact draft/task/conversation/project/principal attempt -> reservation audit -> SENT
provider result/message ID and finished_at. All predecessor positions must be
present, confirmed, and at least 60 seconds old; interval=60 and size 2..50 remain
mandatory. Missing/mismatched journal, any failed/unknown predecessor, changed
binding or stop latch fails closed. The common final sender repeats this check;
a caller cannot skip the worker to deliver item N early. Media/document/Rich
sequence prohibitions and existing exact formatting hashes remain unchanged.

For authenticated original message/business_message webhook echoes, owner sender,
connection, account, chat and exact SENT provider ID must resolve uniquely to that
item/attempt. Ingress records an own-echo audit linked to the exact raw receipt,
then exempts only that series from stop. The revision still increases, preserving
ordinary snapshot invalidation for every other Work. Exact replay creates no new
increment/audit; a second update for the same proven message has its own increment
and exact receipt proof. Delayed/permuted proven echoes remain own progress.

Own outbound projection rows are excluded only by an exact attempt marker plus
provider identity, or the exact audited echo update ID plus provider identity,
account/project/connection/chat/direction and the saved text. Text coincidence and
time proximity alone are never proof. Edited/deleted events are not own original
message echoes. Manual outgoing, another Work, or an inbound with the same text
and numeric message ID stops the series.

Before the provider response has been durably confirmed, an echo has no proven
attempt/message-ID link. It therefore stops the tail. A subsequent SENT outcome
explains the already-started item but does not undo that stop. Lost ACK/UNKNOWN
also stops/fences continuation and never permits a resend. S4 does not add provider
reconciliation or heuristically attach early echoes to an in-flight attempt.
Legacy series lacking the necessary receipt proof fail closed; no old approvals
are silently rebound to this contract.

Raw-read completeness, hidden-project isolation, gap fences and bounded text
continuation remain enforced. Only exact audited own receipts are excluded from
the sequence's pending raw-read set; generic context still exposes the events.
An arbitrary pending event is never ignored. Payload expiry, hashes, budgets,
access checks and terminal replay/media receipts remain in the shared sender.

## Visible result and concrete notification gap

`gate_get_reply_context` returns scoped `reply_sequences` and a short text summary:
series identity/state/reason, ordered item IDs/positions, actual attempt states and
provider IDs, sent/remaining/ambiguous counts, auto_resume=false, and notification
status. It is task/account/project/workspace scoped and requires the existing inbox
read permission at the MCP route; refusal does not grant read access. Stopped
series require a fresh read, new exact drafts/package and new exact approval.

**notification_status=route_unavailable**: current source has a dormant generic
HTTP queue-incident notifier, not a verified bound owner destination with series
payload, durable idempotency, ACK/lost-response reconciliation and retry policy.
It cannot be called an existing verified owner series-stop notification route.
No notification is sent, no speculative destination/credentials/config is added,
and a stored stop/audit is not called a delivered message. Synthetic preparation
and duplicate/restart stop-status readback are tested; actual notification delivery
and retry are not implemented/tested because this route is absent. A subsequent
bounded owner-notification integration must establish that route before claiming
end-to-end #722 notification acceptance.

## Local evidence matrix

Literal S3 baseline was exercised independently with the same synthetic sequence:
after item 1 was confirmed and voice ingress committed, item 2 was still scheduled
before another worker ran. The S4 immediate-readback regression instead observes
a durable stopped tail at that same boundary (no subsequent worker needed).


Providers are synthetic recorders/MockTransport only. PostgreSQL 17.11 runs in a
throwaway UTF8 cluster; Python test processes deny all non-loopback networking.
No production data/credentials/models/STT/messages are involved.

| Scenario | Actual exercised path |
| --- | --- |
| 2 and 50, order, 59/60-second boundary | queue -> worker -> common sender -> durable receipts; simulated consistent sender/admission clock |
| inbound text/voice/photo/file, manual identical text, edit/delete | actual raw receipt -> immediate status before worker, SENT receipt preserved, 49 stopped items |
| own original/repeated/delayed echo, projection | exact SENT-ID ingress audit, normalized fingerprint, actual next-item sender |
| early echo, lost ACK and UNKNOWN | ingress during recorder HTTP, SENDING readback, terminal outcome, restarted worker refuses tail |
| same text/ID inbound and delayed own echo after stop | immediate stop remains unchanged; no resurrection |
| other Work and scope | separately approved actual sender stops pending series; another account and chat remain independent |
| two workers during HTTP | one real recorder write, second worker does not falsely stop own progress; committed event preserves in-flight receipt |
| two final senders vs independent ingress process | three distinct PG backend PIDs, observed row-lock waits, explicit stdin COMMIT barrier, both final admissions refuse |
| completeness beyond 500 | 600 matching rows discovered and stopped with no hidden remainder |
| status/access | actual SQL status and context, JSON schema/text rendering, wrong account/project/workspace denied |
| S2/S3 preservation | snapshot, final-admission/media, plain/formatting/document/queue/direct ingress regression slices; final exact-head CI full suite |

The controlled race exercises actual SQL guards, not a mocked revision. Separate
actual full-sender S3 regressions cover event/gap committed after reservation and
before provider write. An admitted/started HTTP cannot be revoked; an event not yet
delivered to Gate is outside the observation boundary. Complete DB failure cannot
persist a marker in that unavailable database: ingress remains non-2xx and sends
fail closed, as in S3. No gap is automatically healed.

Final counts/SHA/CI links, unsuccessful harness/compatibility cases and skips are
recorded in the #745 S4 author receipt. No unchanged hosted reruns are authorized by
this document. C5 whole-candidate independent review, C6 rehearsal/recovery and
notification route gap, C7 separately authorized cutover, C8 connected catalogue
and live product acceptance remain. #745/#718/#722 are not closed by source tests.

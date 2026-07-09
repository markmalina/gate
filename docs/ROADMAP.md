# Gate Roadmap

This roadmap captures the agreed product direction after the project audit.

## Product target

Gate should become the durable communication layer for Mark's Telegram work:

```text
Telegram update
→ durable inbound storage
→ normalized message
→ conversation / inbox state
→ unanswered detection
→ AI draft suggestion
→ Mark approval
→ controlled outbound send
→ delivery/audit/memory
```

Gate must keep the current safety principle: AI may suggest text, but must not approve or send by itself.

## Sprint 1: durable message storage

Goal: every inbound Telegram update is durably stored, normalized and deduplicated before any send attempt.

Scope:

- add raw Telegram update receipt storage;
- add normalized `messages` storage;
- add Telegram update idempotency;
- persist inbound message state before any Telegram send;
- keep `ping` as the only safe automatic reply;
- audit send success and send failure;
- do not add AI replies;
- do not add free-form sending.

Acceptance criteria:

- one Telegram `update_id` creates at most one inbound event/message;
- repeated Telegram webhook delivery does not duplicate storage or resend `ping`;
- inbound message remains stored even if Telegram send fails;
- `/admin/status` exposes normalized message count and latest message timestamp;
- CI is green.

## Sprint 2: conversations and unanswered inbox

Goal: Mark can ask where replies are still needed.

Scope:

- add `conversations` table;
- add `reply_tasks` or `inbox_items` table;
- update conversation state on inbound and outbound messages;
- add `GET /admin/inbox/unanswered`;
- no AI drafts yet;
- no free-form sending.

Acceptance criteria:

- inbound human message creates or updates a conversation;
- inbound human message creates a `needs_reply` item;
- existing safe outbound reply closes or satisfies the matching item where applicable;
- unanswered items can be listed with chat/person/project context.

## Sprint 3: manual reply state management

Goal: Mark can manage the inbox before AI is introduced.

Scope:

- mark replied;
- dismiss as no reply needed;
- snooze;
- reopen;
- audit every state transition.

Acceptance criteria:

- every manual state change is auditable;
- no Telegram send happens from these endpoints.

## Sprint 4: AI draft suggestions, no send

Goal: AI can propose reply drafts for unanswered items.

Scope:

- add `reply_drafts` table;
- add a draft-generation endpoint;
- keep prompt context bounded to the selected conversation;
- store AI draft text;
- no direct Telegram send.

Acceptance criteria:

- a draft can be generated and inspected;
- a draft can be edited/rejected later;
- the AI path cannot send messages.

## Sprint 5: approval workflow

Goal: Mark can approve or reject drafts.

Scope:

- approve draft;
- reject draft;
- edit draft;
- create an outbound queue item only after approval;
- audit approval decisions.

Acceptance criteria:

- approved text is immutable or versioned;
- rejected drafts are retained for audit;
- approval does not by itself send to Telegram unless a later send command is explicitly invoked.

## Sprint 6: controlled Telegram send

Goal: approved replies can be sent through Gate.

Scope:

- add `outbound_messages` table;
- add explicit send endpoint;
- record Telegram API response;
- record failed send attempts;
- support retry under explicit control;
- close reply task after confirmed send.

Acceptance criteria:

- only approved outbound messages can be sent;
- all sends are audited;
- failed sends do not lose message or approval history.

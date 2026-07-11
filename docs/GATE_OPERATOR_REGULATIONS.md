# Gate Operator Regulations

This document defines the operating rules for ChatGPT-assisted Gate development and Telegram operator guidance.

## Current product context

Gate is currently a Telegram Business gateway, not an MCP server.

Current operator loop:

```text
Telegram inbound
→ Gate
→ /inbox
→ /draft_<task>
→ /send_<draft>
→ Telegram outbound
→ task closed
```

MCP may become a later integration layer:

```text
ChatGPT / Claude / Codex
→ MCP
→ Gate / SysteM / Memory / Telegram
```

But MCP is not the current implementation target unless explicitly selected as the sprint scope.

## Telegram-first operator guidance

The operator works from Telegram messages, not from REST/admin panels and not from manual ID substitution.

### Do not ask the operator to substitute IDs

Do not say:

```text
/draft_<task>
/send_<draft>
подставь task id
подставь draft id
```

Instead, instruct the operator to tap the already visible blue Telegram command from the latest Gate message.

Good:

```text
Открой /inbox и нажми синюю команду /draft_... у нужной задачи.
После создания драфта нажми синюю команду /send_... в этом же сообщении.
```

Bad:

```text
Возьми task id и подставь его в /draft_<task>.
```

### Do not ask the operator to wait for a commit

Do not say:

```text
Дождись Commit: <sha>
```

Every Telegram-admin command response must include the deployed commit at the top. The operator should paste/send the Telegram result as-is; the assistant reads the `Commit:` line from that result.

Good:

```text
Нажми /inbox и пришли ответ Gate. В ответе уже будет Commit и состояние.
```

Bad:

```text
Дождись коммита, потом нажми /inbox.
```

### Give concrete next actions from the current screen

When the operator asks what to do next, provide the smallest next Telegram action that is visible or will be visible in the current Gate output.

Good:

```text
Нажми синюю /draft_... у сообщения «Баран».
Потом нажми синюю /send_... в драфте.
```

Bad:

```text
Выполни /draft_<task>, потом /send_<draft>.
```

### Prefer clickable command flow over typed command flow

All operator UX should be designed so the operator can tap commands/buttons from Telegram:

```text
/inbox
→ tap /draft_...
→ tap /send_...
→ /inbox confirms task disappeared
```

Manual typing is acceptable only as a fallback for debugging, not as the primary instruction.

## Response contract for assistant

When reporting a deploy/check task, the assistant should:

1. State what changed.
2. State the current merge commit if known.
3. Ask the operator to run or tap one concrete Telegram command and paste the full Gate response.
4. Avoid placeholder commands unless no Gate output exists yet.
5. Avoid telling the operator to wait for a commit; the pasted Gate response contains the commit.

## Product contract for Gate Telegram replies

Gate admin/diagnostic replies should include:

```text
Commit: <short-sha>
```

at the top whenever feasible, especially for:

```text
/status
/webhook
/events
/raw_updates
/inbox
/draft_...
/send_...
/clean_...
```

Gate replies that contain next actions should present those actions as clickable Telegram commands with real IDs already filled in.

## Sprint planning note

Do not pivot to MCP unless the sprint is explicitly declared as MCP scope. The active stream is Telegram-native Gate operator UX and reliable message handling.

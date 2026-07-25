# Лента — owner communication feed

`Лента` is the owner-facing chronological projection of communication facts already
durably held by Gate.

## Read selector

Use the existing read-only `gate_get_reply_context` MCP tool with:

```json
{"task_id":"feed:day:2026-07-25","limit":200}
```

`lenta:day:YYYY-MM-DD` is an equivalent selector. The day boundary currently uses
`Europe/Berlin` and is returned explicitly in every response.

## Sources

The first projection combines:

- canonical Telegram messages from both owner accounts;
- persisted Telegram voice transcripts, deduplicated against their corresponding
  message identity;
- persisted phone-call transcripts from the bounded Dropbox call intake.

Every event keeps its exact source time, account/source label, medium, direction,
participants and an opaque durable source reference.

## Semantic boundary

Gate exposes `semantic_input_private` only inside the owner-scoped read result so the
calling Alfa/ChatGPT layer can derive meaning. The default presentation must:

1. write a concise semantic summary instead of quoting the source;
2. retain whether the original was text, voice or a call;
3. retain source account, direction, participants and timestamp;
4. treat one phone call as one event with positions, agreements and unresolved items;
5. never put message text, transcripts, audio, chat IDs or person payloads into GitHub,
   logs, CI evidence or deployment receipts.

Gate owns durable facts and provenance. Relations owns normalized person identity.
Alfa owns semantic interpretation, importance, commitments and follow-up extraction.

## Safety

The selector is read-only and reuses `gate:inbox:read`. It does not add a tool or OAuth
scope, call Telegram/OpenAI/Dropbox, change Telegram read state, send a message or mutate
communication records.

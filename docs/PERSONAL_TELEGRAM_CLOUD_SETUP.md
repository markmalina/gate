# Cloud session foundation — Gate #696

This is a default-off preparation step, not a connected Telegram worker.

## Protected configuration
The cloud runtime reads these server-only names:
- GATE_PERSONAL_TELEGRAM_CLOUD_ENABLED (only literal true enables inspection in Gate status)
- GATE_PERSONAL_TELEGRAM_API_ID
- GATE_PERSONAL_TELEGRAM_API_HASH
- GATE_PERSONAL_TELEGRAM_OWNER_USER_ID
- GATE_PERSONAL_TELEGRAM_SESSION_REF
- GATE_PERSONAL_TELEGRAM_SESSION_CIPHERTEXT
- GATE_PERSONAL_TELEGRAM_ENCRYPTION_KEY

No values belong in repository files, logs, shell arguments, AI messages or fixtures. The encryption key belongs in protected server configuration separately from ciphertext custody. Changing production configuration is a separate protected operation, not an instruction to copy a Windows session.

The ciphertext is Fernet over UTF-8 JSON with schema `gate.personal-telegram.cloud-session/v1`, integer `api_id`, integer `owner_user_id`, opaque `session_ref`, and secret StringSession `session`. The decryptor verifies all three pins before returning session material inside the process. The upcoming protected enrollment surface must create it after verifying the actual account. This slice has no enrollment UI and does not invent or export a session.

## Inspection
`gate-personal-telegram-cloud-check` inspects presence, shape, encryption and binding without importing a Telegram client or using the network. Exit 0 means only that the configured ciphertext can be unsealed for its expected scope. It is not proof of valid Telegram authorization.

`gate-personal-telegram-cloud-check --live-readonly` additionally requires cloud inspection enabled. It connects once, calls only is_user_authorized/get_me and disconnects with bounded timeouts. No dialogs, history, contacts, groups or messages are accessed. It does not log in, send a code, create a session, write artifacts, save a persistent probe receipt or activate a worker.

The returned probe_connected is temporary. runtime_connected stays false because this slice has no persistent runtime. send_enabled/create_group_enabled also stay false after a successful probe. A missing or revoked session stops; no automatic enrollment/retry.

Gate status defaults to its previous unbound contract while cloud inspection is disabled. When explicitly enabled it reports real decryptability, never authorization inferred from presence. API application, session, runtime connection and delivery are distinct states.

## Next
Implement protected owner enrollment and durable custody, then one-session worker/queue and scoped MCP tools according to [the full plan](PERSONAL_TELEGRAM_CLOUD_PLAN.md). Supply no session or API hash to Work to unblock this stage. Runtime activation and live receipts remain separate from tests/merge/deploy.


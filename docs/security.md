# Security

Gate handles communication. Security defaults must be conservative.

## Secret handling

Do not commit secrets.

Forbidden in GitHub:

- BotFather tokens;
- `.env`;
- private session files;
- raw account sessions;
- personal message exports;
- production database credentials;
- API keys.

Allowed in `.env.example`:

- variable names;
- safe default identifiers such as `TELEGRAM_BOT_USERNAME=gate_m_bot`.

## Required `.gitignore` entries

- `.env`;
- `*.session`;
- `*.sqlite`;
- `*.db`;
- `logs/`;
- `*.log`.

## Repository visibility

Repository must stay private while Gate may contain integration details.

## Consent boundary

Gate must not connect third-party accounts or conversations without owner consent.

Client accounts require explicit permission and isolated context.

## Automation boundary

Default outgoing behavior:

- v0: manual / approval;
- automation only after explicit permission;
- every outgoing message logged.

## Auditability

Every outgoing message must record:

- source bot;
- source account;
- target chat;
- target project;
- reply mode;
- approving user;
- final body or secure reference;
- timestamp;
- delivery status.

## Sensitive content policy

Do not store private conversations in README, docs or public examples.

Use synthetic examples only.

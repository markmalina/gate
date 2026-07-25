# LENTA NOW

```yaml
work: Gate #326
execution: gate-lenta-326-20260725T1045Z
base: 8a7ec0ed1d4df6b181d26f39bf6b8ce895264da4
branch: feat/unified-communication-feed
status: implementation_complete_ci_pending
pre_user_mode: true
external_users: 0
production_data_reads: 0
provider_calls: 0
telegram_deliveries: 0
production_mutations: 0
```

## Implemented

- one owner-only date selector: `feed:day:YYYY-MM-DD`;
- unchanged MCP tool count and unchanged `gate:inbox:read` scope;
- chronological projection of Telegram text, stored Telegram voice transcripts and stored
  phone-call transcripts;
- explicit RU/BY source identity, original medium, direction and participants;
- Telegram voice/message deduplication by exact provider identity;
- private semantic input contract instructing Alfa/ChatGPT not to quote source wording by
  default;
- synthetic coverage for selector validation, source identity, ordering, deduplication,
  schema validation and non-mutating tool discovery.

## Not claimed

- production deployment or runtime acceptance;
- a complete 25 July feed before the selector is deployed and called against the owner
  scope;
- live Dropbox call intake configuration;
- semantic persistence inside Gate.

## Next

One exact-head GitHub CI. If green and mergeable, merge, allow the existing automatic
production deploy, verify exact deployed fingerprint through GitHub/Deploy surfaces and
perform one read-only owner feed smoke. No Railway connector is required for repository,
CI or merge work.

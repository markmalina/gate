# Gate #737 — local implementation verification

Execution `gate-rich-737-20261006-155742`, base
`d99cd943749a3c4953c7f07ff2ba0f1126f08f0c`, branch
`agent/737-rich-sprint-1`. This is local implementation evidence, not merge,
Production readiness, account capability, delivery or visual acceptance.

## Implemented

Existing draft/send/MCP/Business transport now supports six strict native Rich
blocks with complete semantic preview, original content/native payload persisted
in existing JSON, scope/version/revision-bound SHA-256 and exact send consent.
Existing reservation/replay/conversation and admission fences remain. Rich
cannot use series/captions or automatically downgrade. Native transport reuses
the common httpx client, sends one blocks-only request, and never retries.

Errors remain safe and delivery-unknown where required. Malformed acknowledgments
and post-success receipt failure preserve no-resend reservations/private evidence.
The durable reservation audit stores original runtime revision/time for replay.
Config defaults off, with source-controlled Production activation honoring an
explicit existing environment false. Staging remains off. No migration/model
access/cross-project/provider action was performed by the implementing β.

## Actual local checks (2026-10-06)

Pinned `constraints/ci.txt` dependencies, Python3.12:

- `ruff check .`: PASS.
- `mypy app`: PASS, 303 source files.
- Targeted native/formatting/transport/sequence/caption/readiness regression:
  **250 collected; 195 passed; 55 PostgreSQL skipped; 0 failures/errors**.
- New rich tests alone: **71 collected; 37 passed; 34 PostgreSQL skipped**.
- Full repository pytest ran and is **not green** locally: 15 failures and
  44 setup errors require mandatory missing DATABASE_URL; other PostgreSQL
  tests skip. No database skip is claimed as acceptance evidence. The isolated
  migration-graph failure was rechecked and likewise fails at mandatory
  DATABASE_URL before running Alembic, rather than a changed migration.
- The pinned unchanged LKG formatting reader (Git blob
  `5ddc5ab6e6459384561af7632d3da050fae806aa`) was actually executed against a
  saved rich snapshot and refused it with `invalid_formatting`. No migration
  is needed; old-runtime JSON consumption remains fail-closed.

Targeted command:

```sh
PYTHONPATH=. python -m pytest tests/test_reply_rich_mcp_transport.py tests/test_reply_rich_send.py tests/test_telegram_formatting.py tests/test_reply_formatting_mcp_transport.py tests/test_reply_formatting_sequences_documents.py tests/test_reply_formatting_send.py tests/test_railway_readiness.py -q -ra
```

Existing CI backend starts PostgreSQL17 and runs the complete suite plus
upgrade/drift/backup-restore/downgrade checks. Hosted exact-head PostgreSQL
results and independent δ remain mandatory before merge/publication.

## Remaining authority-owned acceptance

α owns atomic remote candidate/PR, independent exact-head review, one necessary
CI, merge, current-main Ops release/receipt, fresh health and real connected
Work catalogue refresh/call. Limited own RU↔BY P/D/L/M execution and real
native table/code client proof are pending. Caps, grant expiry, redacted private
bindings and restart-safe live attempt ledger belong to that single execution.

Ops LKG/recovery route is recorded in the
[accepted implementation contract](../specs/ISSUE-737-RICH-SPRINT-1.md).
Only current-main publication is exposed; rollback is a reviewed fix-forward
revert followed by publication with its new source SHA. Historical LKG SHA is
not falsely presented as a directly publishable target. Parent #735 remains
open; no implementation result here closes #737 or records personal acceptance.

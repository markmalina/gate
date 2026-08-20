# AQUB owner-photo archive boundary

This repository slice is default-off. When separately enabled with an explicit
UTC `not_before` cutoff, it may download only a strictly validated photo from
the already bound AQUB owner and group. The webhook receipt and its truthful
`Фото принято.` response remain independent of download success.

Downloaded bytes are due for logical purge seven days from `archived_at`.
The provider-independent purge worker nulls only
`aqub_photo_archives.raw_data`; it retains the
SHA-256 digest, byte count, canonical media type, binding/source references and
audit timestamps.

This is a logical BYTEA purge, not a claim of immediate physical erasure.
PostgreSQL MVCC/TOAST history, WAL and infrastructure backups can retain former
copies under their own policies. The source `TelegramReceipt.payload` also
continues to contain Telegram `file_id`, `file_unique_id`, all photo-size
metadata and an optional caption under the existing receipt-retention policy.
Therefore the product may say only that the separately downloaded byte copy is
retained for seven days; it must not say that the photo or all provider
references have been deleted.

Production rollback disables the download worker but leaves the independent
purge worker enabled, and rolls application code back while leaving the
additive ledger table and revision
`0100_aqub_photo_archive` in place. After 0100 has reached a database, an exact
source rollback to `c6522edfcc3b0aecb12cbc5f04c97044ccf85bbb` is unsafe: that
tree does not know revision 0100, so its `alembic upgrade head` pre-deploy step
cannot validate the database. The safe sequence is:

1. Set `GATE_AQUB_PHOTO_ARCHIVE_ENABLED=false` while keeping
   `GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=true`.
2. Selectively revert archive application behavior while retaining the 0100
   migration file, model metadata needed by `alembic check`, additive table,
   repository purge operation, and purge-only lifecycle until every stored
   BYTEA has been cleared.
3. Deploy and verify ordinary webhook readiness and the existing text/photo
   receipt acknowledgements.

Production rollback never runs an Alembic downgrade, drops archive data, or
reverts to an application tree that cannot resolve the database revision.

The first production deployment must keep both archive flags false. Its
boundary is only the additive migration plus unchanged webhook behavior: no
archive or purge task, downloader, archive runtime database work, or
archive-originated provider call exists while both are disabled. Gate's independent webhook
reconciliation lifecycle is unchanged. A later canary is a separate configuration
change that must first set `GATE_AQUB_PHOTO_ARCHIVE_PURGE_ENABLED=true`, then
enable downloads with an explicit current UTC
`GATE_AQUB_PHOTO_ARCHIVE_NOT_BEFORE` cutoff and reuse the existing AQUB bot
token and process-owned HTTP client. The canary must prove that no pre-cutoff
receipt is discovered, one post-cutoff owner photo archives independently, and
the already committed `Фото принято.` path does not wait for archival.

Download enablement with purge disabled is an invalid fail-closed state: the
download worker stays degraded and constructs no downloader or provider path.
Purge-only mode requires neither the Telegram token nor the cutoff; the purge
task itself makes no HTTP/provider calls.

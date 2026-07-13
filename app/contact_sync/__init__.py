"""Read-only Telegram contact synchronization component."""

from app.contact_sync.models import ContactSnapshotItem, ContactSyncResult
from app.contact_sync.service import ContactSyncService

__all__ = ["ContactSnapshotItem", "ContactSyncResult", "ContactSyncService"]

"""SQLAlchemy adapter for atomic account-scoped contact snapshot imports."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.contact_snapshots import (
    ContactSnapshotCounts,
    ContactSnapshotError,
    ContactSnapshotResult,
    ContactSnapshotWriter,
    ImportContactSnapshotCommand,
)
from app.models.domain import Account, AuditLog, Person, PersonIdentity


class SqlContactSnapshotWriter(ContactSnapshotWriter):
    """Upsert one bounded contact snapshot in a single transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def write(
        self,
        command: ImportContactSnapshotCommand,
    ) -> ContactSnapshotResult:
        try:
            account_result = await self._session.execute(
                select(Account)
                .where(
                    Account.alias == command.account_alias,
                    Account.is_active.is_(True),
                )
                .with_for_update()
            )
            account = account_result.scalar_one_or_none()
            if account is None:
                raise ContactSnapshotError(
                    code="account_not_found",
                    message="Active Gate account not found",
                )

            created = 0
            updated = 0
            unchanged = 0
            for contact in command.contacts:
                identity_result = await self._session.execute(
                    select(PersonIdentity, Person)
                    .join(Person, PersonIdentity.person_id == Person.id)
                    .where(
                        PersonIdentity.account_id == account.id,
                        PersonIdentity.platform == "telegram",
                        PersonIdentity.platform_user_id == contact.telegram_user_id,
                    )
                    .with_for_update()
                )
                row = identity_result.one_or_none()
                if row is None:
                    person = Person(display_name=contact.display_name)
                    self._session.add(person)
                    await self._session.flush()
                    self._session.add(
                        PersonIdentity(
                            person_id=person.id,
                            account_id=account.id,
                            platform="telegram",
                            platform_user_id=contact.telegram_user_id,
                            username=contact.username,
                            display_name=None,
                            is_verified=True,
                            metadata_=_contact_metadata(
                                revision=command.revision,
                                display_name=contact.display_name,
                                username=contact.username,
                            ),
                        )
                    )
                    created += 1
                    continue

                identity, person = row
                metadata = dict(identity.metadata_ or {})
                existing_revision = _optional_text(
                    metadata.get("telegram_contact_revision")
                )
                existing_name = _optional_text(metadata.get("telegram_contact_name"))
                existing_username = _optional_text(
                    metadata.get("telegram_contact_username")
                )
                if existing_revision == command.revision:
                    if (
                        existing_name != contact.display_name
                        or existing_username != contact.username
                    ):
                        raise ContactSnapshotError(
                            code="revision_conflict",
                            message=(
                                "The same contact snapshot revision cannot contain "
                                "different contact data"
                            ),
                        )
                    unchanged += 1
                    continue

                metadata.update(
                    _contact_metadata(
                        revision=command.revision,
                        display_name=contact.display_name,
                        username=contact.username,
                    )
                )
                identity.metadata_ = metadata
                identity.is_verified = True
                if contact.username is not None:
                    identity.username = contact.username
                person.display_name = contact.display_name
                updated += 1

            revision_sha256 = _revision_sha256(command.revision)
            counts = ContactSnapshotCounts(
                received=len(command.contacts),
                created=created,
                updated=updated,
                unchanged=unchanged,
            )
            self._session.add(
                AuditLog(
                    actor="gate-contact-sync",
                    action="telegram_contact_snapshot_imported",
                    account_id=account.id,
                    metadata_={
                        "revision_sha256": revision_sha256,
                        "received": counts.received,
                        "created": counts.created,
                        "updated": counts.updated,
                        "unchanged": counts.unchanged,
                    },
                )
            )
            await self._session.commit()
            return ContactSnapshotResult(
                account_alias=account.alias,
                revision_sha256=revision_sha256,
                counts=counts,
            )
        except Exception:
            await self._session.rollback()
            raise


def _contact_metadata(
    *,
    revision: str,
    display_name: str,
    username: str | None,
) -> dict[str, Any]:
    return {
        "telegram_contact_name": display_name,
        "telegram_contact_username": username,
        "telegram_contact_revision": revision,
        "telegram_contact_source": "telegram_user_session",
        "name_source": "telegram_contact",
    }


def _revision_sha256(revision: str) -> str:
    return hashlib.sha256(revision.encode("utf-8")).hexdigest()


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None

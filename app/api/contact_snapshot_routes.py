"""Protected admin boundary for account-scoped Telegram contact snapshots."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_bearer
from app.api.deps import get_db_session
from app.application.contact_snapshots import (
    ContactSnapshotContact,
    ContactSnapshotError,
    ImportContactSnapshotCommand,
    ImportContactSnapshotService,
)
from app.core.config import Settings, get_settings
from app.infrastructure.contact_snapshots import SqlContactSnapshotWriter

router = APIRouter()


class ContactSnapshotItemRequest(BaseModel):
    """One sanitized Telegram contact accepted by the admin boundary."""

    model_config = ConfigDict(extra="forbid")

    telegram_user_id: str = Field(min_length=1, max_length=255, pattern=r"^[0-9]+$")
    display_name: str = Field(min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)


class ContactSnapshotRequest(BaseModel):
    """One bounded snapshot for a single existing Gate account."""

    model_config = ConfigDict(extra="forbid")

    account_alias: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)
    source: str = Field(default="telegram_user_session", min_length=1, max_length=64)
    source_account_telegram_user_id: str | None = Field(
        default=None,
        max_length=255,
        pattern=r"^[0-9]+$",
    )
    contacts: list[ContactSnapshotItemRequest] = Field(min_length=1, max_length=1000)


class ContactSnapshotCountsResponse(BaseModel):
    """Aggregate counts that reveal no contact data."""

    received: int
    created: int
    updated: int
    unchanged: int


class ContactSnapshotResponse(BaseModel):
    """Sanitized response for a successful atomic import."""

    status: str
    account_alias: str
    revision_sha256: str
    counts: ContactSnapshotCountsResponse


def get_contact_snapshot_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ImportContactSnapshotService:
    """Build the contact snapshot application service for one request."""

    return ImportContactSnapshotService(SqlContactSnapshotWriter(session))


@router.post(
    "/admin/contact-snapshots",
    response_model=ContactSnapshotResponse,
)
async def import_contact_snapshot(
    payload: ContactSnapshotRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[
        ImportContactSnapshotService,
        Depends(get_contact_snapshot_service),
    ],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> ContactSnapshotResponse:
    """Atomically upsert a sanitized contact snapshot without returning contact data."""

    require_admin_bearer(authorization=authorization, settings=settings)
    try:
        result = await service.execute(
            ImportContactSnapshotCommand(
                account_alias=payload.account_alias,
                revision=payload.revision,
                source=payload.source,
                source_account_telegram_user_id=(
                    payload.source_account_telegram_user_id
                ),
                contacts=tuple(
                    ContactSnapshotContact(
                        telegram_user_id=contact.telegram_user_id,
                        display_name=contact.display_name,
                        username=contact.username,
                    )
                    for contact in payload.contacts
                ),
            )
        )
    except ContactSnapshotError as exc:
        error_status = {
            "account_not_found": status.HTTP_404_NOT_FOUND,
            "account_identity_unavailable": status.HTTP_409_CONFLICT,
            "account_identity_mismatch": status.HTTP_409_CONFLICT,
            "revision_conflict": status.HTTP_409_CONFLICT,
        }.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise HTTPException(
            status_code=error_status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return ContactSnapshotResponse(
        status="ok",
        account_alias=result.account_alias,
        revision_sha256=result.revision_sha256,
        counts=ContactSnapshotCountsResponse(
            received=result.counts.received,
            created=result.counts.created,
            updated=result.counts.updated,
            unchanged=result.counts.unchanged,
        ),
    )

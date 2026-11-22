"""Authenticated Dropbox webhook for durable mobile-call intake."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import DropboxCallInboxState
from app.models.watch_voice import WatchEarlyCursor

router = APIRouter(prefix="/dropbox/calls", tags=["dropbox-calls"])


@router.get("/webhook")
async def verify_dropbox_call_webhook(
    challenge: Annotated[str, Query(min_length=1, max_length=256)],
) -> Response:
    """Echo Dropbox's one-time webhook verification challenge."""

    return Response(
        content=challenge,
        media_type="text/plain",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/webhook")
async def receive_dropbox_call_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_dropbox_signature: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """Verify the app-secret signature and durably schedule a delta scan."""

    watch_enabled = settings.gate_watch_webhook_enabled
    if not settings.gate_dropbox_call_intake_enabled and not watch_enabled:
        raise HTTPException(status_code=404, detail="Dropbox call intake is disabled")
    app_secret = settings.dropbox_call_app_secret.get_secret_value()
    account_id = settings.dropbox_call_account_id
    if not app_secret or not account_id:
        raise HTTPException(status_code=503, detail="Dropbox call intake is not configured")

    body = await request.body()
    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if (
        x_dropbox_signature is None
        or not hmac.compare_digest(expected, x_dropbox_signature)
    ):
        raise HTTPException(status_code=401, detail="Invalid Dropbox signature")

    try:
        payload: Any = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Dropbox payload") from None
    changes = payload.get("list_folder") if isinstance(payload, dict) else None
    users = changes.get("accounts") if isinstance(changes, dict) else None
    if not isinstance(users, list) or not all(isinstance(item, str) for item in users):
        raise HTTPException(status_code=400, detail="Invalid Dropbox payload")
    if account_id not in users:
        return {"ok": True}

    now = datetime.now(UTC)
    calls_enabled = settings.gate_dropbox_call_intake_enabled
    if calls_enabled:
        await session.execute(
            insert(DropboxCallInboxState)
            .values(account_id=account_id, webhook_received_at=now)
            .on_conflict_do_update(
                index_elements=[DropboxCallInboxState.account_id],
                set_={
                    "pending": True,
                    "next_attempt_at": None,
                    "webhook_received_at": now,
                    "updated_at": now,
                },
            )
        )
    watch_signaled = False
    if watch_enabled and settings.gate_workspace_id == "mark-personal":
        wake = await session.execute(
            update(WatchEarlyCursor)
            .where(
                WatchEarlyCursor.workspace_key == settings.gate_workspace_id,
                WatchEarlyCursor.account_id == account_id,
            )
            .values(
                wake_generation=WatchEarlyCursor.wake_generation + 1,
                wake_received_at=now,
            )
        )
        watch_signaled = bool(wake.rowcount)  # type: ignore[attr-defined]
    await session.commit()
    if calls_enabled:
        request.app.state.dropbox_call_wake_event.set()
    if watch_signaled:
        request.app.state.watch_wake_event.set()
    return {"ok": True}

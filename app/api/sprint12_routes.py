"""Sprint 12 direct web editor links for Telegram draft cards."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import sprint10_routes as sprint10
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)


@router.get("/admin/drafts/{draft_ref}/web-edit")
async def open_draft_web_editor(
    draft_ref: str,
    token: Annotated[str, Query()],
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> RedirectResponse:
    """Resolve a short draft reference and redirect to the protected full editor URL."""

    await sprint10._require_valid_draft_token(
        draft_ref=draft_ref,
        token=token,
        settings=settings,
    )
    draft, _task = await sprint10._load_single_draft_for_edit(
        session=session,
        draft_ref=draft_ref,
    )
    full_ref = str(draft.id)
    full_token = sprint10._draft_edit_token(draft_ref=full_ref, settings=settings)
    location = f"/admin/drafts/{full_ref}/edit?token={full_token}"
    return RedirectResponse(url=location, status_code=303)

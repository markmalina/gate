"""Sprint 12 direct draft editor link tests."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.api import sprint12_routes as sprint12
from app.core.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_short_draft_link_redirects_to_full_protected_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    settings = Settings(ADMIN_BEARER_TOKEN="secret")
    validations: list[dict[str, Any]] = []

    async def fake_require_valid_draft_token(**kwargs: Any) -> None:
        validations.append(kwargs)

    async def fake_load_single_draft_for_edit(**kwargs: Any) -> tuple[Any, Any]:
        assert kwargs["draft_ref"] == "12345678"
        return SimpleNamespace(id=draft_id), SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        sprint12.sprint10,
        "_require_valid_draft_token",
        fake_require_valid_draft_token,
    )
    monkeypatch.setattr(
        sprint12.sprint10,
        "_load_single_draft_for_edit",
        fake_load_single_draft_for_edit,
    )

    response = await sprint12.open_draft_web_editor(
        draft_ref="12345678",
        token="short-token",
        settings=settings,
        session=object(),
    )

    full_token = sprint12.sprint10._draft_edit_token(
        draft_ref=str(draft_id),
        settings=settings,
    )
    assert validations == [
        {
            "draft_ref": "12345678",
            "token": "short-token",
            "settings": settings,
        }
    ]
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/drafts/{draft_id}/edit?token={full_token}"
    )

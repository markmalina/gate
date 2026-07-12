from uuid import UUID

import pytest
from app.application.reply_drafts import (
    ReplyDraftError,
    SavedReplyDraft,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.core.mcp_security import AccessScope, Principal

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")


class CapturingWriter:
    def __init__(self) -> None:
        self.commands: list[SaveReplyDraftCommand] = []

    async def save(self, command: SaveReplyDraftCommand) -> SavedReplyDraft:
        self.commands.append(command)
        return SavedReplyDraft(
            draft_id=UUID("44444444-4444-4444-4444-444444444444"),
            task_id=command.task_id,
            account_id=ACCOUNT_ID,
            project_id=PROJECT_ID,
            account_alias="telegram_mark_ru",
            account_label="RU",
            recipient_chat_id="12345",
            status="generated",
            text=command.text,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _principal(*, scopes: frozenset[str]) -> Principal:
    return Principal(
        subject="user-123",
        auth_method="oauth2",
        scopes=scopes,
        access_scope=AccessScope(
            workspace_id="mark-personal",
            account_ids=frozenset({ACCOUNT_ID}),
            project_ids=frozenset({PROJECT_ID}),
        ),
    )


@pytest.mark.anyio
async def test_save_reply_draft_normalizes_text_and_never_sends() -> None:
    writer = CapturingWriter()
    service = SaveReplyDraftService(writer)

    saved = await service.execute(
        SaveReplyDraftCommand(
            principal=_principal(scopes=frozenset({"gate:drafts:write"})),
            task_id=TASK_ID,
            text="  Привет! Рад знакомству.  ",
        )
    )

    assert saved.text == "Привет! Рад знакомству."
    assert writer.commands[0].text == saved.text


@pytest.mark.anyio
async def test_save_reply_draft_requires_write_scope() -> None:
    writer = CapturingWriter()
    service = SaveReplyDraftService(writer)

    with pytest.raises(ReplyDraftError) as exc_info:
        await service.execute(
            SaveReplyDraftCommand(
                principal=_principal(scopes=frozenset({"gate:inbox:read"})),
                task_id=TASK_ID,
                text="Привет!",
            )
        )

    assert exc_info.value.code == "insufficient_scope"
    assert writer.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["", "   ", "x" * 4097])
async def test_save_reply_draft_rejects_invalid_text(text: str) -> None:
    writer = CapturingWriter()
    service = SaveReplyDraftService(writer)

    with pytest.raises(ReplyDraftError) as exc_info:
        await service.execute(
            SaveReplyDraftCommand(
                principal=_principal(scopes=frozenset({"gate:drafts:write"})),
                task_id=TASK_ID,
                text=text,
            )
        )

    assert exc_info.value.code == "invalid_text"
    assert writer.commands == []

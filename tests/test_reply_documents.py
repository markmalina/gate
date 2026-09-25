import base64
import hashlib
import io
import zipfile
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.application.reply_documents import (
    PreparedReplyDocument,
    PrepareReplyDocumentCommand,
    PrepareReplyDocumentService,
)
from app.application.reply_drafts import ReplyDraftError
from app.application.telegram_attachments import serialize_telegram_attachments
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sends import _validate_document_provider_result

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
DRAFT_ID = UUID("44444444-4444-4444-4444-444444444444")


def _real_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("gate-test.txt", "Gate #700")
    return output.getvalue()


REAL_ZIP = _real_zip()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class CapturingWriter:
    def __init__(self) -> None:
        self.received: list[bytes] = []

    async def save(
        self, command: PrepareReplyDocumentCommand, data: bytes
    ) -> PreparedReplyDocument:
        self.received.append(data)
        return PreparedReplyDocument(
            draft_id=DRAFT_ID,
            task_id=command.task_id,
            account_label="RU",
            recipient_chat_id="123",
            filename=command.filename,
            media_type=command.media_type,
            size_bytes=len(data),
            sha256=command.expected_sha256,
            caption=command.caption,
        )


def _command(
    *,
    filename: str = "bundle.zip",
    media_type: str = "application/zip",
    data: bytes = REAL_ZIP,
    expected_sha256: str | None = None,
    scope: str = "gate:drafts:write",
) -> PrepareReplyDocumentCommand:
    return PrepareReplyDocumentCommand(
        principal=Principal(
            subject="user-123",
            auth_method="oauth2",
            scopes=frozenset({scope}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({UUID("22222222-2222-2222-2222-222222222222")}),
                project_ids=frozenset({UUID("33333333-3333-3333-3333-333333333333")}),
            ),
        ),
        task_id=TASK_ID,
        filename=filename,
        media_type=media_type,
        content_base64=base64.b64encode(data).decode("ascii"),
        expected_sha256=expected_sha256 or hashlib.sha256(data).hexdigest(),
    )


@pytest.mark.anyio
async def test_selected_document_is_staged_without_sending_or_returning_bytes() -> None:
    writer = CapturingWriter()
    prepared = await PrepareReplyDocumentService(writer).execute(_command())
    assert writer.received == [REAL_ZIP]
    assert prepared.size_bytes == len(writer.received[0])
    assert prepared.sha256 == hashlib.sha256(writer.received[0]).hexdigest()
    assert not hasattr(prepared, "data")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("command", "expected_code"),
    [
        (_command(filename="../bundle.zip"), "invalid_document"),
        (_command(filename="wrong.pdf"), "invalid_document"),
        (_command(data=b"PK\\x03\\x04fake escaped signature"), "invalid_document"),
        (_command(expected_sha256="0" * 64), "document_changed"),
        (_command(scope="gate:inbox:read"), "insufficient_scope"),
    ],
)
async def test_invalid_artifact_never_reaches_writer(
    command: PrepareReplyDocumentCommand, expected_code: str
) -> None:
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError) as error:
        await PrepareReplyDocumentService(writer).execute(command)
    assert error.value.code == expected_code
    assert writer.received == []


def test_provider_acknowledgement_must_match_scoped_document() -> None:
    context = SimpleNamespace(
        target_chat_id="123",
        document_filename="bundle.zip",
        document_media_type="application/zip",
        document_size_bytes=19,
        document_caption="Ready",
    )
    result = {
        "result": {
            "chat": {"id": 123},
            "business_connection_id": "business",
            "caption": "Ready",
            "document": {
                "file_id": "opaque-file-id",
                "file_unique_id": "opaque-file-unique-id",
                "file_name": "bundle.zip",
                "mime_type": "application/zip",
                "file_size": 19,
            },
        }
    }
    _validate_document_provider_result(result, context, "business")
    attachment = serialize_telegram_attachments(
        result, task_id=TASK_ID, message_id=DRAFT_ID
    )
    assert attachment[0]["filename"] == "bundle.zip"
    assert attachment[0]["mime_type"] == "application/zip"
    assert "file_id" not in attachment[0]
    result["result"]["document"]["file_name"] = "different.zip"
    with pytest.raises(RuntimeError):
        _validate_document_provider_result(result, context, "business")

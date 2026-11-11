"""Work structured uploads, actual image blocks and exact media approval."""

import base64
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api import mcp_media, mcp_routes
from app.application.reply_drafts import ReplyDraftError
from app.application.reply_media import PreparedMediaItem, PreparedReplyMedia
from app.application.reply_sends import ReplySendResult
from app.models.domain import ReplyDraft
from app.models.reply_media import ReplyMediaPackage
from jsonschema import Draft202012Validator

from test_reply_formatting_mcp_transport import _principal, _settings
from test_reply_media_assets import image_bytes, media_input


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_work_upload_returns_normalized_actual_images_full_snapshot_and_hash(monkeypatch):
    task_id, draft_id, package_id = uuid4(), uuid4(), uuid4()
    deadline = datetime.now(UTC) + timedelta(hours=47)
    commands, stored = [], []
    owner = _principal()
    snapshot = {
        "mode": "album",
        "account_label": "RU",
        "scope": {"recipient_chat_id": "synthetic-chat"},
        "rich": None,
    }

    class Writer:
        async def save(self, command, items, input_sha256):
            commands.append(command)
            for ordinal, item in enumerate(items, 1):
                metadata = asdict(item)
                data = metadata.pop("data")
                prepared = PreparedMediaItem(**metadata, media_ref=str(uuid4()), ordinal=ordinal)
                stored.append((prepared, data))
            return PreparedReplyMedia(
                draft_id=draft_id,
                package_id=package_id,
                task_id=task_id,
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                mode="album",
                package_sha256="a" * 64,
                expected_message_count=2,
                expires_at=deadline,
                items=tuple(item for item, _ in stored),
                rich_content=None,
                execution_id=command.execution_id,
                execution_expires_at=deadline,
            )

    async def read(_session, principal, requested):
        assert principal == owner and requested == package_id
        return tuple(stored)

    async def get(model, key):
        if model is ReplyMediaPackage:
            assert key == package_id
            return SimpleNamespace(
                id=package_id,
                reply_draft_id=draft_id,
                snapshot_json=snapshot,
                package_sha256="a" * 64,
                expected_message_count=2,
                expires_at=deadline,
            )
        assert model is ReplyDraft and key == draft_id
        return SimpleNamespace(reply_task_id=task_id)

    session = SimpleNamespace(commit=AsyncMock(), get=AsyncMock(side_effect=get))
    monkeypatch.setattr(mcp_media, "SqlReplyMediaWriter", lambda _: Writer())
    monkeypatch.setattr(mcp_media, "read_reply_media_preview", read)
    uploads = [asdict(media_input(image_bytes(), caption=f"Кадр {i}")) for i in (1, 2)]
    result = await mcp_media.media_tool_result(
        request_id=1,
        arguments={
            "task_id": str(task_id),
            "expected_context_snapshot": "dialog-v1:" + "0" * 64,
            "mode": "album",
            "items": uploads,
            "staging_key": "work-upload",
            "execution_id": "media-test",
            "execution_expires_at": deadline.isoformat(),
            "follow_up": True,
        },
        principal=owner,
        session=session,
        prepare=True,
    )
    assert not result["result"]["isError"]
    content = result["result"]["structuredContent"]
    Draft202012Validator(mcp_media.MEDIA_OUTPUT_SCHEMA).validate(content)
    assert content["snapshot"] == snapshot
    assert [item["caption"] for item in content["items"]] == ["Кадр 1", "Кадр 2"]
    assert content["confirmation"] == {
        "expected_media_package_sha256": "a" * 64,
        "expected_account": "RU",
        "expected_recipient_chat_id": "synthetic-chat",
    }
    assert content["expected_message_count"] == 2
    blocks = result["result"]["content"][1:]
    assert len(blocks) == 2 and all(block["type"] == "image" for block in blocks)
    assert [base64.b64decode(block["data"]) for block in blocks] == [data for _, data in stored]
    assert commands[0].execution_expires_at == deadline and commands[0].follow_up
    session.commit.assert_awaited_once()
    preview = await mcp_media.media_tool_result(
        request_id=2,
        arguments={"package_id": str(package_id)},
        principal=owner,
        session=session,
        prepare=False,
    )
    assert preview["result"]["content"][1:] == blocks
    assert len(commands) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra",
    [{"url": "https://example.test/a.png"}, {"path": "/private/image.png"}, {"package_id": "bad"}],
)
async def test_upload_rejects_unknown_resolver_fields_before_sql(extra):
    session = SimpleNamespace(commit=AsyncMock(), get=AsyncMock())
    result = await mcp_media.media_tool_result(
        request_id=1,
        arguments=extra,
        principal=_principal(),
        session=session,
        prepare=True,
    )
    assert result["result"]["structuredContent"] == {"error": "invalid_arguments"}
    session.commit.assert_not_awaited()
    session.get.assert_not_awaited()


@pytest.mark.anyio
async def test_preview_scope_denial_never_fetches_package_or_exposes_images(monkeypatch):
    async def denied(*_):
        raise ReplyDraftError(code="media_not_found", message="Media package not found in scope")

    monkeypatch.setattr(mcp_media, "read_reply_media_preview", denied)
    session = SimpleNamespace(get=AsyncMock())
    result = await mcp_media.media_tool_result(
        request_id=1,
        arguments={"package_id": str(uuid4())},
        principal=_principal(),
        session=session,
        prepare=False,
    )
    assert result["result"]["isError"]
    assert all(block["type"] == "text" for block in result["result"]["content"])
    session.get.assert_not_awaited()


@pytest.mark.anyio
async def test_approved_send_preserves_media_hash_execution_and_full_receipt(monkeypatch):
    commands = []
    receipt = {
        "mode": "album",
        "items": [{"ordinal": 1, "message_id": "11"}, {"ordinal": 2, "message_id": "12"}],
    }

    class Sender:
        async def send(self, command):
            commands.append(command)
            return ReplySendResult(
                attempt_id=uuid4(),
                draft_id=command.draft_id,
                task_id=uuid4(),
                account_id=uuid4(),
                project_id=uuid4(),
                account_alias="ru",
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                status="sent",
                sent=True,
                idempotent_replay=False,
                delivery_mode="album",
                draft_revision=1,
                runtime_revision="synthetic-runtime",
                reservation_time=datetime.now(UTC).isoformat(),
                media=receipt,
            )

    monkeypatch.setattr(mcp_routes, "SqlTelegramReplySender", lambda **_: Sender())
    result = await mcp_routes._send_approved_reply_tool_result(
        request_id=1,
        arguments={
            "draft_id": str(uuid4()),
            "confirmed": True,
            "expected_account": "RU",
            "expected_recipient_chat_id": "synthetic-chat",
            "expected_media_package_sha256": "a" * 64,
            "execution_id": "media-test",
        },
        principal=_principal(),
        session=SimpleNamespace(),
        telegram=SimpleNamespace(),
        settings=_settings(),
    )
    assert commands[0].expected_media_package_sha256 == "a" * 64
    assert commands[0].execution_id == "media-test"
    content = result["result"]["structuredContent"]
    assert content["media"] == receipt and content["delivery_mode"] == "album"
    Draft202012Validator(mcp_routes.MCP_TOOL_OUTPUT_SCHEMAS["gate_send_approved_reply"]).validate(
        content
    )

"""Native media wire shapes and full durable cardinality/replay checks."""

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.api import mcp_routes
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.application.reply_media import PrepareReplyMediaCommand, PrepareReplyMediaService
from app.application.reply_sends import ReplySendError
from app.application.telegram_formatting import FormattingError
from app.application.telegram_rich_media import compile_rich_media, validate_rich_media_attachments
from app.core.config import get_settings
from app.infrastructure.reply_media import SqlReplyMediaWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.main import create_app
from app.models.domain import Conversation, Message, ReplyDraft, ReplyTask, SendAttempt
from app.models.reply_media import ReplyMediaExecutionBudget, ReplyMediaItem
from app.services.telegram import TelegramApiError, TelegramClient
from sqlalchemy import func, select

from test_reply_media_assets import image_bytes, media_input
from test_reply_sends_postgres import (
    _command,
    _live_connection,
    _postgres_sessions,
    _seed_reply,
    _settings,
)

TEST_EXECUTION_DEADLINE = datetime.now(UTC) + timedelta(hours=47)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def native_photo() -> list[dict[str, Any]]:
    return [
        {"file_id": "synthetic-file", "file_unique_id": "unique-file", "width": 96, "height": 72}
    ]


def source(mode: str, refs: list[tuple[str, str]]) -> dict[str, Any]:
    photos = [
        {
            "type": "photo",
            "media_ref": ref,
            "sha256": digest,
            "version": 1,
            "caption": {"text": ["Фото 💚 ", {"type": "bold", "text": str(index)}]},
        }
        for index, (ref, digest) in enumerate(refs, 1)
    ]
    media_block = (
        photos[0]
        if mode == "rich_photo"
        else {
            "type": mode.removeprefix("rich_"),
            "photos": photos,
            "caption": {"text": "Общая подпись", "credit": "Марк"},
        }
    )
    return {
        "schema_version": "gate.rich.v2",
        "blocks": [
            {"type": "heading", "size": 1, "text": "Готовые изображения"},
            media_block,
            {"type": "pre", "text": "x = 1\n\n    y = 2\n", "language": "python"},
        ],
    }


@pytest.mark.parametrize("mode", ["rich_photo", "rich_slideshow", "rich_collage"])
def test_compiler_caption_native_blocks_order_and_input_immutable(mode: str) -> None:
    n = 1 if mode == "rich_photo" else 3
    metadata = [{"media_ref": f"opaque-{i}", "output_sha256": str(i) * 64} for i in range(1, n + 1)]
    content = source(mode, [(item["media_ref"], item["output_sha256"]) for item in metadata])
    original = deepcopy(content)
    result = compile_rich_media(content, metadata)
    assert content == original
    block = result["blocks"][1]
    photos = [block] if mode == "rich_photo" else block["blocks"]
    assert [p["photo"]["media"] for p in photos] == [f"attach://photo_{i}" for i in range(1, n + 1)]
    assert all(set(p["photo"]) == {"type", "media"} for p in photos)
    assert all("caption" in p and p["caption"]["text"][0] == "Фото 💚 " for p in photos)
    if n > 1:
        assert block["caption"] == {"text": "Общая подпись", "credit": "Марк"}
    validate_rich_media_attachments(result["blocks"], {f"photo_{i}" for i in range(1, n + 1)})
    content["blocks"][0]["text"] = "mutated after preview"
    assert result["source_content"] == original
    assert result["blocks"][2]["text"] == "x = 1\n\n    y = 2\n"


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "hash", "url", "nested", "unknown", "version"]
)
def test_media_source_fail_closed(mutation: str) -> None:
    items = [
        {"media_ref": "a", "output_sha256": "a" * 64},
        {"media_ref": "b", "output_sha256": "b" * 64},
    ]
    content = source("rich_slideshow", [(i["media_ref"], i["output_sha256"]) for i in items])
    photos = content["blocks"][1]["photos"]
    if mutation == "missing":
        photos.pop()
    elif mutation == "duplicate":
        photos[1] = deepcopy(photos[0])
    elif mutation == "hash":
        photos[0]["sha256"] = "c" * 64
    elif mutation == "url":
        photos[0]["media_ref"] = "https://example.test/private.jpg"
    elif mutation == "nested":
        photos[0] = {"type": "slideshow", "photos": photos}
    elif mutation == "unknown":
        photos[0]["crop"] = True
    else:
        photos[0]["version"] = 2
    with pytest.raises((FormattingError, ValueError, RecursionError)):
        compile_rich_media(content, items)


def parts(request: httpx.Request) -> dict[str, Any]:
    parsed = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + request.content
    )
    return {
        part.get_param("name", header="content-disposition"): part for part in parsed.iter_parts()
    }


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["photo", "album", "rich_photo", "rich_slideshow", "rich_collage"])
async def test_wire_one_multipart_post_correct_caption_and_attachments(mode: str) -> None:
    calls = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"ok": True, "result": [] if mode == "album" else {"message_id": 1}}
        )

    data = image_bytes()
    n = 1 if mode in {"photo", "rich_photo"} else 3
    items = [
        {
            "filename": f"photo-{i}.png",
            "media_type": "image/png",
            "data": data,
            "caption": f"Фото 💚 {i}",
            "caption_entities": [],
        }
        for i in range(1, n + 1)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = TelegramClient("synthetic-credential", http_client=http)
        if mode == "photo":
            await client.send_business_photo(chat_id="11", business_connection_id="bc", **items[0])
        elif mode == "album":
            await client.send_business_album(chat_id="11", business_connection_id="bc", items=items)
        else:
            metadata = [
                {"media_ref": str(i), "output_sha256": str(i) * 64} for i in range(1, n + 1)
            ]
            compiled = compile_rich_media(
                source(mode, [(i["media_ref"], i["output_sha256"]) for i in metadata]), metadata
            )
            await client.send_business_rich_media(
                chat_id="11", business_connection_id="bc", items=items, blocks=compiled["blocks"]
            )
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path.endswith(
        {"photo": "/sendPhoto", "album": "/sendMediaGroup"}.get(mode, "/sendRichMessage")
    )
    fields = parts(request)
    assert fields["chat_id"].get_content() == "11"
    assert fields["business_connection_id"].get_content() == "bc"
    for i in range(1, n + 1):
        assert fields[f"photo_{i}"].get_payload(decode=True) == data
    if mode == "album":
        assert [
            m["caption"]
            for m in json.loads(fields["media"].get_payload(decode=True).decode("utf-8"))
        ] == [i["caption"] for i in items]
    elif mode.startswith("rich_"):
        rich = json.loads(fields["rich_message"].get_payload(decode=True).decode("utf-8"))
        assert (
            set(rich) == {"blocks", "skip_entity_detection"}
            and rich["skip_entity_detection"] is True
        )
        assert "media" not in rich and "parse_mode" not in fields


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "caption"), [("photo", ""), ("photo", "Кадр 1"), ("album", "")])
async def test_mcp_upload_preview_confirm_send_and_replay_over_real_http_routes(
    mode: str, caption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    count = 1 if mode == "photo" else 4
    fixtures = Path(__file__).parent / "fixtures" / "gate_media_sprint2"
    uploads = [
        replace(
            media_input((fixtures / f"image-{i}.png").read_bytes(), caption=caption),
            filename=f"image-{i}.png",
        )
        for i in range(1, count + 1)
    ]
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            conversation = await session.get(Conversation, seed.conversation_id)
            assert conversation is not None
            conversation.title = "Synthetic recipient"
            await session.commit()
        principal = replace(
            _command(seed, seed.draft_ids[0]).principal,
            scopes=frozenset({"gate:drafts:write", "gate:inbox:read", "gate:messages:send"}),
        )
        provider_posts = []
        preview_hashes = []

        async def authenticate(**kwargs):
            assert kwargs["required_scope"] in principal.scopes
            return principal

        async def session_dependency():
            async with sessions() as session:
                yield session

        async def telegram_api(request):
            if request.url.path.endswith("/getBusinessConnection"):
                return httpx.Response(200, json=_live_connection(seed.business_connection_id))
            provider_posts.append(request)
            fields = parts(request)
            assert fields["chat_id"].get_content() == seed.recipient_chat_id
            assert fields["business_connection_id"].get_content() == seed.business_connection_id
            for i, digest in enumerate(preview_hashes, 1):
                image = fields[f"photo_{i}"].get_payload(decode=True)
                assert hashlib.sha256(image).hexdigest() == digest
            if mode == "photo":
                assert request.url.path.endswith("/sendPhoto")
                assert ("caption" in fields) == bool(caption)
                if caption:
                    assert fields["caption"].get_payload(decode=True).decode("utf-8") == caption
            else:
                assert request.url.path.endswith("/sendMediaGroup")
                media = json.loads(fields["media"].get_payload(decode=True))
                assert media == [
                    {"type": "photo", "media": f"attach://photo_{i}"} for i in range(1, count + 1)
                ]
            messages = [
                {
                    "message_id": 742 + i,
                    "chat": {"id": int(seed.recipient_chat_id)},
                    "business_connection_id": seed.business_connection_id,
                    "photo": native_photo(),
                    **({"caption": caption} if caption else {}),
                    **({"media_group_id": "synthetic-album"} if mode == "album" else {}),
                }
                for i in range(count)
            ]
            return httpx.Response(
                200, json={"ok": True, "result": messages if mode == "album" else messages[0]}
            )

        monkeypatch.setattr(mcp_routes, "_require_mcp_principal", authenticate)
        settings = _settings().model_copy(update={"gate_mcp_enabled": True})
        app = create_app()
        app.dependency_overrides[get_db_session] = session_dependency
        app.dependency_overrides[get_settings] = lambda: settings
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(telegram_api)) as provider,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://gate.example"
            ) as client,
        ):
            telegram = TelegramClient("synthetic-credential", http_client=provider)
            app.dependency_overrides[get_telegram_client] = lambda: telegram

            async def call(name, arguments):
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                response.raise_for_status()
                return response.json()["result"]

            read = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert read["structuredContent"]["context_ready"]
            upload = await call(
                "gate_prepare_reply_media",
                {
                    "task_id": str(seed.task_id),
                    "mode": mode,
                    "expected_context_snapshot": read["structuredContent"]["context_snapshot"],
                    "staging_key": "work-upload",
                    "execution_id": "work-test",
                    "execution_expires_at": TEST_EXECUTION_DEADLINE.isoformat(),
                    "items": [asdict(item) for item in uploads],
                },
            )
            assert not upload["isError"], upload
            snapshot = upload["structuredContent"]
            preview = await call("gate_preview_reply_media", {"package_id": snapshot["package_id"]})
            assert preview == upload
            assert snapshot["confirmation"]["expected_account"] == "RU"
            assert snapshot["confirmation"]["expected_recipient_chat_id"] == seed.recipient_chat_id
            assert snapshot["snapshot"]["recipient_label"] == "Synthetic recipient"
            assert [item["filename"] for item in snapshot["items"]] == [
                item.filename for item in uploads
            ]
            assert [item["caption"] for item in snapshot["items"]] == [caption] * count
            preview_hashes.extend(
                hashlib.sha256(base64.b64decode(block["data"], validate=True)).hexdigest()
                for block in preview["content"]
                if block["type"] == "image"
            )
            assert preview_hashes == [item["output_sha256"] for item in snapshot["items"]]

            approval = {
                **snapshot["confirmation"],
                "draft_id": snapshot["draft_id"],
                "confirmed": True,
                "execution_id": "work-test",
            }
            rejected = await call(
                "gate_send_approved_reply", {**approval, "expected_media_package_sha256": "0" * 64}
            )
            assert rejected["isError"] and provider_posts == []
            first = (await call("gate_send_approved_reply", approval))["structuredContent"]
            replay = (await call("gate_send_approved_reply", approval))["structuredContent"]
            assert first["sent"] and replay["idempotent_replay"], (first, replay)
            assert first["attempt_id"] == replay["attempt_id"] and first["media"] == replay["media"]
            assert first["media"]["provider_message_ids"] == [str(742 + i) for i in range(count)]
            assert len(provider_posts) == 1


class MediaTelegram:
    def __init__(self, outcome: str = "sent", *, blocked: bool = False) -> None:
        self.outcome, self.blocked = outcome, blocked
        self.calls: list[dict[str, Any]] = []
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.probes: list[str] = []

    async def get_business_connection(self, *, business_connection_id: str) -> dict[str, Any]:
        self.probes.append(business_connection_id)
        result = _live_connection(business_connection_id)
        if self.outcome == "rights":
            result["result"]["rights"] = {"can_reply": False}
        return result

    async def send_business_photo(self, **kwargs: Any) -> dict[str, Any]:
        return await self._send("photo", kwargs)

    async def send_business_album(self, **kwargs: Any) -> dict[str, Any]:
        return await self._send("album", kwargs)

    async def send_business_rich_media(self, **kwargs: Any) -> dict[str, Any]:
        return await self._send("rich", kwargs)

    async def send_plain_message(self, **kwargs: Any) -> dict[str, Any]:
        return await self._send("plain", kwargs)

    async def _send(self, mode: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"mode": mode, **kwargs})
        self.started.set()
        if self.blocked:
            await self.release.wait()
        if self.outcome == "timeout":
            raise httpx.ReadTimeout("synthetic ambiguous")
        if self.outcome == "400":
            raise TelegramApiError(
                error_code=400, http_status=400, description="private payload not logged"
            )
        count = len(kwargs["items"]) if mode == "album" else 1
        messages = [
            {
                "message_id": 742 + 100 * (len(self.calls) - 1) + i,
                "chat": {"id": int(kwargs["chat_id"])},
                "business_connection_id": kwargs["business_connection_id"],
            }
            for i in range(count)
        ]
        for i, message in enumerate(messages):
            if mode in {"photo", "album"}:
                item = kwargs if mode == "photo" else kwargs["items"][i]
                message.update(
                    {
                        "photo": native_photo(),
                        "caption": item["caption"],
                        "caption_entities": item["caption_entities"],
                    }
                )
                if mode == "album":
                    message["media_group_id"] = "album-group"
            elif mode == "rich":

                def native(block: dict[str, Any]) -> dict[str, Any]:
                    output = deepcopy(block)
                    if output["type"] == "photo":
                        output["photo"] = native_photo()
                    elif output["type"] in {"slideshow", "collage"}:
                        output["blocks"] = [native(b) for b in output["blocks"]]
                    return output

                message["rich_message"] = {"blocks": [native(b) for b in kwargs["blocks"]]}
            else:
                message["text"] = kwargs["text"]
        if self.outcome == "short":
            messages.pop(1)
        elif self.outcome == "duplicate":
            messages[1]["message_id"] = messages[0]["message_id"]
        elif self.outcome == "reorder":
            messages.reverse()
        elif self.outcome == "wrong_caption":
            messages[0]["caption"] = "unexpected"
        elif self.outcome == "wrong_chat":
            messages[0]["chat"]["id"] += 1
        elif self.outcome == "wrong_connection":
            messages[0]["business_connection_id"] = "wrong"
        elif self.outcome == "wrong_group":
            messages[1]["media_group_id"] = "another-group"
        return {"ok": True, "result": messages if mode == "album" else messages[0]}


async def prepared(
    sessions: Any, seed: Any, mode: str = "album", *, blank_captions: bool = False
) -> tuple[Any, Any]:
    send = _command(seed, seed.draft_ids[0])
    send = replace(
        send,
        principal=replace(
            send.principal, scopes=frozenset({"gate:drafts:write", "gate:messages:send"})
        ),
    )
    n = 1 if mode in {"photo", "rich_photo"} else 3
    items = tuple(
        media_input(caption=f"Фото 💚 {i}", caption_format="markdown")
        if not blank_captions and not mode.startswith("rich_")
        else media_input()
        for i in range(1, n + 1)
    )
    content = (
        source(mode, [(f"item:{i}", item.expected_sha256) for i, item in enumerate(items, 1)])
        if mode.startswith("rich_")
        else None
    )
    async with sessions() as session:
        package = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(
            PrepareReplyMediaCommand(
                principal=send.principal,
                task_id=seed.task_id,
                expected_context_snapshot=seed.context_snapshot,
                mode=mode,
                items=items,
                staging_key=f"fixture-{mode}",
                execution_id="media-test-execution",
                execution_expires_at=TEST_EXECUTION_DEADLINE,
                rich_content=content,
            )
        )
        await session.commit()
    send = replace(
        send,
        draft_id=package.draft_id,
        expected_media_package_sha256=package.package_sha256,
        execution_id="media-test-execution",
    )
    return package, send


async def deliver(
    sessions: Any,
    telegram: MediaTelegram,
    command: Any,
    sender_type: Any = SqlTelegramReplySender,
    *,
    settings: Any = None,
) -> Any:
    async with sessions() as session:
        return await sender_type(
            session=session,
            telegram=telegram,
            settings=settings
            or _settings().model_copy(
                update={
                    "gate_rich_send_enabled": True,
                    "gate_rich_media_send_enabled": True,
                    "deployment_sha": "synthetic-runtime",
                }
            ),
        ).send(command)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["photo", "album", "rich_photo", "rich_slideshow", "rich_collage"])
async def test_durable_media_modes_restarted_replay_exact_n_outward_no_tasks(mode: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        package, command = await prepared(sessions, seed, mode)
        telegram = MediaTelegram()
        sent = await deliver(sessions, telegram, command)
        replay = await deliver(sessions, telegram, command)
        assert sent.sent and sent.status == "sent" and sent.media
        assert replay.sent and replay.idempotent_replay and replay.media == sent.media
        assert sent.draft_revision == replay.draft_revision == 1
        assert sent.runtime_revision == replay.runtime_revision == "synthetic-runtime"
        assert sent.reservation_time == replay.reservation_time
        assert len(telegram.calls) == 1
        async with sessions() as session:
            outward = list(
                (
                    await session.scalars(
                        select(Message).where(Message.source == "gate-approved-media")
                    )
                ).all()
            )
            assert len(outward) == package.expected_message_count
            assert all(
                m.direction == "outbound"
                and m.business_connection_id == seed.business_connection_id
                for m in outward
            )
            assert await session.scalar(select(func.count()).select_from(ReplyTask)) == 1
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert (
                budget.writes_reserved == 1
                and budget.messages_reserved == package.expected_message_count
            )
            assert sent.media["confirmed_message_count"] == package.expected_message_count
            assert sent.media["confirmed_item_count"] == len(package.items)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        "short",
        "duplicate",
        "reorder",
        "wrong_caption",
        "wrong_chat",
        "wrong_connection",
        "wrong_group",
        "timeout",
        "400",
    ],
)
async def test_album_inconsistent_or_ambiguous_receipt_never_resends(outcome: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed, blank_captions=outcome == "short")
        telegram = MediaTelegram(outcome)
        first = await deliver(sessions, telegram, command)
        second = await deliver(sessions, telegram, command)
        assert not first.sent and second.idempotent_replay and not second.sent
        assert first.status == ("failed" if outcome == "400" else "unknown")
        assert second.status == first.status and len(telegram.calls) == 1
        assert first.media == second.media
        if outcome == "short":
            assert first.media["provider_message_ids"] == ["742", "744"]
            assert first.media["confirmed_message_count"] == 2
            assert first.media["confirmed_item_count"] == 0
            assert first.media["unknown_item_count"] == 3
        if outcome == "400":
            assert (
                first.error_detail == "telegram_api_400" and first.media["unknown_item_count"] == 0
            )
        async with sessions() as session:
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.writes_reserved == 1 and budget.messages_reserved == 3
            assert await session.scalar(select(func.count()).select_from(ReplyTask)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["sent", "timeout"])
async def test_media_replay_keeps_original_receipt_after_business_reconnect(outcome: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        telegram = MediaTelegram(outcome)
        first = await deliver(sessions, telegram, command)
        async with sessions() as session:
            conversation = await session.get(Conversation, seed.conversation_id)
            assert conversation is not None
            conversation.business_connection_id = "synthetic-reconnected"
            await session.commit()

        replay = await deliver(
            sessions,
            telegram,
            command,
            settings=_settings().model_copy(update={"gate_reply_media_send_enabled": False}),
        )
        assert first.status == ("sent" if outcome == "sent" else "unknown")
        assert replay.status == first.status and replay.media == first.media
        assert replay.idempotent_replay
        assert len(telegram.calls) == 1 and len(telegram.probes) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "media_enabled", "error"),
    [("photo", False, "media_send_disabled"), ("rich_photo", True, "rich_send_disabled")],
)
async def test_media_flags_stop_new_attempts_without_affecting_old_text_rich(
    mode: str, media_enabled: bool, error: str
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed, mode)
        telegram = MediaTelegram()
        settings = _settings().model_copy(
            update={
                "gate_reply_media_send_enabled": media_enabled,
                "gate_rich_send_enabled": True,
            }
        )
        with pytest.raises(ReplySendError) as exc:
            await deliver(sessions, telegram, command, settings=settings)
        assert exc.value.code == error
        assert telegram.probes == telegram.calls == []


@pytest.mark.anyio
async def test_concurrent_media_reservation_charges_once_and_no_second_http() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        telegram = MediaTelegram(blocked=True)
        first = asyncio.create_task(deliver(sessions, telegram, command))
        await asyncio.wait_for(telegram.started.wait(), timeout=5)
        second = await deliver(sessions, telegram, command)
        assert second.status == "unknown" and second.idempotent_replay
        assert len(telegram.calls) == 1
        telegram.release.set()
        assert (await first).sent


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "digest",
        "execution",
        "scope",
        "caption",
        "bytes",
        "draft",
        "disabled",
        "rights",
        "cap",
        "expiry",
        "deadline",
    ],
)
async def test_media_boundary_failures_before_provider(mutation: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        package, command = await prepared(sessions, seed)
        telegram = MediaTelegram("rights" if mutation == "rights" else "sent")
        settings = None
        if mutation == "digest":
            command = replace(command, expected_media_package_sha256="0" * 64)
        elif mutation == "execution":
            command = replace(command, execution_id="another")
        elif mutation == "scope":
            command = replace(
                command, principal=replace(command.principal, subject="another-principal")
            )
        elif mutation == "disabled":
            settings = _settings().model_copy(update={"gate_send_enabled": False})
        else:
            async with sessions() as session:
                if mutation == "cap":
                    budget = await session.scalar(select(ReplyMediaExecutionBudget))
                    budget.messages_reserved = 19
                elif mutation in {"expiry", "deadline"}:
                    budget = await session.scalar(select(ReplyMediaExecutionBudget))
                    budget.expires_at = datetime.now(UTC) + timedelta(
                        seconds=-1 if mutation == "expiry" else 3600
                    )
                elif mutation == "draft":
                    draft = await session.get(ReplyDraft, package.draft_id)
                    draft.draft_text = "edited after approval"
                elif mutation in {"caption", "bytes"}:
                    item = await session.scalar(
                        select(ReplyMediaItem).order_by(ReplyMediaItem.ordinal)
                    )
                    if mutation == "caption":
                        item.caption = "edited after preview"
                    else:
                        item.data = b"tampered immutable bytes"
                await session.commit()
        with pytest.raises(ReplySendError):
            await deliver(sessions, telegram, command, settings=settings)
        assert telegram.calls == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
async def test_receipt_finalize_failure_keeps_known_delivery_unknown_and_replay_no_post() -> None:
    class BrokenFinalize(SqlTelegramReplySender):
        async def _finalize_success(self, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic DB receipt failure after POST")

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        telegram = MediaTelegram()
        first = await deliver(sessions, telegram, command, BrokenFinalize)
        replay = await deliver(sessions, telegram, command)
        assert (
            first.status == replay.status == "unknown"
            and not first.sent
            and replay.idempotent_replay
        )
        assert first.media["confirmed_message_count"] == 3
        assert first.media["provider_message_ids"] == ["742", "743", "744"]
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_shared_execution_budget_charges_plain_control_and_preserves_media_replay() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        telegram = MediaTelegram()
        assert (await deliver(sessions, telegram, command)).sent
        from app.application.mcp_inbox import get_reply_context
        from app.application.reply_drafts import SaveReplyDraftService
        from app.infrastructure.reply_drafts import SqlReplyDraftWriter

        from test_reply_sends_postgres import _save_draft_command

        async with sessions() as session:
            current = await get_reply_context(
                session=session,
                scope=command.principal.access_scope,
                task_id=seed.task_id,
                limit=30,
            )
            saved = await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                replace(
                    _save_draft_command(seed, "Fresh control"),
                    follow_up=True,
                    expected_context_snapshot=current["context_snapshot"],
                )
            )
            await session.commit()
        control = replace(command, draft_id=saved.draft_id, expected_media_package_sha256=None)
        assert (await deliver(sessions, telegram, control)).sent
        assert (await deliver(sessions, telegram, command)).idempotent_replay
        async with sessions() as session:
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.writes_reserved == 2 and budget.messages_reserved == 4
        assert [call["mode"] for call in telegram.calls] == ["album", "plain"]


@pytest.mark.anyio
async def test_two_conversations_share_atomic_last_budget_slot() -> None:
    async with _postgres_sessions() as sessions:
        first_seed, second_seed = await _seed_reply(sessions), await _seed_reply(sessions)
        _, first_command = await prepared(sessions, first_seed, "photo")
        # Distinct staging key avoids intentionally idempotent staging reuse.
        second = _command(second_seed, second_seed.draft_ids[0])
        second = replace(
            second,
            principal=replace(
                second.principal, scopes=frozenset({"gate:drafts:write", "gate:messages:send"})
            ),
        )
        async with sessions() as session:
            package = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(
                PrepareReplyMediaCommand(
                    principal=second.principal,
                    task_id=second_seed.task_id,
                    expected_context_snapshot=second_seed.context_snapshot,
                    mode="photo",
                    items=(media_input(),),
                    staging_key="second-conversation",
                    execution_id="media-test-execution",
                    execution_expires_at=TEST_EXECUTION_DEADLINE,
                )
            )
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            budget.writes_reserved = 11
            await session.commit()
        second_command = replace(
            second,
            draft_id=package.draft_id,
            expected_media_package_sha256=package.package_sha256,
            execution_id="media-test-execution",
        )
        telegram = MediaTelegram()
        outcomes = await asyncio.gather(
            deliver(sessions, telegram, first_command),
            deliver(sessions, telegram, second_command),
            return_exceptions=True,
        )
        assert (
            sum(
                isinstance(outcome, ReplySendError)
                and outcome.code == "media_execution_budget_exhausted"
                for outcome in outcomes
            )
            == 1
        )
        assert (
            sum(not isinstance(outcome, BaseException) and outcome.sent for outcome in outcomes)
            == 1
        )
        assert len(telegram.calls) == 1
        async with sessions() as session:
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.writes_reserved == 12 and budget.messages_reserved == 1
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1


@pytest.mark.anyio
async def test_crash_after_provider_before_any_receipt_keeps_durable_unknown_fence() -> None:
    class CrashedSender(SqlTelegramReplySender):
        async def _finalize_success(self, **kwargs: Any) -> Any:
            raise asyncio.CancelledError()

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        telegram = MediaTelegram()
        with pytest.raises(asyncio.CancelledError):
            await deliver(sessions, telegram, command, CrashedSender)
        replay = await deliver(sessions, telegram, command)
        assert replay.status == "unknown" and not replay.sent and replay.idempotent_replay
        assert len(telegram.calls) == 1
        async with sessions() as session:
            attempt = await session.scalar(select(SendAttempt))
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert attempt.status == "sending" and budget.writes_reserved == 1


@pytest.mark.anyio
@pytest.mark.parametrize("marker", ["source", "sentinel", "kind"])
async def test_media_legacy_edit_and_send_paths_block_before_body_or_provider(
    monkeypatch, marker: str
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api import sprint9_routes, sprint10_routes, sprint17_send_draft_routes
    from fastapi import HTTPException

    context = (
        {"_gate_media_package": {}}
        if marker == "sentinel"
        else ({"kind": "media"} if marker == "kind" else {})
    )
    draft = SimpleNamespace(
        prompt_context=context,
        source="chatgpt-mcp-media" if marker == "source" else "legacy",
        draft_text="Media package opaque-id",
        status="generated",
    )
    task = SimpleNamespace(status="needs_reply")
    telegram = SimpleNamespace(
        send_message=AsyncMock(side_effect=AssertionError("Legacy text provider call"))
    )
    monkeypatch.setattr(sprint10_routes, "_require_valid_draft_token", AsyncMock())
    monkeypatch.setattr(
        sprint10_routes, "_load_single_draft_for_edit", AsyncMock(return_value=(draft, task))
    )
    request = SimpleNamespace(
        body=AsyncMock(side_effect=AssertionError("Media edit form body read"))
    )
    for handler, extra in (
        (sprint10_routes.edit_draft_form, {}),
        (sprint10_routes.save_draft_form, {"request": request, "telegram": telegram}),
    ):
        with pytest.raises(HTTPException) as failure:
            await handler(
                draft_ref="opaque",
                token="synthetic",
                settings=_settings(),
                session=object(),
                **extra,
            )
        assert failure.value.status_code == 409 and "media package" in failure.value.detail
    assert draft.draft_text == "Media package opaque-id"
    request.body.assert_not_awaited()
    monkeypatch.setattr(
        sprint9_routes,
        "_find_reply_draft_rows_by_ref",
        AsyncMock(return_value=[(draft, task, object())]),
    )
    for handler in (
        sprint10_routes._original_send_approved_draft,
        sprint10_routes._send_approved_draft_once,
    ):
        text = await handler(
            session=object(),
            telegram=telegram,
            settings=_settings(),
            bot=object(),
            gate_event=object(),
            admin_chat_id="synthetic-admin",
            admin_business_connection_id=None,
            draft_ref="opaque",
        )
        assert "MCP" in text
    monkeypatch.setattr(
        sprint17_send_draft_routes,
        "_load_draft_rows_by_ref",
        AsyncMock(return_value=[(draft, task, object(), object())]),
    )
    text = await sprint17_send_draft_routes._send_draft_reply(
        session=object(),
        telegram=telegram,
        settings=_settings(),
        draft_ref="opaque",
        account_id=None,
        scope_label="synthetic",
    )
    assert "status: blocked" in text
    telegram.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("marker", ["source", "sentinel", "kind"])
async def test_media_cannot_enter_legacy_text_sequence_or_lose_markers(marker: str) -> None:
    from app.application.reply_sequences import ReplySequenceError
    from app.infrastructure.reply_sequences import SqlReplySequenceWriter

    from test_reply_sends_postgres import _sequence_command

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        package, _ = await prepared(sessions, seed)
        async with sessions() as session:
            draft = await session.get(ReplyDraft, package.draft_id)
            original_text = draft.draft_text
            if marker == "source":
                draft.prompt_context = {}
            elif marker == "sentinel":
                draft.source = "legacy"
                draft.prompt_context = {"_gate_media_package": {}}
            else:
                draft.source = "legacy"
                draft.prompt_context = {"kind": "media"}
            await session.commit()
        async with sessions() as session:
            command = _sequence_command(seed, (seed.draft_ids[0], package.draft_id))
            with pytest.raises(ReplySequenceError) as failure:
                await SqlReplySequenceWriter(session).queue(command)
            assert failure.value.code == "media_surface_unsupported"
        async with sessions() as session:
            draft = await session.get(ReplyDraft, package.draft_id)
            assert draft.draft_text == original_text
            assert "reply_sequence" not in draft.prompt_context
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0

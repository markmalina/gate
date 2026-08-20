"""PostgreSQL proof for durable AQUB owner photo-reference receipts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.aqub_transport import (
    AQUBPhotoReference,
    AQUBStoreDisposition,
    AQUBTransportCommand,
    AQUBTransportContentKind,
    AQUBTransportRole,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.core.config import get_settings
from app.infrastructure.aqub_installation import (
    AQUB_RECEIPT_BOT_KEY,
    SqlAQUBTransportReceiptStore,
)
from app.models.domain import TelegramReceipt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _photo_command(*, caption: str = "/status") -> AQUBTransportCommand:
    occurred_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    preferred = AQUBPhotoReference(
        file_id="synthetic-file-large",
        file_unique_id="synthetic-unique-large",
        width=1280,
        height=960,
        file_size=12_345,
    )
    payload = {
        "update_id": 987654322,
        "message": {
            "message_id": 42,
            "date": int(occurred_at.timestamp()),
            "chat": {"id": -1007001, "type": "supergroup"},
            "from": {"id": 7003, "is_bot": False},
            "photo": [
                {
                    "file_id": "synthetic-file-small",
                    "file_unique_id": "synthetic-unique-small",
                    "width": 320,
                    "height": 240,
                    "file_size": 1_234,
                },
                {
                    "file_id": preferred.file_id,
                    "file_unique_id": preferred.file_unique_id,
                    "width": preferred.width,
                    "height": preferred.height,
                    "file_size": preferred.file_size,
                },
            ],
            "caption": caption,
        },
    }
    return AQUBTransportCommand(
        source="telegram_bot_api",
        external_id=str(payload["update_id"]),
        payload_sha256=canonical_telegram_payload_digest(payload),
        payload=payload,
        chat_id="-1007001",
        actor_id="7003",
        role=AQUBTransportRole.ALPHA,
        command=None,
        occurred_at=occurred_at,
        received_at=occurred_at + timedelta(seconds=1),
        content_kind=AQUBTransportContentKind.PHOTO,
        photo=preferred,
    )


@pytest.mark.anyio
async def test_raw_photo_reference_reopens_exactly_and_changed_replay_fails() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB photo receipt test")

    schema = f"aqub_photo_receipt_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(TelegramReceipt.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        command = _photo_command()

        async with sessions() as session:
            store = SqlAQUBTransportReceiptStore(session)
            first = await store.accept(command)
            duplicate = await store.accept(command)
            with pytest.raises(RuntimeError, match="aqub_receipt_mutated_replay"):
                await store.accept(_photo_command(caption="changed"))

        assert first.disposition is AQUBStoreDisposition.CREATED
        assert duplicate.disposition is AQUBStoreDisposition.DUPLICATE
        assert duplicate.command_receipt_id == first.command_receipt_id

        async with sessions() as session:
            receipt = (
                await session.execute(
                    select(TelegramReceipt).where(
                        TelegramReceipt.bot_key == AQUB_RECEIPT_BOT_KEY,
                        TelegramReceipt.update_id == 987654322,
                    )
                )
            ).scalar_one()

        assert receipt.payload == command.payload
        message = receipt.payload["message"]
        assert message["caption"] == "/status"
        assert message["photo"][1]["file_id"] == "synthetic-file-large"
        assert message["photo"][1]["file_unique_id"] == "synthetic-unique-large"
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()

"""PostgreSQL regression for text linked to a pending Food album."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.aqub_transport import (
    AQUBPhotoReference,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)
from app.core.config import get_settings
from app.infrastructure.aqub_food_media import SqlAQUBFoodMediaStore
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.aqub import AQUBFoodMedia
from app.models.domain import TelegramReceipt, TelegramReceiptStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
CHAT_ID = "-1001234567890"
ACTOR_ID = "123456789"
GROUP_ID = "synthetic-album"


def _photo_payload(*, update_id: int, message_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(NOW.timestamp()),
            "chat": {"id": int(CHAT_ID), "type": "supergroup"},
            "from": {"id": int(ACTOR_ID), "is_bot": False},
            "media_group_id": GROUP_ID,
            "photo": [
                {
                    "file_id": f"file-{message_id}",
                    "file_unique_id": f"unique-{message_id}",
                    "width": 640,
                    "height": 480,
                    "file_size": 1234,
                }
            ],
            **({"caption": "Новый приём: яблоко"} if message_id == 101 else {}),
        },
    }


def _text_payload(*, update_id: int = 3, message_id: int = 103) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(NOW.timestamp()),
            "chat": {"id": int(CHAT_ID), "type": "supergroup"},
            "from": {"id": int(ACTOR_ID), "is_bot": False},
            "text": "Вес 120 г",
        },
    }


def _project_photo(payload: dict[str, object], received_at: datetime) -> AQUBTransportInbound:
    message = payload["message"]
    assert isinstance(message, dict)
    photo = AQUBPhotoReference(
        file_id=str(message["photo"][0]["file_id"]),
        file_unique_id=str(message["photo"][0]["file_unique_id"]),
        width=640,
        height=480,
        file_size=1234,
    )
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id=str(payload["update_id"]),
        chat_id=CHAT_ID,
        actor_id=ACTOR_ID,
        command=None,
        payload=payload,
        occurred_at=NOW,
        received_at=received_at,
        content_kind=AQUBTransportContentKind.PHOTO,
        photo=photo,
        media_group_id=GROUP_ID,
    )


def _project_text(payload: dict[str, object], received_at: datetime) -> AQUBTransportInbound:
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id=str(payload["update_id"]),
        chat_id=CHAT_ID,
        actor_id=ACTOR_ID,
        command="Вес 120 г",
        payload=payload,
        occurred_at=NOW,
        received_at=received_at,
        content_kind=AQUBTransportContentKind.TEXT,
    )


@pytest.mark.anyio
async def test_text_receipt_is_linked_replayed_and_finalized_with_album() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for album link regression")

    schema = f"aqub_album_link_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(TelegramReceipt.__table__.create)
            await connection.run_sync(AQUBFoodMedia.__table__.create)

        photo_payloads = [
            _photo_payload(update_id=1, message_id=101),
            _photo_payload(update_id=2, message_id=102),
        ]
        text_payload = _text_payload()
        earlier_text_payload = _text_payload(update_id=4, message_id=100)
        received = [NOW, NOW + timedelta(milliseconds=100)]
        text_received = NOW + timedelta(milliseconds=700)
        photo_receipt_ids = [uuid4(), uuid4()]
        text_receipt_id = uuid4()
        earlier_text_receipt_id = uuid4()

        async with sessions() as session:
            session.add_all(
                [
                    TelegramReceipt(
                        id=receipt_id,
                        bot_key=AQUB_RECEIPT_BOT_KEY,
                        update_id=index + 1,
                        payload=payload,
                        status=TelegramReceiptStatus.PROCESSED.value,
                        received_at=received[index],
                        processed_at=received[index],
                    )
                    for index, (receipt_id, payload) in enumerate(
                        zip(photo_receipt_ids, photo_payloads, strict=True)
                    )
                ]
                + [
                    TelegramReceipt(
                        id=text_receipt_id,
                        bot_key=AQUB_RECEIPT_BOT_KEY,
                        update_id=3,
                        payload=text_payload,
                        status=TelegramReceiptStatus.PROCESSED.value,
                        received_at=text_received,
                        processed_at=text_received,
                    ),
                    TelegramReceipt(
                        id=earlier_text_receipt_id,
                        bot_key=AQUB_RECEIPT_BOT_KEY,
                        update_id=4,
                        payload=earlier_text_payload,
                        status=TelegramReceiptStatus.PROCESSED.value,
                        received_at=text_received,
                        processed_at=text_received,
                    ),
                ]
            )
            session.add_all(
                [
                    AQUBFoodMedia(
                        receipt_id=receipt_id,
                        content_kind="photo",
                        status="queued",
                        attempt_count=0,
                        claim_token=uuid4(),
                        processing_started_at=received[index],
                    )
                    for index, receipt_id in enumerate(photo_receipt_ids)
                ]
            )
            await session.commit()

            store = SqlAQUBFoodMediaStore(session)
            photos = [
                _project_photo(payload, received[index])
                for index, payload in enumerate(photo_payloads)
            ]
            linked_text = _project_text(text_payload, text_received)
            earlier_text = _project_text(earlier_text_payload, text_received)

            assert (
                await store.link_text_to_pending_album(
                    receipt_id=str(earlier_text_receipt_id),
                    inbound=earlier_text,
                    now=text_received,
                )
                is False
            )

            assert (
                await store.link_text_to_pending_album(
                    receipt_id=str(text_receipt_id),
                    inbound=linked_text,
                    now=text_received,
                )
                is True
            )
            assert (
                await store.link_text_to_pending_album(
                    receipt_id=str(text_receipt_id),
                    inbound=linked_text,
                    now=text_received,
                )
                is True
            )

            linked = await store.linked_texts_for_album(
                inbounds=photos,
                now=text_received + timedelta(seconds=1),
            )
            assert [(item[0], item[1]["message"]["text"]) for item in linked] == [
                (str(text_receipt_id), "Вес 120 г")
            ]

            finalized = await store.finalize_album(
                media_receipt_ids=[str(value) for value in photo_receipt_ids],
                linked_text_receipt_ids=[str(text_receipt_id)],
                now=text_received + timedelta(seconds=2),
            )
            assert finalized == (2, 1)

            text_receipt = await session.scalar(
                select(TelegramReceipt).where(TelegramReceipt.id == text_receipt_id)
            )
            assert text_receipt is not None
            assert text_receipt.status == TelegramReceiptStatus.PROCESSED.value
            assert text_receipt.processing_started_at == text_received
            assert text_receipt.processed_at == text_received + timedelta(seconds=2)
            media = (
                await session.scalars(select(AQUBFoodMedia).order_by(AQUBFoodMedia.receipt_id))
            ).all()
            assert all(item.final_sent_at is not None for item in media)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()

"""Two OS processes contend on the real PostgreSQL dialog admission fence."""

import asyncio
import multiprocessing
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.reply_sends import ReplySendError
from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.config import Settings
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import SendAttempt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _seed_reply,
    _settings,
    _wait_for_backend_lock,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _sender_process(schema: str, seed: Any, channel: Any) -> None:
    """Only an isolated test schema and a synthetic Telegram adapter are allowed."""
    try:
        if re.fullmatch(r"reply_send_test_[0-9a-f]{32}", schema) is None:
            raise ValueError("unexpected test schema")
        asyncio.run(_send_in_child(schema, seed, channel))
    except BaseException as exc:
        # Do not serialize a connection string, traceback, payload or exception
        # message into the process protocol. The parent owns bounded cleanup.
        channel.send({"phase": "failure", "type": type(exc).__name__})
        raise SystemExit(1) from None
    finally:
        channel.close()


async def _send_in_child(schema: str, seed: Any, channel: Any) -> None:
    engine = create_async_engine(
        Settings(_env_file=None).database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.connect() as pinned:
            pid = await pinned.scalar(text("SELECT pg_backend_pid()"))
            await pinned.rollback()

            class Telegram(_LiteralRecordingTelegram):
                async def get_business_connection(self, *, business_connection_id: str):
                    channel.send({"phase": "probe", "pid": pid})
                    return await super().get_business_connection(
                        business_connection_id=business_connection_id,
                    )

            telegram = Telegram()
            async with AsyncSession(bind=pinned, expire_on_commit=False) as session:
                try:
                    result = await SqlTelegramReplySender(
                        session=session, telegram=telegram, settings=_settings(),
                    ).send(_command(seed, seed.draft_ids[0]))
                except ReplySendError as exc:
                    channel.send({
                        "phase": "result", "error": exc.code,
                        "dispatches": len(telegram.plain_calls),
                    })
                else:
                    channel.send({
                        "phase": "result", "sent": result.sent,
                        "dispatches": len(telegram.plain_calls),
                    })
    finally:
        await engine.dispose()


async def _read_process_message(channel: Any) -> dict[str, Any]:
    assert await asyncio.to_thread(channel.poll, 15), "child process did not report in time"
    return channel.recv()


@pytest.mark.anyio
@pytest.mark.parametrize("owner", [False, True])
async def test_other_process_cannot_dispatch_after_durable_ingress_commit(owner: bool) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            schema = await session.scalar(text("SELECT current_schema()"))
        committing, release = asyncio.Event(), asyncio.Event()

        class IngressSession(AsyncSession):
            async def commit(self) -> None:
                committing.set()
                await asyncio.wait_for(release.wait(), 20)
                await super().commit()

        async def ingress() -> None:
            async with IngressSession(
                bind=sessions.kw["bind"], expire_on_commit=False,
            ) as session:
                await SqlTelegramReceiptStore(session).create_if_absent(
                    AcceptTelegramReceiptCommand(
                        bot_key=TelegramConnectorKey.BUSINESS.value, update_id=42,
                        received_at=datetime.now(UTC), payload={
                            "update_id": 42, "business_message": {
                                "business_connection_id": seed.business_connection_id,
                                "chat": {"id": int(seed.recipient_chat_id)},
                                "from": {"id": 999 if owner else int(seed.recipient_chat_id)},
                                "message_id": 42, "text": "Initial inbound message",
                            },
                        },
                    ),
                )

        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_sender_process, args=(schema, seed, child))
        receiver = asyncio.create_task(ingress())
        started = False
        try:
            await asyncio.wait_for(committing.wait(), 10)
            process.start()
            started = True
            child.close()
            message = await _read_process_message(parent)
            assert message["phase"] == "probe", message
            await _wait_for_backend_lock(sessions, backend_pid=message["pid"])
            # The process reached admission but is blocked by the ingress row,
            # so it cannot have dispatched the stale response yet.
            assert not parent.poll()
            release.set()
            await asyncio.wait_for(receiver, 10)
            result = await _read_process_message(parent)
            assert result == {"phase": "result", "error": "context_changed", "dispatches": 0}
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode == 0
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
        finally:
            release.set()
            if not receiver.done():
                receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            if started:
                if process.is_alive():
                    process.terminate()
                await asyncio.to_thread(process.join, 10)
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 10)
                process.close()
            parent.close()
            child.close()

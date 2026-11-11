"""Synthetic PG race peer. No provider access; controlled commit over stdin."""

import asyncio
import os
import sys
from datetime import UTC, datetime

from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.dialog_snapshots import mark_ingress_unavailable
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


async def main() -> None:
    schema, connection_id, chat_id, kind = sys.argv[1:]
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        connect_args={"server_settings": {"search_path": schema}},
    )

    class ControlledCommit(AsyncSession):
        async def commit(self) -> None:
            pid = await self.scalar(text("select pg_backend_pid()"))
            print(f"READY {pid}", flush=True)
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "COMMIT"
            await super().commit()
            print("COMMITTED", flush=True)

    try:
        async with ControlledCommit(engine, expire_on_commit=False) as session:
            if kind == "gap":
                await mark_ingress_unavailable(
                    session,
                    reason="synthetic_observed_gap",
                    connection_id=connection_id,
                )
                await session.commit()
            else:
                await SqlTelegramReceiptStore(session).create_if_absent(
                    AcceptTelegramReceiptCommand(
                        bot_key=TelegramConnectorKey.BUSINESS.value,
                        update_id=9999,
                        received_at=datetime.now(UTC),
                        payload={
                            "update_id": 9999,
                            "business_message": {
                                "business_connection_id": connection_id,
                                "chat": {"id": int(chat_id)},
                                "voice": {"file_id": "synthetic-untranscribed"},
                            },
                        },
                    ),
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

"""Resume one definitely rejected Mark delivery after the operator fixes access."""

import argparse
import asyncio

from app.core.config import get_settings
from app.infrastructure.database import database_runtime_lifespan
from app.worker.watch_voice import resume_rejected_delivery


async def main(note_id: str) -> None:
    settings = get_settings()
    async with database_runtime_lifespan(settings.database_url) as runtime:
        resumed = await resume_rejected_delivery(settings, runtime.session_factory, note_id)
    print("resumed" if resumed else "not_resumed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_id")
    asyncio.run(main(parser.parse_args().event_id))

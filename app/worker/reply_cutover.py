"""Fixed #745 migration predeploy gate; never invokes a delivery provider."""

import asyncio
import json
import logging

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.infrastructure.database import database_runtime_lifespan
from app.infrastructure.reply_cutover import (
    BASELINE_REVISION,
    GUARDED_REVISION,
    ReplyCutoverObservation,
    observe_reply_cutover,
)

FAILURE_SCHEMA_READ_TIMEOUT = 5.0


async def _observe() -> ReplyCutoverObservation:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("reply_cutover_database_missing")
    async with database_runtime_lifespan(settings.database_url) as runtime:
        async with runtime.session_factory() as session:
            return await observe_reply_cutover(session)


async def _schema_after_migration_failure() -> str:
    try:
        async with asyncio.timeout(FAILURE_SCHEMA_READ_TIMEOUT):
            observation = await _observe()
    except Exception:
        return "unverified"
    if observation.schema_revisions in ((BASELINE_REVISION,), (GUARDED_REVISION,)):
        return observation.schema_revisions[0]
    return "unverified"


def main() -> None:
    """After Ops proves every old instance stopped, admit only the fixed migration."""

    logging.basicConfig(level=logging.INFO)
    migration_started = False
    try:
        observation = asyncio.run(_observe())
        print(json.dumps({
            "schema_revisions": observation.schema_revisions,
            "reserved": observation.reserved,
            "sending": observation.sending,
            "unknown": observation.unknown,
        }, sort_keys=True))
        observation.require_migration_preconditions(
            sends_enabled=get_settings().gate_send_enabled,
        )
        migration_config = Config("alembic.ini")
        migration_started = True
        command.upgrade(migration_config, GUARDED_REVISION)
        after = asyncio.run(_observe())
        if after.schema_revisions != (GUARDED_REVISION,) or after.active_attempts:
            raise RuntimeError("reply_cutover_postmigration_check_failed")
    except Exception:
        # Database exceptions can include the URL or driver parameters.
        if migration_started:
            # The upgrade may have committed before the error. Never reuse its input schema.
            logging.error(
                "reply_cutover_predeploy_failed observed_schema_after_attempt=%s",
                asyncio.run(_schema_after_migration_failure()),
            )
        else:
            logging.error("reply_cutover_predeploy_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

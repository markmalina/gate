"""Durable package identity, scoped preview and cleanup/send locking on PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.reply_drafts import ReplyDraftError
from app.application.reply_media import PrepareReplyMediaCommand, PrepareReplyMediaService
from app.infrastructure.reply_media import (
    SqlReplyMediaWriter,
    prune_expired_reply_media,
    read_reply_media_preview,
)
from app.models.domain import ReplyDraft, SendAttempt
from app.models.reply_media import ReplyMediaExecutionBudget, ReplyMediaItem, ReplyMediaPackage
from sqlalchemy import func, select

from test_reply_media_assets import media_input
from test_reply_sends_postgres import _postgres_sessions, _save_draft_command, _seed_reply


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _prepare(seed, *, key="fixture-stage", mode="photo", items=None, rich_content=None):
    return PrepareReplyMediaCommand(
        principal=_save_draft_command(seed, "unused").principal,
        task_id=seed.task_id,
        mode=mode,
        items=items or (media_input(),),
        staging_key=key,
        execution_id="exec742-fixture",
        execution_expires_at=datetime.now(UTC) + timedelta(hours=1),
        rich_content=rich_content,
    )


@pytest.mark.anyio
async def test_concurrent_staging_and_restart_replay_return_one_immutable_package() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        cmd = _prepare(seed)

        async def stage():
            async with sessions() as session:
                result = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
                await session.commit()
                return result

        first, second = await asyncio.gather(stage(), stage())
        third = await stage()
        assert first.package_id == second.package_id == third.package_id
        assert first.package_sha256 == second.package_sha256 == third.package_sha256
        assert sum([first.idempotent_replay, second.idempotent_replay]) == 1
        assert third.idempotent_replay and third.account_label == "RU"
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ReplyMediaPackage)) == 1
            assert await session.scalar(select(func.count()).select_from(ReplyMediaItem)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(ReplyMediaExecutionBudget))
                == 1
            )
            preview = await read_reply_media_preview(session, cmd.principal, first.package_id)
            assert preview[0][0].output_sha256 == first.items[0].output_sha256
            assert preview[0][1]
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.writes_reserved == budget.messages_reserved == 0
            package = await session.get(ReplyMediaPackage, first.package_id)
            expiry = package.expires_at
            package.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        # Expired staging replay returns metadata without renewing bytes or expiry.
        fourth = await stage()
        assert fourth.idempotent_replay and fourth.expires_at < expiry
        async with sessions() as session:
            with pytest.raises(ReplyDraftError, match="expired"):
                await read_reply_media_preview(session, cmd.principal, first.package_id)


@pytest.mark.anyio
async def test_changed_source_caption_or_workspace_cannot_rebind_prepared_media() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        cmd = _prepare(seed)
        async with sessions() as session:
            prepared = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            await session.commit()
        changed = replace(cmd, items=(media_input(caption="new caption"),))
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(changed)
            assert error.value.code == "media_staging_conflict"
        outsider = replace(
            cmd.principal,
            access_scope=replace(cmd.principal.access_scope, workspace_id="other-workspace"),
        )
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await read_reply_media_preview(session, outsider, prepared.package_id)
            assert error.value.code == "media_not_found"
            assert await session.scalar(select(func.count()).select_from(ReplyMediaPackage)) == 1
            draft = await session.get(ReplyDraft, prepared.draft_id)
            draft.draft_text = "edited after preview"
            await session.commit()
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await read_reply_media_preview(session, cmd.principal, prepared.package_id)
            assert error.value.code == "media_changed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,expected_count",
    [
        ("reserved", 0),
        ("sending", 0),
        ("unknown", 0),
        ("sent", 1),
        ("failed", 1),
        ("cancelled", 1),
    ],
)
async def test_expiry_cleanup_preserves_unresolved_send_bytes_and_all_evidence(
    status, expected_count
):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        cmd = _prepare(seed)
        async with sessions() as session:
            prepared = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            package = await session.get(ReplyMediaPackage, prepared.package_id)
            package.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            package.receipt_json = {"status": status, "evidence": "preserve"}
            session.add(
                SendAttempt(
                    id=uuid4(),
                    reply_draft_id=prepared.draft_id,
                    reply_task_id=seed.task_id,
                    conversation_id=seed.conversation_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    principal_subject=cmd.principal.subject,
                    status=status,
                    account_label="RU",
                    target_chat_id=seed.recipient_chat_id,
                    business_connection_id=seed.business_connection_id,
                )
            )
            await session.commit()
        async with sessions() as session:
            assert await prune_expired_reply_media(session) == expected_count
            await session.commit()
        async with sessions() as session:
            item = await session.scalar(select(ReplyMediaItem))
            package = await session.get(ReplyMediaPackage, prepared.package_id)
            assert (item.data is None) == bool(expected_count)
            assert item.output_sha256 == prepared.items[0].output_sha256
            assert package.snapshot_json["items"][0]["output_sha256"] == item.output_sha256
            assert package.receipt_json == {"status": status, "evidence": "preserve"}


@pytest.mark.anyio
async def test_cleanup_skips_draft_locked_by_a_reserving_send_then_cleans_terminal_bytes() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        cmd = _prepare(seed)
        async with sessions() as session:
            prepared = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            package = await session.get(ReplyMediaPackage, prepared.package_id)
            package.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        async with sessions() as reserving:
            await reserving.scalar(
                select(ReplyDraft).where(ReplyDraft.id == prepared.draft_id).with_for_update()
            )
            async with sessions() as cleaning:
                assert await prune_expired_reply_media(cleaning) == 0
                await cleaning.commit()
            await reserving.rollback()
        async with sessions() as cleaning:
            assert await prune_expired_reply_media(cleaning) == 1
            await cleaning.commit()


@pytest.mark.anyio
async def test_invalid_rich_reference_rolls_back_new_draft_and_all_items() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        content = {
            "schema_version": "gate.rich.v2",
            "blocks": [{"type": "photo", "media_ref": "other-package:1"}],
        }
        cmd = _prepare(seed, mode="rich_photo", rich_content=content)
        async with sessions() as session:
            with pytest.raises(ReplyDraftError):
                await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            assert await session.scalar(select(func.count()).select_from(ReplyMediaPackage)) == 0
            assert await session.scalar(select(func.count()).select_from(ReplyMediaItem)) == 0
            assert await session.scalar(select(func.count()).select_from(ReplyDraft)) == 1


@pytest.mark.anyio
async def test_exact_execution_deadline_and_budget_counters_survive_prepare_replay_and_new_key():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        cmd = _prepare(seed)
        async with sessions() as session:
            prepared = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.expires_at == cmd.execution_expires_at
            assert prepared.execution_expires_at == cmd.execution_expires_at
            assert prepared.expires_at == cmd.execution_expires_at
            budget.writes_reserved = 3
            budget.messages_reserved = 7
            await session.commit()
        async with sessions() as session:
            replay = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(cmd)
            assert replay.idempotent_replay
            assert replay.execution_expires_at == cmd.execution_expires_at
            await session.commit()
        extending = replace(
            cmd,
            staging_key="new-key",
            execution_expires_at=cmd.execution_expires_at + timedelta(minutes=1),
        )
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(extending)
            assert error.value.code == "media_execution_conflict"
        async with sessions() as session:
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert budget.expires_at == cmd.execution_expires_at
            assert (budget.writes_reserved, budget.messages_reserved) == (3, 7)
            assert await session.scalar(select(func.count()).select_from(ReplyMediaPackage)) == 1


@pytest.mark.anyio
async def test_actual_0115_migration_roundtrip_has_no_media_metadata_drift_and_keeps_core_data():
    """Run the authored migration, not create_all, for every new media table."""
    import importlib.util
    from pathlib import Path

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.models.domain import Base
    from sqlalchemy import CheckConstraint, inspect

    media_tables = {
        "reply_media_execution_budgets",
        "reply_media_packages",
        "reply_media_items",
        "reply_media_budget_reservations",
    }
    drop_order = (
        "reply_media_budget_reservations",
        "reply_media_items",
        "reply_media_packages",
        "reply_media_execution_budgets",
    )
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0115_reply_media_packages.py"
    spec = importlib.util.spec_from_file_location("gate742_migration_0115", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def roundtrip(connection):
        core_tables = set(inspect(connection).get_table_names()) - media_tables
        assert connection.execute(select(func.count()).select_from(ReplyDraft)).scalar_one() == 1
        for name in drop_order:
            Base.metadata.tables[name].drop(connection)
        assert set(inspect(connection).get_table_names()) == core_tables
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        assert set(inspect(connection).get_table_names()) == core_tables | media_tables

        def include_object(obj, name, kind, reflected, compare_to):
            if kind == "table":
                return name in media_tables
            table = getattr(obj, "table", None)
            return table is not None and table.name in media_tables

        comparison = MigrationContext.configure(
            connection,
            opts={
                "include_object": include_object,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        assert compare_metadata(comparison, Base.metadata) == []
        inspector = inspect(connection)
        for name in media_tables:
            expected = {
                constraint.name
                for constraint in Base.metadata.tables[name].constraints
                if isinstance(constraint, CheckConstraint)
            }
            assert {
                constraint["name"] for constraint in inspector.get_check_constraints(name)
            } == expected
        with Operations.context(context):
            module.downgrade()
        assert set(inspect(connection).get_table_names()) == core_tables
        assert connection.execute(select(func.count()).select_from(ReplyDraft)).scalar_one() == 1
        with Operations.context(context):
            module.upgrade()
        assert compare_metadata(comparison, Base.metadata) == []
        assert connection.execute(select(func.count()).select_from(ReplyDraft)).scalar_one() == 1

    async with _postgres_sessions() as sessions:
        await _seed_reply(sessions)
        async with sessions() as session:
            connection = await session.connection()
            await connection.run_sync(roundtrip)
            await session.commit()

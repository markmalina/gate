"""PostgreSQL proof for the isolated AQUB photo archive ledger."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.application.aqub_photo_archive import (
    AQUB_PHOTO_ARCHIVE_STALE_AFTER,
    AQUBPhotoArchiveCompletion,
    AQUBPhotoArchiveError,
    DownloadedAQUBPhoto,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.core.config import get_settings
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.infrastructure.aqub_photo_archive import SqlAQUBPhotoArchiveRepository
from app.models.aqub import AQUBInstallationBinding, AQUBPhotoArchive
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramReceipt,
    TelegramReceiptStatus,
)
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0100_aqub_photo_archive.py"
)
OCCURRED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
CUTOFF = OCCURRED_AT - timedelta(minutes=1)
RECEIVED_AT = OCCURRED_AT + timedelta(seconds=1)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _migration_0100() -> Any:
    spec = spec_from_file_location("gate_test_0100", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_0100(sync_connection: Any) -> None:
    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        _migration_0100().upgrade()


def _downgrade_0100(sync_connection: Any) -> None:
    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        _migration_0100().downgrade()


def _photo_payload(*, update_id: int = 9_001) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(OCCURRED_AT.timestamp()),
            "chat": {"id": -1_007_001, "type": "supergroup"},
            "from": {"id": 7_003, "is_bot": False},
            "photo": [
                {
                    "file_id": "synthetic-file-small",
                    "file_unique_id": "synthetic-unique-small",
                    "width": 320,
                    "height": 240,
                    "file_size": 1_234,
                },
                {
                    "file_id": "synthetic-file-large",
                    "file_unique_id": "synthetic-unique-large",
                    "width": 1_280,
                    "height": 960,
                    "file_size": 12_345,
                },
            ],
            "caption": "/status remains raw metadata",
        },
    }


@dataclass(frozen=True, slots=True)
class _ArchivePG:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    receipt_id: UUID
    binding_id: UUID
    payload: dict[str, Any]


@pytest.fixture
async def archive_pg() -> AsyncIterator[_ArchivePG]:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for AQUB photo archive tests")

    schema = f"aqub_photo_archive_test_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(TelegramReceipt.__table__.create)
            # 0100 adds this index to an already-existing production table.
            await connection.execute(
                text('DROP INDEX "ix_telegram_receipts_aqub_photo_discovery"')
            )
            await connection.run_sync(TelegramOutboxEvent.__table__.create)
            await connection.run_sync(AQUBInstallationBinding.__table__.create)
            await connection.run_sync(_apply_0100)

        receipt_id = uuid4()
        binding_id = uuid4()
        payload = _photo_payload()
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(
                TelegramReceipt(
                    id=receipt_id,
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    update_id=payload["update_id"],
                    payload=payload,
                    status=TelegramReceiptStatus.PROCESSED.value,
                    received_at=RECEIVED_AT,
                    processed_at=RECEIVED_AT,
                )
            )
            await session.flush()
            session.add(
                AQUBInstallationBinding(
                    id=binding_id,
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    group_chat_id="-1007001",
                    alpha_user_id="7003",
                    evidence_receipt_id=receipt_id,
                    bound_at=CUTOFF,
                )
            )
            await session.commit()

        yield _ArchivePG(
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            receipt_id=receipt_id,
            binding_id=binding_id,
            payload=payload,
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        await admin.dispose()


async def _receipt_snapshot(pg: _ArchivePG) -> dict[str, Any]:
    async with pg.sessions() as session:
        snapshot = await session.scalar(
            text(
                "SELECT to_jsonb(receipt) FROM telegram_receipts AS receipt "
                "WHERE receipt.id = :receipt_id"
            ),
            {"receipt_id": pg.receipt_id},
        )
    assert isinstance(snapshot, dict)
    return snapshot


async def _archive_values(pg: _ArchivePG) -> dict[str, Any]:
    async with pg.sessions() as session:
        row = (
            await session.execute(
                select(
                    AQUBPhotoArchive.id,
                    AQUBPhotoArchive.binding_id,
                    AQUBPhotoArchive.source_payload_sha256,
                    AQUBPhotoArchive.file_unique_digest,
                    AQUBPhotoArchive.width,
                    AQUBPhotoArchive.height,
                    AQUBPhotoArchive.declared_size_bytes,
                    AQUBPhotoArchive.status,
                    AQUBPhotoArchive.attempt_count,
                    AQUBPhotoArchive.lease_generation,
                    AQUBPhotoArchive.lease_token,
                    AQUBPhotoArchive.available_at,
                    AQUBPhotoArchive.locked_at,
                    AQUBPhotoArchive.last_error_code,
                    AQUBPhotoArchive.raw_data,
                    AQUBPhotoArchive.raw_sha256,
                    AQUBPhotoArchive.raw_size_bytes,
                    AQUBPhotoArchive.raw_media_type,
                    AQUBPhotoArchive.archived_at,
                    AQUBPhotoArchive.purge_at,
                    AQUBPhotoArchive.raw_purged_at,
                    AQUBPhotoArchive.updated_at,
                ).where(AQUBPhotoArchive.receipt_id == pg.receipt_id)
            )
        ).one()
    return dict(row._mapping)


async def _discover(pg: _ArchivePG) -> SqlAQUBPhotoArchiveRepository:
    repository = SqlAQUBPhotoArchiveRepository(pg.sessions)
    cursor = await repository.discover(
        not_before=CUTOFF,
        cursor=None,
        limit=100,
        observed_at=RECEIVED_AT + timedelta(seconds=1),
    )
    assert cursor is not None
    assert cursor.received_at == RECEIVED_AT
    return repository


def _downloaded() -> DownloadedAQUBPhoto:
    raw = b"\x89PNG\r\n\x1a\npostgres-archive"
    return DownloadedAQUBPhoto(
        data=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        media_type="image/png",
    )


@pytest.mark.anyio
async def test_migration_contract_and_exhausted_sweep_are_stable(
    archive_pg: _ArchivePG,
) -> None:
    def schema_names(
        sync_connection: Any,
    ) -> tuple[set[str], set[str], set[str]]:
        inspector = inspect(sync_connection)
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("aqub_photo_archives")
        }
        indexes = {
            item["name"] for item in inspector.get_indexes("aqub_photo_archives")
        }
        receipt_indexes = {
            item["name"] for item in inspector.get_indexes("telegram_receipts")
        }
        return checks, indexes, receipt_indexes

    async with archive_pg.engine.connect() as connection:
        checks, indexes, receipt_indexes = await connection.run_sync(schema_names)
    assert checks == {
        "ck_aqub_photo_archives_status",
        "ck_aqub_photo_archives_attempts",
        "ck_aqub_photo_archives_source_digest",
        "ck_aqub_photo_archives_declared_size",
        "ck_aqub_photo_archives_photo_identity",
        "ck_aqub_photo_archives_error_pair",
        "ck_aqub_photo_archives_lease_state",
        "ck_aqub_photo_archives_raw_state",
    }
    assert indexes >= {
        "ix_aqub_photo_archives_due",
        "ix_aqub_photo_archives_stale",
        "ix_aqub_photo_archives_purge",
    }
    assert "ix_telegram_receipts_aqub_photo_discovery" in receipt_indexes
    assert "ix_telegram_receipts_aqub_photo_discovery" in {
        index.name for index in TelegramReceipt.__table__.indexes
    }

    excluded: list[TelegramReceipt] = []
    pre_cutoff = copy.deepcopy(archive_pg.payload)
    pre_cutoff["update_id"] = 9_002
    pre_cutoff["message"]["message_id"] = 9_002
    excluded.append(
        TelegramReceipt(
            id=uuid4(),
            bot_key=AQUB_RECEIPT_BOT_KEY,
            update_id=9_002,
            payload=pre_cutoff,
            status=TelegramReceiptStatus.PROCESSED.value,
            received_at=CUTOFF - timedelta(seconds=1),
            processed_at=CUTOFF - timedelta(seconds=1),
        )
    )
    wrong_actor = copy.deepcopy(archive_pg.payload)
    wrong_actor["update_id"] = 9_003
    wrong_actor["message"]["message_id"] = 9_003
    wrong_actor["message"]["from"]["id"] = 99_999
    excluded.append(
        TelegramReceipt(
            id=uuid4(),
            bot_key=AQUB_RECEIPT_BOT_KEY,
            update_id=9_003,
            payload=wrong_actor,
            status=TelegramReceiptStatus.PROCESSED.value,
            received_at=RECEIVED_AT,
            processed_at=RECEIVED_AT,
        )
    )
    wrong_group = copy.deepcopy(archive_pg.payload)
    wrong_group["update_id"] = 9_004
    wrong_group["message"]["message_id"] = 9_004
    wrong_group["message"]["chat"]["id"] = -1_009_999
    excluded.append(
        TelegramReceipt(
            id=uuid4(),
            bot_key=AQUB_RECEIPT_BOT_KEY,
            update_id=9_004,
            payload=wrong_group,
            status=TelegramReceiptStatus.PROCESSED.value,
            received_at=RECEIVED_AT,
            processed_at=RECEIVED_AT,
        )
    )
    text_payload = copy.deepcopy(archive_pg.payload)
    text_payload["update_id"] = 9_005
    text_payload["message"]["message_id"] = 9_005
    text_payload["message"].pop("photo")
    text_payload["message"].pop("caption")
    text_payload["message"]["text"] = "plain text"
    excluded.append(
        TelegramReceipt(
            id=uuid4(),
            bot_key=AQUB_RECEIPT_BOT_KEY,
            update_id=9_005,
            payload=text_payload,
            status=TelegramReceiptStatus.PROCESSED.value,
            received_at=RECEIVED_AT,
            processed_at=RECEIVED_AT,
        )
    )
    async with archive_pg.sessions.begin() as session:
        session.add_all(excluded)

    receipt_before = await _receipt_snapshot(archive_pg)
    repository = await _discover(archive_pg)
    archive_before = await _archive_values(archive_pg)
    assert archive_before["binding_id"] == archive_pg.binding_id
    assert archive_before["source_payload_sha256"] == (
        canonical_telegram_payload_digest(archive_pg.payload)
    )
    assert archive_before["file_unique_digest"] == hashlib.sha256(
        b"synthetic-unique-large"
    ).hexdigest()
    assert archive_before["width"] == 1_280
    assert archive_before["height"] == 960
    assert archive_before["declared_size_bytes"] == 12_345
    cursor = await repository.discover(
        not_before=CUTOFF,
        cursor=None,
        limit=100,
        observed_at=RECEIVED_AT + timedelta(hours=1),
    )
    assert cursor is None
    assert await _archive_values(archive_pg) == archive_before
    assert await _receipt_snapshot(archive_pg) == receipt_before
    async with archive_pg.sessions() as session:
        status_rows = (
            await session.execute(
                select(
                    AQUBPhotoArchive.status,
                    AQUBPhotoArchive.attempt_count,
                    AQUBPhotoArchive.last_error_code,
                    TelegramReceipt.update_id,
                )
                .join(TelegramReceipt)
                .order_by(TelegramReceipt.update_id)
            )
        ).all()
        assert status_rows == [
            ("pending_download", 0, None, 9_001),
            ("dead_letter", 0, "aqub_photo_source_scope_invalid", 9_003),
            ("dead_letter", 0, "aqub_photo_source_scope_invalid", 9_004),
        ]
        assert (
            await session.scalar(select(func.count()).select_from(TelegramOutboxEvent))
            == 0
        )


@pytest.mark.anyio
async def test_0100_migration_downgrade_upgrade_round_trip(
    archive_pg: _ArchivePG,
) -> None:
    def physical_state(sync_connection: Any) -> tuple[bool, set[str]]:
        inspector = inspect(sync_connection)
        tables = set(inspector.get_table_names())
        indexes = {
            item["name"] for item in inspector.get_indexes("telegram_receipts")
        }
        return "aqub_photo_archives" in tables, indexes

    async with archive_pg.engine.begin() as connection:
        await connection.run_sync(_downgrade_0100)
    async with archive_pg.engine.connect() as connection:
        table_exists, indexes = await connection.run_sync(physical_state)
    assert table_exists is False
    assert "ix_telegram_receipts_aqub_photo_discovery" not in indexes

    async with archive_pg.engine.begin() as connection:
        await connection.run_sync(_apply_0100)
    async with archive_pg.engine.connect() as connection:
        table_exists, indexes = await connection.run_sync(physical_state)
    assert table_exists is True
    assert "ix_telegram_receipts_aqub_photo_discovery" in indexes


@pytest.mark.anyio
async def test_concurrent_discovery_is_one_row_and_then_exhausted(
    archive_pg: _ArchivePG,
) -> None:
    receipt_before = await _receipt_snapshot(archive_pg)
    repositories = (
        SqlAQUBPhotoArchiveRepository(archive_pg.sessions),
        SqlAQUBPhotoArchiveRepository(archive_pg.sessions),
    )
    cursors = await asyncio.gather(
        *(
            repository.discover(
                not_before=CUTOFF,
                cursor=None,
                limit=100,
                observed_at=RECEIVED_AT + timedelta(seconds=1),
            )
            for repository in repositories
        )
    )
    assert any(cursor is not None for cursor in cursors)
    async with archive_pg.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(AQUBPhotoArchive))
            == 1
        )
    exhausted = await asyncio.gather(
        *(
            repository.discover(
                not_before=CUTOFF,
                cursor=None,
                limit=100,
                observed_at=RECEIVED_AT + timedelta(seconds=2),
            )
            for repository in repositories
        )
    )
    assert exhausted == [None, None]
    assert await _receipt_snapshot(archive_pg) == receipt_before


@pytest.mark.anyio
async def test_claim_is_single_consumer_and_completion_purge_use_db_clock(
    archive_pg: _ArchivePG,
) -> None:
    receipt_before = await _receipt_snapshot(archive_pg)
    repository = await _discover(archive_pg)
    first, second = await asyncio.gather(
        repository.claim(not_before=CUTOFF, observed_at=RECEIVED_AT + timedelta(seconds=2)),
        repository.claim(not_before=CUTOFF, observed_at=RECEIVED_AT + timedelta(seconds=2)),
    )
    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    claim = claims[0]

    downloaded = _downloaded()
    raw = downloaded.data
    async with archive_pg.sessions() as session:
        db_before = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(db_before, datetime)
    assert await repository.complete(
        claim=claim,
        downloaded=downloaded,
        observed_at=RECEIVED_AT + timedelta(days=365),
        not_before=CUTOFF,
    ) is AQUBPhotoArchiveCompletion.ARCHIVED
    async with archive_pg.sessions() as session:
        db_after = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(db_after, datetime)
    archived = await _archive_values(archive_pg)
    assert db_before <= archived["archived_at"] <= db_after
    assert archived["purge_at"] == archived["archived_at"] + timedelta(days=7)
    assert archived["updated_at"] == archived["archived_at"]
    assert archived["raw_data"] == raw
    assert await repository.purge(
        observed_at=RECEIVED_AT + timedelta(days=365), limit=100
    ) == 0

    async with archive_pg.sessions.begin() as session:
        database_now = await session.scalar(select(func.now()))
        assert isinstance(database_now, datetime)
        backdated = database_now - timedelta(days=8)
        await session.execute(
            update(AQUBPhotoArchive)
            .where(AQUBPhotoArchive.receipt_id == archive_pg.receipt_id)
            .values(archived_at=backdated, purge_at=backdated + timedelta(days=7))
        )
    async with archive_pg.sessions() as session:
        purge_before = await session.scalar(select(func.clock_timestamp()))
    assert await repository.purge(observed_at=CUTOFF, limit=100) == 1
    async with archive_pg.sessions() as session:
        purge_after = await session.scalar(select(func.clock_timestamp()))
    purged = await _archive_values(archive_pg)
    assert purge_before <= purged["raw_purged_at"] <= purge_after
    assert purged["updated_at"] == purged["raw_purged_at"]
    assert purged["status"] == "purged"
    assert purged["raw_data"] is None
    assert purged["raw_sha256"] == downloaded.sha256
    assert purged["raw_size_bytes"] == len(raw)
    assert purged["raw_media_type"] == "image/png"
    assert await _receipt_snapshot(archive_pg) == receipt_before


@pytest.mark.anyio
async def test_old_claim_is_fenced_and_five_crashes_are_terminal(
    archive_pg: _ArchivePG,
) -> None:
    receipt_before = await _receipt_snapshot(archive_pg)
    repository = await _discover(archive_pg)
    observed = RECEIVED_AT + timedelta(seconds=2)
    tokens: set[UUID] = set()
    stale_claim = None

    for expected_attempt in range(1, 6):
        claim = await repository.claim(not_before=CUTOFF, observed_at=observed)
        assert claim is not None
        assert claim.attempt_count == expected_attempt
        assert claim.lease_generation == expected_attempt
        assert claim.lease_token not in tokens
        tokens.add(claim.lease_token)
        if stale_claim is not None:
            assert not await repository.fail(
                claim=stale_claim,
                failure=AQUBPhotoArchiveError(
                    "aqub_photo_download_failed", retryable=True
                ),
                observed_at=observed,
            )
        stale_claim = claim
        observed += AQUB_PHOTO_ARCHIVE_STALE_AFTER
        assert await repository.recover_stale(observed_at=observed, limit=100) == 1
        assert await repository.complete(
            claim=claim,
            downloaded=_downloaded(),
            observed_at=observed,
            not_before=CUTOFF,
        ) is AQUBPhotoArchiveCompletion.FENCED
        row = await _archive_values(archive_pg)
        assert row["attempt_count"] == expected_attempt
        assert row["lease_generation"] == expected_attempt
        assert row["lease_token"] is None
        assert row["locked_at"] is None
        assert row["last_error_code"] == "aqub_photo_claim_stale"
        assert row["raw_data"] is None
        assert row["status"] == (
            "dead_letter" if expected_attempt == 5 else "retry_scheduled"
        )
        observed += timedelta(microseconds=1)

    assert await repository.claim(not_before=CUTOFF, observed_at=observed) is None
    assert await _receipt_snapshot(archive_pg) == receipt_before


@pytest.mark.anyio
async def test_binding_mutation_after_discovery_dead_letters_before_claim(
    archive_pg: _ArchivePG,
) -> None:
    repository = await _discover(archive_pg)
    async with archive_pg.sessions.begin() as session:
        await session.execute(
            update(AQUBInstallationBinding)
            .where(AQUBInstallationBinding.id == archive_pg.binding_id)
            .values(alpha_user_id="9999")
        )
    assert await repository.claim(
        not_before=CUTOFF,
        observed_at=RECEIVED_AT + timedelta(seconds=2),
    ) is None
    archive = await _archive_values(archive_pg)
    assert archive["status"] == "dead_letter"
    assert archive["last_error_code"] == "aqub_photo_source_scope_invalid"
    assert archive["attempt_count"] == 0
    assert archive["raw_data"] is None


@pytest.mark.anyio
async def test_source_digest_is_revalidated_before_success_cas(
    archive_pg: _ArchivePG,
) -> None:
    repository = await _discover(archive_pg)
    claim = await repository.claim(
        not_before=CUTOFF,
        observed_at=RECEIVED_AT + timedelta(seconds=2),
    )
    assert claim is not None
    mutated = copy.deepcopy(archive_pg.payload)
    mutated["message"]["caption"] = "mutated after claim"
    async with archive_pg.sessions.begin() as session:
        await session.execute(
            update(TelegramReceipt)
            .where(TelegramReceipt.id == archive_pg.receipt_id)
            .values(payload=mutated)
        )
    assert await repository.complete(
        claim=claim,
        downloaded=_downloaded(),
        observed_at=RECEIVED_AT + timedelta(seconds=3),
        not_before=CUTOFF,
    ) is AQUBPhotoArchiveCompletion.SOURCE_REJECTED
    archive = await _archive_values(archive_pg)
    assert archive["status"] == "dead_letter"
    assert archive["last_error_code"] == "aqub_photo_source_changed"
    assert archive["raw_data"] is None
    assert archive["raw_sha256"] is None


def _pending_values(pg: _ArchivePG) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "receipt_id": pg.receipt_id,
        "binding_id": pg.binding_id,
        "source_payload_sha256": canonical_telegram_payload_digest(pg.payload),
        "file_unique_digest": hashlib.sha256(
            b"synthetic-unique-large"
        ).hexdigest(),
        "width": 1_280,
        "height": 960,
        "declared_size_bytes": 12_345,
        "status": "pending_download",
        "attempt_count": 0,
        "lease_generation": 0,
        "available_at": RECEIVED_AT,
    }


async def _assert_rejected(pg: _ArchivePG, values: dict[str, Any]) -> None:
    async with pg.sessions() as session:
        with pytest.raises(IntegrityError):
            await session.execute(insert(AQUBPhotoArchive).values(**values))
            await session.commit()
        await session.rollback()


@pytest.mark.anyio
async def test_postgres_checks_reject_unknown_partial_and_zero_attempt_terminal_rows(
    archive_pg: _ArchivePG,
) -> None:
    now = RECEIVED_AT
    raw = b"\xff\xd8\xffarchive"
    archived = {
        **_pending_values(archive_pg),
        "status": "archived",
        "attempt_count": 1,
        "lease_generation": 1,
        "available_at": None,
        "raw_data": raw,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_size_bytes": len(raw),
        "raw_media_type": "image/jpeg",
        "archived_at": now,
        "purge_at": now + timedelta(days=7),
    }
    invalid_rows = [
        {**_pending_values(archive_pg), "file_unique_digest": None},
        {**_pending_values(archive_pg), "width": None},
        {
            **_pending_values(archive_pg),
            "last_error_at": now,
        },
        {**archived, "attempt_count": 0, "lease_generation": 0},
        {**archived, "raw_sha256": None},
        {**archived, "raw_size_bytes": len(raw) + 1},
        {
            **_pending_values(archive_pg),
            "status": "dead_letter",
            "available_at": None,
            "file_unique_digest": None,
            "width": None,
            "height": None,
            "declared_size_bytes": 1,
            "last_error_code": "aqub_photo_source_invalid",
            "last_error_at": now,
        },
        {
            **_pending_values(archive_pg),
            "status": "dead_letter",
            "available_at": None,
            "file_unique_digest": None,
            "width": -1,
            "height": None,
            "declared_size_bytes": None,
            "last_error_code": "aqub_photo_source_invalid",
            "last_error_at": now,
        },
        {
            **archived,
            "status": "purged",
            "attempt_count": 0,
            "lease_generation": 0,
            "raw_data": None,
            "raw_purged_at": now + timedelta(days=7),
        },
    ]
    for values in invalid_rows:
        values["id"] = uuid4()
        await _assert_rejected(archive_pg, values)

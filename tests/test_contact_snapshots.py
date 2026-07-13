"""Contact snapshot contract, API and PostgreSQL integration tests."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.contact_snapshot_routes import get_contact_snapshot_service
from app.application.contact_snapshots import (
    ContactSnapshotContact,
    ContactSnapshotCounts,
    ContactSnapshotError,
    ContactSnapshotResult,
    ImportContactSnapshotCommand,
    ImportContactSnapshotService,
)
from app.core.config import get_settings
from app.infrastructure.contact_snapshots import SqlContactSnapshotWriter
from app.main import app
from app.models.domain import Account, AuditLog, Base, Person, PersonIdentity


class RecordingWriter:
    """Application port fake that records only the normalized command."""

    def __init__(self) -> None:
        self.command: ImportContactSnapshotCommand | None = None

    async def write(
        self,
        command: ImportContactSnapshotCommand,
    ) -> ContactSnapshotResult:
        self.command = command
        return ContactSnapshotResult(
            account_alias=command.account_alias,
            revision_sha256="a" * 64,
            counts=ContactSnapshotCounts(
                received=len(command.contacts),
                created=len(command.contacts),
                updated=0,
                unchanged=0,
            ),
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def contact_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingWriter]:
    monkeypatch.setenv("ADMIN_BEARER_TOKEN", "admin-token")
    get_settings.cache_clear()
    writer = RecordingWriter()
    service = ImportContactSnapshotService(writer)
    app.dependency_overrides[get_contact_snapshot_service] = lambda: service
    yield writer
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_contact_snapshot_normalizes_input_before_persistence() -> None:
    writer = RecordingWriter()
    service = ImportContactSnapshotService(writer)

    await service.execute(
        ImportContactSnapshotCommand(
            account_alias=" telegram_mark_ru ",
            revision=" snapshot-1 ",
            contacts=(
                ContactSnapshotContact(
                    telegram_user_id=" 12345 ",
                    display_name=" Мама ",
                    username=" @mama ",
                ),
            ),
        )
    )

    assert writer.command == ImportContactSnapshotCommand(
        account_alias="telegram_mark_ru",
        revision="snapshot-1",
        contacts=(
            ContactSnapshotContact(
                telegram_user_id="12345",
                display_name="Мама",
                username="mama",
            ),
        ),
    )


@pytest.mark.anyio
async def test_contact_snapshot_rejects_duplicate_ids_before_persistence() -> None:
    writer = RecordingWriter()
    service = ImportContactSnapshotService(writer)

    with pytest.raises(ContactSnapshotError, match="unique"):
        await service.execute(
            ImportContactSnapshotCommand(
                account_alias="telegram_mark_ru",
                revision="snapshot-1",
                contacts=(
                    ContactSnapshotContact("12345", "Мама"),
                    ContactSnapshotContact(" 12345 ", "Мама"),
                ),
            )
        )

    assert writer.command is None


@pytest.mark.anyio
async def test_contact_snapshot_requires_admin_bearer(
    contact_api: RecordingWriter,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.post(
            "/admin/contact-snapshots",
            json={
                "account_alias": "telegram_mark_ru",
                "revision": "snapshot-1",
                "contacts": [
                    {
                        "telegram_user_id": "12345",
                        "display_name": "Мама",
                    }
                ],
            },
        )
        wrong = await client.post(
            "/admin/contact-snapshots",
            headers={"Authorization": "Bearer wrong"},
            json={
                "account_alias": "telegram_mark_ru",
                "revision": "snapshot-1",
                "contacts": [
                    {
                        "telegram_user_id": "12345",
                        "display_name": "Мама",
                    }
                ],
            },
        )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert contact_api.command is None


@pytest.mark.anyio
async def test_contact_snapshot_returns_counts_without_contact_data(
    contact_api: RecordingWriter,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/contact-snapshots",
            headers={"Authorization": "Bearer admin-token"},
            json={
                "account_alias": " telegram_mark_ru ",
                "revision": " snapshot-1 ",
                "contacts": [
                    {
                        "telegram_user_id": "12345",
                        "display_name": "Мама",
                        "username": "@mama",
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "account_alias": "telegram_mark_ru",
        "revision_sha256": "a" * 64,
        "counts": {
            "received": 1,
            "created": 1,
            "updated": 0,
            "unchanged": 0,
        },
    }
    assert "Мама" not in response.text
    assert "12345" not in response.text
    assert contact_api.command is not None
    assert contact_api.command.contacts[0].username == "mama"


@pytest.mark.anyio
async def test_postgres_contact_snapshot_is_idempotent_and_account_scoped() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL contact snapshot test")

    schema = f"contact_snapshot_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            ru_alias = f"telegram_ru_{uuid4().hex}"
            by_alias = f"telegram_by_{uuid4().hex}"
            session.add_all(
                [
                    Account(
                        alias=ru_alias,
                        owner="mark",
                        country_code="RU",
                        account_type="telegram",
                    ),
                    Account(
                        alias=by_alias,
                        owner="mark",
                        country_code="BY",
                        account_type="telegram",
                    ),
                ]
            )
            await session.commit()

            service = ImportContactSnapshotService(SqlContactSnapshotWriter(session))
            ru_first = await service.execute(
                ImportContactSnapshotCommand(
                    account_alias=ru_alias,
                    revision="snapshot-1",
                    contacts=(
                        ContactSnapshotContact(
                            telegram_user_id="12345",
                            display_name="Мама RU",
                            username="mama_ru",
                        ),
                    ),
                )
            )
            assert ru_first.counts.created == 1

            ru_replay = await service.execute(
                ImportContactSnapshotCommand(
                    account_alias=ru_alias,
                    revision="snapshot-1",
                    contacts=(
                        ContactSnapshotContact(
                            telegram_user_id="12345",
                            display_name="Мама RU",
                            username="mama_ru",
                        ),
                    ),
                )
            )
            assert ru_replay.counts.unchanged == 1

            by_first = await service.execute(
                ImportContactSnapshotCommand(
                    account_alias=by_alias,
                    revision="snapshot-1",
                    contacts=(
                        ContactSnapshotContact(
                            telegram_user_id="12345",
                            display_name="Мама BY",
                            username="mama_by",
                        ),
                    ),
                )
            )
            assert by_first.counts.created == 1

            ru_update = await service.execute(
                ImportContactSnapshotCommand(
                    account_alias=ru_alias,
                    revision="snapshot-2",
                    contacts=(
                        ContactSnapshotContact(
                            telegram_user_id="12345",
                            display_name="Мама — личное",
                            username="mama_ru",
                        ),
                    ),
                )
            )
            assert ru_update.counts.updated == 1

            identities_result = await session.execute(
                select(PersonIdentity, Person, Account)
                .join(Person, PersonIdentity.person_id == Person.id)
                .join(Account, PersonIdentity.account_id == Account.id)
                .where(PersonIdentity.platform_user_id == "12345")
                .order_by(Account.alias)
            )
            identity_rows = identities_result.all()
            assert len(identity_rows) == 2
            names_by_alias = {
                account.alias: person.display_name
                for _identity, person, account in identity_rows
            }
            assert names_by_alias == {
                by_alias: "Мама BY",
                ru_alias: "Мама — личное",
            }

            with pytest.raises(ContactSnapshotError, match="same contact snapshot"):
                await service.execute(
                    ImportContactSnapshotCommand(
                        account_alias=ru_alias,
                        revision="snapshot-2",
                        contacts=(
                            ContactSnapshotContact(
                                telegram_user_id="12345",
                                display_name="Conflicting name",
                                username="mama_ru",
                            ),
                        ),
                    )
                )

            audit_result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "telegram_contact_snapshot_imported"
                )
            )
            audits = audit_result.scalars().all()
            assert len(audits) == 4
            serialized_audits = str([audit.metadata_ for audit in audits])
            assert "Мама" not in serialized_audits
            assert "12345" not in serialized_audits
            assert "mama_ru" not in serialized_audits
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()

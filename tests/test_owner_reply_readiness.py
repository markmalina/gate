"""Real SQL isolation checks; PostgreSQL cases run in the mandatory CI DB lane."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import jsonschema
import pytest
from app.application.owner_reply_readiness import (
    OWNER_REPLY_READINESS_INPUT_SCHEMA,
    OWNER_REPLY_READINESS_OUTPUT_SCHEMA,
    OwnerReplyReadinessError,
    build_owner_reply_readiness,
    build_reply_sequence_readiness,
    reply_sequence_worker_metadata,
)
from app.application.worker_health import REPLY_SEQUENCE_WORKER, WorkerHealthRegistry
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)
from app.models.alpha import AgentInstallationModel
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import (
    DEFAULT_PROJECT_ID,
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TELEGRAM_MARK_RU_ACCOUNT_ID,
    Account,
    Base,
    Bot,
    Conversation,
    Message,
    Project,
    ReplyDraft,
    ReplyTask,
    SendAttempt,
    TelegramBusinessConnection,
    TelegramReceipt,
)
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, insert, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
RECIPIENT = "20202"  # Synthetic owner fixture, never a real Telegram identity.
_MODELS = (
    Account,
    Bot,
    Project,
    Conversation,
    Message,
    ReplyTask,
    ReplyDraft,
    SendAttempt,
    TelegramBusinessConnection,
    Workspace,
    TransportConnector,
    Dialog,
    AgentDefinitionModel,
    InstructionRevisionModel,
    AgentDeploymentModel,
    AgentInstallationModel,
    AgentSessionModel,
    AgentTurnModel,
    CanonicalMessage,
    TelegramReceipt,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _principal(*, projects: frozenset[UUID] | None = None) -> Principal:
    return Principal(
        subject="synthetic-owner",
        auth_method="oauth",
        scopes=frozenset({"gate:inbox:read", "gate:status:read"}),
        access_scope=AccessScope(
            workspace_id="mark-personal",
            account_ids=frozenset({TELEGRAM_MARK_RU_ACCOUNT_ID, TELEGRAM_MARK_BY_ACCOUNT_ID}),
            project_ids=projects or frozenset({DEFAULT_PROJECT_ID}),
        ),
    )


class _SQLiteSession:
    """Execute production SQL against a disposable SQLite database, not fake rows."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return self.session.connection().execute(statement)

    async def scalar(self, statement: Any) -> Any:
        return (await self.execute(statement)).scalar()

    async def commit(self) -> None:
        self.session.commit()

    @property
    def no_autoflush(self) -> Any:
        return self.session.no_autoflush


@asynccontextmanager
async def _database(backend: str) -> AsyncIterator[AsyncSession]:
    if backend == "sqlite":
        engine = create_engine("sqlite://")
        metadata = MetaData()
        # Keep every real column and execute real production SELECTs. SQLite
        # omits PostgreSQL-only DDL constraints; the second parametrization
        # exercises actual Base metadata, JSONB, FK/check constraints in CI.
        for model in _MODELS:
            Table(
                model.__tablename__,
                metadata,
                *(
                    Column(
                        column.name,
                        JSON() if isinstance(column.type, JSONB) else column.type,
                        nullable=True,
                    )
                    for column in model.__table__.columns
                ),
            )
        metadata.create_all(engine)
        try:
            with Session(engine) as sync_session:
                yield cast(AsyncSession, _SQLiteSession(sync_session))
        finally:
            engine.dispose()
        return
    database_url = Settings(_env_file=None).database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL owner-readiness isolation checks")
    schema = f"owner_reply_readiness_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def _seed(session: AsyncSession, *, backend: str) -> dict[str, UUID | str]:
    ids: dict[str, UUID | str] = {
        name: uuid4()
        for name in (
            "bot",
            "conversation",
            "message",
            "task",
            "workspace",
            "connector",
            "dialog",
            "receipt",
            "canonical",
            "definition",
            "revision",
            "deployment",
            "installation",
            "session",
        )
    }
    ids["connection"] = "synthetic-owner-ru-connection"
    await session.execute(
        insert(Project).values(
            id=DEFAULT_PROJECT_ID,
            alias="unclassified",
            name="Synthetic project",
            adapter_type="telegram",
            is_active=True,
        )
    )
    await session.execute(insert(Bot).values(id=ids["bot"], username="synthetic_gate"))
    for account_id, alias, identity, connection in (
        (TELEGRAM_MARK_RU_ACCOUNT_ID, "telegram_mark_ru", 10101, ids["connection"]),
        (
            TELEGRAM_MARK_BY_ACCOUNT_ID,
            "telegram_mark_by",
            int(RECIPIENT),
            "synthetic-owner-by-connection",
        ),
    ):
        await session.execute(
            insert(Account).values(
                id=account_id,
                alias=alias,
                country_code=alias[-2:].upper(),
                owner="synthetic-owner",
                telegram_user_id=identity,
                account_type="telegram_business",
                permissions={},
                is_active=True,
                read_enabled=True,
                send_enabled=True,
            )
        )
        await session.execute(
            insert(TelegramBusinessConnection).values(
                account_id=account_id,
                business_connection_id=connection,
                telegram_user_id=identity,
                user_chat_id=identity,
                rights={"can_reply": True},
                is_enabled=True,
            )
        )
    await session.execute(
        insert(Workspace).values(
            id=ids["workspace"], key="mark-personal", display_name="Synthetic owner", is_active=True
        )
    )
    await session.execute(
        insert(TransportConnector).values(
            id=ids["connector"],
            workspace_id=ids["workspace"],
            connector_key="telegram_business",
            connector_kind="telegram_business",
            is_enabled=True,
        )
    )
    await session.execute(
        insert(Dialog).values(
            id=ids["dialog"],
            workspace_id=ids["workspace"],
            connector_id=ids["connector"],
            provider_dialog_id=RECIPIENT,
            dialog_kind="private",
        )
    )
    await session.execute(
        insert(TelegramReceipt).values(
            id=ids["receipt"],
            bot_key="telegram_business",
            update_id=777,
            payload={"private_raw": "PRIVATE_RAW_SENTINEL"},
            status="processed",
            received_at=NOW,
            processed_at=NOW,
        )
    )
    await session.execute(
        insert(CanonicalMessage).values(
            id=ids["canonical"],
            workspace_id=ids["workspace"],
            dialog_id=ids["dialog"],
            receipt_id=ids["receipt"],
            provider_message_id="77",
            provider_sender_id=RECIPIENT,
            direction="inbound",
            text_content="PRIVATE_CANONICAL_SENTINEL",
            occurred_at=NOW,
        )
    )
    await session.execute(
        insert(Conversation).values(
            id=ids["conversation"],
            bot_id=ids["bot"],
            account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
            project_id=DEFAULT_PROJECT_ID,
            chat_id=RECIPIENT,
            business_connection_id=ids["connection"],
            title="PRIVATE_TITLE_SENTINEL",
            status="open",
            requires_reply=True,
            last_message_at=NOW,
            last_inbound_at=NOW if backend == "postgres" else None,
        )
    )
    await session.execute(
        insert(Message).values(
            id=ids["message"],
            bot_id=ids["bot"],
            account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
            project_id=DEFAULT_PROJECT_ID,
            canonical_message_id=ids["canonical"],
            direction="inbound",
            source="telegram",
            event_type="business_message",
            chat_id=RECIPIENT,
            sender_id=RECIPIENT,
            text="PRIVATE_MESSAGE_SENTINEL",
            payload={"private": "PRIVATE_PAYLOAD_SENTINEL"},
            telegram_message_id=77,
            business_connection_id=ids["connection"],
            received_at=NOW,
        )
    )
    await session.execute(
        insert(ReplyTask).values(
            id=ids["task"],
            account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
            project_id=DEFAULT_PROJECT_ID,
            conversation_id=ids["conversation"],
            trigger_message_id=ids["message"],
            status="needs_reply",
            reason="inbound_human_text",
            created_at=NOW,
        )
    )
    await session.commit()
    return ids


async def _read(session: AsyncSession, **kwargs: Any) -> dict[str, Any]:
    return await build_owner_reply_readiness(
        session=session,
        principal=_principal(),
        account_alias="RU",
        recipient_chat_id=RECIPIENT,
        now=NOW,
        **kwargs,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_exact_metadata_uses_real_sql_without_private_columns_or_effects(
    backend: str,
) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        if isinstance(session, _SQLiteSession):
            session.statements.clear()
        result = await _read(session, provider_message_id="77")
        jsonschema.validate(result, OWNER_REPLY_READINESS_OUTPUT_SCHEMA)
        assert result["task"]["task_id"] == str(ids["task"])
        assert result["task"]["exact_provider_message_match"] is True
        assert result["task"]["receipt_processed"] is True
        assert result["task"]["correlated_agent_turn_count"] == 0
        assert result["task"]["mayak_reason_code_persisted"] is False
        # SQLite's JSONB result conversion returns integer 1 for JSON true;
        # production PostgreSQL preserves bool. The helper fails closed for
        # this reduced local backend, while CI checks the real positive path.
        assert result["seed_observation"]["eligible_observed"] is (backend == "postgres")
        assert result["seed_observation"]["guarantees_zero_provider_calls"] is False
        assert result["reply_sequence"]["worker"]["available"] is False
        assert "PRIVATE_" not in json.dumps(result)
        if isinstance(session, _SQLiteSession):
            for statement in session.statements:
                sql = str(statement).lower()
                assert sql.startswith("select")
                for protected in (
                    "messages.text",
                    "messages.payload",
                    "canonical_messages.text_content",
                    "telegram_receipts.payload",
                    "conversations.title",
                    "accounts.owner",
                ):
                    assert protected not in sql
        absent = await _read(session, provider_message_id="999")
        assert absent["status"] == "no_task" and absent["task"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cross_account_project_recipient_and_alias_fail_closed(backend: str) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        with pytest.raises(OwnerReplyReadinessError, match="exact permitted binding") as error:
            await build_owner_reply_readiness(
                session=session,
                principal=_principal(),
                account_alias="RU",
                recipient_chat_id="30303",
                now=NOW,
            )
        assert error.value.code == "scope_denied"
        await session.execute(
            update(Account)
            .where(Account.id == TELEGRAM_MARK_RU_ACCOUNT_ID)
            .values(country_code="BY", alias="spoofed_by")
        )
        with pytest.raises(OwnerReplyReadinessError) as error:
            await _read(session)
        assert error.value.code == "owner_binding_unavailable"
        await session.execute(
            update(Account)
            .where(Account.id == TELEGRAM_MARK_RU_ACCOUNT_ID)
            .values(country_code="RU", alias="telegram_mark_ru")
        )
        await session.execute(
            update(ReplyTask)
            .where(ReplyTask.id == ids["task"])
            .values(account_id=TELEGRAM_MARK_BY_ACCOUNT_ID)
        )
        assert (await _read(session))["task"] is None
        await session.execute(
            update(ReplyTask)
            .where(ReplyTask.id == ids["task"])
            .values(account_id=TELEGRAM_MARK_RU_ACCOUNT_ID)
        )
        other_project = uuid4()
        await session.execute(
            insert(Project).values(
                id=other_project,
                alias="other",
                name="Synthetic other project",
                adapter_type="manual",
            )
        )
        await session.execute(
            update(Message).where(Message.id == ids["message"]).values(project_id=other_project)
        )
        principal = _principal(projects=frozenset({DEFAULT_PROJECT_ID, other_project}))
        result = await build_owner_reply_readiness(
            session=session,
            principal=principal,
            account_alias="RU",
            recipient_chat_id=RECIPIENT,
            now=NOW,
        )
        assert result["task"] is None
        no_project = await build_owner_reply_readiness(
            session=session,
            principal=_principal(projects=frozenset({other_project})),
            account_alias="RU",
            recipient_chat_id=RECIPIENT,
            now=NOW,
        )
        assert no_project["task"] is None
        assert no_project["seed_observation"]["eligible_observed"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_binding_blocks_seed_and_missing_mapping_is_not_unbound(backend: str) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        await _seed_agent(session, ids)
        result = await _read(session)
        assert result["seed_observation"]["state"] == "blocked"
        assert result["seed_observation"]["active_binding_count"] == 1
        assert result["seed_observation"]["eligible_observed"] is False
        await session.execute(
            update(TransportConnector)
            .where(TransportConnector.id == ids["connector"])
            .values(is_enabled=False)
        )
        with pytest.raises(OwnerReplyReadinessError) as error:
            await _read(session)
        assert error.value.code == "canonical_binding_unavailable"


async def _seed_agent(session: AsyncSession, ids: dict[str, UUID | str]) -> None:
    await session.execute(
        insert(AgentDefinitionModel).values(
            id=ids["definition"],
            workspace_id=ids["workspace"],
            agent_key="oko",
            display_name="Synthetic agent",
        )
    )
    await session.execute(
        insert(InstructionRevisionModel).values(
            id=ids["revision"],
            workspace_id=ids["workspace"],
            agent_definition_id=ids["definition"],
            revision_key="other-profile",
            source_repository="synthetic/repo",
            source_path="synthetic",
            source_commit_sha="a" * 40,
            content_sha256="b" * 64,
            schema_version="v1",
            validation_state="pending",
        )
    )
    await session.execute(
        insert(AgentDeploymentModel).values(
            id=ids["deployment"],
            workspace_id=ids["workspace"],
            agent_definition_id=ids["definition"],
            instruction_revision_id=ids["revision"],
            deployment_key="synthetic",
            runtime_profile="different-profile",
            model_profile="synthetic",
            policy_version="v1",
            state="staged",
        )
    )
    await session.execute(
        insert(AgentInstallationModel).values(
            id=ids["installation"],
            workspace_id=ids["workspace"],
            connector_id=ids["connector"],
            dialog_id=ids["dialog"],
            deployment_id=ids["deployment"],
            instruction_revision_id=ids["revision"],
            installation_key="synthetic-owner",
            dialog_label="PRIVATE_DIALOG_LABEL",
            state="active",
        )
    )


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_correlated_turn_observation_never_claims_zero_model_calls(backend: str) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        await _seed_agent(session, ids)
        await session.execute(
            insert(AgentSessionModel).values(
                id=ids["session"],
                workspace_id=ids["workspace"],
                dialog_id=ids["dialog"],
                deployment_id=ids["deployment"],
                instruction_revision_id=ids["revision"],
                session_key="synthetic",
            )
        )
        await session.execute(
            insert(AgentTurnModel).values(
                workspace_id=ids["workspace"],
                session_id=ids["session"],
                trigger_message_id=ids["canonical"],
                instruction_revision_id=ids["revision"],
                context_message_ids=["PRIVATE_AGENT_CONTEXT"],
                status="running",
            )
        )
        result = await _read(session, provider_message_id="77")
        assert result["task"]["correlated_agent_turn_count"] == 1
        assert "model_calls" not in json.dumps(result)
        assert "PRIVATE_" not in json.dumps(result)
        await session.execute(
            update(TelegramReceipt)
            .where(TelegramReceipt.id == ids["receipt"])
            .values(status="processing")
        )
        assert (await _read(session, provider_message_id="77"))["task"][
            "receipt_processed"
        ] is False


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scoped_pending_counts_and_worker_heartbeat_projection(backend: str) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        sequence_id = str(uuid4())
        drafts = [uuid4() for _ in range(4)]
        for position, draft_id in enumerate(drafts):
            metadata: Any = {
                "id": sequence_id,
                "state": "scheduled",
                "workspace_id": "mark-personal",
            }
            if position == 3:
                metadata = {"state": "scheduled"}
            await session.execute(
                insert(ReplyDraft).values(
                    id=draft_id,
                    account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
                    project_id=DEFAULT_PROJECT_ID,
                    reply_task_id=ids["task"],
                    conversation_id=ids["conversation"],
                    trigger_message_id=ids["message"],
                    status="generated",
                    source="synthetic",
                    draft_text="PRIVATE_DRAFT_SENTINEL",
                    prompt_context={
                        "reply_sequence": metadata,
                        "private_history": "PRIVATE_HISTORY_SENTINEL",
                    },
                )
            )
        for draft_id, status in ((drafts[1], "sent"), (drafts[2], "unknown")):
            await session.execute(
                insert(SendAttempt).values(
                    reply_draft_id=draft_id,
                    reply_task_id=ids["task"],
                    conversation_id=ids["conversation"],
                    account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
                    project_id=DEFAULT_PROJECT_ID,
                    principal_subject="synthetic-owner",
                    status=status,
                    account_label="RU",
                    target_chat_id=RECIPIENT,
                    business_connection_id=ids["connection"],
                )
            )
        registry = WorkerHealthRegistry()
        registry.register(REPLY_SEQUENCE_WORKER, enabled=True)
        registry.record_success(REPLY_SEQUENCE_WORKER, observed_at=NOW - timedelta(seconds=4))
        result = await _read(session, worker_registry=registry)
        sequence = result["reply_sequence"]
        assert sequence["pending_items"] == 2
        assert sequence["pending_sequences"] == 1
        assert sequence["unresolved_attempts"] == 1
        assert sequence["malformed_items"] == 1
        assert sequence["worker"]["state"] == "healthy"
        assert sequence["worker"]["heartbeat_age_seconds"] == 4
        assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "inconsistency",
    [
        "workspace",
        "task_account",
        "task_project",
        "conversation_account",
        "conversation_project",
        "attempt_account",
        "attempt_project",
    ],
)
async def test_inconsistent_related_binding_never_hides_scoped_sequence(
    backend: str, inconsistency: str
) -> None:
    async with _database(backend) as session:
        ids = await _seed(session, backend=backend)
        draft_id = uuid4()
        foreign_project = uuid4()
        await session.execute(
            insert(Project).values(
                id=foreign_project,
                alias="synthetic-foreign",
                name="PRIVATE_FOREIGN_PROJECT_SENTINEL",
                adapter_type="telegram",
                is_active=True,
            )
        )
        sequence_metadata = {
            "id": str(uuid4()),
            "state": "scheduled",
            "workspace_id": (
                "foreign-workspace" if inconsistency == "workspace" else "mark-personal"
            ),
            "position": 0,
            "scheduled_at": NOW.isoformat(),
            "interval_seconds": 60,
            "expected_account": "RU",
            "expected_recipient_chat_id": RECIPIENT,
            "principal_subject": "synthetic-owner",
        }
        await session.execute(
            insert(ReplyDraft).values(
                id=draft_id,
                account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
                project_id=DEFAULT_PROJECT_ID,
                reply_task_id=ids["task"],
                conversation_id=ids["conversation"],
                trigger_message_id=ids["message"],
                status="generated",
                source="synthetic",
                draft_text="PRIVATE_DRAFT_SENTINEL",
                prompt_context={"reply_sequence": sequence_metadata},
            )
        )
        if inconsistency.startswith("task_"):
            values = (
                {"account_id": TELEGRAM_MARK_BY_ACCOUNT_ID}
                if inconsistency == "task_account"
                else {"project_id": foreign_project}
            )
            await session.execute(
                update(ReplyTask).where(ReplyTask.id == ids["task"]).values(**values)
            )
        elif inconsistency.startswith("conversation_"):
            values = (
                {"account_id": TELEGRAM_MARK_BY_ACCOUNT_ID}
                if inconsistency == "conversation_account"
                else {"project_id": foreign_project}
            )
            await session.execute(
                update(Conversation).where(Conversation.id == ids["conversation"]).values(**values)
            )
        elif inconsistency.startswith("attempt_"):
            await session.execute(
                insert(SendAttempt).values(
                    reply_draft_id=draft_id,
                    reply_task_id=ids["task"],
                    conversation_id=ids["conversation"],
                    account_id=(
                        TELEGRAM_MARK_BY_ACCOUNT_ID
                        if inconsistency == "attempt_account"
                        else TELEGRAM_MARK_RU_ACCOUNT_ID
                    ),
                    project_id=(
                        foreign_project
                        if inconsistency == "attempt_project"
                        else DEFAULT_PROJECT_ID
                    ),
                    principal_subject="synthetic-owner",
                    status="unknown",
                    account_label="RU",
                    target_chat_id="30303",
                    business_connection_id="PRIVATE_FOREIGN_CONNECTION_SENTINEL",
                )
            )
        result = await build_reply_sequence_readiness(
            session=session, principal=_principal(), worker_registry=None, now=NOW
        )
        has_attempt = inconsistency.startswith("attempt_")
        assert result["pending_items"] == (0 if has_attempt else 1)
        assert result["pending_sequences"] == (0 if has_attempt else 1)
        assert result["unresolved_attempts"] == (1 if has_attempt else 0)
        assert result["malformed_items"] == 1
        serialized = json.dumps(result)
        assert "PRIVATE_" not in serialized
        assert "foreign-workspace" not in serialized
        assert str(draft_id) not in serialized
        assert str(foreign_project) not in serialized

        # Moving the draft itself outside the granted project excludes it;
        # related-row defects never expand the scope to foreign drafts.
        await session.execute(
            update(ReplyDraft).where(ReplyDraft.id == draft_id).values(project_id=foreign_project)
        )
        outside_scope = await build_reply_sequence_readiness(
            session=session, principal=_principal(), worker_registry=None, now=NOW
        )
        assert {key: value for key, value in outside_scope.items() if key != "worker"} == {
            "pending_items": 0,
            "pending_sequences": 0,
            "unresolved_attempts": 0,
            "malformed_items": 0,
        }


class _NoDatabase:
    no_autoflush = nullcontext()

    async def execute(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("Invalid scope or input must fail before SQL")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "missing_account",
        "empty_project",
        "workspace",
        "scope",
        "alias",
        "group",
        "zero",
        "unicode",
        "leading_zero",
        "overflow",
        "provider",
    ],
)
async def test_invalid_scope_and_input_fail_before_sql(case: str) -> None:
    principal = _principal()
    alias, recipient, provider = "RU", RECIPIENT, None
    if case == "missing_account":
        principal = replace(
            principal,
            access_scope=replace(
                principal.access_scope, account_ids=frozenset({TELEGRAM_MARK_RU_ACCOUNT_ID})
            ),
        )
    elif case == "empty_project":
        principal = replace(
            principal, access_scope=replace(principal.access_scope, project_ids=frozenset())
        )
    elif case == "workspace":
        principal = replace(
            principal, access_scope=replace(principal.access_scope, workspace_id="other-workspace")
        )
    elif case == "scope":
        principal = replace(principal, scopes=frozenset({"gate:status:read"}))
    elif case == "alias":
        alias = "ru"
    elif case == "provider":
        provider = "0"
    else:
        recipient = {
            "group": "-100123",
            "zero": "0",
            "unicode": "２０２０２",
            "leading_zero": "020202",
            "overflow": str(1 << 52),
        }[case]
    with pytest.raises(OwnerReplyReadinessError):
        await build_owner_reply_readiness(
            session=cast(AsyncSession, _NoDatabase()),
            principal=principal,
            account_alias=alias,
            recipient_chat_id=recipient,
            provider_message_id=provider,
            now=NOW,
        )


def test_input_schema_is_exact_and_private_user_bounded() -> None:
    jsonschema.validate(
        {"account_alias": "RU", "recipient_chat_id": RECIPIENT}, OWNER_REPLY_READINESS_INPUT_SCHEMA
    )
    for value in (
        {"account_alias": "ru", "recipient_chat_id": RECIPIENT},
        {"account_alias": "RU", "recipient_chat_id": "-100"},
        {"account_alias": "RU", "recipient_chat_id": RECIPIENT, "marker": "x"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, OWNER_REPLY_READINESS_INPUT_SCHEMA)


def test_worker_health_unavailable_disabled_and_stale_remain_distinct() -> None:
    assert reply_sequence_worker_metadata(None, now=NOW)["state"] is None
    registry = WorkerHealthRegistry()
    registry.register(REPLY_SEQUENCE_WORKER, enabled=False)
    assert reply_sequence_worker_metadata(registry, now=NOW)["state"] == "disabled"
    active = WorkerHealthRegistry()
    active.register(REPLY_SEQUENCE_WORKER, enabled=True)
    assert reply_sequence_worker_metadata(active, now=NOW)["state"] == "starting"
    active.record_success(REPLY_SEQUENCE_WORKER, observed_at=NOW - timedelta(seconds=61))
    stale = reply_sequence_worker_metadata(active, now=NOW)
    assert stale["state"] == "degraded" and stale["last_error_code"] == "worker_heartbeat_stale"

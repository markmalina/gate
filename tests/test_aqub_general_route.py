"""Focused exact-route proofs for the Kakhovich General Cube Gate seam."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.aqub_live import AQUBLiveIngressAdapter
from app.application.aqub_transport import (
    AQUBTransportBinding,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_general_route import (
    AQUBGeneralRouteStatus,
    general_route_evidence_from_receipt,
    register_aqub_general_route_hold,
    select_aqub_inbound_binding,
    transition_aqub_general_route,
)
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.aqub import AQUBGeneralRoute, AQUBInstallationBinding
from app.models.domain import TelegramReceipt, TelegramReceiptStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
SIGNING_KEY = b"general-route-test-signing-key-32-bytes"


class _Store:
    def __init__(self) -> None:
        self.calls = 0

    async def accept(self, command):
        from app.application.aqub_transport import (
            AQUBDurableAcceptance,
            AQUBReceiptStatus,
            AQUBStoreDisposition,
        )

        self.calls += 1
        return AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id=str(uuid4()),
            root_event_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id="synthetic:cause",
        )


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def post_envelope(self, *, raw_body: bytes, signature: str):
        self.calls += 1
        return {"ok": True, "result": {"kind": "general", "reply_text": "ok"}}


def _binding(*, group: str, actor: str) -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id=group,
        ace_user_id="__unbound_ace__",
        alpha_user_id=actor,
        ready_at=NOW,
        receipt_signing_key=SIGNING_KEY,
    )


def _inbound(*, group: str, actor: str) -> AQUBTransportInbound:
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id="41",
        chat_id=group,
        actor_id=actor,
        command="Свободный вопрос",
        payload={
            "update_id": 41,
            "message": {
                "message_id": 42,
                "date": int(NOW.timestamp()),
                "chat": {"id": -43, "type": "supergroup"},
                "from": {"id": 44, "is_bot": False},
                "text": "Свободный вопрос",
            },
        },
        occurred_at=NOW,
        received_at=NOW,
        content_kind=AQUBTransportContentKind.TEXT,
    )


@pytest.mark.anyio
async def test_exact_group_and_actor_forward_once() -> None:
    store, runtime = _Store(), _Runtime()
    result = await AQUBLiveIngressAdapter(
        binding=_binding(group="group:general", actor="actor:general"),
        store=store,
        runtime=runtime,
    ).handle(_inbound(group="group:general", actor="actor:general"))
    assert result.accepted is True
    assert result.downstream == {
        "ok": True,
        "result": {"kind": "general", "reply_text": "ok"},
    }
    assert store.calls == runtime.calls == 1


def test_terminal_receipt_projects_private_ids_without_repr_exposure() -> None:
    receipt = TelegramReceipt(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        update_id=45,
        payload={
            "message": {
                "chat": {"id": -46, "type": "supergroup"},
                "from": {"id": 47, "is_bot": False},
            }
        },
        status=TelegramReceiptStatus.PROCESSED.value,
        received_at=NOW,
        processed_at=NOW,
    )
    evidence = general_route_evidence_from_receipt(receipt)
    assert evidence is not None
    assert "46" not in repr(evidence)
    assert "47" not in repr(evidence)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        GATE_AQUB_RECEIPT_SIGNING_KEY="general-route-test-signing-key-32-bytes",
    )


async def _schema_engine(database_url: str):
    schema = f"aqub_general_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(TelegramReceipt.__table__.create)
        await connection.run_sync(AQUBInstallationBinding.__table__.create)
        await connection.run_sync(AQUBGeneralRoute.__table__.create)
    return schema, admin_engine, engine


async def _drop_schema(schema: str, admin_engine, engine) -> None:
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


def _receipt(*, update_id: int, group: int, actor: int) -> TelegramReceipt:
    return TelegramReceipt(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        update_id=update_id,
        payload={
            "message": {
                "chat": {"id": group, "type": "supergroup"},
                "from": {"id": actor, "is_bot": False},
            }
        },
        status=TelegramReceiptStatus.PROCESSED.value,
        received_at=NOW,
        processed_at=NOW,
    )


@pytest.mark.anyio
async def test_postgres_route_is_hold_by_default_and_exact_when_active() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for General route test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    food_group, food_actor = "-1000000000101", "1000000101"
    general_group, general_actor = "-1000000000202", "1000000202"
    food = _binding(group=food_group, actor=food_actor)
    try:
        async with sessions() as session:
            food_receipt = _receipt(update_id=101, group=int(food_group), actor=int(food_actor))
            general_receipt = _receipt(
                update_id=102,
                group=int(general_group),
                actor=int(general_actor),
            )
            session.add_all([food_receipt, general_receipt])
            await session.commit()
            session.add(
                AQUBInstallationBinding(
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    group_chat_id=food_group,
                    alpha_user_id=food_actor,
                    evidence_receipt_id=food_receipt.id,
                    bound_at=NOW,
                )
            )
            await session.commit()

            route = await register_aqub_general_route_hold(
                session,
                evidence_receipt_id=general_receipt.id,
            )
            assert route.status == AQUBGeneralRouteStatus.HOLD.value
            selected = await select_aqub_inbound_binding(
                session,
                _settings(),
                food_binding=food,
                chat_id=general_group,
                actor_id=general_actor,
                content_kind=AQUBTransportContentKind.TEXT,
            )
            assert selected is food

            await transition_aqub_general_route(
                session,
                route_id=route.id,
                expected_status=AQUBGeneralRouteStatus.HOLD,
                next_status=AQUBGeneralRouteStatus.ACTIVE,
                occurred_at=NOW,
            )
            selected = await select_aqub_inbound_binding(
                session,
                _settings(),
                food_binding=food,
                chat_id=general_group,
                actor_id=general_actor,
                content_kind=AQUBTransportContentKind.TEXT,
            )
            assert selected.group_chat_id == general_group
            assert selected.alpha_user_id == general_actor
            wrong_actor = await select_aqub_inbound_binding(
                session,
                _settings(),
                food_binding=food,
                chat_id=general_group,
                actor_id=food_actor,
                content_kind=AQUBTransportContentKind.TEXT,
            )
            wrong_group = await select_aqub_inbound_binding(
                session,
                _settings(),
                food_binding=food,
                chat_id=food_group,
                actor_id=general_actor,
                content_kind=AQUBTransportContentKind.TEXT,
            )
            media = await select_aqub_inbound_binding(
                session,
                _settings(),
                food_binding=food,
                chat_id=general_group,
                actor_id=general_actor,
                content_kind=AQUBTransportContentKind.PHOTO,
            )
            assert wrong_actor is not food
            assert wrong_actor.group_chat_id == general_group
            assert wrong_actor.alpha_user_id == general_actor
            assert wrong_group is media is food
            denied_store, denied_runtime = _Store(), _Runtime()
            denied = await AQUBLiveIngressAdapter(
                binding=wrong_actor,
                store=denied_store,
                runtime=denied_runtime,
            ).handle(_inbound(group=general_group, actor=food_actor))
            assert denied.accepted is False
            assert denied_store.calls == denied_runtime.calls == 0
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_postgres_route_rejects_food_group_conflict() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for General route test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    shared_group, food_actor, general_actor = "-1000000000303", "1000000303", "1000000404"
    try:
        async with sessions() as session:
            food_receipt = _receipt(update_id=103, group=int(shared_group), actor=int(food_actor))
            general_receipt = _receipt(
                update_id=104,
                group=int(shared_group),
                actor=int(general_actor),
            )
            session.add_all([food_receipt, general_receipt])
            await session.commit()
            session.add(
                AQUBInstallationBinding(
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    group_chat_id=shared_group,
                    alpha_user_id=food_actor,
                    evidence_receipt_id=food_receipt.id,
                    bound_at=NOW,
                )
            )
            await session.commit()
            with pytest.raises(ValueError, match="food_group_conflict"):
                await register_aqub_general_route_hold(
                    session,
                    evidence_receipt_id=general_receipt.id,
                )
    finally:
        await _drop_schema(schema, admin_engine, engine)

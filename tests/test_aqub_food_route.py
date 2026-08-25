"""Focused proofs for isolated AQUB Food routes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.aqub_live import AQUBLiveIngressAdapter
from app.application.aqub_transport import (
    AQUBTransportBinding,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_food_route import (
    AQUBFoodRouteStatus,
    food_route_evidence_from_receipt,
    register_aqub_food_route_hold,
    select_aqub_food_inbound_binding,
    transition_aqub_food_route,
)
from app.infrastructure.aqub_general_route import select_aqub_inbound_binding
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.aqub import (
    AQUBFoodRoute,
    AQUBGeneralRoute,
    AQUBInstallationBinding,
)
from app.models.domain import TelegramReceipt, TelegramReceiptStatus

NOW = datetime(2026, 8, 25, 13, 30, tzinfo=UTC)
SIGNING_KEY = b"isolated-food-route-test-key-32-bytes"


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
        return {
            "ok": True,
            "result": {
                "kind": "food_ready_for_confirmation",
                "reply_text": "ok",
            },
        }


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        GATE_AQUB_RECEIPT_SIGNING_KEY=SIGNING_KEY.decode(),
    )


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


def _inbound(*, group: str, actor: str) -> AQUBTransportInbound:
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id="901",
        chat_id=group,
        actor_id=actor,
        command="Я съел яблоко 100 г",
        payload={
            "update_id": 901,
            "message": {
                "message_id": 902,
                "date": int(NOW.timestamp()),
                "chat": {"id": int(group), "type": "supergroup"},
                "from": {"id": int(actor), "is_bot": False},
                "text": "Я съел яблоко 100 г",
            },
        },
        occurred_at=NOW,
        received_at=NOW,
        content_kind=AQUBTransportContentKind.TEXT,
    )


async def _schema_engine(database_url: str):
    schema = f"aqub_food_route_{uuid4().hex}"
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
        await connection.run_sync(AQUBFoodRoute.__table__.create)
    return schema, admin_engine, engine


async def _drop_schema(schema: str, admin_engine, engine) -> None:
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


def test_terminal_receipt_projects_private_ids_without_repr_exposure() -> None:
    evidence = food_route_evidence_from_receipt(
        _receipt(update_id=801, group=-1000000000801, actor=1000000801)
    )
    assert evidence is not None
    assert re.fullmatch(
        r"<app\.infrastructure\.aqub_food_route\.AQUBFoodRouteEvidence "
        r"object at 0x[0-9a-f]+>",
        repr(evidence),
    )


@pytest.mark.anyio
async def test_route_is_hold_idempotent_exact_and_supports_all_food_kinds() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Food route test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    production_group, actor = "-1000000000811", "1000000811"
    test_group = "-1000000000812"
    production = _binding(group=production_group, actor=actor)
    try:
        async with sessions() as session:
            production_receipt = _receipt(
                update_id=811,
                group=int(production_group),
                actor=int(actor),
            )
            test_receipt = _receipt(
                update_id=812,
                group=int(test_group),
                actor=int(actor),
            )
            session.add_all([production_receipt, test_receipt])
            await session.commit()
            session.add(
                AQUBInstallationBinding(
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    group_chat_id=production_group,
                    alpha_user_id=actor,
                    evidence_receipt_id=production_receipt.id,
                    bound_at=NOW,
                )
            )
            await session.commit()

            route = await register_aqub_food_route_hold(
                session,
                evidence_receipt_id=test_receipt.id,
            )
            replay = await register_aqub_food_route_hold(
                session,
                evidence_receipt_id=test_receipt.id,
            )
            assert replay.id == route.id
            assert route.status == AQUBFoodRouteStatus.HOLD.value
            selected = await select_aqub_food_inbound_binding(
                session,
                _settings(),
                fallback_binding=production,
                chat_id=test_group,
            )
            assert selected is production

            await transition_aqub_food_route(
                session,
                route_id=route.id,
                expected_status=AQUBFoodRouteStatus.HOLD,
                next_status=AQUBFoodRouteStatus.ACTIVE,
                occurred_at=NOW,
            )
            selected = await select_aqub_food_inbound_binding(
                session,
                _settings(),
                fallback_binding=production,
                chat_id=test_group,
            )
            assert selected.group_chat_id == test_group
            assert selected.alpha_user_id == actor
            assert selected.enabled is True

            for kind in (
                AQUBTransportContentKind.TEXT,
                AQUBTransportContentKind.PHOTO,
                AQUBTransportContentKind.VOICE,
            ):
                routed = await select_aqub_inbound_binding(
                    session,
                    _settings(),
                    food_binding=selected,
                    chat_id=test_group,
                    actor_id=actor,
                    content_kind=kind,
                )
                assert routed is selected
                assert routed.enabled is True

            denied_store, denied_runtime = _Store(), _Runtime()
            denied = await AQUBLiveIngressAdapter(
                binding=selected,
                store=denied_store,
                runtime=denied_runtime,
            ).handle(_inbound(group=test_group, actor="1000000899"))
            assert denied.accepted is False
            assert denied_store.calls == denied_runtime.calls == 0

            accepted_store, accepted_runtime = _Store(), _Runtime()
            accepted = await AQUBLiveIngressAdapter(
                binding=selected,
                store=accepted_store,
                runtime=accepted_runtime,
            ).handle(_inbound(group=test_group, actor=actor))
            assert accepted.accepted is True
            assert accepted_store.calls == accepted_runtime.calls == 1

            await transition_aqub_food_route(
                session,
                route_id=route.id,
                expected_status=AQUBFoodRouteStatus.ACTIVE,
                next_status=AQUBFoodRouteStatus.HOLD,
                occurred_at=NOW,
            )
            held = await select_aqub_food_inbound_binding(
                session,
                _settings(),
                fallback_binding=production,
                chat_id=test_group,
            )
            assert held is production
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.anyio
async def test_route_rejects_production_and_general_conflicts() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Food route test")

    schema, admin_engine, engine = await _schema_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    production_group, general_group = "-1000000000821", "-1000000000822"
    actor = "1000000821"
    try:
        async with sessions() as session:
            production_receipt = _receipt(
                update_id=821,
                group=int(production_group),
                actor=int(actor),
            )
            general_receipt = _receipt(
                update_id=822,
                group=int(general_group),
                actor=int(actor),
            )
            session.add_all([production_receipt, general_receipt])
            await session.commit()
            session.add_all(
                [
                    AQUBInstallationBinding(
                        bot_key=AQUB_RECEIPT_BOT_KEY,
                        group_chat_id=production_group,
                        alpha_user_id=actor,
                        evidence_receipt_id=production_receipt.id,
                        bound_at=NOW,
                    ),
                    AQUBGeneralRoute(
                        bot_key=AQUB_RECEIPT_BOT_KEY,
                        group_chat_id=general_group,
                        actor_user_id=actor,
                        evidence_receipt_id=general_receipt.id,
                        status="hold",
                        bound_at=NOW,
                    ),
                ]
            )
            await session.commit()
            with pytest.raises(ValueError, match="production_conflict"):
                await register_aqub_food_route_hold(
                    session,
                    evidence_receipt_id=production_receipt.id,
                )
            with pytest.raises(ValueError, match="general_conflict"):
                await register_aqub_food_route_hold(
                    session,
                    evidence_receipt_id=general_receipt.id,
                )
    finally:
        await _drop_schema(schema, admin_engine, engine)

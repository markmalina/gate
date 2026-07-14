"""Production-shaped synthetic replay for Telegram ingress identity parity."""

import asyncio
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.telegram_ingress_identity import (
    ResolveTelegramIngressIdentityService,
    TelegramIngressIdentitySnapshot,
    build_telegram_business_identity_command,
    direction_evidence_from_identity,
    direction_evidence_from_telegram_message,
    not_applicable_identity_resolution,
)
from app.application.telegram_ingress_shadow import (
    build_telegram_ingress_routing_context,
    classify_telegram_ingress,
)
from app.application.telegram_work_ownership_shadow import (
    evaluate_telegram_work_ownership_shadow,
)
from app.application.work_ownership import WORK_OWNERSHIP_SHADOW_POLICY_VERSION
from app.core.config import Settings
from app.core.telegram_connectors import TelegramConnectorRole

JsonObject = dict[str, Any]
FixtureCase = dict[str, Any]


class ReplayIdentityReader:
    def __init__(self, snapshot: TelegramIngressIdentitySnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        self.calls += 1
        return self.snapshot


@dataclass(frozen=True, slots=True)
class ReplayRouting:
    account_alias: str | None
    account_resolution: str


def _fixture_document() -> JsonObject:
    path = Path(__file__).parent / "fixtures" / "telegram_ingress_identity_replay_v1.json"
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


DOCUMENT = _fixture_document()
CASES = cast(list[FixtureCase], DOCUMENT["cases"])


def _snapshot(case: FixtureCase) -> TelegramIngressIdentitySnapshot | None:
    raw = case.get("identity_snapshot")
    if not isinstance(raw, dict):
        return None
    return TelegramIngressIdentitySnapshot(
        account_id=cast(str, raw["account_id"]),
        account_alias=cast(str, raw["account_alias"]),
        project_id=cast(str, raw["project_id"]),
        owner_identity_ids=frozenset(cast(list[str], raw["owner_identity_ids"])),
    )


@pytest.mark.parametrize("case", CASES, ids=[str(case["name"]) for case in CASES])
def test_identity_replay_matches_direction_reply_and_ownership(case: FixtureCase) -> None:
    update = cast(JsonObject, copy.deepcopy(case["update"]))
    original = copy.deepcopy(update)
    connector_role = TelegramConnectorRole(cast(str, case["connector_role"]))
    reader = ReplayIdentityReader(_snapshot(case))
    decision = classify_telegram_ingress(update)
    configured_routing = build_telegram_ingress_routing_context(
        update,
        decision=decision,
        settings=Settings(_env_file=None),
    )
    identity = not_applicable_identity_resolution()
    direction_evidence = direction_evidence_from_telegram_message(update)
    ownership_routing: Any = configured_routing
    command = (
        build_telegram_business_identity_command(update)
        if connector_role is TelegramConnectorRole.BUSINESS
        and decision.reason == "business_update"
        and decision.command is None
        else None
    )
    if command is not None:
        identity = asyncio.run(ResolveTelegramIngressIdentityService(reader).execute(command))
        direction_evidence = direction_evidence_from_identity(identity)
        ownership_routing = ReplayRouting(
            account_alias=identity.account_alias if identity.resolved else None,
            account_resolution=(
                "database_snapshot" if identity.resolved else identity.resolution.value
            ),
        )

    ownership = evaluate_telegram_work_ownership_shadow(
        update,
        decision=decision,
        routing=ownership_routing,
        connector_role=connector_role,
        bot_username=cast(str, case.get("bot_username", "synthetic_business_bot")),
        direction_evidence=direction_evidence,
    )
    expected = cast(JsonObject, case["expected"])

    assert reader.calls == expected["reader_calls"]
    assert identity.resolution.value == expected["identity_resolution"]
    assert identity.direction.value == expected["identity_direction"]
    assert identity.reply_task_intent.value == expected["reply_task_intent"]
    assert direction_evidence.value == expected["direction_evidence"]
    assert ownership.proposed_state.value == expected["ownership_state"]
    assert (
        ownership.candidate_agent.value if ownership.candidate_agent is not None else None
    ) == expected["ownership_candidate"]
    assert ownership.reason.value == expected["ownership_reason"]
    assert ownership.policy_version == DOCUMENT["policy_version"]
    assert ownership.policy_version == WORK_OWNERSHIP_SHADOW_POLICY_VERSION
    assert ownership.authoritative is False
    assert update == original

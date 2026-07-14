"""Synthetic fixture replay for Telegram work-ownership shadowing."""

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.telegram_ingress_shadow import (
    build_telegram_ingress_routing_context,
    classify_telegram_ingress,
)
from app.application.telegram_work_ownership_shadow import (
    TelegramConnectorRole,
    evaluate_telegram_work_ownership_shadow,
    normalize_oko_enrollment_text,
)
from app.application.work_ownership import DirectionEvidence
from app.core.config import Settings

JsonObject = dict[str, Any]
FixtureCase = dict[str, Any]


def _fixture_document() -> JsonObject:
    path = Path(__file__).parent / "fixtures" / "telegram_work_ownership_shadow_v1.json"
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


CASES = cast(list[FixtureCase], _fixture_document()["cases"])


def _settings() -> Settings:
    return Settings(_env_file=None).model_copy(
        update={
            "telegram_bot_username": "gate_m_bot",
            "gate_account_ru_business_connection_id": "ru-connection-fixture",
            "gate_account_by_business_connection_id": "by-connection-fixture",
        }
    )


@pytest.mark.parametrize("case", CASES, ids=[str(case["name"]) for case in CASES])
def test_synthetic_telegram_fixture_produces_expected_shadow_result(
    case: FixtureCase,
) -> None:
    update = cast(JsonObject, copy.deepcopy(case["update"]))
    original = copy.deepcopy(update)
    settings = _settings()
    decision = classify_telegram_ingress(update)
    routing = build_telegram_ingress_routing_context(
        update,
        decision=decision,
        settings=settings,
    )

    result = evaluate_telegram_work_ownership_shadow(
        update,
        decision=decision,
        routing=routing,
        connector_role=TelegramConnectorRole(
            cast(str, case.get("connector_role", TelegramConnectorRole.BUSINESS.value))
        ),
        bot_username=cast(str, case.get("bot_username", settings.telegram_bot_username)),
        direction_evidence=DirectionEvidence(
            cast(str, case.get("direction_evidence", DirectionEvidence.UNKNOWN.value))
        ),
    )

    expected = {
        "observed_signal": "none",
        **cast(JsonObject, case["expected"]),
        "policy_version": _fixture_document()["policy_version"],
        "authoritative": False,
    }
    assert result.log_fields() == expected
    assert result.authoritative is False
    assert update == original


def test_oko_normalization_is_exact_after_nfkc_whitespace_and_casefold() -> None:
    assert normalize_oko_enrollment_text("  ОКО \nВОЗРОЖДЕНИЯ ") == "око возрождения"
    assert normalize_oko_enrollment_text("Око возрождения!") != "око возрождения"
    assert normalize_oko_enrollment_text("Старт Око возрождения") != "око возрождения"


def test_shadow_fields_do_not_contain_payload_or_provider_identifiers() -> None:
    update: JsonObject = {
        "update_id": 901,
        "business_message": {
            "message_id": 902,
            "business_connection_id": "private-connection-secret",
            "chat": {"id": "private-chat-secret", "type": "private"},
            "from": {"id": "private-sender-secret"},
            "text": "private-message-secret",
        },
    }
    settings = _settings()
    decision = classify_telegram_ingress(update)
    routing = build_telegram_ingress_routing_context(
        update,
        decision=decision,
        settings=settings,
    )

    wire = json.dumps(
        evaluate_telegram_work_ownership_shadow(
            update,
            decision=decision,
            routing=routing,
            connector_role=TelegramConnectorRole.BUSINESS,
            bot_username=settings.telegram_bot_username,
            direction_evidence=DirectionEvidence.UNKNOWN,
        ).log_fields(),
        ensure_ascii=False,
    )

    assert "private-message-secret" not in wire
    assert "private-connection-secret" not in wire
    assert "private-chat-secret" not in wire
    assert "private-sender-secret" not in wire
    assert "901" not in wire
    assert "902" not in wire

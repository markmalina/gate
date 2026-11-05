import json
import os
from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest
from app.worker import gate745_fixture_identity as identity
from app.worker.gate745_fixture_policy import PRODUCTION_IDS, FixtureDenied, digest


def target() -> dict[str, str]:
    return {
        "projectId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c211",
        "environmentId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c212",
        "postgresServiceId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c214",
        "volumeInstanceId": "b527b8a3-2c7a-4bd6-92b3-f5244c74c215",
    }


def row() -> dict[str, object]:
    return {
        "name": "gate745_synthetic",
        "address": "10.0.0.12",
        "port": 5432,
        "system_identifier": "7560123456789012345",
    }


def test_digest_matches_existing_h2_identity_but_is_not_a_collected_receipt() -> None:
    result = identity.candidate_observation(target(), row())
    expected = target() | {
        "databaseName": "gate745_synthetic",
        "serverAddress": "10.0.0.12",
        "serverPort": 5432,
        "systemIdentifier": "7560123456789012345",
    }
    assert result["databaseIdentityDigest"] == digest(expected)
    assert result["physical"] == expected
    assert result["evidenceKind"] == "UNAUTHENTICATED_CALLER_INPUT"
    assert result["effectAuthority"] is False and result["nativeLaunchAllowed"] is False
    assert "readOnly" not in result and "receipt" not in result and "origin" not in result


@pytest.mark.parametrize("key", sorted(identity.TARGET_KEYS))
@pytest.mark.parametrize("production", sorted(PRODUCTION_IDS))
def test_every_production_tuple_slot_denied(key: str, production: str) -> None:
    value = target()
    value[key] = production
    with pytest.raises(FixtureDenied, match="E_FIXTURE_IDENTITY_TARGET"):
        identity.candidate_observation(value, row())


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "gate"),
        ("name", None),
        ("name", True),
        ("port", True),
        ("port", "5432"),
        ("port", 5433),
        ("address", None),
        ("address", "postgres.internal"),
        ("address", "0.0.0.0"),
        ("address", "224.0.0.1"),
        ("address", "::"),
        ("address", "fe80::1%eth0"),
        ("address", "2001:0db8::1"),
        ("system_identifier", "0"),
        ("system_identifier", "00123"),
        ("system_identifier", -1),
        ("system_identifier", "-1"),
        ("system_identifier", "18446744073709551616"),
        ("system_identifier", "１２３"),
        ("system_identifier", "1\n"),
    ],
)
def test_wrong_physical_facts_fail_closed(field: str, value: object) -> None:
    facts = row()
    facts[field] = value
    with pytest.raises(FixtureDenied):
        identity.candidate_observation(target(), facts)


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", "2001:db8::1"),
        ("address", "::ffff:10.0.0.12"),
        ("system_identifier", "18446744073709551615"),
    ],
)
def test_canonical_ipv6_and_uint64_supported(field: str, value: object) -> None:
    facts = row()
    facts[field] = value
    assert identity.candidate_observation(target(), facts)["nativeLaunchAllowed"] is False


def test_copy_isolated_and_scope_or_server_change_invalidates_hash() -> None:
    scope, facts = target(), row()
    original = identity.candidate_observation(scope, facts)
    for changes in ({"address": "10.0.0.13"}, {"system_identifier": "7560123456789012346"}):
        assert (
            identity.candidate_observation(scope, facts | changes)["databaseIdentityDigest"]
            != (original["databaseIdentityDigest"])
        )
    scope["projectId"] = "b527b8a3-2c7a-4bd6-92b3-f5244c74c299"
    facts["address"] = "10.0.0.14"
    assert original["physical"]["projectId"] != scope["projectId"]
    assert original["physical"]["serverAddress"] != facts["address"]


def test_recipe_is_fixed_readonly_and_cannot_be_enabled_by_returned_copy() -> None:
    first = identity.collection_recipe()
    assert first["sql"].count("SELECT ") == 1  # type: ignore[union-attr]
    assert "FROM pg_control_system()" in first["sql"]  # type: ignore[operator]
    assert (
        first["metadataGetConnections"] == 0 and first["durableReservationRequiredBeforeConnection"]
    )
    first["effectAuthority"] = True
    assert identity.collection_recipe()["effectAuthority"] is False
    assert identity.collection_recipe()["reservationAdapter"] is None


def test_selectors_origin_and_fake_dict_getters_are_not_authority() -> None:
    for extra in (
        {"dsn": "postgresql://example.invalid/db"},
        {"sql": "DROP TABLE users"},
        {"authenticated": True},
        {"readOnly": True},
    ):
        with pytest.raises(FixtureDenied):
            identity.candidate_observation(target(), row() | extra)

    class Poison(dict[str, Any]):
        def __iter__(self) -> Any:
            raise AssertionError("not allowed to inspect caller mapping")

    with pytest.raises(FixtureDenied):
        identity.candidate_observation(Poison(target()), row())


def test_public_entry_stops_before_environment_and_database_inputs() -> None:
    with patch.object(os, "getenv", side_effect=AssertionError("environment accessed")):
        with pytest.raises(FixtureDenied, match="E_FIXTURE_IDENTITY_UNBOUND"):
            identity.run()
    wire = json.dumps(identity.collection_recipe())
    assert "password" not in wire and "token" not in wire and "database_url" not in wire
    assert deepcopy(identity.collection_recipe()) == identity.collection_recipe()

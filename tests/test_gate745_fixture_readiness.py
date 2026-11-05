from copy import deepcopy

import pytest
from app.worker.gate745_fixture_policy import FixtureDenied
from app.worker.gate745_fixture_readiness import PENDING_EVIDENCE, check_source_contract


def proposal() -> dict[str, object]:
    return {
        "schemaVersion": "gate745.fixture-preparation-source/v1",
        "binding": {"state": "UNBOUND", "enabled": False, "allocation": None, "grants": []},
        "time": {
            "armedAt": "2026-10-12T12:00:00.000Z",
            "ownerWriteExpiresAt": "2026-10-12T14:00:00.000Z",
            "writeDeadline": "2026-10-12T14:00:00.000Z",
            "ownerReadExpiresAt": "2026-10-15T12:00:00.000Z",
            "readDeadline": "2026-10-15T12:00:00.000Z",
        },
        "resources": {
            "gateCpuMilliPerReplica": 500,
            "gateMemoryMiBPerReplica": 512,
            "gatePhysicalReplicas": 2,
            "pgCpuMilli": 1000,
            "pgMemoryMiB": 1024,
            "pgPhysicalReplicas": 1,
            "volumeMiB": 1024,
        },
        "read": {
            "invocations": 1,
            "connections": 1,
            "transactions": 1,
            "rows": 1,
            "connectTimeoutMilliseconds": 5000,
            "statementTimeoutMilliseconds": 5000,
            "totalTimeoutMilliseconds": 10000,
            "requestBytes": 131072,
            "responseBytes": 131072,
            "metadataGetConnections": 0,
            "readOnly": True,
            "reserveBeforeConnect": True,
        },
        "cost": {
            "preparation": True,
            "overlap": True,
            "retention": True,
            "observer": True,
            "egress": True,
            "billingAuthority": "NOT_PROVEN",
        },
    }


def changed(section: str, key: str, value: object) -> dict[str, object]:
    result = proposal()
    assert isinstance(result[section], dict)
    result[section][key] = value
    return result


def test_consistent_proposal_accounts_for_overlap_but_grants_no_effects() -> None:
    value = proposal()
    old = deepcopy(value)
    result = check_source_contract(value)
    assert value == old
    assert result["maximumProjectCpuMilli"] == 2000
    assert result["maximumProjectMemoryMiB"] == 2048
    assert result["kind"] == "SOURCE_CONTRACT_CONSISTENT"
    assert result["effectAuthority"] is False and result["nativeLaunchAllowed"] is False
    assert result["terminalDeletionProven"] is False
    assert result["pendingEvidence"] == list(PENDING_EVIDENCE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("gateCpuMilliPerReplica", 501),
        ("gateMemoryMiBPerReplica", 513),
        ("gatePhysicalReplicas", 3),
        ("pgPhysicalReplicas", 2),
        ("volumeMiB", 1025),
        ("gateCpuMilliPerReplica", True),
        ("pgMemoryMiB", 0),
        ("volumeMiB", 1024.0),
    ],
)
def test_per_replica_limits_cannot_hide_aggregate_overrun(field: str, value: object) -> None:
    with pytest.raises(FixtureDenied):
        check_source_contract(changed("resources", field, value))


def test_one_gate_instance_does_not_create_unlisted_overlap_headroom() -> None:
    one = changed("resources", "gatePhysicalReplicas", 1)
    assert isinstance(one["resources"], dict)
    one["resources"]["gateCpuMilliPerReplica"] = 1000
    assert check_source_contract(one)["maximumProjectCpuMilli"] == 2000
    one["resources"]["gatePhysicalReplicas"] = 2
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_OVERLAP"):
        check_source_contract(one)


@pytest.mark.parametrize(
    "field,value",
    [
        ("writeDeadline", "2026-10-12T14:00:00.001Z"),
        ("writeDeadline", "2026-10-12T12:00:00.000Z"),
        ("ownerWriteExpiresAt", "2026-10-12T13:00:00.000Z"),
        ("readDeadline", "2026-10-15T12:00:00.001Z"),
        ("ownerReadExpiresAt", "2026-10-14T12:00:00.000Z"),
        ("readDeadline", "2026-10-12T13:00:00.000Z"),
        ("armedAt", "2026-10-12T12:00:00+00:00"),
        ("armedAt", "9999-12-31T23:00:00.000Z"),
    ],
)
def test_arm_and_owner_expiries_bound_both_windows(field: str, value: object) -> None:
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_TIME"):
        check_source_contract(changed("time", field, value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("invocations", 2),
        ("connections", 2),
        ("transactions", 2),
        ("rows", 2),
        ("connectTimeoutMilliseconds", 5001),
        ("statementTimeoutMilliseconds", 5001),
        ("totalTimeoutMilliseconds", 10001),
        ("requestBytes", 131073),
        ("responseBytes", 131073),
        ("metadataGetConnections", 1),
        ("metadataGetConnections", False),
        ("readOnly", False),
        ("reserveBeforeConnect", False),
    ],
)
def test_identity_read_cannot_enlarge_or_renew_any_budget(field: str, value: object) -> None:
    with pytest.raises(FixtureDenied):
        check_source_contract(changed("read", field, value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("preparation", False),
        ("overlap", False),
        ("retention", False),
        ("observer", False),
        ("egress", False),
        ("billingAuthority", "PROVEN"),
    ],
)
def test_omitted_storage_cost_or_self_asserted_billing_proof_denied(
    field: str, value: object
) -> None:
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_COST"):
        check_source_contract(changed("cost", field, value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "BOUND"),
        ("enabled", True),
        ("enabled", 0),
        ("allocation", {"projectId": "self-asserted"}),
        ("grants", [{"approved": True}]),
        ("grants", ()),
    ],
)
def test_caller_cannot_promote_source_proposal_to_runtime_authority(
    field: str, value: object
) -> None:
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_AUTHORITY"):
        check_source_contract(changed("binding", field, value))


@pytest.mark.parametrize(
    "section,key",
    [
        ("binding", "dsn"),
        ("read", "sql"),
        ("resources", "providerSetter"),
        ("time", "laterGrantResetsArm"),
        ("cost", "additionalUSD"),
    ],
)
def test_unknown_selector_or_illusory_invoice_cap_is_rejected(section: str, key: str) -> None:
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_SHAPE"):
        check_source_contract(changed(section, key, "unsafe"))


def test_new_closed_protocol_does_not_accept_old_source_or_implicit_extra_fields() -> None:
    value = proposal()
    value["schemaVersion"] = "gate745.fixture-bootstrap-manifest/v1"
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_VERSION"):
        check_source_contract(value)
    value = proposal()
    value["effectAuthority"] = True
    with pytest.raises(FixtureDenied, match="E_FIXTURE_PREPARATION_SHAPE"):
        check_source_contract(value)

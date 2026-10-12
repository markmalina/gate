"""Pure closed preparation contract checker; never a native admission adapter.

Valid caller data means only that a proposed source contract is internally
consistent. Every runtime fact and owner grant still needs authenticated proof.
"""

from datetime import timedelta
from typing import Any

from app.worker.gate745_fixture_policy import FixtureDenied, exact, instant

PENDING_EVIDENCE = (
    "accepted_compatible_source_and_original_checks",
    "exact_preparation_owner_grant",
    "durable_cleanup_arm_before_first_paid_dispatch",
    "supported_limits_before_start_and_whole_fleet",
    "isolated_variables_and_effective_configuration",
    "exact_least_scope_permissions",
    "authenticated_allocation_and_physical_identity",
    "durable_one_use_identity_reservation_and_adapter",
    "approved_cleanup_source_and_lifecycle_contract",
    "supported_terminal_observer_and_retention_billing",
    "exact_native_effect_grant_and_final_source_binding",
)


def _shape(value: object, keys: set[str]) -> dict[str, Any]:
    try:
        return exact(value, keys)
    except FixtureDenied:
        raise FixtureDenied("E_FIXTURE_PREPARATION_SHAPE") from None


def _positive(value: object, ceiling: int) -> int:
    if type(value) is not int or not 1 <= value <= ceiling:
        raise FixtureDenied("E_FIXTURE_PREPARATION_LIMIT")
    return value


def check_source_contract(value: object) -> dict[str, object]:
    """Check an UNBOUND proposal without file/network/environment/DB access."""
    proposal = _shape(value, {"schemaVersion", "binding", "time", "resources", "read", "cost"})
    if (
        type(proposal["schemaVersion"]) is not str
        or proposal["schemaVersion"] != "gate745.fixture-preparation-source/v1"
    ):
        raise FixtureDenied("E_FIXTURE_PREPARATION_VERSION")
    binding = _shape(proposal["binding"], {"state", "enabled", "allocation", "grants"})
    if (
        type(binding["state"]) is not str
        or binding["state"] != "UNBOUND"
        or binding["enabled"] is not False
        or binding["allocation"] is not None
        or type(binding["grants"]) is not list
        or binding["grants"] != []
    ):
        raise FixtureDenied("E_FIXTURE_PREPARATION_AUTHORITY")
    time = _shape(
        proposal["time"],
        {"armedAt", "ownerWriteExpiresAt", "writeDeadline", "ownerReadExpiresAt", "readDeadline"},
    )
    try:
        arm = instant(time["armedAt"])
        write_expiry = instant(time["ownerWriteExpiresAt"])
        write_deadline = instant(time["writeDeadline"])
        read_expiry = instant(time["ownerReadExpiresAt"])
        read_deadline = instant(time["readDeadline"])
        write_ceiling = min(write_expiry, arm + timedelta(minutes=120))
        read_ceiling = min(read_expiry, arm + timedelta(hours=72))
    except (FixtureDenied, OverflowError):
        raise FixtureDenied("E_FIXTURE_PREPARATION_TIME") from None
    if (
        not arm < write_deadline <= write_ceiling
        or not write_deadline <= read_deadline <= read_ceiling
    ):
        raise FixtureDenied("E_FIXTURE_PREPARATION_TIME")
    resources = _shape(
        proposal["resources"],
        {
            "gateCpuMilliPerReplica",
            "gateMemoryMiBPerReplica",
            "gatePhysicalReplicas",
            "pgCpuMilli",
            "pgMemoryMiB",
            "pgPhysicalReplicas",
            "volumeMiB",
        },
    )
    gate_cpu = _positive(resources["gateCpuMilliPerReplica"], 1000)
    gate_memory = _positive(resources["gateMemoryMiBPerReplica"], 1024)
    gate_replicas = _positive(resources["gatePhysicalReplicas"], 2)
    pg_cpu = _positive(resources["pgCpuMilli"], 1000)
    pg_memory = _positive(resources["pgMemoryMiB"], 1024)
    _positive(resources["pgPhysicalReplicas"], 1)
    _positive(resources["volumeMiB"], 1024)
    if gate_cpu * gate_replicas > 1000 or gate_memory * gate_replicas > 1024:
        raise FixtureDenied("E_FIXTURE_PREPARATION_OVERLAP")
    read = _shape(
        proposal["read"],
        {
            "invocations",
            "connections",
            "transactions",
            "rows",
            "connectTimeoutMilliseconds",
            "statementTimeoutMilliseconds",
            "totalTimeoutMilliseconds",
            "requestBytes",
            "responseBytes",
            "metadataGetConnections",
            "readOnly",
            "reserveBeforeConnect",
        },
    )
    for key in ("invocations", "connections", "transactions", "rows"):
        _positive(read[key], 1)
    for key in ("connectTimeoutMilliseconds", "statementTimeoutMilliseconds"):
        _positive(read[key], 5000)
    _positive(read["totalTimeoutMilliseconds"], 10000)
    for key in ("requestBytes", "responseBytes"):
        _positive(read[key], 131072)
    if (
        type(read["metadataGetConnections"]) is not int
        or read["metadataGetConnections"] != 0
        or read["readOnly"] is not True
        or read["reserveBeforeConnect"] is not True
    ):
        raise FixtureDenied("E_FIXTURE_PREPARATION_READ")
    cost = _shape(
        proposal["cost"],
        {"preparation", "overlap", "retention", "observer", "egress", "billingAuthority"},
    )
    if (
        any(cost[key] is not True for key in cost if key != "billingAuthority")
        or type(cost["billingAuthority"]) is not str
        or cost["billingAuthority"] != "NOT_PROVEN"
    ):
        raise FixtureDenied("E_FIXTURE_PREPARATION_COST")
    return {
        "schemaVersion": "gate745.fixture-preparation-source-report/v1",
        "kind": "SOURCE_CONTRACT_CONSISTENT",
        "authorityState": "UNBOUND",
        "effectAuthority": False,
        "nativeLaunchAllowed": False,
        "maximumProjectCpuMilli": gate_cpu * gate_replicas + pg_cpu,
        "maximumProjectMemoryMiB": gate_memory * gate_replicas + pg_memory,
        "writeDeadline": time["writeDeadline"],
        "readDeadline": time["readDeadline"],
        "terminalDeletionProven": False,
        "pendingEvidence": list(PENDING_EVIDENCE),
    }

"""Fixed preparation identity recipe and pure observation validation.

This module never opens a connection. The public effect entry is unconditionally
UNBOUND until a separately reviewed durable one-use preparation adapter exists.
Caller-provided rows are not authenticated PostgreSQL observations or grants.
"""

import ipaddress
from typing import Any, NoReturn

from app.worker.gate745_fixture_policy import PRODUCTION_IDS, UUID, FixtureDenied, digest

IDENTITY_SQL = (
    "SELECT current_database() AS name, inet_server_addr()::text AS address, "
    "inet_server_port() AS port, system_identifier::text AS system_identifier "
    "FROM pg_control_system()"
)
TARGET_KEYS = frozenset({"projectId", "environmentId", "postgresServiceId", "volumeInstanceId"})
ROW_KEYS = frozenset({"name", "address", "port", "system_identifier"})


def collection_recipe() -> dict[str, object]:
    """A fixed source recipe, never an executable connection/permission plan."""
    return {
        "schemaVersion": "gate745.fixture-identity-recipe/v1",
        "authorityState": "UNBOUND",
        "effectAuthority": False,
        "nativeLaunchAllowed": False,
        "method": "POST",
        "path": "/admin/ops/gate745-fixture-identity",
        "routeRegistered": False,
        "sql": IDENTITY_SQL,
        "readOnly": True,
        "connections": 1,
        "transactions": 1,
        "rows": 1,
        "invocations": 1,
        "connectTimeoutMilliseconds": 5000,
        "statementTimeoutMilliseconds": 5000,
        "totalTimeoutMilliseconds": 10000,
        "requestBytes": 131072,
        "responseBytes": 131072,
        "metadataGetConnections": 0,
        "durableReservationRequiredBeforeConnection": True,
        "reservationAdapter": None,
    }


def candidate_observation(target: object, row: object) -> dict[str, Any]:
    """Validate untrusted facts and compute the existing H2 canonical digest.

    The discriminator makes a pure result ineligible as the original collected
    identity receipt. There is no receipt/origin/SQL-success field to fabricate.
    """
    if type(target) is not dict or set(target) != TARGET_KEYS:
        raise FixtureDenied("E_FIXTURE_IDENTITY_TARGET")
    if type(row) is not dict or set(row) != ROW_KEYS:
        raise FixtureDenied("E_FIXTURE_IDENTITY_ROW")
    identifiers = []
    for key in sorted(TARGET_KEYS):
        value = target[key]
        if type(value) is not str or not UUID.fullmatch(value) or value in PRODUCTION_IDS:
            raise FixtureDenied("E_FIXTURE_IDENTITY_TARGET")
        identifiers.append(value)
    if len(set(identifiers)) != len(identifiers):
        raise FixtureDenied("E_FIXTURE_IDENTITY_TARGET")
    if type(row["name"]) is not str or row["name"] != "gate745_synthetic":
        raise FixtureDenied("E_FIXTURE_IDENTITY_DATABASE")
    if type(row["port"]) is not int or row["port"] != 5432:
        raise FixtureDenied("E_FIXTURE_IDENTITY_PORT")
    address = row["address"]
    if type(address) is not str or len(address) > 45 or "%" in address:
        raise FixtureDenied("E_FIXTURE_IDENTITY_ADDRESS")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        raise FixtureDenied("E_FIXTURE_IDENTITY_ADDRESS") from None
    spellings = {str(parsed)}
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        spellings.add(f"::ffff:{parsed.ipv4_mapped}")
    if address not in spellings or parsed.is_unspecified or parsed.is_multicast:
        raise FixtureDenied("E_FIXTURE_IDENTITY_ADDRESS")
    system = row["system_identifier"]
    if (
        type(system) is not str
        or not system.isascii()
        or not system.isdecimal()
        or len(system) > 20
        or system.startswith("0")
        or int(system) > 2**64 - 1
    ):
        raise FixtureDenied("E_FIXTURE_IDENTITY_SYSTEM")
    physical = {key: target[key] for key in TARGET_KEYS}
    physical.update(
        {
            "databaseName": row["name"],
            "serverAddress": address,
            "serverPort": row["port"],
            "systemIdentifier": system,
        }
    )
    return {
        "schemaVersion": "gate745.fixture-identity-candidate/v1",
        "evidenceKind": "UNAUTHENTICATED_CALLER_INPUT",
        "effectAuthority": False,
        "nativeLaunchAllowed": False,
        "physical": physical,
        "databaseIdentityDigest": digest(physical),
    }


def run() -> NoReturn:
    """Deny before request, environment, credentials, connection or SQL access."""
    raise FixtureDenied("E_FIXTURE_IDENTITY_UNBOUND")


if __name__ == "__main__":
    run()

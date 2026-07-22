"""Repository contract tests for the Gate capability registry."""

from json import loads
from pathlib import Path


REGISTRY_PATH = Path(__file__).parents[1] / "registry" / "gate-capabilities.v1.json"
ALLOWED_LIFECYCLES = {
    "production-bounded",
    "canary",
    "shadow",
    "contract-only",
    "planned",
    "not-implemented",
}
EXPECTED_OWNERS = {"gate", "alfa", "relations", "mayaki", "take", "center"}
EXPECTED_ROADMAP = {f"P{index}" for index in range(8)}


def test_gate_capability_registry_is_well_formed() -> None:
    registry = loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert registry["schemaVersion"] == "1.0.0"
    assert registry["status"] == "current-target"
    assert registry["sourceIssue"] == "https://github.com/markmalina/gate/issues/277"
    assert len(registry["repositoryBaseline"]) == 40
    assert set(registry["ownership"]) == EXPECTED_OWNERS
    assert len(registry["invariants"]) >= 10

    capabilities = registry["capabilities"]
    capability_ids = [capability["id"] for capability in capabilities]
    assert len(capability_ids) == len(set(capability_ids))
    assert set(capability_ids) >= {
        "gate-kernel",
        "telegram-business-owner",
        "gate-m-control",
        "telegram-alpha-group",
        "telegram-personal-mtproto",
        "alfa-relations-memory-projection",
        "mayak-installation",
        "instagram-meta",
        "alpha-platform",
    }
    assert {capability["lifecycle"] for capability in capabilities} <= ALLOWED_LIFECYCLES
    assert all(capability["owner"] in EXPECTED_OWNERS for capability in capabilities)

    roadmap = registry["roadmap"]
    assert {stage["id"] for stage in roadmap} == EXPECTED_ROADMAP
    p0 = next(stage for stage in roadmap if stage["id"] == "P0")
    assert p0["state"] == "running"
    assert p0["pointer"] == registry["sourceIssue"]


def test_gate_capability_registry_contains_no_secret_shaped_keys() -> None:
    registry = loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    forbidden_keys = {
        "access_token",
        "api_hash",
        "api_key",
        "authorization_code",
        "client_secret",
        "password",
        "phone",
        "refresh_token",
        "session_string",
        "telegram_id",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(registry)

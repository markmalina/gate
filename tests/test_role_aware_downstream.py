import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from app.become.role_aware_contract import ContractError, canonical_digest
from app.become.role_aware_downstream import create_role_aware_gate_adapter

CONTRACT_ROOT = Path(__file__).parent.parent / "contracts/downstream/v1"
FIXTURE_BYTES = (CONTRACT_ROOT / "fixtures/positive-chain.json").read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)
MANIFEST = json.loads((CONTRACT_ROOT / "manifest.json").read_text())
SOURCE_SHA = "0" * 40
GATE_REQUEST = next(
    item["request"] for item in FIXTURE["transitions"] if item["service"] == "GATE"
)


def test_role_aware_fixture_digest_and_default_off_adapter() -> None:
    assert sha256(FIXTURE_BYTES).hexdigest() == MANIFEST["fixtures"]["positive-chain.json"][
        "sha256"
    ]
    assert canonical_digest(GATE_REQUEST) == FIXTURE["expected_canonical_request_digest"]
    adapter = create_role_aware_gate_adapter(SOURCE_SHA)
    assert adapter.production_routing_enabled is False
    assert adapter.durable_replay_claimed is False
    receipt = adapter.execute(deepcopy(GATE_REQUEST))
    assert receipt["output"] == {
        "validation": "accepted",
        "orchestration_enabled": False,
        "max_operations": 0,
    }
    assert receipt["external_effects"] == 0
    assert adapter.execute(deepcopy(GATE_REQUEST)) == receipt


def test_role_aware_gate_fails_closed_on_roles_production_and_replay() -> None:
    same_person = deepcopy(GATE_REQUEST)
    same_person["participants"]["learner"]["person_ref"] = same_person["participants"][
        "expert"
    ]["person_ref"]
    with pytest.raises(ContractError, match="participants_not_distinct"):
        create_role_aware_gate_adapter(SOURCE_SHA).execute(same_person)

    synthetic = deepcopy(GATE_REQUEST)
    synthetic["mode"] = "PRODUCTION"
    synthetic["participants"]["expert"]["person_ref"] = "synthetic://fixture/expert"
    with pytest.raises(ContractError, match="synthetic_person_forbidden"):
        create_role_aware_gate_adapter(SOURCE_SHA).execute(synthetic)

    adapter = create_role_aware_gate_adapter(SOURCE_SHA)
    adapter.execute(deepcopy(GATE_REQUEST))
    mutated = deepcopy(GATE_REQUEST)
    mutated["input"]["validate_only"] = False
    with pytest.raises(ContractError, match="mutated_replay"):
        adapter.execute(mutated)

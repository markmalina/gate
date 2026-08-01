import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "contracts" / "proof-loop" / "v1"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_w0_contracts_and_fixture_pack_are_strict_json() -> None:
    for name in (
        "gate/provider-capability.schema.json",
        "gate/delivery-receipt.schema.json",
        "gate/payment-receipt.schema.json",
        "fixtures/gate-transport-w0.json",
    ):
        assert _load(PACK / name)


def test_six_simulated_positive_cases_obey_effect_fences() -> None:
    pack = _load(PACK / "fixtures/gate-transport-w0.json")
    assert pack["mode"] == "SIMULATED"
    assert len(pack["positive"]) == 6
    for case in pack["positive"]:
        assert case.get("automatic_retry") is not True
        assert case.get("provider_calls_allowed") is not True
        assert case.get("receipt_type", "").startswith("gate.")
        assert "state" not in case
        if case.get("effect_state") == "unknown":
            assert case.get("reconciliation_required") is True
            assert case.get("delivery_outcome", case.get("payment_outcome")) == "pending"
        if case.get("receipt_type") == "gate.payment_receipt":
            assert case.get("result_receipt_ref", "").startswith("result_")
            assert case.get("payment_outcome") in {
                "pending",
                "declined",
                "paid",
                "refunded",
                "not_attempted",
            }


def test_five_negative_cases_are_declared_fail_closed() -> None:
    pack = _load(PACK / "fixtures/gate-transport-w0.json")
    expected = {
        "mode_not_simulated",
        "consent_receipt_missing",
        "result_receipt_missing",
        "automatic_retry_forbidden",
        "effect_state_outcome_mixed",
    }
    assert len(pack["fail_closed"]) == 5
    assert {case["reason"] for case in pack["fail_closed"]} == expected
    assert all(case["candidate"] for case in pack["fail_closed"])

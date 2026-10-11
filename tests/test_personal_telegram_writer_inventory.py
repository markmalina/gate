"""Known bypass candidates cannot disappear from the review inventory."""

from scripts.personal_telegram_writer_inventory import inventory


def test_known_business_and_personal_writers_are_reviewed_without_claiming_closure():
    proof = inventory()
    assert proof["provider_calls"] == 0
    assert not proof["exhaustive_writer_closure_verified"]
    paths = {call["path"] for call in proof["calls"]}
    assert {
        "app/infrastructure/reply_sends.py",
        "app/infrastructure/universal_mayak_delivery.py",
        "app/infrastructure/alpha_delivery.py",
        "app/infrastructure/aqub_live.py",
        "app/personal_telegram/local_runtime.py",
    } <= paths
    assert all(not call["shared_admission_verified"] for call in proof["calls"])

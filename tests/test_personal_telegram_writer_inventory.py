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

    assert {"send_business_photo", "send_business_album", "send_business_rich_media"} <= {
        call["method"] for call in proof["calls"]
    }
    assert {"sendPhoto", "sendMediaGroup", "sendMessage", "editMessageText"} <= {
        endpoint["method"] for endpoint in proof["bot_api_effect_endpoints"]
    }
    assert {"app/personal_telegram/local_runtime.py", "app/personal_telegram/cloud_worker.py"} <= {
        entry["path"] for entry in proof["personal_session_constructors"]
    }
    stage_paths = {
        "app/api/personal_stage.py",
        "app/api/personal_stage_file.py",
        "app/personal_telegram/work_stage.py",
    }
    for entries in ("calls", "personal_session_constructors", "bot_api_effect_endpoints"):
        assert stage_paths.isdisjoint(entry["path"] for entry in proof[entries])

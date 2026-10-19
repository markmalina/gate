"""Executable catalogue proof: a constructor is not product availability."""

import inspect

from app.personal_telegram.capability_catalog import CAPABILITIES
from scripts.personal_telegram_contract_inventory import inventory


def test_catalogue_covers_every_family_with_unique_typed_records():
    assert {record.family for record in CAPABILITIES} == {f"C{i:02d}" for i in range(1, 24)}
    assert len({record.id for record in CAPABILITIES}) == len(CAPABILITIES) == 104
    assert len({record.scope for record in CAPABILITIES}) == len(CAPABILITIES)
    assert all(record.state == "planned" for record in CAPABILITIES)
    assert all(record.activation_dependency for record in CAPABILITIES)


def test_pinned_actual_library_inventory_has_no_runtime_or_work_claim():
    proof = inventory()
    assert proof["telethon_version"] == "1.44.0" and proof["telegram_layer"] == 227
    assert proof["live_provider_calls"] == 0
    assert proof["methods"]
    for method in proof["methods"].values():
        assert method["library_method_present"]
        assert not method["gate_dispatcher_present"]
        assert not method["deployed_runtime_verified"]
        assert not method["actual_work_verified"]


def test_paid_fields_are_optional_not_required_by_pinned_constructors():
    from telethon.tl import functions

    for path in {path for record in CAPABILITIES for path in record.methods}:
        namespace, name = path.split(".")
        parameters = inspect.signature(getattr(getattr(functions, namespace), name)).parameters
        for field in ("allow_paid_stars", "allow_paid_floodskip"):
            if field in parameters:
                assert parameters[field].default is None


def test_forum_operations_use_actual_layer227_messages_namespace():
    topics = {record.operation: record.methods for record in CAPABILITIES if record.family == "C19"}
    assert topics["list_topics"] == ("messages.GetForumTopicsRequest",)
    assert topics["create_topic"] == ("messages.CreateForumTopicRequest",)
    assert topics["rename_topic"] == ("messages.EditForumTopicRequest",)

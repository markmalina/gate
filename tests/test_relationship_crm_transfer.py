"""Portable Relationship CRM transfer contract tests."""

from datetime import UTC, datetime

import pytest
from app.application.relationship_crm_transfer import (
    RelationshipCrmRecordKind,
    RelationshipCrmTransferError,
    RelationshipCrmTransferRecord,
    RelationshipCrmTransferV1,
)


def _transfer() -> RelationshipCrmTransferV1:
    now = datetime(2026, 7, 16, 20, 0, tzinfo=UTC)
    return RelationshipCrmTransferV1(
        workspace_key="workspace-1",
        exported_at=now,
        records=(
            RelationshipCrmTransferRecord(
                kind=RelationshipCrmRecordKind.PERSON,
                record_key="person:anna",
                payload={
                    "display_name": "Анна",
                    "created_at": "2026-07-10T10:00:00Z",
                },
            ),
            RelationshipCrmTransferRecord(
                kind=RelationshipCrmRecordKind.SPACE,
                record_key="space:personal",
                payload={"kind": "personal", "name": "Личное"},
            ),
        ),
    )


def test_transfer_is_deterministic_and_round_trips() -> None:
    original = _transfer()
    portable = original.to_portable_dict()

    assert [item["kind"] for item in portable["records"]] == ["person", "space"]
    restored = RelationshipCrmTransferV1.from_portable_dict(portable)
    assert restored == original
    assert restored.checksum() == original.checksum()
    assert len(original.checksum()) == 64


def test_transfer_is_independent_of_gate_database_identifiers() -> None:
    portable = _transfer().to_portable_dict()
    serialized = str(portable)

    assert "workspace_id" not in serialized
    assert "dialog_id" not in serialized
    assert "telegram_chat_id" not in serialized
    assert "reply_task_id" not in serialized


def test_duplicate_record_identity_is_rejected() -> None:
    record = RelationshipCrmTransferRecord(
        kind=RelationshipCrmRecordKind.NOTE,
        record_key="note:1",
        payload={"body": "Мысль"},
    )
    with pytest.raises(
        RelationshipCrmTransferError,
        match="transfer_record_duplicate",
    ):
        RelationshipCrmTransferV1(
            workspace_key="workspace-1",
            exported_at=datetime.now(UTC),
            records=(record, record),
        )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"schema_version": "relationship_crm.transfer.v2"}, "transfer_schema_unsupported"),
        ({"unexpected": True}, "transfer_envelope_invalid"),
    ],
)
def test_import_rejects_unknown_version_and_envelope_fields(
    mutation: dict[str, object],
    code: str,
) -> None:
    portable = _transfer().to_portable_dict()
    portable.update(mutation)

    with pytest.raises(RelationshipCrmTransferError, match=code):
        RelationshipCrmTransferV1.from_portable_dict(portable)


def test_import_rejects_unknown_record_kind_and_non_json_payload() -> None:
    portable = _transfer().to_portable_dict()
    records = portable["records"]
    assert isinstance(records, list)
    records[0]["kind"] = "unknown"

    with pytest.raises(
        RelationshipCrmTransferError,
        match="transfer_record_kind_unsupported",
    ):
        RelationshipCrmTransferV1.from_portable_dict(portable)

    with pytest.raises(
        RelationshipCrmTransferError,
        match="transfer_payload_not_json",
    ):
        RelationshipCrmTransferRecord(
            kind=RelationshipCrmRecordKind.PERSON,
            record_key="person:bad",
            payload={"bad": datetime.now(UTC)},
        )


def test_all_current_module_aggregates_have_transfer_kinds() -> None:
    assert {kind.value for kind in RelationshipCrmRecordKind} == {
        "space",
        "person",
        "relationship",
        "channel_identity",
        "interaction",
        "attention",
        "note",
        "work_item",
        "pipeline",
        "pipeline_stage",
        "deal",
        "deal_transition",
    }

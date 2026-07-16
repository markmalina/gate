"""Versioned, deterministic transfer contract for Relationship CRM extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Mapping


class RelationshipCrmTransferError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RelationshipCrmRecordKind(StrEnum):
    SPACE = "space"
    PERSON = "person"
    RELATIONSHIP = "relationship"
    CHANNEL_IDENTITY = "channel_identity"
    INTERACTION = "interaction"
    ATTENTION = "attention"
    NOTE = "note"
    WORK_ITEM = "work_item"
    PIPELINE = "pipeline"
    PIPELINE_STAGE = "pipeline_stage"
    DEAL = "deal"
    DEAL_TRANSITION = "deal_transition"


@dataclass(frozen=True, slots=True)
class RelationshipCrmTransferRecord:
    kind: RelationshipCrmRecordKind
    record_key: str
    payload: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.record_key, "transfer_record_key_invalid", 255)
        if not self.payload:
            raise RelationshipCrmTransferError("transfer_payload_empty")
        _validate_json_object(self.payload)


@dataclass(frozen=True, slots=True)
class RelationshipCrmTransferV1:
    """Portable full-module snapshot, independent of database UUIDs and Gate tables."""

    SCHEMA_VERSION: ClassVar[str] = "relationship_crm.transfer.v1"

    workspace_key: str
    exported_at: datetime
    records: tuple[RelationshipCrmTransferRecord, ...] = field(
        default=(),
        repr=False,
    )

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "transfer_workspace_key_invalid", 128)
        _require_aware(self.exported_at)
        identities = [(record.kind, record.record_key) for record in self.records]
        if len(set(identities)) != len(identities):
            raise RelationshipCrmTransferError("transfer_record_duplicate")

    def to_portable_dict(self) -> dict[str, object]:
        records = [
            {
                "kind": record.kind.value,
                "record_key": record.record_key,
                "payload": dict(record.payload),
            }
            for record in sorted(
                self.records,
                key=lambda item: (item.kind.value, item.record_key),
            )
        ]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "workspace_key": self.workspace_key,
            "exported_at": _timestamp(self.exported_at),
            "records": records,
        }

    def checksum(self) -> str:
        encoded = json.dumps(
            self.to_portable_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_portable_dict(
        cls,
        value: Mapping[str, object],
    ) -> RelationshipCrmTransferV1:
        expected_keys = {
            "schema_version",
            "workspace_key",
            "exported_at",
            "records",
        }
        if set(value) != expected_keys:
            raise RelationshipCrmTransferError("transfer_envelope_invalid")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise RelationshipCrmTransferError("transfer_schema_unsupported")

        workspace_key = value["workspace_key"]
        exported_at = value["exported_at"]
        raw_records = value["records"]
        if not isinstance(workspace_key, str) or not isinstance(exported_at, str):
            raise RelationshipCrmTransferError("transfer_envelope_invalid")
        if not isinstance(raw_records, list):
            raise RelationshipCrmTransferError("transfer_records_invalid")

        records: list[RelationshipCrmTransferRecord] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                raise RelationshipCrmTransferError("transfer_record_invalid")
            if set(raw_record) != {"kind", "record_key", "payload"}:
                raise RelationshipCrmTransferError("transfer_record_invalid")
            kind = raw_record["kind"]
            record_key = raw_record["record_key"]
            payload = raw_record["payload"]
            if (
                not isinstance(kind, str)
                or not isinstance(record_key, str)
                or not isinstance(payload, dict)
            ):
                raise RelationshipCrmTransferError("transfer_record_invalid")
            try:
                record_kind = RelationshipCrmRecordKind(kind)
            except ValueError as exc:
                raise RelationshipCrmTransferError(
                    "transfer_record_kind_unsupported"
                ) from exc
            records.append(
                RelationshipCrmTransferRecord(
                    kind=record_kind,
                    record_key=record_key,
                    payload=payload,
                )
            )

        return cls(
            workspace_key=workspace_key,
            exported_at=_parse_timestamp(exported_at),
            records=tuple(records),
        )


def _validate_json_object(value: Mapping[str, object]) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise RelationshipCrmTransferError("transfer_payload_key_invalid")
        _validate_json_value(item)


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        _validate_json_object(value)
        return
    raise RelationshipCrmTransferError("transfer_payload_not_json")


def _require_token(value: str, code: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise RelationshipCrmTransferError(code)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RelationshipCrmTransferError("transfer_timestamp_timezone_required")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RelationshipCrmTransferError("transfer_timestamp_invalid") from exc
    _require_aware(parsed)
    return parsed

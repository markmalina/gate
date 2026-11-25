"""Shared definitions for MCP JSON output schemas."""

from typing import Any

JsonObject = dict[str, Any]


def _object(
    properties: JsonObject,
    *,
    required: list[str] | None = None,
    additional_properties: bool = False,
) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": additional_properties,
    }


def _nullable(value_type: str) -> JsonObject:
    return {"type": [value_type, "null"]}


def _ref(name: str) -> JsonObject:
    return {"$ref": f"#/$defs/{name}"}


ACCOUNT_SCHEMA = _object(
    {
        "id": {"type": "string", "format": "uuid"},
        "alias": {"type": "string", "minLength": 1},
        "label": {"type": "string", "minLength": 1},
    }
)

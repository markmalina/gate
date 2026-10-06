"""Gate rich v1: bounded structured text, immutable blocks and semantic preview.

No rendering, execution, sending, retries, fallback or external I/O occurs here.
Provider objects follow Telegram Bot API 10.3 (verified 2026-10-06).
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, NoReturn

from app.application.telegram_formatting import FormattingError, FormattingPreview, _unicode, _url

SCHEMA_VERSION = "gate.rich.v1"
CONVERTER_VERSION = "gate.telegram-rich.v1"
LIMITS = {
    "blocks": 32,
    "text_units": 16000,
    "json_bytes": 65536,
    "inline_depth": 4,
    "inline_nodes": 512,
    "list_items": 32,
    "table_rows": 20,
    "table_columns": 6,
    "cell_units": 512,
    "code_units": 8192,
}


def _fail(message: str, code: str = "invalid_rich_content") -> NoReturn:
    raise FormattingError(code, message)


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError, RecursionError):
        _fail("Rich content must be bounded JSON")
    raise AssertionError


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - (optional or set())
    ):
        _fail("Rich object has missing or unsupported fields")
    return value


def _array(value: Any, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        _fail("Rich array is empty or exceeds its application limit")
    return value


def _string(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail("Rich text must be a string")
    _unicode(value)
    if (not value and not empty) or len(value.encode("utf-16-le")) // 2 > maximum:
        _fail("Rich text is empty or exceeds its application limit")
    return value


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_plain(part) for part in value)
    return _plain(value["text"])


class _Inline:
    def __init__(self) -> None:
        self.nodes = 0
        self.units = 0

    def parse(
        self, value: Any, depth: int = 0, in_url: bool = False, in_code: bool = False
    ) -> tuple[Any, str]:
        self.nodes += 1
        if depth > LIMITS["inline_depth"] or self.nodes > LIMITS["inline_nodes"]:
            _fail("Rich inline nesting or node count exceeds its application limit")
        if isinstance(value, str):
            text = _string(value, LIMITS["text_units"])
            self.units += len(text.encode("utf-16-le")) // 2
            if self.units > LIMITS["text_units"]:
                _fail("Rich total text exceeds its application limit")
            return text, text
        if isinstance(value, list):
            parts = [
                self.parse(v, depth + 1, in_url, in_code)
                for v in _array(value, LIMITS["inline_nodes"])
            ]
            return [p[0] for p in parts], "".join(p[1] for p in parts)
        obj = _object(value, {"type", "text"}, {"url"})
        kind = obj["type"]
        if (
            not isinstance(kind, str)
            or kind not in {"bold", "italic", "underline", "strikethrough", "code", "url"}
            or in_code
        ):
            _fail("Rich inline type or code nesting is unsupported")
        if kind == "url":
            if in_url or set(obj) != {"type", "text", "url"}:
                _fail("Rich links cannot be nested and require a URL")
            url = _string(obj["url"], 2048)
            _url(url)
        elif "url" in obj:
            _fail("URL is only permitted on a link")
        if kind == "code" and not isinstance(obj["text"], str):
            _fail("Inline code must contain literal text")
        text, preview = self.parse(obj["text"], depth + 1, in_url or kind == "url", kind == "code")
        result = {"type": kind, "text": text}
        if kind == "url":
            result["url"] = obj["url"]
            preview = f"[{preview}]({obj['url']})"
        else:
            preview = f"<{kind}>{preview}</{kind}>"
        return result, preview


@dataclass(frozen=True, slots=True)
class RichPreview(FormattingPreview):
    source_content: dict[str, Any]
    blocks: tuple[dict[str, Any], ...]
    scope: dict[str, str]
    revision: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "text": self.text,
            "text_format": "rich",
            "converter_version": self.converter_version,
            "entities": [],
            "payload_sha256": self.payload_sha256,
            "schema_version": SCHEMA_VERSION,
            "source_content": deepcopy(self.source_content),
            "blocks": deepcopy(list(self.blocks)),
            "scope": deepcopy(self.scope),
            "revision": self.revision,
            "limits": dict(LIMITS),
        }

    def bind(self, scope: dict[str, str]) -> RichPreview:
        preview = replace(self, scope=deepcopy(scope), payload_sha256="")
        return replace(preview, payload_sha256=_digest(preview.to_dict()))


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        _json({k: v for k, v in value.items() if k != "payload_sha256"}).encode()
    ).hexdigest()


def compile_rich(content: Any) -> RichPreview:
    obj = _object(content, {"schema_version", "blocks"})
    if obj["schema_version"] != SCHEMA_VERSION:
        _fail("Rich schema version is unsupported")
    source = _json(obj)
    _unicode(source)
    if len(source.encode()) > LIMITS["json_bytes"]:
        _fail("Rich input JSON exceeds its application byte limit")
    inline = _Inline()
    blocks: list[dict[str, Any]] = []
    previews: list[str] = []
    for raw in _array(obj["blocks"], LIMITS["blocks"]):
        block = _object(
            raw,
            {"type"},
            {"text", "size", "language", "ordered", "start", "items", "header", "rows"},
        )
        kind = block["type"]
        if not isinstance(kind, str):
            _fail("Rich block type must be a string")
        if kind in {"paragraph", "heading", "pre", "blockquote"}:
            required = {"type", "text"} | ({"size"} if kind == "heading" else set())
            _object(block, required, {"language"} if kind == "pre" else set())
            if kind == "pre":
                text = _string(block["text"], LIMITS["code_units"])
                value, preview = inline.parse(text)
                language = block.get("language", "")
                if not isinstance(language, str) or not re.fullmatch(
                    r"[A-Za-z0-9_+.-]{0,32}", language
                ):
                    _fail("Code language is invalid")
                result = {"type": "pre", "text": value, "language": language}
                previews.append(f"<pre language={language!r}>\n{preview}</pre>")
            else:
                value, preview = inline.parse(block["text"])
                if kind == "heading":
                    size = block["size"]
                    if type(size) is not int or not 1 <= size <= 3:
                        _fail("Heading size must be 1, 2 or 3")
                    result = {"type": kind, "text": value, "size": size}
                    previews.append(f"<heading size={size}>{preview}</heading>")
                elif kind == "blockquote":
                    result = {"type": kind, "blocks": [{"type": "paragraph", "text": value}]}
                    previews.append(f"<blockquote>{preview}</blockquote>")
                else:
                    result = {"type": kind, "text": value}
                    previews.append(preview)
        elif kind == "list":
            _object(block, {"type", "items"}, {"ordered", "start"})
            ordered, start = block.get("ordered", False), block.get("start", 1)
            if (
                type(ordered) is not bool
                or type(start) is not int
                or not 1 <= start <= 1000
                or (not ordered and "start" in block)
            ):
                _fail("List numbering is invalid")
            items, lines = [], []
            for index, item in enumerate(_array(block["items"], LIMITS["list_items"])):
                value, preview = inline.parse(item)
                items.append(
                    {
                        "blocks": [{"type": "paragraph", "text": value}],
                        **({"value": start + index, "type": "1"} if ordered else {}),
                    }
                )
                lines.append(f"{str(start + index) + '.' if ordered else '•'} {preview}")
            result = {"type": "list", "items": items}
            previews.append("\n".join(lines))
        elif kind == "table":
            _object(block, {"type", "header", "rows"})
            header = _array(block["header"], LIMITS["table_columns"])
            rows = _array(block["rows"], LIMITS["table_rows"])
            cells, lines = [], []
            for index, row in enumerate([header, *rows]):
                if not isinstance(row, list) or len(row) != len(header):
                    _fail("Table rows must have the same number of cells as the header")
                out, labels = [], []
                for cell in row:
                    before = inline.units
                    value, preview = inline.parse(cell)
                    if not _plain(value).strip():
                        _fail("Table cells must contain visible text")
                    if inline.units - before > LIMITS["cell_units"]:
                        _fail("Table cell exceeds its application limit")
                    out.append(
                        {
                            "text": value,
                            "align": "left",
                            "valign": "top",
                            **({"is_header": True} if index == 0 else {}),
                        }
                    )
                    labels.append(preview)
                cells.append(out)
                lines.append(_json(labels))
            result = {"type": "table", "cells": cells, "is_bordered": True}
            previews.append("<table header-first>\n" + "\n".join(lines) + "\n</table>")
        else:
            _fail("Rich block type is unsupported")
        blocks.append(result)
    if (
        len(_json({"blocks": blocks, "skip_entity_detection": True}).encode())
        > LIMITS["json_bytes"]
    ):
        _fail("Rich provider JSON exceeds its application byte limit")
    rich_preview = RichPreview(
        source,
        "\n\n".join(previews),
        "rich",
        CONVERTER_VERSION,
        (),
        "",
        deepcopy(obj),
        tuple(blocks),
        {},
    )
    return rich_preview.bind({})


def stored_rich_preview(text: str, value: Any) -> RichPreview:
    expected = {
        "source_text",
        "text",
        "text_format",
        "converter_version",
        "entities",
        "payload_sha256",
        "schema_version",
        "source_content",
        "blocks",
        "scope",
        "revision",
        "limits",
    }
    obj = _object(value, expected)
    if (
        obj["text_format"] != "rich"
        or obj["converter_version"] != CONVERTER_VERSION
        or obj["schema_version"] != SCHEMA_VERSION
        or obj["revision"] != 1
        or type(obj["revision"]) is not int
        or obj["entities"] != []
        or obj["limits"] != LIMITS
        or obj["text"] != text
        or obj["payload_sha256"] != _digest(obj)
    ):
        _fail("Saved rich snapshot changed or its version is unsupported", "formatting_changed")
    scope = _object(
        obj["scope"],
        {
            "workspace_id",
            "account_id",
            "project_id",
            "recipient_chat_id",
            "connection_id",
            "draft_id",
        },
    )
    if any(not isinstance(v, str) or not v for v in scope.values()):
        _fail("Saved rich scope is invalid")
    # The initial conversion was persisted. Validate the saved native payload,
    # source JSON and digest, then consume those exact blocks without rerendering.
    if obj["source_text"] != _json(obj["source_content"]):
        _fail("Saved rich source JSON changed", "formatting_changed")
    _string(text, 65536)
    if len(obj["source_text"].encode()) > LIMITS["json_bytes"]:
        _fail("Saved rich source exceeds its byte limit")
    validate_provider_blocks(obj["blocks"])
    return RichPreview(
        obj["source_text"],
        text,
        "rich",
        CONVERTER_VERSION,
        (),
        obj["payload_sha256"],
        deepcopy(obj["source_content"]),
        tuple(deepcopy(obj["blocks"])),
        deepcopy(scope),
    )


def rich_content_schema() -> dict[str, Any]:
    """Finite inline schema: arrays/styles/links share the runtime depth bound."""

    def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def inline(depth: int) -> dict[str, Any]:
        alternatives: list[dict[str, Any]] = [
            {"type": "string", "minLength": 1, "maxLength": LIMITS["text_units"]}
        ]
        if depth:
            child = inline(depth - 1)
            alternatives.extend(
                [
                    {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": LIMITS["inline_nodes"],
                        "items": child,
                    },
                    object_schema(
                        {
                            "type": {"enum": ["bold", "italic", "underline", "strikethrough"]},
                            "text": child,
                        },
                        ["type", "text"],
                    ),
                    object_schema(
                        {"type": {"const": "code"}, "text": {"type": "string", "minLength": 1}},
                        ["type", "text"],
                    ),
                    object_schema(
                        {
                            "type": {"const": "url"},
                            "text": child,
                            "url": {"type": "string", "maxLength": 2048},
                        },
                        ["type", "text", "url"],
                    ),
                ]
            )
        return {"anyOf": alternatives}

    rich_text = inline(LIMITS["inline_depth"])
    types = []
    for kind in ("paragraph", "heading", "blockquote", "pre"):
        props = {"type": {"const": kind}, "text": rich_text}
        required = ["type", "text"]
        if kind == "heading":
            props["size"] = {"type": "integer", "minimum": 1, "maximum": 3}
            required.append("size")
        if kind == "pre":
            props["text"] = {"type": "string", "minLength": 1, "maxLength": LIMITS["code_units"]}
            props["language"] = {"type": "string", "pattern": "^[A-Za-z0-9_+.-]{0,32}$"}
        types.append(object_schema(props, required))
    types.append(
        object_schema(
            {
                "type": {"const": "list"},
                "ordered": {"type": "boolean"},
                "start": {"type": "integer", "minimum": 1, "maximum": 1000},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": LIMITS["list_items"],
                    "items": rich_text,
                },
            },
            ["type", "items"],
        )
    )
    row = {"type": "array", "minItems": 1, "maxItems": LIMITS["table_columns"], "items": rich_text}
    types.append(
        object_schema(
            {
                "type": {"const": "table"},
                "header": row,
                "rows": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": LIMITS["table_rows"],
                    "items": row,
                },
            },
            ["type", "header", "rows"],
        )
    )
    return object_schema(
        {
            "schema_version": {"const": SCHEMA_VERSION},
            "blocks": {
                "type": "array",
                "minItems": 1,
                "maxItems": LIMITS["blocks"],
                "items": {"oneOf": types},
            },
        },
        ["schema_version", "blocks"],
    )


def validate_provider_blocks(blocks: Any) -> None:
    """Check frozen native blocks without invoking the converter or rebuilding them."""
    inline = _Inline()
    for raw in _array(blocks, LIMITS["blocks"]):
        block = _object(
            raw, {"type"}, {"text", "size", "language", "items", "blocks", "cells", "is_bordered"}
        )
        kind = block["type"]
        if not isinstance(kind, str):
            _fail("Rich provider block type is invalid")
        if kind in {"paragraph", "heading", "pre"}:
            _object(
                block,
                {"type", "text"} | ({"size"} if kind == "heading" else set()),
                {"language"} if kind == "pre" else set(),
            )
            inline.parse(block["text"])
            if kind == "heading" and (
                type(block["size"]) is not int or not 1 <= block["size"] <= 3
            ):
                _fail("Rich provider heading is invalid")
            if kind == "pre":
                _string(block["text"], LIMITS["code_units"])
                if not isinstance(block.get("language", ""), str) or not re.fullmatch(
                    r"[A-Za-z0-9_+.-]{0,32}", block.get("language", "")
                ):
                    _fail("Rich provider code language is invalid")
        elif kind == "blockquote":
            _object(block, {"type", "blocks"})
            children = _array(block["blocks"], 1)
            child = _object(children[0], {"type", "text"})
            if child["type"] != "paragraph":
                _fail("Rich provider quote nesting is unsupported")
            inline.parse(child["text"])
        elif kind == "list":
            _object(block, {"type", "items"})
            ordered: bool | None = None
            first_value: int | None = None
            for index, raw_item in enumerate(_array(block["items"], LIMITS["list_items"])):
                item = _object(raw_item, {"blocks"}, {"value", "type"})
                is_ordered = "value" in item
                if ordered is None:
                    ordered = is_ordered
                    first_value = item.get("value")
                if ordered != is_ordered:
                    _fail("Rich provider list numbering is inconsistent")
                if is_ordered:
                    if (
                        type(item["value"]) is not int
                        or type(first_value) is not int
                        or not 1 <= first_value <= 1000
                        or item["value"] != first_value + index
                        or item.get("type") != "1"
                    ):
                        _fail("Rich provider list numbering is invalid")
                elif "type" in item:
                    _fail("Rich provider unordered item is invalid")
                child = _object(_array(item["blocks"], 1)[0], {"type", "text"})
                if child["type"] != "paragraph":
                    _fail("Rich provider list nesting is unsupported")
                inline.parse(child["text"])
        elif kind == "table":
            _object(block, {"type", "cells", "is_bordered"})
            if block["is_bordered"] is not True:
                _fail("Rich provider table borders are invalid")
            rows = _array(block["cells"], LIMITS["table_rows"] + 1)
            if len(rows) < 2:
                _fail("Rich provider table requires header and data")
            width = len(_array(rows[0], LIMITS["table_columns"]))
            for index, row in enumerate(rows):
                if not isinstance(row, list) or len(row) != width:
                    _fail("Rich provider table rows are ragged")
                for raw_cell in row:
                    cell = _object(raw_cell, {"text", "align", "valign"}, {"is_header"})
                    if (
                        cell["align"] != "left"
                        or cell["valign"] != "top"
                        or (index == 0 and cell.get("is_header") is not True)
                        or (index > 0 and "is_header" in cell)
                    ):
                        _fail("Rich provider table cell is invalid")
                    before = inline.units
                    inline.parse(cell["text"])
                    if not _plain(cell["text"]).strip():
                        _fail("Rich provider cells must contain visible text")
                    if inline.units - before > LIMITS["cell_units"]:
                        _fail("Rich provider table cell exceeds its application limit")
        else:
            _fail("Rich provider block is unsupported")
    if (
        len(_json({"blocks": blocks, "skip_entity_detection": True}).encode())
        > LIMITS["json_bytes"]
    ):
        _fail("Rich provider payload exceeds its application byte limit")

"""Gate rich v2 media compiler; immutable attachment identities, no I/O.

Native shapes verified against core.telegram.org/bots/api on 2026-10-07.
The six v1 text blocks retain their existing compiler and hashes.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.application.telegram_formatting import FormattingError
from app.application.telegram_rich import LIMITS, _Inline, _json, _object, compile_rich

SCHEMA_VERSION = "gate.rich.v2"
CONVERTER_VERSION = "gate.telegram-rich-media.v1"
MEDIA_TYPES = frozenset({"photo", "slideshow", "collage"})


def _fail(message: str) -> None:
    raise FormattingError("invalid_rich_media", message)


def compile_rich_media(content: Any, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile prepared opaque refs to one blocks-only provider representation.

    Source refs must name each prepared item once and in approved item order.
    Alias normalization and deterministic image transforms precede this call.
    """
    obj = _object(content, {"schema_version", "blocks"})
    if obj["schema_version"] != SCHEMA_VERSION:
        _fail("Rich media requires gate.rich.v2")
    source_json = _json(obj)
    if len(source_json.encode()) > LIMITS["json_bytes"]:
        _fail("Rich media JSON exceeds 65536 bytes")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        _fail("Rich media requires 1-10 prepared photos")
    refs: dict[str, tuple[int, str]] = {}
    for index, item in enumerate(items, 1):
        ref = str(item.get("media_ref", item.get("id", "")))
        digest = item.get("output_sha256")
        if not ref or ref in refs or not isinstance(digest, str) or len(digest) != 64:
            _fail("Prepared photo identity is invalid")
        assert isinstance(digest, str)
        refs[ref] = index, digest
    raw_blocks = obj["blocks"]
    if not isinstance(raw_blocks, list) or not 1 <= len(raw_blocks) <= LIMITS["blocks"]:
        _fail("Rich media block count is invalid")
    inline = _Inline()
    used: list[str] = []
    previews: list[str] = []

    def caption(raw: Any) -> dict[str, Any]:
        cap = _object(raw, {"text"}, {"credit"})
        parsed = {}
        for key, value in cap.items():
            parsed[key], preview = inline.parse(value)
            previews.append(f"<caption {key}>{preview}</caption>")
        return parsed

    def photo(raw: Any) -> dict[str, Any]:
        block = _object(raw, {"type", "media_ref", "sha256"}, {"caption", "version"})
        if "version" in block and (type(block["version"]) is not int or block["version"] != 1):
            _fail("Prepared photo version is unsupported")
        ref = block["media_ref"]
        if block["type"] != "photo" or not isinstance(ref, str) or ref not in refs:
            _fail("Rich photo must reference a prepared media item")
        index, digest = refs[ref]
        if block["sha256"] != digest or ref in used:
            _fail("Rich photo digest or unique reference changed")
        used.append(ref)
        result = {"type": "photo", "photo": {"type": "photo", "media": f"attach://photo_{index}"}}
        previews.append(f"<photo position={index} sha256={digest}>")
        if "caption" in block:
            result["caption"] = caption(block["caption"])
        return result

    output = []
    # Compile all text together to preserve v1 aggregate bounds; then interleave
    # exact compiled native blocks at their source positions.
    text_sources = [
        b for b in raw_blocks if isinstance(b, dict) and b.get("type") not in MEDIA_TYPES
    ]
    text_preview = (
        compile_rich({"schema_version": "gate.rich.v1", "blocks": text_sources})
        if text_sources
        else None
    )
    text_iterator = iter(text_preview.blocks if text_preview else ())
    text_units = 0
    if text_preview:

        def count_strings(value: Any) -> int:
            if isinstance(value, str):
                return len(value.encode("utf-16-le")) // 2
            if isinstance(value, list):
                return sum(count_strings(v) for v in value)
            if isinstance(value, dict):
                return sum(
                    count_strings(v)
                    for k, v in value.items()
                    if k in {"text", "blocks", "cells", "items"}
                )
            return 0

        text_units = sum(count_strings(b) for b in text_preview.blocks)
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            _fail("Rich block must be an object")
        kind = raw.get("type")
        if kind == "photo":
            output.append(photo(raw))
        elif kind in {"slideshow", "collage"}:
            group = _object(raw, {"type", "photos"}, {"caption"})
            photos = group["photos"]
            if not isinstance(photos, list) or not 2 <= len(photos) <= 10:
                _fail("Slideshow/collage requires 2-10 photo children")
            result = {"type": kind, "blocks": [photo(child) for child in photos]}
            if "caption" in group:
                result["caption"] = caption(group["caption"])
            output.append(result)
        else:
            output.append(deepcopy(next(text_iterator)))
            previews.append(compile_rich({"schema_version": "gate.rich.v1", "blocks": [raw]}).text)
    if used != list(refs):
        _fail("Every prepared photo must occur once in approved order")

    def inline_nodes(value: Any) -> int:
        if isinstance(value, list):
            return 1 + sum(inline_nodes(child) for child in value)
        if isinstance(value, dict):
            return 1 + inline_nodes(value["text"])
        return 1

    text_nodes = 0
    for text_source in text_sources:
        if "text" in text_source:
            text_nodes += inline_nodes(text_source["text"])
        if text_source["type"] == "list":
            text_nodes += sum(inline_nodes(value) for value in text_source["items"])
        if text_source["type"] == "table":
            text_nodes += sum(
                inline_nodes(value)
                for row in [text_source["header"], *text_source["rows"]]
                for value in row
            )
    if inline.nodes + text_nodes > LIMITS["inline_nodes"]:
        _fail("Rich text and captions exceed their combined inline node limit")
    if inline.units + text_units > LIMITS["text_units"]:
        _fail("Rich text and captions exceed their combined limit")
    if (
        len(_json({"blocks": output, "skip_entity_detection": True}).encode())
        > LIMITS["json_bytes"]
    ):
        _fail("Native rich media JSON exceeds 65536 bytes")
    semantic = "\n\n".join(previews)
    return {
        "schema_version": SCHEMA_VERSION,
        "converter_version": CONVERTER_VERSION,
        "source_content": deepcopy(obj),
        "blocks": output,
        "semantic_preview": semantic,
    }


def validate_rich_media_attachments(blocks: list[dict[str, Any]], names: set[str]) -> None:
    """Fail closed on missing, unused or remotely fetched attachment references."""
    found: list[str] = []
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= LIMITS["blocks"]:
        _fail("Invalid rich media block count")

    def visit(block: Any, nested: bool = False) -> None:
        if not isinstance(block, dict):
            _fail("Native block must be an object")
        kind = block.get("type")
        if kind == "photo":
            obj = _object(block, {"type", "photo"}, {"caption"})
            media = _object(obj["photo"], {"type", "media"})
            ref = media["media"]
            if (
                media["type"] != "photo"
                or not isinstance(ref, str)
                or not ref.startswith("attach://")
            ):
                _fail("Native photo requires a local prepared attachment")
            found.append(ref.removeprefix("attach://"))
            if "caption" in obj:
                cap = _object(obj["caption"], {"text"}, {"credit"})
                parser = _Inline()
                for value in cap.values():
                    parser.parse(value)
        elif kind in {"slideshow", "collage"}:
            if nested:
                _fail("Nested slideshow/collage is unsupported")
            obj = _object(block, {"type", "blocks"}, {"caption"})
            children = obj["blocks"]
            if not isinstance(children, list) or not 2 <= len(children) <= 10:
                _fail("Native group requires 2-10 photos")
            for child in children:
                if not isinstance(child, dict) or child.get("type") != "photo":
                    _fail("Native group permits only photo children")
                visit(child, True)
            if "caption" in obj:
                cap = _object(obj["caption"], {"text"}, {"credit"})
                parser = _Inline()
                for value in cap.values():
                    parser.parse(value)
        else:
            if nested:
                _fail("Native group permits only photo children")
            from app.application.telegram_rich import validate_provider_blocks

            validate_provider_blocks([block])

    for block in blocks:
        visit(block)
    if len(found) != len(set(found)) or set(found) != names:
        _fail("Native attachment map has missing, duplicate or unused files")
    if len(json.dumps(blocks, ensure_ascii=False).encode()) > LIMITS["json_bytes"]:
        _fail("Native rich media JSON exceeds limit")


def is_media_draft(draft: Any) -> bool:
    """Any media marker independently fences legacy edits and text dispatch."""
    context = getattr(draft, "prompt_context", None)
    return getattr(draft, "source", None) == "chatgpt-mcp-media" or (
        isinstance(context, dict)
        and ("_gate_media_package" in context or context.get("kind") == "media")
    )

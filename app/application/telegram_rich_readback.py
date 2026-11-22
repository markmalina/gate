"""Plain text projection of received Bot API RichMessage content."""

from collections.abc import Iterator
from typing import Any

_TEXT_BLOCKS = {"paragraph", "heading", "pre", "footer", "pullquote", "expandable_blockquote"}
_CONTAINER_BLOCKS = {"blockquote", "details", "collage", "slideshow"}
_MEDIA_BLOCKS = {"animation", "audio", "document", "map", "photo", "video", "voice_note"}
_NON_TEXT_BLOCKS = _MEDIA_BLOCKS | {"anchor", "divider"}
_TEXT_LIMIT = 100_000
_INLINE_TEXT_TYPES = {
    "bold", "italic", "underline", "strikethrough", "spoiler", "date_time",
    "text_mention", "subscript", "superscript", "marked", "code", "url",
    "email_address", "phone_number", "bank_card_number", "ton_address",
    "mention", "hashtag", "cashtag", "bot_command", "anchor_link", "reference",
    "reference_link",
}


class TelegramRichReadbackError(ValueError):
    """The whole text cannot be projected safely from this RichMessage."""


def project_telegram_rich_text(value: object) -> str | None:
    """Preserve visible text and block order, excluding transport metadata."""
    if not isinstance(value, dict):
        raise TelegramRichReadbackError("invalid_rich_message")

    remaining_nodes = 4096
    remaining_chars = _TEXT_LIMIT

    def visit(depth: int) -> None:
        nonlocal remaining_nodes
        remaining_nodes -= 1
        if depth > 20 or remaining_nodes < 0:
            raise TelegramRichReadbackError("rich_message_too_complex")

    def inline(node: Any, depth: int) -> Iterator[str]:
        nonlocal remaining_chars
        visit(depth)
        if isinstance(node, str):
            remaining_chars -= len(node)
            if remaining_chars < 0:
                raise TelegramRichReadbackError("rich_message_text_too_long")
            yield node
        elif isinstance(node, list):
            for part in node:
                yield from inline(part, depth + 1)
        elif isinstance(node, dict) and isinstance(kind := node.get("type"), str):
            if kind in _INLINE_TEXT_TYPES:
                yield from inline(node.get("text"), depth + 1)
            elif kind == "custom_emoji":
                yield from inline(node.get("alternative_text"), depth + 1)
            elif kind == "mathematical_expression":
                yield from inline(node.get("expression"), depth + 1)
            elif kind == "button" and isinstance(button := node.get("button"), dict):
                yield from inline(button.get("text"), depth + 1)
            elif kind != "anchor":
                raise TelegramRichReadbackError("unsupported_rich_text_type")
        else:
            raise TelegramRichReadbackError("invalid_rich_text")

    def text(node: Any, depth: int) -> str:
        return "".join(inline(node, depth))

    def blocks(nodes: Any, depth: int) -> Iterator[str]:
        if not isinstance(nodes, list):
            raise TelegramRichReadbackError("invalid_rich_blocks")
        for block in nodes:
            visit(depth)
            if not isinstance(block, dict) or not isinstance(kind := block.get("type"), str):
                raise TelegramRichReadbackError("invalid_rich_block")
            if kind in _TEXT_BLOCKS:
                yield text(block.get("text"), depth + 1)
            elif kind == "mathematical_expression":
                yield text(block.get("expression"), depth + 1)
            elif kind in _CONTAINER_BLOCKS:
                if kind == "details":
                    yield text(block.get("summary"), depth + 1)
                yield from blocks(block.get("blocks"), depth + 1)
            elif kind == "list":
                for item in objects(block.get("items"), depth + 1):
                    lines = blocks(item.get("blocks"), depth + 2)
                    first = next(lines, "")
                    label = text(item["label"], depth + 2) if "label" in item else ""
                    yield f"{label} {first}" if label else first
                    yield from lines
            elif kind == "table":
                cells = block.get("cells")
                if not isinstance(cells, list):
                    raise TelegramRichReadbackError("invalid_rich_table")
                for row in cells:
                    visit(depth + 1)
                    yield " | ".join(
                        text(cell["text"], depth + 2) if "text" in cell else ""
                        for cell in objects(row, depth + 2)
                    )
            elif kind == "buttons":
                for button in objects(block.get("buttons"), depth + 1):
                    yield text(button.get("text"), depth + 2)
            elif kind not in _NON_TEXT_BLOCKS:
                raise TelegramRichReadbackError("unsupported_rich_block_type")

            if "caption" in block:
                caption = block["caption"]
                if isinstance(caption, dict) and "type" not in caption:
                    yield text(caption.get("text"), depth + 1)
                    if "credit" in caption:
                        yield text(caption["credit"], depth + 1)
                else:
                    yield text(caption, depth + 1)
            if "credit" in block:
                yield text(block["credit"], depth + 1)

    def objects(nodes: Any, depth: int) -> Iterator[dict[str, Any]]:
        if not isinstance(nodes, list):
            raise TelegramRichReadbackError("invalid_rich_items")
        for node in nodes:
            visit(depth)
            if not isinstance(node, dict):
                raise TelegramRichReadbackError("invalid_rich_item")
            yield node

    result = "\n\n".join(blocks(value.get("blocks"), 0))
    if len(result) > _TEXT_LIMIT:
        raise TelegramRichReadbackError("rich_message_text_too_long")
    return result if result.strip() else None

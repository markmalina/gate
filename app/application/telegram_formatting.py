"""Bounded Markdown v1 compiled once into Telegram UTF-16 entities.

Explicit plain mode is literal. Markdown supports **bold**, *italic* / _italic_,
~~strike~~, __underline__, ||spoiler||, inline and fenced backtick code,
HTTP(S) links, ATX headings (bold), literal list markers and > blockquotes.
Backslash escapes punctuation. Tables, images, HTML, reference links, nested
quotes, crossing ranges and styles containing code are rejected. Oversized
results are rejected; this module never splits, sends or retries messages.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

FORMAT_KEY = "telegram_formatting"
CAPTION_FORMAT_KEY = "telegram_document_caption_formatting"
CONVERTER_VERSION = "gate.telegram-markdown.v1"
MAX_SOURCE_LENGTH = 65536
MAX_ENTITIES = 100
MAX_NESTING = 16
_STYLES = {
    "**": "bold",
    "__": "underline",
    "~~": "strikethrough",
    "||": "spoiler",
    "*": "italic",
    "_": "italic",
}
_ENTITY_TYPES = frozenset((*_STYLES.values(), "code", "pre", "text_link", "blockquote"))
_INLINE_STYLES = frozenset(_STYLES.values())
_FIELDS = frozenset(
    {
        "source_text",
        "text",
        "text_format",
        "converter_version",
        "entities",
        "payload_sha256",
    }
)


class FormattingError(ValueError):
    """Safe, bounded error containing no source text or recipient data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(message: str, code: str = "invalid_formatting") -> None:
    raise FormattingError(code, message)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _unicode(text: str) -> None:
    if any(0xD800 <= ord(c) <= 0xDFFF or c == "\x00" for c in text):
        _fail("Text contains invalid Unicode or a NUL character")


def _digest(value: dict[str, Any]) -> str:
    body = {k: v for k, v in value.items() if k != "payload_sha256"}
    return hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class FormattingPreview:
    source_text: str
    text: str
    text_format: str
    converter_version: str
    entities: tuple[dict[str, Any], ...]
    payload_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "text": self.text,
            "text_format": self.text_format,
            "converter_version": self.converter_version,
            "entities": deepcopy(list(self.entities)),
            "payload_sha256": self.payload_sha256,
        }


def _url(url: str) -> None:
    _unicode(url)
    if not url or any(c.isspace() or ord(c) < 32 for c in url):
        _fail("Links require a valid HTTP or HTTPS URL")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            _fail("Links require a valid HTTP or HTTPS URL")
        _port = parsed.port
    except ValueError as exc:
        raise FormattingError("invalid_formatting", "Link URL is invalid") from exc


class _Inline:
    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0
        self.parts: list[str] = []
        self.length = 0
        self.entities: list[dict[str, Any]] = []

    def append(self, text: str) -> None:
        self.parts.append(text)
        self.length += _units(text)

    def parse(
        self,
        terminator: str | None = None,
        depth: int = 0,
        in_link: bool = False,
        parents: tuple[str, ...] = (),
    ) -> None:
        if depth > MAX_NESTING:
            _fail("Formatting nesting exceeds the supported limit")
        s = self.source
        while self.position < len(s):
            p = self.position
            closing = bool(terminator and s.startswith(terminator, p))
            if terminator in {"*", "_"} and s.startswith(terminator * 2, p):
                closing = terminator * 2 in parents and s.startswith(terminator * 3, p)
            if closing and terminator:
                self.position += len(terminator)
                return
            c = s[p]
            if c == "\\":
                if p + 1 >= len(s) or s[p + 1].isalnum() or s[p + 1].isspace():
                    _fail("Backslash must escape a punctuation character")
                self.append(s[p + 1])
                self.position += 2
                continue
            if c == "`":
                if s.startswith("``", p):
                    _fail("Inline code requires a single backtick delimiter")
                end = s.find("`", p + 1)
                if end < 0 or end == p + 1 or "\n" in s[p + 1 : end]:
                    _fail("Inline code must be nonempty and closed on one line")
                start = self.length
                self.append(s[p + 1 : end])
                self.entities.append(
                    {"type": "code", "offset": start, "length": self.length - start}
                )
                self.position = end + 1
                continue
            if c == "[":
                if in_link:
                    _fail("Links cannot be nested")
                self.position += 1
                start = self.length
                self.parse("]", depth + 1, True, parents + ((terminator,) if terminator else ()))
                if self.length == start or not s.startswith("(", self.position):
                    _fail(
                        "Links require a nonempty label and inline URL; references are unsupported"
                    )
                self.position += 1
                begin = self.position
                balance = 1
                while self.position < len(s) and balance:
                    if s[self.position] == "(":
                        balance += 1
                    elif s[self.position] == ")":
                        balance -= 1
                    self.position += 1
                if balance:
                    _fail("Link URL is not closed")
                url = s[begin : self.position - 1]
                _url(url)
                self.entities.append(
                    {
                        "type": "text_link",
                        "offset": start,
                        "length": self.length - start,
                        "url": url,
                    }
                )
                continue
            if c == "]":
                _fail("Unmatched link delimiter")
            if c == "!" and s.startswith("![", p):
                _fail("Markdown images are unsupported")
            if c == "<" and re.match(r"</?[A-Za-z!]|<\?", s[p:]):
                _fail("HTML and angle-bracket links are unsupported")
            marker = next((m for m in _STYLES if s.startswith(m, p)), None)
            if marker == "_" and p and p + 1 < len(s) and s[p - 1].isalnum() and s[p + 1].isalnum():
                marker = None
            if marker:
                self.position += len(marker)
                start = self.length
                self.parse(
                    marker, depth + 1, in_link, parents + ((terminator,) if terminator else ())
                )
                if self.length == start:
                    _fail("Formatting spans must not be empty")
                self.entities.append(
                    {
                        "type": _STYLES[marker],
                        "offset": start,
                        "length": self.length - start,
                    }
                )
                continue
            if c in {"~", "|"}:
                # Single pipes and tildes are literal; block tables are rejected below.
                self.append(c)
                self.position += 1
                continue
            self.append(c)
            self.position += 1
        if terminator:
            _fail("Markdown contains an unclosed formatting or link delimiter")


def _markdown(source: str) -> tuple[str, list[dict[str, Any]]]:
    lines = source.splitlines(keepends=True)
    parts: list[str] = []
    entities: list[dict[str, Any]] = []
    offset = 0
    index = 0
    quote_start: int | None = None
    quote_end = 0

    def finish_quote() -> None:
        nonlocal quote_start
        if quote_start is not None:
            if quote_end <= quote_start:
                _fail("Blockquotes must contain text")
            entities.append(
                {"type": "blockquote", "offset": quote_start, "length": quote_end - quote_start}
            )
            quote_start = None

    while index < len(lines):
        raw = lines[index]
        body = raw.rstrip("\r\n")
        ending = raw[len(body) :]
        fence = re.fullmatch(r" {0,3}(`{3,})([A-Za-z0-9_+-]*)[ \t]*", body)
        if fence:
            finish_quote()
            delimiter, language = fence.groups()
            index += 1
            code: list[str] = []
            while index < len(lines) and not re.fullmatch(
                r" {0,3}" + re.escape(delimiter) + r"[ \t]*[\r\n]*",
                lines[index],
            ):
                code.append(lines[index])
                index += 1
            if index >= len(lines) or not code or not "".join(code).strip():
                _fail("Fenced code must be nonempty and have a matching closing fence")
            text = "".join(code)
            entity: dict[str, Any] = {"type": "pre", "offset": offset, "length": _units(text)}
            if language:
                entity["language"] = language
            entities.append(entity)
            parts.append(text)
            offset += _units(text)
            # Code's last newline already separates the following paragraph.
            index += 1
            continue
        if "```" in body or re.match(r" {0,3}~~~", body):
            _fail("Code fences must use standalone matching backtick lines")
        if re.match(r"\s*\|?\s*:?-{3,}:?\s*\|", body):
            _fail("Markdown tables are unsupported; use plain text or lists")
        if re.match(r"\s*\[[^]]+\]:", body):
            _fail("Reference links are unsupported")
        if re.match(r"\s*(?:[-*_]\s*){3,}$", body) or re.fullmatch(r"\s*={3,}\s*", body):
            _fail("Horizontal rules and setext headings are unsupported; use ATX headings")
        quote = re.match(r" {0,3}>[ \t]?(.*)$", body)
        if quote:
            body = quote.group(1)
            if body.startswith(">"):
                _fail("Nested blockquotes are unsupported")
            if quote_start is None:
                quote_start = offset
        else:
            finish_quote()
        heading = re.match(r" {0,3}#{1,6}[ \t]+(.*)$", body)
        if heading:
            body = heading.group(1)
        # A leading list marker is literal even when '*' is the bullet.
        bullet = re.match(r"(\s*(?:[-+*]|\d+[.)])[ \t]+)(.*)$", body)
        parser = _Inline(bullet.group(2) if bullet else body)
        prefix = bullet.group(1) if bullet else ""
        parser.append(prefix)
        parser.parse()
        text = "".join(parser.parts)
        for entity in parser.entities:
            entities.append({**entity, "offset": entity["offset"] + offset})
        if heading:
            if not text.strip():
                _fail("Headings must contain text")
            entities.append({"type": "bold", "offset": offset, "length": _units(text)})
        parts.append(text + ending)
        if quote:
            quote_end = offset + _units(text)
        offset += _units(text + ending)
        index += 1
    finish_quote()
    return "".join(parts), entities


def _validate_entities(text: str, entities: list[dict[str, Any]]) -> None:
    if len(entities) > MAX_ENTITIES:
        _fail("Formatting exceeds the supported entity count")
    boundaries = {0}
    total = 0
    for c in text:
        total += _units(c)
        boundaries.add(total)
    seen: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict):
            _fail("Saved formatting entities are invalid")
        kind = entity.get("type")
        if not isinstance(kind, str) or kind not in _ENTITY_TYPES:
            _fail("Formatting entity type is unsupported")
        fields = {"type", "offset", "length"}
        if kind == "text_link":
            fields.add("url")
            if not isinstance(entity.get("url"), str):
                _fail("Saved link URL is invalid")
            _url(entity["url"])
        if kind == "pre" and "language" in entity:
            fields.add("language")
            if not isinstance(entity["language"], str) or not re.fullmatch(
                r"[A-Za-z0-9_+-]{1,64}",
                entity["language"],
            ):
                _fail("Code language is invalid")
        if set(entity) != fields:
            _fail("Saved formatting entity fields are invalid")
        start, length = entity.get("offset"), entity.get("length")
        if type(start) is not int or type(length) is not int or length <= 0:
            _fail("Formatting entity offsets and lengths are invalid")
        if start not in boundaries or start + length not in boundaries:
            _fail("Formatting entity splits a Unicode character or exceeds text")
        canonical = json.dumps(entity, sort_keys=True)
        if canonical in seen:
            _fail("Duplicate formatting entities are invalid")
        seen.add(canonical)
    for i, left in enumerate(entities):
        a, b = left["offset"], left["offset"] + left["length"]
        for right in entities[i + 1 :]:
            c, d = right["offset"], right["offset"] + right["length"]
            if max(a, c) >= min(b, d):
                continue
            if not ((a <= c and d <= b) or (c <= a and b <= d)):
                _fail("Formatting entity ranges must not cross")
            kinds = {left["type"], right["type"]}
            if kinds & {"code", "pre"}:
                _fail("Code cannot overlap other formatting")
            if left["type"] not in _INLINE_STYLES and right["type"] not in _INLINE_STYLES:
                _fail("Links and blockquotes cannot contain each other")


def _text_limits(text: str, max_length: int, allow_empty: bool) -> None:
    _unicode(text)
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        _fail("Formatting length limit is invalid")
    if not text.strip() and not (allow_empty and text == ""):
        _fail("Text must not be empty or whitespace only", "invalid_text")
    if _units(text) > max_length:
        _fail(
            f"Converted text exceeds the conservative {max_length} UTF-16 unit limit",
            "text_too_long",
        )


def validate_entities(
    text: str,
    entities: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate the exact outbound entities without parsing the source again."""
    if not isinstance(text, str):
        _fail("Formatting text must be a string")
    _unicode(text)
    if not isinstance(entities, Sequence) or any(
        not isinstance(entity, Mapping) for entity in entities
    ):
        _fail("Formatting entities must be a sequence of objects")
    copied = [dict(entity) for entity in entities]
    _validate_entities(text, copied)
    return tuple(deepcopy(copied))


def compile_text(
    source_text: str,
    text_format: str = "plain",
    max_length: int = 4096,
    allow_empty: bool = False,
) -> FormattingPreview:
    """Compile and snapshot; no IO, guessing, splitting or external effects."""
    if (
        not isinstance(source_text, str)
        or not isinstance(text_format, str)
        or text_format not in {"plain", "markdown"}
    ):
        _fail("Text must be a string and text_format must be plain or markdown")
    _unicode(source_text)
    if len(source_text) > MAX_SOURCE_LENGTH:
        _fail("Source text exceeds the supported parsing limit", "text_too_long")
    text, entities = (source_text, []) if text_format == "plain" else _markdown(source_text)
    _text_limits(text, max_length, allow_empty)
    # Equal style entities (for example '# **Heading**') have one meaning.
    entities = list({json.dumps(e, sort_keys=True): e for e in entities}.values())
    entities.sort(key=lambda e: (e["offset"], -e["length"], e["type"]))
    _validate_entities(text, entities)
    value = {
        "source_text": source_text,
        "text": text,
        "text_format": text_format,
        "converter_version": CONVERTER_VERSION,
        "entities": entities,
    }
    return FormattingPreview(
        source_text, text, text_format, CONVERTER_VERSION, tuple(deepcopy(entities)), _digest(value)
    )


def stored_preview(
    text: str,
    metadata: dict[str, Any],
    key: str = FORMAT_KEY,
    max_length: int = 4096,
    allow_empty: bool = False,
) -> FormattingPreview | None:
    """Validate saved snapshot exactly, without rerendering; absent key is legacy."""
    if not isinstance(metadata, dict):
        _fail("Saved formatting metadata must be an object")
    if key not in metadata:
        return None
    value = metadata[key]
    if isinstance(value, dict) and value.get("text_format") == "rich":
        if key != FORMAT_KEY:
            _fail("Rich document captions are unsupported", "rich_surface_unsupported")
        from app.application.telegram_rich import stored_rich_preview
        return stored_rich_preview(text, value)
    if not isinstance(value, dict) or set(value) != _FIELDS:
        _fail("Saved formatting snapshot structure is invalid")
    if any(not isinstance(value[k], str) for k in _FIELDS - {"entities"}):
        _fail("Saved formatting snapshot fields are invalid")
    if value["text"] != text or value["text_format"] not in {"plain", "markdown"}:
        _fail("Saved formatting no longer matches the draft", "formatting_changed")
    if value["converter_version"] != CONVERTER_VERSION:
        _fail("Saved formatting version is unsupported")
    _unicode(value["source_text"])
    if len(value["source_text"]) > MAX_SOURCE_LENGTH:
        _fail("Saved formatting source exceeds the supported limit")
    _text_limits(text, max_length, allow_empty)
    if not isinstance(value["entities"], list):
        _fail("Saved formatting entities must be a list")
    entities = value["entities"]
    _validate_entities(text, entities)
    if entities != sorted(entities, key=lambda e: (e["offset"], -e["length"], e["type"])):
        _fail("Saved formatting entities are not canonical")
    if value["text_format"] == "plain" and (entities or value["source_text"] != text):
        _fail("Plain formatting must contain literal source text without entities")
    if value["payload_sha256"] != _digest(value):
        _fail("Saved formatting digest does not match its payload", "formatting_changed")
    return FormattingPreview(
        value["source_text"],
        text,
        value["text_format"],
        CONVERTER_VERSION,
        tuple(deepcopy(entities)),
        value["payload_sha256"],
    )

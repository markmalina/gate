"""Synthetic converter, immutable snapshot and Telegram entity contract tests."""

from copy import deepcopy

import pytest
from app.application.telegram_formatting import (
    CAPTION_FORMAT_KEY,
    FORMAT_KEY,
    FormattingError,
    compile_text,
    stored_preview,
    validate_entities,
)


def test_plain_is_literal_including_whitespace_and_markdown() -> None:
    source = "  **text** _word_ `code` <b>tag</b>\n "
    result = compile_text(source)
    assert result.text == source
    assert result.source_text == source
    assert result.text_format == "plain"
    assert result.entities == ()
    assert stored_preview(source, {FORMAT_KEY: result.to_dict()}) == result


def test_synthetic_incident_preview_exact_text_and_entities() -> None:
    source = (
        "**Итог**\n- **Важно:** задача готова.\n"
        "- *Примечание:* проверить оформление.\n"
        "- Код: `sample_value`\n"
        "- [Документация](https://example.com/docs?a=1&b=2)"
    )
    result = compile_text(source, "markdown")
    assert result.text == (
        "Итог\n- Важно: задача готова.\n- Примечание: проверить оформление.\n"
        "- Код: sample_value\n- Документация"
    )
    assert result.entities == (
        {"type": "bold", "offset": 0, "length": 4},
        {"type": "bold", "offset": 7, "length": 6},
        {"type": "italic", "offset": 31, "length": 11},
        {"type": "code", "offset": 72, "length": 12},
        {
            "type": "text_link",
            "offset": 87,
            "length": 12,
            "url": "https://example.com/docs?a=1&b=2",
        },
    )


def test_emoji_and_cyrillic_use_utf16_units_and_unicode_boundaries() -> None:
    result = compile_text("😀 **Привет 😀**", "markdown")
    assert result.text == "😀 Привет 😀"
    assert result.entities == ({"type": "bold", "offset": 3, "length": 9},)
    with pytest.raises(FormattingError, match="Unicode"):
        validate_entities(result.text, [{"type": "bold", "offset": 1, "length": 2}])


@pytest.mark.parametrize(
    "source,text,types",
    [
        ("**bold *italic***", "bold italic", ["bold", "italic"]),
        ("*italic **bold***", "italic bold", ["italic", "bold"]),
        ("__underline _italic___", "underline italic", ["underline", "italic"]),
        ("_italic __underline___", "italic underline", ["italic", "underline"]),
        ("***both***", "both", ["bold", "italic"]),
        ("~~strike~~ ||hidden||", "strike hidden", ["strikethrough", "spoiler"]),
        ("**[link](https://example.com/a(b)?x=1&y=2)**", "link", ["bold", "text_link"]),
        ("[**link**](https://example.com)", "link", ["bold", "text_link"]),
    ],
)
def test_nested_inline_styles_are_telegram_valid(source: str, text: str, types: list[str]) -> None:
    result = compile_text(source, "markdown")
    assert result.text == text
    assert [e["type"] for e in result.entities] == types
    assert validate_entities(text, result.entities) == result.entities


def test_headings_lists_quotes_code_and_newlines() -> None:
    result = compile_text(
        "# **Title**\n* **first**\n2. _second_\n> quote **strong**\n> next\n"
        "```python\nvalue = '<b>**literal**</b>'\n```\nend\n",
        "markdown",
    )
    assert result.text == (
        "Title\n* first\n2. second\nquote strong\nnext\nvalue = '<b>**literal**</b>'\nend\n"
    )
    assert [e["type"] for e in result.entities] == [
        "bold",
        "bold",
        "italic",
        "blockquote",
        "bold",
        "pre",
    ]
    pre = result.entities[-1]
    assert pre["language"] == "python"
    assert len([e for e in result.entities if e["offset"] == 0]) == 1


def test_escapes_specials_query_string_and_intraword_underscore() -> None:
    result = compile_text(
        r"\*\*literal\*\* sample_value A&B <3 [go](https://example.com/?q=a_b&v=2)",
        "markdown",
    )
    assert result.text == "**literal** sample_value A&B <3 go"
    assert result.entities[0]["url"] == "https://example.com/?q=a_b&v=2"


@pytest.mark.parametrize(
    "source",
    [
        "**unclosed",
        "*unclosed",
        "__unclosed",
        "~~unclosed",
        "||unclosed",
        "`unclosed",
        "``code``",
        "```,bad\nvalue\n```",
        "```\nvalue\n",
        "```\n\n```",
        "**cross *ranges** end*",
        "**`code`**",
        "# `code`",
        "[link]",
        "[label][ref]",
        "[ref]: https://example.com",
        "![img](https://example.com)",
        "<b>text</b>",
        "<https://example.com>",
        "| a | b |\n| --- | --- |",
        ">> nested",
        "> `code`",
        "[x](javascript:alert(1))",
        "[x](https://)",
        "[x](https://example.com/has space)",
        "[x](https://example.com:bad)",
        "[x](https://example.com",
        "[[x](https://example.com)](https://example.com)",
        "---",
        "~~~python\nx\n~~~",
        "x\\",
        r"x\q",
        "[]()",
        "**line\nbreak**",
    ],
)
def test_malformed_or_unsupported_markdown_is_rejected_safely(source: str) -> None:
    with pytest.raises(FormattingError) as exc:
        compile_text(source, "markdown")
    assert exc.value.code in {"invalid_formatting", "invalid_text"}
    assert source not in exc.value.message


@pytest.mark.parametrize("source", ["", " ", "\n\t", "\ud800", "hello\x00world"])
def test_empty_invalid_unicode_and_nul_are_rejected(source: str) -> None:
    with pytest.raises(FormattingError):
        compile_text(source)


def test_caption_empty_and_conservative_limits_after_conversion() -> None:
    assert compile_text("", "markdown", allow_empty=True, max_length=1024).text == ""
    assert compile_text("**" + "x" * 4096 + "**", "markdown").text == "x" * 4096
    assert compile_text("😀" * 2048).text == "😀" * 2048
    assert compile_text("**" + "x" * 1024 + "**", "markdown", max_length=1024).text == "x" * 1024
    for source, limit in [("x" * 4097, 4096), ("😀" * 2049, 4096), ("😀" * 513, 1024)]:
        with pytest.raises(FormattingError) as exc:
            compile_text(source, max_length=limit)
        assert exc.value.code == "text_too_long"


def test_snapshot_legacy_caption_and_no_rerender(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.application.telegram_formatting as formatting

    result = compile_text("**caption**", "markdown", max_length=1024)
    monkeypatch.setattr(formatting, "_markdown", lambda _: pytest.fail("rerender is forbidden"))
    assert (
        stored_preview(
            result.text,
            {CAPTION_FORMAT_KEY: result.to_dict()},
            key=CAPTION_FORMAT_KEY,
            max_length=1024,
        )
        == result
    )
    assert stored_preview("old", {}) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_text", "other source"),
        ("text", "other text"),
        ("text_format", "plain"),
        ("converter_version", "v999"),
        ("payload_sha256", "0" * 64),
        ("entities", [{"type": "italic", "offset": 0, "length": 5}]),
        ("entities", [{"type": "bold", "offset": True, "length": 5}]),
        ("entities", [{"type": "bold", "offset": 0, "length": 5, "extra": "x"}]),
        ("entities", None),
    ],
)
def test_snapshot_mutations_fail_closed(field: str, value: object) -> None:
    result = compile_text("**hello**", "markdown")
    snapshot = result.to_dict()
    snapshot[field] = value
    with pytest.raises(FormattingError):
        stored_preview(result.text, {FORMAT_KEY: snapshot})


def test_digest_binds_source_entities_link_and_mode_even_for_equal_text() -> None:
    assert compile_text("hello").payload_sha256 != compile_text("hello", "markdown").payload_sha256
    assert (
        compile_text("*hello*", "markdown").payload_sha256
        != compile_text(
            "_hello_",
            "markdown",
        ).payload_sha256
    )
    left = compile_text("[hello](https://example.com/a)", "markdown")
    right = compile_text("[hello](https://example.com/b)", "markdown")
    assert left.text == right.text
    assert left.payload_sha256 != right.payload_sha256
    snapshot = left.to_dict()
    snapshot["entities"][0]["url"] = "https://example.com/b"
    with pytest.raises(FormattingError, match="digest"):
        stored_preview(left.text, {FORMAT_KEY: snapshot})


@pytest.mark.parametrize(
    "entities",
    [
        [{"type": "bold", "offset": 0, "length": 4}, {"type": "italic", "offset": 2, "length": 4}],
        [{"type": "code", "offset": 0, "length": 2}, {"type": "bold", "offset": 0, "length": 2}],
        [
            {"type": "blockquote", "offset": 0, "length": 6},
            {"type": "blockquote", "offset": 1, "length": 3},
        ],
        [{"type": "unknown", "offset": 0, "length": 2}],
        [{"type": "bold", "offset": -1, "length": 2}],
        [{"type": "bold", "offset": 0, "length": 0}],
        [{"type": "bold", "offset": 0, "length": 10}],
        [{"type": "bold", "offset": 0, "length": 1}] * 2,
    ],
)
def test_transport_boundary_rejects_invalid_ranges_and_types(entities: list[dict]) -> None:
    with pytest.raises(FormattingError):
        validate_entities("abcdef", entities)


def test_snapshots_do_not_alias_mutable_entities() -> None:
    result = compile_text("**hello**", "markdown")
    snapshot = result.to_dict()
    stored = stored_preview(result.text, {FORMAT_KEY: snapshot})
    assert stored is not None
    before = deepcopy(stored.entities)
    snapshot["entities"][0]["type"] = "italic"
    assert stored.entities == before
    assert result.entities == before


def test_bounded_input_depth_entity_count_and_argument_types() -> None:
    with pytest.raises(FormattingError):
        compile_text("x" * 65537)
    with pytest.raises(FormattingError):
        compile_text(" ".join("**x**" for _ in range(101)), "markdown")
    for invalid_format in [None, [], 1, "html"]:
        with pytest.raises(FormattingError):
            compile_text("x", invalid_format)  # type: ignore[arg-type]
    with pytest.raises(FormattingError):
        compile_text("x", max_length=True)


def test_nesting_bound_is_enforced_before_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.application.telegram_formatting as formatting

    monkeypatch.setattr(formatting, "MAX_NESTING", 1)
    with pytest.raises(FormattingError, match="nesting"):
        compile_text("**bold *italic***", "markdown")


def test_malformed_snapshot_envelope_is_safe() -> None:
    for value in [None, "bad", [], {}, {"extra": "field"}]:
        with pytest.raises(FormattingError):
            stored_preview("x", {FORMAT_KEY: value})
    with pytest.raises(FormattingError):
        stored_preview("x", None)  # type: ignore[arg-type]

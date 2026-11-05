"""Native rich MCP schema and shared HTTP payload checks, without external writes."""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.api import mcp_routes
from app.application.reply_drafts import SavedReplyDraft
from app.application.telegram_formatting import CAPTION_FORMAT_KEY, FormattingError, stored_preview
from app.application.telegram_rich import compile_rich, rich_content_schema
from app.services.telegram import TelegramClient
from jsonschema import Draft202012Validator, ValidationError

from test_reply_formatting_mcp_transport import _output, _principal, _settings
from test_reply_rich_send import expected_blocks, rich_fixture


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_tools_list_rich_schema_matches_actual_contract() -> None:
    schema = mcp_routes._gate_save_reply_draft_tool(_settings())["inputSchema"]
    validator = Draft202012Validator(schema)
    base = {
        "expected_context_snapshot": "dialog-v1:" + "0" * 64, "task_id": str(uuid4()),
        "text_format": "rich", "rich_content": rich_fixture(),
    }
    validator.validate(base)
    validator.validate({**base, "text": ""})
    for changed in (
        {**base, "text": "legacy conflict"},
        {**base, "rich_content": None},
        {**base, "text_format": "plain"},
        {**base, "text_format": "markdown"},
        {"task_id": base["task_id"], "text_format": "rich"},
        {**base, "parse_mode": "HTML"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(changed)
    for kind in ("document", "photo", "details", "thinking", "mathematical_expression"):
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **base,
                    "rich_content": {
                        "schema_version": "gate.rich.v1",
                        "blocks": [{"type": kind, "text": "x"}],
                    },
                }
            )


@pytest.mark.anyio
async def test_mcp_rich_save_complete_immutable_preview_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = _principal()
    scope = {
        "workspace_id": principal.access_scope.workspace_id,
        "account_id": str(next(iter(principal.access_scope.account_ids))),
        "project_id": str(next(iter(principal.access_scope.project_ids))),
        "recipient_chat_id": "synthetic-chat",
        "connection_id": "synthetic-connection",
        "draft_id": str(uuid4()),
    }

    class Writer:
        async def save(self, command: Any) -> SavedReplyDraft:
            preview = command.formatting.bind(scope)
            return SavedReplyDraft(
                draft_id=uuid4(),
                task_id=command.task_id,
                account_id=uuid4(),
                project_id=uuid4(),
                account_alias="ru",
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                status="draft",
                text=command.text,
                formatting=preview,
            )

    monkeypatch.setattr(mcp_routes, "SqlReplyDraftWriter", lambda session: Writer())
    session = SimpleNamespace(commit=AsyncMock())
    result = await mcp_routes._save_reply_draft_tool_result(
        request_id=1,
        arguments={"task_id": str(uuid4()), "text_format": "rich", "rich_content": rich_fixture()},
        principal=principal,
        session=session,
    )
    content = _output(mcp_routes._gate_save_reply_draft_tool(_settings()), result)
    assert content["sent"] is False and content["approval_required"] is True
    assert content["formatting"]["blocks"] == expected_blocks()
    assert content["formatting"]["scope"] == scope
    assert content["formatting"]["source_content"] == rich_fixture()
    assert (
        content["confirmation"]["expected_payload_sha256"]
        == content["formatting"]["payload_sha256"]
    )
    assert "Готово 💚" in content["text"]
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_rich_provider_uses_shared_native_method_without_guessing_or_retry() -> None:
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 737,
                    "chat": {"id": "synthetic-chat"},
                    "business_connection_id": "synthetic-connection",
                    "rich_message": {"blocks": expected_blocks()},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        client = TelegramClient("synthetic-only", http_client=http)
        await client.send_rich_message(
            chat_id="synthetic-chat",
            business_connection_id="synthetic-connection",
            blocks=expected_blocks(),
        )
    assert len(requests) == 1 and requests[0].url.path.endswith("/sendRichMessage")
    assert json.loads(requests[0].content) == {
        "chat_id": "synthetic-chat",
        "business_connection_id": "synthetic-connection",
        "rich_message": {"blocks": expected_blocks(), "skip_entity_detection": True},
    }


@pytest.mark.anyio
async def test_invalid_saved_native_blocks_rejected_before_http() -> None:
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Provider must not be called")

    broken = deepcopy(expected_blocks())
    broken[-1]["cells"][1].pop()
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        client = TelegramClient("synthetic-only", http_client=http)
        with pytest.raises(FormattingError):
            await client.send_rich_message(
                chat_id="synthetic-chat",
                business_connection_id="synthetic-connection",
                blocks=broken,
            )
    assert requests == []


def test_rich_caption_rejected_explicitly() -> None:
    preview = compile_rich(rich_fixture())
    with pytest.raises(FormattingError) as error:
        stored_preview(
            preview.text, {CAPTION_FORMAT_KEY: preview.to_dict()}, key=CAPTION_FORMAT_KEY
        )
    assert error.value.code == "rich_surface_unsupported"


@pytest.mark.parametrize(
    "limit,valid,invalid",
    [
        (
            "blocks",
            [{"type": "paragraph", "text": "x"}] * 32,
            [{"type": "paragraph", "text": "x"}] * 33,
        ),
        ("code", [{"type": "pre", "text": "x" * 8192}], [{"type": "pre", "text": "x" * 8193}]),
        (
            "total",
            [{"type": "paragraph", "text": "x" * 16000}],
            [{"type": "paragraph", "text": "x" * 16001}],
        ),
        (
            "columns",
            [{"type": "table", "header": ["h"] * 6, "rows": [["x"] * 6]}],
            [{"type": "table", "header": ["h"] * 7, "rows": [["x"] * 7]}],
        ),
        (
            "rows",
            [{"type": "table", "header": ["h"], "rows": [["x"]] * 20}],
            [{"type": "table", "header": ["h"], "rows": [["x"]] * 21}],
        ),
        (
            "cell",
            [{"type": "table", "header": ["h"], "rows": [["x" * 512]]}],
            [{"type": "table", "header": ["h"], "rows": [["x" * 513]]}],
        ),
        ("list", [{"type": "list", "items": ["x"] * 32}], [{"type": "list", "items": ["x"] * 33}]),
    ],
)
def test_application_limit_boundary_and_excess(limit: str, valid: Any, invalid: Any) -> None:
    content = {"schema_version": "gate.rich.v1", "blocks": valid}
    Draft202012Validator(rich_content_schema()).validate(content)
    assert compile_rich(content).blocks
    with pytest.raises(FormattingError):
        compile_rich({**content, "blocks": invalid})


def test_production_rich_activation_is_source_controlled_and_staging_is_off() -> None:
    import tomllib
    from pathlib import Path

    from app.core.config import Settings

    config = tomllib.loads((Path(__file__).parents[1] / "railway.toml").read_text())
    assert (
        "export GATE_RICH_SEND_ENABLED=${GATE_RICH_SEND_ENABLED:-true}"
        in config["deploy"]["startCommand"]
    )
    assert (
        "GATE_RICH_SEND_ENABLED" not in config["environments"]["staging"]["deploy"]["startCommand"]
    )
    assert Settings(_env_file=None, GATE_RICH_SEND_ENABLED=False).gate_rich_send_enabled is False
    assert Settings(_env_file=None, GATE_RICH_SEND_ENABLED=True).gate_rich_send_enabled is True


def test_inline_depth_node_and_byte_bounds() -> None:
    def content(text: Any) -> dict[str, Any]:
        return {"schema_version": "gate.rich.v1", "blocks": [{"type": "paragraph", "text": text}]}

    nested: Any = "x"
    for _ in range(4):
        nested = {"type": "bold", "text": nested}
    assert compile_rich(content(nested)).blocks
    with pytest.raises(FormattingError):
        compile_rich(content({"type": "bold", "text": nested}))
    assert compile_rich(content(["x"] * 511)).blocks
    with pytest.raises(FormattingError):
        compile_rich(content(["x"] * 512))
    link = {
        "type": "url",
        "text": "x",
        "url": "https://example.com/?q=" + "x" * (2048 - len("https://example.com/?q=")),
    }
    assert compile_rich(content([link] * 30)).blocks
    with pytest.raises(FormattingError):
        compile_rich(content([link] * 32))


def test_metadata_declares_opt_in_without_claiming_account_or_visual_support() -> None:
    from app.api.mcp_metadata_routes import _mcp_metadata

    data = _mcp_metadata(settings=_settings())
    rich = data["reply_formats"]["rich"]
    assert data["reply_formats"]["text_modes"] == ["plain", "markdown", "rich"]
    assert rich["explicit_opt_in"] is True
    assert rich["series_supported"] is False and rich["captions_supported"] is False
    assert rich["provider_capability"] == "requires_live_business_user_support"
    assert rich["visual_capability"] == "requires_client_verification"


def test_exact_issue_fixtures_and_operator_example_validate() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1]
    fixtures = json.loads((root / "tests/fixtures/gate_rich_sprint1.json").read_text())
    example = json.loads((root / "docs/specs/ISSUE-737-RICH-OPERATOR-EXAMPLE.json").read_text())
    schema = mcp_routes._gate_save_reply_draft_tool(_settings())["inputSchema"]
    for name in ("P", "D", "L", "M"):
        Draft202012Validator(schema).validate({"task_id": fixtures["task_id"],
             "expected_context_snapshot": "dialog-v1:" + "0" * 64, **fixtures[name]})
    Draft202012Validator(schema).validate(example["params"]["arguments"])
    preview = compile_rich(fixtures["D"]["rich_content"])
    assert [block["type"] for block in preview.blocks] == [
        "heading",
        "paragraph",
        "list",
        "blockquote",
        "table",
        "pre",
    ]
    assert (
        preview.blocks[-1]["text"]
        == "def sample_total(values):\n    return sum(values)\n\nprint(sample_total([1, 2, 3]))\n"
    )
    assert example["params"]["arguments"]["rich_content"] == fixtures["D"]["rich_content"]


def test_last_known_good_runtime_fails_closed_on_saved_rich_snapshot() -> None:
    """Execute the pinned unchanged LKG reader, rather than emulating its refusal."""
    import hashlib
    import runpy
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures/telegram_formatting_lkg_d99cd943.py.txt"
    original = fixture.read_bytes()
    # Git blob identity from the restored d99cd943 main tree.
    assert (
        hashlib.sha1(b"blob " + str(len(original)).encode() + b"\0" + original).hexdigest()
        == "5ddc5ab6e6459384561af7632d3da050fae806aa"
    )
    legacy = runpy.run_path(str(fixture))
    preview = compile_rich(rich_fixture())
    with pytest.raises(legacy["FormattingError"]) as denied:
        legacy["stored_preview"](preview.text, {legacy["FORMAT_KEY"]: preview.to_dict()})
    assert denied.value.code == "invalid_formatting"

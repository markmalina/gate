"""Check the published Gate MCP contract used by a future refreshed client."""

from app.api.mcp_routes import (
    _gate_get_reply_context_tool,
    _gate_get_reply_sequence_tool,
    _gate_queue_approved_reply_sequence_tool,
)
from app.core.config import Settings


def test_sequence_client_requires_reviewed_revision_and_full_limit():
    settings = Settings(_env_file=None, GATE_REPLY_SEQUENCE_DISPATCH_V2_ENABLED=True)
    context = _gate_get_reply_context_tool(settings)
    queue = _gate_queue_approved_reply_sequence_tool(settings)
    detail = _gate_get_reply_sequence_tool(settings)

    schema = queue["inputSchema"]
    assert schema["properties"]["draft_ids"]["maxItems"] == 50
    assert "expected_inbound_revision" in schema["required"]
    assert schema["properties"]["expected_inbound_revision"]["minimum"] == 0
    assert "sequence_approval.ready" in queue["description"]
    assert "dialog_inbound_revision" in context["description"]

    assert detail["name"] == "gate_get_reply_sequence"
    assert detail["inputSchema"]["properties"]["limit"]["maximum"] == 50
    assert "next_cursor" in detail["description"]
    assert "draft_text_matches_approval" in detail["description"]

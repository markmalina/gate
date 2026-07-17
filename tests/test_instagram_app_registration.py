from app.api.mcp_routes import MCP_TOOL_SCOPES
from app.main import create_app


def test_instagram_routes_are_registered_in_real_gate_app() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/webhooks/instagram" in paths
    assert "/internal/instagram/commands" in paths
    assert "/mcp" in paths


def test_instagram_mcp_scopes_keep_reads_and_publication_separate() -> None:
    assert MCP_TOOL_SCOPES["gate_instagram_get_reel_insights"] == "gate:instagram:read"
    assert MCP_TOOL_SCOPES["gate_instagram_prepare_reel"] == "gate:instagram:publish"
    assert MCP_TOOL_SCOPES["gate_instagram_publish_reel"] == "gate:instagram:publish"

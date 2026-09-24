"""Regression contract for every public projection of the Gate MCP surface."""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from typing import Literal

import pytest
from app.api import mcp_metadata_routes, mcp_routes
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.core.config import Settings
from jsonschema import Draft202012Validator


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    scope: str
    metadata_bucket: Literal["read_only_tools", "write_tools", "send_tools"]
    descriptor_builder: str
    scope_is_read_only: bool = False


TOOL_CONTRACTS = (
    ToolContract(
        "gate_get_status",
        "gate:status:read",
        "read_only_tools",
        "_gate_status_tool",
        True,
    ),
    ToolContract(
        "gate_list_unanswered",
        "gate:inbox:read",
        "read_only_tools",
        "_gate_list_unanswered_tool",
        True,
    ),
    ToolContract(
        "gate_get_reply_context",
        "gate:inbox:read",
        "read_only_tools",
        "_gate_get_reply_context_tool",
        True,
    ),
    ToolContract(
        "gate_save_reply_draft",
        "gate:drafts:write",
        "write_tools",
        "_gate_save_reply_draft_tool",
    ),
    ToolContract(
        "gate_prepare_reply_document",
        "gate:drafts:write",
        "write_tools",
        "_gate_prepare_reply_document_tool",
    ),
    ToolContract(
        "gate_send_approved_reply",
        "gate:messages:send",
        "send_tools",
        "_gate_send_approved_reply_tool",
    ),
    ToolContract(
        "gate_send_approved_document",
        "gate:messages:send",
        "send_tools",
        "_gate_send_approved_document_tool",
    ),
    ToolContract(
        "gate_queue_approved_reply_sequence",
        "gate:messages:send",
        "send_tools",
        "_gate_queue_approved_reply_sequence_tool",
    ),
    ToolContract(
        "gate_alpha_list_installations",
        "gate:status:read",
        "read_only_tools",
        "_gate_alpha_list_installations_tool",
        True,
    ),
    ToolContract(
        "gate_alpha_get_webhook_status",
        "gate:status:read",
        "read_only_tools",
        "_gate_alpha_get_webhook_status_tool",
        True,
    ),
    ToolContract(
        "gate_alpha_control_installation",
        "gate:agents:manage",
        "write_tools",
        "_gate_alpha_control_installation_tool",
    ),
    ToolContract(
        "gate_alpha_run_model_preflight",
        "gate:agents:manage",
        "write_tools",
        "_gate_alpha_run_model_preflight_tool",
    ),
    ToolContract(
        "gate_alpha_list_shadow_reviews",
        "gate:agents:manage",
        "read_only_tools",
        "_gate_alpha_list_shadow_reviews_tool",
    ),
    ToolContract(
        "gate_alpha_get_test_group_presence",
        "gate:agents:manage",
        "write_tools",
        "_gate_alpha_get_test_group_presence_tool",
    ),
    ToolContract(
        "gate_alpha_send_review",
        "gate:messages:send",
        "send_tools",
        "_gate_alpha_send_review_tool",
    ),
    # Replay proof is read-only: it never crosses the provider boundary.
    ToolContract(
        "gate_alpha_verify_review_replay",
        "gate:status:read",
        "read_only_tools",
        "_gate_alpha_verify_review_replay_tool",
        True,
    ),
    ToolContract(
        "gate_beta_export_space_checkpoint",
        "gate:agents:manage",
        "read_only_tools",
        "_gate_beta_export_space_checkpoint_tool",
    ),
    ToolContract(
        "gate_beta_get_provider_probe",
        "gate:agents:manage",
        "write_tools",
        "_gate_beta_get_provider_probe_tool",
    ),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_every_mcp_projection_matches_the_eighteen_tool_contract() -> None:
    settings = Settings(
        _env_file=None,
        GATE_MCP_ENABLED=True,
        MCP_AUTH_MODE="oauth",
        MCP_AUTHORIZATION_SERVER="https://auth.gate.example",
        MCP_PUBLIC_RESOURCE_URL="https://gate.example/mcp",
        MCP_OAUTH_JWKS_URL="https://auth.gate.example/.well-known/jwks.json",
    )
    tools_response = await mcp_routes.mcp_endpoint(
        payload={"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
        authorization=None,
        settings=settings,
        session=None,  # type: ignore[arg-type] -- tools/list never opens a session
        telegram=None,  # type: ignore[arg-type] -- tools/list never sends
    )
    advertised_tools = tools_response["result"]["tools"]
    expected_names = [contract.name for contract in TOOL_CONTRACTS]
    expected_scopes = {
        contract.name: contract.scope for contract in TOOL_CONTRACTS
    }

    assert len(TOOL_CONTRACTS) == 18
    assert [tool["name"] for tool in advertised_tools] == expected_names
    assert mcp_routes.MCP_TOOL_NAMES == expected_names
    assert mcp_routes.MCP_TOOL_SCOPES == expected_scopes
    assert set(MCP_TOOL_OUTPUT_SCHEMAS) == set(expected_names)

    descriptors_by_name = {tool["name"]: tool for tool in advertised_tools}
    for contract in TOOL_CONTRACTS:
        descriptor = descriptors_by_name[contract.name]
        builder = getattr(mcp_routes, contract.descriptor_builder)
        assert builder(settings) == descriptor
        assert isinstance(descriptor.get("inputSchema"), dict)
        assert descriptor["outputSchema"] == MCP_TOOL_OUTPUT_SCHEMAS[contract.name]
        Draft202012Validator.check_schema(descriptor["inputSchema"])
        Draft202012Validator.check_schema(descriptor["outputSchema"])
        security = [{"type": "oauth2", "scopes": [contract.scope]}]
        assert descriptor["securitySchemes"] == security
        assert descriptor["_meta"]["securitySchemes"] == security

    endpoint_tree = ast.parse(
        textwrap.dedent(inspect.getsource(mcp_routes.mcp_endpoint))
    )
    assert _handled_tool_names(endpoint_tree) == set(expected_names)
    assert _status_uses_canonical_tool_names(endpoint_tree)

    metadata = mcp_metadata_routes._mcp_metadata(settings=settings)
    for bucket in ("read_only_tools", "write_tools", "send_tools"):
        assert metadata[bucket] == [
            contract.name
            for contract in TOOL_CONTRACTS
            if contract.metadata_bucket == bucket
        ]
    assert mcp_metadata_routes.MCP_RESOURCE_SCOPES == list(
        dict.fromkeys(contract.scope for contract in TOOL_CONTRACTS)
    )
    assert mcp_metadata_routes.MCP_READ_ONLY_SCOPES == list(
        dict.fromkeys(
            contract.scope
            for contract in TOOL_CONTRACTS
            if contract.scope_is_read_only
        )
    )


def _handled_tool_names(endpoint_tree: ast.AST) -> set[str]:
    handled: set[str] = set()
    for node in ast.walk(endpoint_tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if (
            not isinstance(comparison.left, ast.Name)
            or comparison.left.id != "tool_name"
            or len(comparison.ops) != 1
            or not isinstance(comparison.ops[0], ast.Eq)
            or len(comparison.comparators) != 1
            or not isinstance(comparison.comparators[0], ast.Constant)
            or not isinstance(comparison.comparators[0].value, str)
        ):
            continue
        assert any(
            isinstance(descendant, ast.Return)
            for statement in node.body
            for descendant in ast.walk(statement)
        )
        handled.add(comparison.comparators[0].value)
    return handled


def _status_uses_canonical_tool_names(endpoint_tree: ast.AST) -> bool:
    for node in ast.walk(endpoint_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "build_gate_status":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "tool_names"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "MCP_TOOL_NAMES"
            ):
                return True
    return False

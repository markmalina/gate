from uuid import UUID

import pytest

from app.core.mcp_security import bearer_token_matches, build_mcp_read_principal


def test_bearer_token_is_required_and_compared_safely() -> None:
    assert bearer_token_matches("Bearer dedicated-token", "dedicated-token") is True
    assert bearer_token_matches("bearer dedicated-token", "dedicated-token") is True
    assert bearer_token_matches(None, "dedicated-token") is False
    assert bearer_token_matches("Bearer wrong", "dedicated-token") is False
    assert bearer_token_matches("Basic dedicated-token", "dedicated-token") is False


def test_principal_requires_explicit_workspace_account_and_project_grants() -> None:
    principal = build_mcp_read_principal(
        workspace_id="mark-personal",
        account_grants="22222222-2222-2222-2222-222222222222",
        project_grants="33333333-3333-3333-3333-333333333333",
    )
    assert principal.access_scope.workspace_id == "mark-personal"
    assert principal.access_scope.account_ids == {
        UUID("22222222-2222-2222-2222-222222222222")
    }
    assert principal.access_scope.project_ids == {
        UUID("33333333-3333-3333-3333-333333333333")
    }

    with pytest.raises(ValueError, match="explicit workspace"):
        build_mcp_read_principal(
            workspace_id="",
            account_grants="22222222-2222-2222-2222-222222222222",
            project_grants="33333333-3333-3333-3333-333333333333",
        )


def test_principal_rejects_invalid_uuid_grants() -> None:
    with pytest.raises(ValueError, match="MCP_READ_ACCOUNT_IDS"):
        build_mcp_read_principal(
            workspace_id="mark-personal",
            account_grants="not-a-uuid",
            project_grants="33333333-3333-3333-3333-333333333333",
        )

"""Fail-closed security primitives for the temporary read-only MCP boundary."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AccessScope:
    """Explicit workspace, account, and project grants for one principal."""

    workspace_id: str
    account_ids: frozenset[UUID]
    project_ids: frozenset[UUID]

    @property
    def is_explicit(self) -> bool:
        """Return whether every containment boundary has at least one explicit grant."""

        return bool(self.workspace_id and self.account_ids and self.project_ids)


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated actor allowed to call the current read-only MCP tools."""

    subject: str
    auth_method: str
    scopes: frozenset[str]
    access_scope: AccessScope
    oauth_issuer: str | None = None
    oauth_client_id: str | None = None


def bearer_token_matches(authorization: str | None, expected_token: str) -> bool:
    """Validate a Bearer header without exposing or directly comparing token values."""

    if not authorization or not expected_token:
        return False
    scheme, separator, credential = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not credential:
        return False
    return secrets.compare_digest(credential, expected_token)


def build_mcp_read_principal(
    *,
    workspace_id: str,
    account_grants: str,
    project_grants: str,
) -> Principal:
    """Build the temporary environment-backed MCP principal and fail on incomplete grants."""

    access_scope = AccessScope(
        workspace_id=workspace_id.strip(),
        account_ids=_parse_uuid_grants(account_grants, setting_name="MCP_READ_ACCOUNT_IDS"),
        project_ids=_parse_uuid_grants(project_grants, setting_name="MCP_READ_PROJECT_IDS"),
    )
    if not access_scope.is_explicit:
        raise ValueError("MCP read access requires explicit workspace, account, and project grants")
    return Principal(
        subject="gate-mcp-read-token",
        auth_method="bearer",
        scopes=frozenset({"gate:status:read", "gate:inbox:read"}),
        access_scope=access_scope,
    )


def _parse_uuid_grants(value: str, *, setting_name: str) -> frozenset[UUID]:
    raw_values = [item.strip() for item in value.split(",") if item.strip()]
    try:
        return frozenset(UUID(item) for item in raw_values)
    except ValueError as exc:
        raise ValueError(f"{setting_name} must contain comma-separated UUID values") from exc

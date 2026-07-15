"""Enable the exact Alpha connector and finish its owner-approved binding.

Revision ID: 0021_enable_alpha_canary
Revises: 0020_alpha_canary_release
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_enable_alpha_canary"
down_revision: str | None = "0020_alpha_canary_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_OWNER_FLAGS = (
    "GATE_ALPHA_CONNECTOR_ENABLED",
    "GATE_TELEGRAM_WORKER_ENABLED",
    "GATE_ALPHA_SHADOW_WORKER_ENABLED",
    "GATE_ALPHA_CANARY_ENABLED",
    "GATE_ALPHA_DELIVERY_ENABLED",
    "GATE_ALPHA_PRIVACY_MODE_CONFIRMED",
)


def upgrade() -> None:
    """Enable and bind only the unique release-pinned Alpha installation."""

    if not _owner_activation_authorized():
        return
    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text(
            "SELECT id FROM workspaces "
            "WHERE key = 'mark-personal' AND is_active IS TRUE"
        )
    )
    if workspace_id is None:
        return

    connectors = bind.execute(
        sa.text(
            "SELECT id, is_enabled FROM transport_connectors "
            "WHERE workspace_id = :workspace_id "
            "AND connector_key = 'telegram_alpha_group' "
            "AND connector_kind = 'telegram_bot' ORDER BY id"
        ),
        {"workspace_id": workspace_id},
    ).mappings().all()
    if len(connectors) != 1:
        raise RuntimeError("alpha_canary_connector_state_ambiguous")
    connector = connectors[0]

    releases = bind.execute(
        sa.text(
            "SELECT d.id AS deployment_id, d.state AS deployment_state, "
            "r.id AS revision_id "
            "FROM agent_deployments d "
            "JOIN instruction_revisions r "
            "ON r.id = d.instruction_revision_id "
            "AND r.workspace_id = d.workspace_id "
            "JOIN agent_definitions a "
            "ON a.id = d.agent_definition_id "
            "AND a.workspace_id = d.workspace_id "
            "WHERE d.workspace_id = :workspace_id "
            "AND d.deployment_key = 'alpha-canary-v1-fixture' "
            "AND d.state IN ('staged', 'active') "
            "AND r.revision_key = 'alpha-canary-v1' "
            "AND r.source_commit_sha = "
            "'0b5295606694c537c2b59915d0c91e789f1cbb51' "
            "AND r.content_sha256 = "
            "'8a40570ed64fe29ba56b50975113a89bee5a59c22c4bf9cc56cf0abf75eba578' "
            "AND r.validation_state = 'validated' "
            "AND a.agent_key = 'alpha_group'"
        ),
        {"workspace_id": workspace_id},
    ).mappings().all()
    if len(releases) != 1:
        raise RuntimeError("alpha_canary_release_state_ambiguous")
    release = releases[0]

    installations = bind.execute(
        sa.text(
            "SELECT id, state, deployment_id, instruction_revision_id "
            "FROM agent_installations "
            "WHERE workspace_id = :workspace_id AND connector_id = :connector_id "
            "ORDER BY created_at, id"
        ),
        {"workspace_id": workspace_id, "connector_id": connector["id"]},
    ).mappings().all()
    if len(installations) != 1:
        raise RuntimeError("alpha_canary_group_state_ambiguous")
    installation = installations[0]
    exact_active = (
        connector["is_enabled"] is True
        and release["deployment_state"] == "active"
        and installation["state"] == "active"
        and installation["deployment_id"] == release["deployment_id"]
        and installation["instruction_revision_id"] == release["revision_id"]
    )
    if exact_active:
        return
    if installation["state"] not in {"discovered", "pending_approval"}:
        raise RuntimeError("alpha_canary_installation_not_pending")

    bind.execute(
        sa.text(
            "UPDATE transport_connectors SET is_enabled = TRUE "
            "WHERE id = :connector_id AND workspace_id = :workspace_id"
        ),
        {"connector_id": connector["id"], "workspace_id": workspace_id},
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'active' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'staged'"
        ),
        {
            "deployment_id": release["deployment_id"],
            "workspace_id": workspace_id,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_installations SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, state = 'active', "
            "max_turns_per_window = 5, rate_window_seconds = 60, "
            "max_tokens_per_turn = 2048, updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND state IN ('discovered', 'pending_approval')"
        ),
        {
            "deployment_id": release["deployment_id"],
            "revision_id": release["revision_id"],
            "installation_id": installation["id"],
            "workspace_id": workspace_id,
        },
    )


def downgrade() -> None:
    """Disable this connector and revoke the exact canary binding."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = 'mark-personal'")
    )
    if workspace_id is None:
        return
    release = bind.execute(
        sa.text(
            "SELECT d.id AS deployment_id, r.id AS revision_id "
            "FROM agent_deployments d "
            "JOIN instruction_revisions r "
            "ON r.id = d.instruction_revision_id "
            "AND r.workspace_id = d.workspace_id "
            "WHERE d.workspace_id = :workspace_id "
            "AND d.deployment_key = 'alpha-canary-v1-fixture' "
            "AND r.revision_key = 'alpha-canary-v1'"
        ),
        {"workspace_id": workspace_id},
    ).mappings().one_or_none()
    if release is None:
        return

    bind.execute(
        sa.text(
            "UPDATE delivery_grants SET revoked_at = now() "
            "WHERE workspace_id = :workspace_id AND used_at IS NULL "
            "AND revoked_at IS NULL AND installation_id IN ("
            "SELECT id FROM agent_installations "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_id = :deployment_id "
            "AND instruction_revision_id = :revision_id)"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": release["deployment_id"],
            "revision_id": release["revision_id"],
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_installations SET state = 'pending_approval', "
            "deployment_id = NULL, instruction_revision_id = NULL, updated_at = now() "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_id = :deployment_id "
            "AND instruction_revision_id = :revision_id AND state = 'active'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": release["deployment_id"],
            "revision_id": release["revision_id"],
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'staged' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'active'"
        ),
        {
            "deployment_id": release["deployment_id"],
            "workspace_id": workspace_id,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE transport_connectors SET is_enabled = FALSE "
            "WHERE workspace_id = :workspace_id "
            "AND connector_key = 'telegram_alpha_group' "
            "AND connector_kind = 'telegram_bot'"
        ),
        {"workspace_id": workspace_id},
    )


def _owner_activation_authorized() -> bool:
    return all(_enabled(os.getenv(name)) for name in _REQUIRED_OWNER_FLAGS)


def _enabled(value: str | None) -> bool:
    return value is not None and value.strip().casefold() in {"1", "true", "yes", "on"}

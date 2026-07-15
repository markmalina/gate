"""Pause the exact fixed Alpha canary for a bounded owner live drill.

Revision ID: 0024_pause_alpha_canary_drill
Revises: 0023_alpha_model_release
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_pause_alpha_canary_drill"
down_revision: str | None = "0023_alpha_model_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_CONNECTOR_KEY = "telegram_alpha_group"
_DEPLOYMENT_KEY = "alpha-canary-v1-fixture"
_REVISION_KEY = "alpha-canary-v1"
_SOURCE_COMMIT_SHA = "0b5295606694c537c2b59915d0c91e789f1cbb51"
_CONTENT_SHA256 = "558db275ab8424e108aabf06687623a3f37ef6f2ac6ea0cd12eace6962a92bca"


def upgrade() -> None:
    """Pause only the unique exact active fixed-canary installation."""

    bind = op.get_bind()
    installation = _exact_installation(bind)
    if installation is None:
        return
    if installation["state"] == "paused":
        return
    if installation["state"] != "active":
        raise RuntimeError("alpha_pause_drill_installation_not_active")

    updated = bind.execute(
        sa.text(
            "UPDATE agent_installations SET state = 'paused', updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND state = 'active'"
        ),
        {
            "installation_id": installation["installation_id"],
            "workspace_id": installation["workspace_id"],
        },
    ).rowcount
    if updated != 1:
        raise RuntimeError("alpha_pause_drill_state_race")
    bind.execute(
        sa.text(
            "UPDATE delivery_grants SET revoked_at = now() "
            "WHERE workspace_id = :workspace_id "
            "AND installation_id = :installation_id "
            "AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {
            "workspace_id": installation["workspace_id"],
            "installation_id": installation["installation_id"],
        },
    )


def downgrade() -> None:
    """Restore only the same exact paused fixed-canary installation."""

    bind = op.get_bind()
    installation = _exact_installation(bind)
    if installation is None:
        return
    if installation["state"] == "active":
        return
    if installation["state"] != "paused":
        raise RuntimeError("alpha_pause_drill_installation_not_paused")
    updated = bind.execute(
        sa.text(
            "UPDATE agent_installations SET state = 'active', updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND state = 'paused'"
        ),
        {
            "installation_id": installation["installation_id"],
            "workspace_id": installation["workspace_id"],
        },
    ).rowcount
    if updated != 1:
        raise RuntimeError("alpha_pause_drill_restore_race")


def _exact_installation(bind: sa.Connection) -> sa.RowMapping | None:
    rows = bind.execute(
        sa.text(
            "SELECT i.id AS installation_id, i.workspace_id, i.state "
            "FROM agent_installations i "
            "JOIN workspaces w ON w.id = i.workspace_id "
            "JOIN transport_connectors c "
            "ON c.id = i.connector_id AND c.workspace_id = i.workspace_id "
            "JOIN agent_deployments d "
            "ON d.id = i.deployment_id AND d.workspace_id = i.workspace_id "
            "JOIN instruction_revisions r "
            "ON r.id = i.instruction_revision_id "
            "AND r.workspace_id = i.workspace_id "
            "AND r.id = d.instruction_revision_id "
            "WHERE w.key = :workspace_key AND w.is_active IS TRUE "
            "AND c.connector_key = :connector_key "
            "AND c.connector_kind = 'telegram_bot' AND c.is_enabled IS TRUE "
            "AND d.deployment_key = :deployment_key AND d.state = 'active' "
            "AND r.revision_key = :revision_key "
            "AND r.source_commit_sha = :source_commit_sha "
            "AND r.content_sha256 = :content_sha256 "
            "AND r.validation_state = 'validated' "
            "ORDER BY i.created_at, i.id"
        ),
        {
            "workspace_key": _WORKSPACE_KEY,
            "connector_key": _CONNECTOR_KEY,
            "deployment_key": _DEPLOYMENT_KEY,
            "revision_key": _REVISION_KEY,
            "source_commit_sha": _SOURCE_COMMIT_SHA,
            "content_sha256": _CONTENT_SHA256,
        },
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("alpha_pause_drill_binding_ambiguous")
    return rows[0]

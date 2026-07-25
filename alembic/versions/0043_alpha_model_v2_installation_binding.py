"""Bind the single Alpha Group installation to the reviewed model v2 release.

Revision ID: 0043_alpha_model_v2_binding
Revises: 0042_dropbox_phone_calls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_alpha_model_v2_binding"
down_revision: str | None = "0042_dropbox_phone_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_CONNECTOR_KEY = "telegram_alpha_group"
_AGENT_KEY = "alpha_group"
_FIXTURE_DEPLOYMENT_KEY = "alpha-canary-v1-fixture"
_FIXTURE_REVISION_KEY = "alpha-canary-v1"
_TARGET_DEPLOYMENT_KEY = "alpha-model-v2-openai"
_TARGET_REVISION_KEY = "alpha-model-v2"
_TARGET_RUNTIME_PROFILE = "openai-responses-v1"
_TARGET_MODEL_PROFILE = "gpt-5.6-luna"
_TARGET_POLICY_VERSION = "alpha-model-v2"


def upgrade() -> None:
    """Rebind one exact active installation; ambiguity fails closed."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text(
            "SELECT id FROM workspaces "
            "WHERE key = :workspace_key AND is_active IS TRUE"
        ),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
        return

    fixture = _deployment(
        bind,
        workspace_id=workspace_id,
        deployment_key=_FIXTURE_DEPLOYMENT_KEY,
        revision_key=_FIXTURE_REVISION_KEY,
        require_target_profile=False,
    )
    target = _deployment(
        bind,
        workspace_id=workspace_id,
        deployment_key=_TARGET_DEPLOYMENT_KEY,
        revision_key=_TARGET_REVISION_KEY,
        require_target_profile=True,
    )
    installation = _installation(bind, workspace_id=workspace_id)
    if (
        installation["state"] != "active"
        or installation["deployment_id"] != fixture["deployment_id"]
        or installation["instruction_revision_id"] != fixture["revision_id"]
    ):
        raise RuntimeError("alpha_model_v2_fixture_binding_not_exact")

    _require_quiescent(
        bind,
        workspace_id=workspace_id,
        installation_id=installation["installation_id"],
        dialog_id=installation["dialog_id"],
    )
    result = bind.execute(
        sa.text(
            "UPDATE agent_installations "
            "SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, "
            "updated_at = now() "
            "WHERE id = :installation_id "
            "  AND workspace_id = :workspace_id "
            "  AND state = 'active' "
            "  AND deployment_id = :expected_deployment_id "
            "  AND instruction_revision_id = :expected_revision_id"
        ),
        {
            "deployment_id": target["deployment_id"],
            "revision_id": target["revision_id"],
            "installation_id": installation["installation_id"],
            "workspace_id": workspace_id,
            "expected_deployment_id": fixture["deployment_id"],
            "expected_revision_id": fixture["revision_id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("alpha_model_v2_installation_rebind_failed")


def downgrade() -> None:
    """Restore the exact fixture binding when the installation is quiescent."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
        return

    fixture = _deployment(
        bind,
        workspace_id=workspace_id,
        deployment_key=_FIXTURE_DEPLOYMENT_KEY,
        revision_key=_FIXTURE_REVISION_KEY,
        require_target_profile=False,
    )
    target = _deployment(
        bind,
        workspace_id=workspace_id,
        deployment_key=_TARGET_DEPLOYMENT_KEY,
        revision_key=_TARGET_REVISION_KEY,
        require_target_profile=True,
    )
    installation = _installation(bind, workspace_id=workspace_id)
    if (
        installation["state"] != "active"
        or installation["deployment_id"] != target["deployment_id"]
        or installation["instruction_revision_id"] != target["revision_id"]
    ):
        raise RuntimeError("alpha_model_v2_live_binding_not_exact")

    _require_quiescent(
        bind,
        workspace_id=workspace_id,
        installation_id=installation["installation_id"],
        dialog_id=installation["dialog_id"],
    )
    result = bind.execute(
        sa.text(
            "UPDATE agent_installations "
            "SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, "
            "updated_at = now() "
            "WHERE id = :installation_id "
            "  AND workspace_id = :workspace_id "
            "  AND state = 'active' "
            "  AND deployment_id = :expected_deployment_id "
            "  AND instruction_revision_id = :expected_revision_id"
        ),
        {
            "deployment_id": fixture["deployment_id"],
            "revision_id": fixture["revision_id"],
            "installation_id": installation["installation_id"],
            "workspace_id": workspace_id,
            "expected_deployment_id": target["deployment_id"],
            "expected_revision_id": target["revision_id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("alpha_model_v2_fixture_restore_failed")


def _deployment(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    deployment_key: str,
    revision_key: str,
    require_target_profile: bool,
) -> sa.RowMapping:
    rows = bind.execute(
        sa.text(
            "SELECT d.id AS deployment_id, r.id AS revision_id, "
            "d.runtime_profile, d.model_profile, d.policy_version, d.state "
            "FROM agent_deployments d "
            "JOIN agent_definitions a "
            "  ON a.id = d.agent_definition_id "
            " AND a.workspace_id = d.workspace_id "
            "JOIN instruction_revisions r "
            "  ON r.id = d.instruction_revision_id "
            " AND r.workspace_id = d.workspace_id "
            "WHERE d.workspace_id = :workspace_id "
            "  AND a.agent_key = :agent_key "
            "  AND d.deployment_key = :deployment_key "
            "  AND r.revision_key = :revision_key "
            "  AND r.validation_state = 'validated'"
        ),
        {
            "workspace_id": workspace_id,
            "agent_key": _AGENT_KEY,
            "deployment_key": deployment_key,
            "revision_key": revision_key,
        },
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("alpha_installation_release_binding_ambiguous")
    row = rows[0]
    if row["state"] != "active":
        raise RuntimeError("alpha_installation_release_not_active")
    if require_target_profile and (
        row["runtime_profile"] != _TARGET_RUNTIME_PROFILE
        or row["model_profile"] != _TARGET_MODEL_PROFILE
        or row["policy_version"] != _TARGET_POLICY_VERSION
    ):
        raise RuntimeError("alpha_model_v2_release_profile_not_exact")
    return row


def _installation(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
) -> sa.RowMapping:
    rows = bind.execute(
        sa.text(
            "SELECT i.id AS installation_id, i.dialog_id, i.deployment_id, "
            "i.instruction_revision_id, i.state "
            "FROM agent_installations i "
            "JOIN transport_connectors c "
            "  ON c.id = i.connector_id "
            " AND c.workspace_id = i.workspace_id "
            "JOIN dialogs d "
            "  ON d.id = i.dialog_id "
            " AND d.workspace_id = i.workspace_id "
            "WHERE i.workspace_id = :workspace_id "
            "  AND c.connector_key = :connector_key "
            "  AND c.connector_kind = 'telegram_bot' "
            "  AND c.is_enabled IS TRUE "
            "  AND d.dialog_kind IN ('group', 'supergroup') "
            "FOR UPDATE OF i"
        ),
        {
            "workspace_id": workspace_id,
            "connector_key": _CONNECTOR_KEY,
        },
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("alpha_model_v2_installation_binding_ambiguous")
    return rows[0]


def _require_quiescent(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    installation_id: object,
    dialog_id: object,
) -> None:
    nonterminal_turns = bind.scalar(
        sa.text(
            "SELECT count(*) "
            "FROM agent_turns t "
            "JOIN agent_sessions s "
            "  ON s.id = t.session_id "
            " AND s.workspace_id = t.workspace_id "
            "WHERE t.workspace_id = :workspace_id "
            "  AND s.dialog_id = :dialog_id "
            "  AND t.status IN ('queued', 'running')"
        ),
        {
            "workspace_id": workspace_id,
            "dialog_id": dialog_id,
        },
    )
    if nonterminal_turns:
        raise RuntimeError("alpha_installation_nonterminal_turn_exists")

    unused_grants = bind.scalar(
        sa.text(
            "SELECT count(*) FROM delivery_grants "
            "WHERE workspace_id = :workspace_id "
            "  AND installation_id = :installation_id "
            "  AND used_at IS NULL "
            "  AND revoked_at IS NULL"
        ),
        {
            "workspace_id": workspace_id,
            "installation_id": installation_id,
        },
    )
    if unused_grants:
        raise RuntimeError("alpha_installation_unused_grant_exists")

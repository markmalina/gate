"""Resolve Alexander by canonical installation ref and finish the live binding.

Revision ID: 0052_beta_alexander_exact_ref
Revises: 0051_beta_alexander_model
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_beta_alexander_exact_ref"
down_revision: str | None = "0051_beta_alexander_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INSTALLATION_REF = "installation-7fbf02145be55011ae8307e57dd8216c"
_WORKSPACE_KEY = "mark-personal"
_CONNECTOR_KEY = "telegram_alpha_group"
_AGENT_KEY = "alpha_group"
_BETA_USER_WORKSPACE_ID = "beta-user-mark-001"
_BETA_GROUP_ID = "beta-group-stroi-alphav-001"
_FIXTURE_DEPLOYMENT_KEY = "alpha-canary-v1-fixture"
_FIXTURE_REVISION_KEY = "alpha-canary-v1"
_TARGET_DEPLOYMENT_KEY = "alpha-model-v2-openai"
_TARGET_REVISION_KEY = "alpha-model-v2"
_TARGET_RUNTIME_PROFILE = "openai-responses-v1"
_TARGET_MODEL_PROFILE = "gpt-5.6-luna"
_TARGET_POLICY_VERSION = "alpha-model-v2"


def upgrade() -> None:
    """Create the exact context binding and rebind the paused installation."""

    bind = op.get_bind()
    installation = _installation(bind)
    if installation is None:
        return
    _bind_context(bind, installation=installation)
    _rebind(
        bind,
        installation=installation,
        expected_deployment_key=_FIXTURE_DEPLOYMENT_KEY,
        expected_revision_key=_FIXTURE_REVISION_KEY,
        target_deployment_key=_TARGET_DEPLOYMENT_KEY,
        target_revision_key=_TARGET_REVISION_KEY,
        require_target_profile=True,
    )


def downgrade() -> None:
    """Restore the fixture while retaining the Sprint 1 context binding."""

    bind = op.get_bind()
    installation = _installation(bind)
    if installation is None:
        return
    _rebind(
        bind,
        installation=installation,
        expected_deployment_key=_TARGET_DEPLOYMENT_KEY,
        expected_revision_key=_TARGET_REVISION_KEY,
        target_deployment_key=_FIXTURE_DEPLOYMENT_KEY,
        target_revision_key=_FIXTURE_REVISION_KEY,
        require_target_profile=False,
    )


def _installation(bind: sa.engine.Connection) -> sa.RowMapping | None:
    rows = bind.execute(
        sa.text(
            "SELECT installation.id AS installation_id, "
            "installation.workspace_id, installation.dialog_id, "
            "installation.deployment_id, installation.instruction_revision_id, "
            "installation.state "
            "FROM agent_installations installation "
            "JOIN workspaces workspace "
            "  ON workspace.id = installation.workspace_id "
            "JOIN transport_connectors connector "
            "  ON connector.id = installation.connector_id "
            " AND connector.workspace_id = installation.workspace_id "
            "JOIN dialogs dialog "
            "  ON dialog.id = installation.dialog_id "
            " AND dialog.workspace_id = installation.workspace_id "
            "WHERE installation.installation_key = :installation_ref "
            "  AND workspace.key = :workspace_key "
            "  AND workspace.is_active IS TRUE "
            "  AND connector.connector_key = :connector_key "
            "  AND connector.connector_kind = 'telegram_bot' "
            "  AND connector.is_enabled IS TRUE "
            "  AND dialog.dialog_kind IN ('group', 'supergroup') "
            "FOR UPDATE OF installation"
        ),
        {
            "installation_ref": _INSTALLATION_REF,
            "workspace_key": _WORKSPACE_KEY,
            "connector_key": _CONNECTOR_KEY,
        },
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("beta_alexander_exact_ref_ambiguous")
    return rows[0]


def _bind_context(
    bind: sa.engine.Connection,
    *,
    installation: sa.RowMapping,
) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT installation_id, installation_ref, user_workspace_id, "
            "group_id, state "
            "FROM beta_context_bindings "
            "WHERE installation_id = :installation_id "
            "   OR installation_ref = :installation_ref "
            "   OR group_id = :group_id "
            "FOR UPDATE"
        ),
        {
            "installation_id": installation["installation_id"],
            "installation_ref": _INSTALLATION_REF,
            "group_id": _BETA_GROUP_ID,
        },
    ).mappings().all()
    if rows:
        if len(rows) != 1:
            raise RuntimeError("beta_alexander_context_binding_ambiguous")
        row = rows[0]
        if (
            row["installation_id"] != installation["installation_id"]
            or row["installation_ref"] != _INSTALLATION_REF
            or row["user_workspace_id"] != _BETA_USER_WORKSPACE_ID
            or row["group_id"] != _BETA_GROUP_ID
            or row["state"] != "active"
        ):
            raise RuntimeError("beta_alexander_context_binding_conflict")
        return

    result = bind.execute(
        sa.text(
            "INSERT INTO beta_context_bindings "
            "(installation_id, installation_ref, user_workspace_id, group_id, state) "
            "VALUES "
            "(:installation_id, :installation_ref, :user_workspace_id, "
            ":group_id, 'active')"
        ),
        {
            "installation_id": installation["installation_id"],
            "installation_ref": _INSTALLATION_REF,
            "user_workspace_id": _BETA_USER_WORKSPACE_ID,
            "group_id": _BETA_GROUP_ID,
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("beta_alexander_context_binding_failed")


def _rebind(
    bind: sa.engine.Connection,
    *,
    installation: sa.RowMapping,
    expected_deployment_key: str,
    expected_revision_key: str,
    target_deployment_key: str,
    target_revision_key: str,
    require_target_profile: bool,
) -> None:
    expected = _deployment(
        bind,
        workspace_id=installation["workspace_id"],
        deployment_key=expected_deployment_key,
        revision_key=expected_revision_key,
        require_target_profile=False,
    )
    target = _deployment(
        bind,
        workspace_id=installation["workspace_id"],
        deployment_key=target_deployment_key,
        revision_key=target_revision_key,
        require_target_profile=require_target_profile,
    )
    if (
        installation["state"] != "paused"
        or installation["deployment_id"] != expected["deployment_id"]
        or installation["instruction_revision_id"] != expected["revision_id"]
    ):
        raise RuntimeError("beta_alexander_exact_source_binding_not_exact")

    _require_quiescent(
        bind,
        workspace_id=installation["workspace_id"],
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
            "  AND installation_key = :installation_ref "
            "  AND state = 'paused' "
            "  AND deployment_id = :expected_deployment_id "
            "  AND instruction_revision_id = :expected_revision_id"
        ),
        {
            "deployment_id": target["deployment_id"],
            "revision_id": target["revision_id"],
            "installation_id": installation["installation_id"],
            "workspace_id": installation["workspace_id"],
            "installation_ref": _INSTALLATION_REF,
            "expected_deployment_id": expected["deployment_id"],
            "expected_revision_id": expected["revision_id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("beta_alexander_exact_rebind_failed")


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
            "SELECT deployment.id AS deployment_id, revision.id AS revision_id, "
            "deployment.runtime_profile, deployment.model_profile, "
            "deployment.policy_version, deployment.state "
            "FROM agent_deployments deployment "
            "JOIN agent_definitions definition "
            "  ON definition.id = deployment.agent_definition_id "
            " AND definition.workspace_id = deployment.workspace_id "
            "JOIN instruction_revisions revision "
            "  ON revision.id = deployment.instruction_revision_id "
            " AND revision.workspace_id = deployment.workspace_id "
            "WHERE deployment.workspace_id = :workspace_id "
            "  AND definition.agent_key = :agent_key "
            "  AND deployment.deployment_key = :deployment_key "
            "  AND revision.revision_key = :revision_key "
            "  AND revision.validation_state = 'validated'"
        ),
        {
            "workspace_id": workspace_id,
            "agent_key": _AGENT_KEY,
            "deployment_key": deployment_key,
            "revision_key": revision_key,
        },
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("beta_alexander_exact_release_ambiguous")
    row = rows[0]
    if row["state"] != "active":
        raise RuntimeError("beta_alexander_exact_release_not_active")
    if require_target_profile and (
        row["runtime_profile"] != _TARGET_RUNTIME_PROFILE
        or row["model_profile"] != _TARGET_MODEL_PROFILE
        or row["policy_version"] != _TARGET_POLICY_VERSION
    ):
        raise RuntimeError("beta_alexander_exact_model_profile_not_exact")
    return row


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
            "FROM agent_turns turn "
            "JOIN agent_sessions session "
            "  ON session.id = turn.session_id "
            " AND session.workspace_id = turn.workspace_id "
            "WHERE turn.workspace_id = :workspace_id "
            "  AND session.dialog_id = :dialog_id "
            "  AND turn.status IN ('queued', 'running')"
        ),
        {"workspace_id": workspace_id, "dialog_id": dialog_id},
    )
    if nonterminal_turns:
        raise RuntimeError("beta_alexander_exact_nonterminal_turn_exists")

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
        raise RuntimeError("beta_alexander_exact_unused_grant_exists")

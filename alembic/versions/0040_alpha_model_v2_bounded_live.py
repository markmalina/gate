"""Promote the exact reviewed Alpha model v2 deployment to bounded-live.

Revision ID: 0040_alpha_model_v2_bounded_live
Revises: 0039_voice_archive
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_alpha_model_v2_bounded_live"
down_revision: str | None = "0039_voice_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_AGENT_KEY = "alpha_group"
_REVISION_KEY = "alpha-model-v2"
_DEPLOYMENT_KEY = "alpha-model-v2-openai"
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_POLICY_VERSION = "alpha-model-v2"


def upgrade() -> None:
    """Promote one exact preflight-verified deployment; fail closed otherwise."""

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

    deployment = bind.execute(
        sa.text(
            "SELECT d.id, d.state "
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
            "  AND d.runtime_profile = :runtime_profile "
            "  AND d.model_profile = :model_profile "
            "  AND d.policy_version = :policy_version "
            "  AND r.revision_key = :revision_key "
            "  AND r.validation_state = 'validated'"
        ),
        {
            "workspace_id": workspace_id,
            "agent_key": _AGENT_KEY,
            "deployment_key": _DEPLOYMENT_KEY,
            "runtime_profile": _RUNTIME_PROFILE,
            "model_profile": _MODEL_PROFILE,
            "policy_version": _POLICY_VERSION,
            "revision_key": _REVISION_KEY,
        },
    ).mappings().one_or_none()
    if deployment is None or deployment["state"] != "staged":
        raise RuntimeError("alpha_model_v2_staged_binding_not_exact")

    preflight = bind.execute(
        sa.text(
            "SELECT "
            "  count(*) FILTER (WHERE state = 'succeeded') AS succeeded, "
            "  count(*) FILTER (WHERE state <> 'succeeded') AS not_succeeded "
            "FROM alpha_model_preflights "
            "WHERE workspace_id = :workspace_id "
            "  AND deployment_id = :deployment_id"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment["id"],
        },
    ).mappings().one()
    if preflight["succeeded"] != 1 or preflight["not_succeeded"] != 0:
        raise RuntimeError("alpha_model_v2_preflight_not_exact")

    nonterminal_turns = bind.scalar(
        sa.text(
            "SELECT count(*) "
            "FROM agent_turns t "
            "JOIN agent_sessions s "
            "  ON s.id = t.session_id "
            " AND s.workspace_id = t.workspace_id "
            "WHERE t.workspace_id = :workspace_id "
            "  AND s.deployment_id = :deployment_id "
            "  AND t.status IN ('queued', 'running')"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment["id"],
        },
    )
    if nonterminal_turns:
        raise RuntimeError("alpha_model_v2_nonterminal_turn_exists")

    result = bind.execute(
        sa.text(
            "UPDATE agent_deployments "
            "SET state = 'active' "
            "WHERE id = :deployment_id "
            "  AND workspace_id = :workspace_id "
            "  AND state = 'staged'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment["id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("alpha_model_v2_activation_failed")


def downgrade() -> None:
    """Return the exact deployment to STAGED when no turn is nonterminal."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
        return

    deployment = bind.execute(
        sa.text(
            "SELECT id, state FROM agent_deployments "
            "WHERE workspace_id = :workspace_id "
            "  AND deployment_key = :deployment_key"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": _DEPLOYMENT_KEY,
        },
    ).mappings().one_or_none()
    if deployment is None:
        return
    if deployment["state"] != "active":
        raise RuntimeError("alpha_model_v2_active_binding_not_exact")

    nonterminal_turns = bind.scalar(
        sa.text(
            "SELECT count(*) "
            "FROM agent_turns t "
            "JOIN agent_sessions s "
            "  ON s.id = t.session_id "
            " AND s.workspace_id = t.workspace_id "
            "WHERE t.workspace_id = :workspace_id "
            "  AND s.deployment_id = :deployment_id "
            "  AND t.status IN ('queued', 'running')"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment["id"],
        },
    )
    if nonterminal_turns:
        raise RuntimeError("alpha_model_v2_nonterminal_turn_exists")

    result = bind.execute(
        sa.text(
            "UPDATE agent_deployments "
            "SET state = 'staged' "
            "WHERE id = :deployment_id "
            "  AND workspace_id = :workspace_id "
            "  AND state = 'active'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment["id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("alpha_model_v2_deactivation_failed")

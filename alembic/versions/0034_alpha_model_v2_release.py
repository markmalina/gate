"""Stage immutable Alpha model v2 and retain v1 as the paused rollback target.

Revision ID: 0034_alpha_model_v2_release
Revises: 0033_canonical_input_origin
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_alpha_model_v2_release"
down_revision: str | None = "0033_canonical_input_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_PREVIOUS_DEPLOYMENT_KEY = "alpha-model-v1-openai"
_REVISION_KEY = "alpha-model-v2"
_DEPLOYMENT_KEY = "alpha-model-v2-openai"
_SOURCE_COMMIT_SHA = "0675e284e2a5b1a4b1e7255a033d5b1f2aa06ec3"
_CONTENT_SHA256 = "ad3a1ee421358afb2e137841c0dfec7a8e83fe17688e6916ad818c923e14fb00"
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_POLICY_VERSION = "alpha-model-v2"


def upgrade() -> None:
    """Stage v2 exactly once and pause v1 without touching installations."""

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

    definitions = bind.execute(
        sa.text(
            "SELECT id FROM agent_definitions "
            "WHERE workspace_id = :workspace_id AND agent_key = 'alpha_group' "
            "ORDER BY id"
        ),
        {"workspace_id": workspace_id},
    ).mappings().all()
    if len(definitions) != 1:
        raise RuntimeError("alpha_model_v2_definition_state_ambiguous")
    definition_id = definitions[0]["id"]

    previous = bind.execute(
        sa.text(
            "SELECT id, state FROM agent_deployments "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_key = :deployment_key"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": _PREVIOUS_DEPLOYMENT_KEY,
        },
    ).mappings().one_or_none()
    if previous is None or previous["state"] not in {"staged", "paused"}:
        raise RuntimeError("alpha_model_v1_rollback_target_invalid")

    expected_revision = {
        "source_repository": "markmalina/Gate",
        "source_path": "alpha_model_v2",
        "source_commit_sha": _SOURCE_COMMIT_SHA,
        "content_sha256": _CONTENT_SHA256,
        "schema_version": "agent-bundle-v1",
        "validation_state": "validated",
    }
    revision_row = bind.execute(
        sa.text(
            "SELECT id, source_repository, source_path, source_commit_sha, "
            "content_sha256, schema_version, validation_state "
            "FROM instruction_revisions "
            "WHERE workspace_id = :workspace_id "
            "AND agent_definition_id = :definition_id "
            "AND revision_key = :revision_key"
        ),
        {
            "workspace_id": workspace_id,
            "definition_id": definition_id,
            "revision_key": _REVISION_KEY,
        },
    ).mappings().one_or_none()
    if revision_row is None:
        revision_id = bind.scalar(
            sa.text(
                "INSERT INTO instruction_revisions "
                "(id, workspace_id, agent_definition_id, revision_key, "
                "source_repository, source_path, source_commit_sha, content_sha256, "
                "schema_version, validation_state) "
                "VALUES (:id, :workspace_id, :definition_id, :revision_key, "
                ":source_repository, :source_path, :source_commit_sha, :content_sha256, "
                ":schema_version, :validation_state) RETURNING id"
            ),
            {
                "id": "1f5d16c3-4e10-5c25-82cf-8fefc13139fd",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_key": _REVISION_KEY,
                **expected_revision,
            },
        )
    else:
        if any(revision_row[key] != value for key, value in expected_revision.items()):
            raise RuntimeError("alpha_model_v2_revision_conflict")
        revision_id = revision_row["id"]

    expected_deployment = {
        "agent_definition_id": definition_id,
        "instruction_revision_id": revision_id,
        "runtime_profile": _RUNTIME_PROFILE,
        "model_profile": _MODEL_PROFILE,
        "policy_version": _POLICY_VERSION,
        "state": "staged",
    }
    deployment_row = bind.execute(
        sa.text(
            "SELECT id, agent_definition_id, instruction_revision_id, "
            "runtime_profile, model_profile, policy_version, state "
            "FROM agent_deployments "
            "WHERE workspace_id = :workspace_id AND deployment_key = :deployment_key"
        ),
        {"workspace_id": workspace_id, "deployment_key": _DEPLOYMENT_KEY},
    ).mappings().one_or_none()
    if deployment_row is None:
        bind.execute(
            sa.text(
                "INSERT INTO agent_deployments "
                "(id, workspace_id, agent_definition_id, instruction_revision_id, "
                "deployment_key, runtime_profile, model_profile, policy_version, state) "
                "VALUES (:id, :workspace_id, :definition_id, :revision_id, "
                ":deployment_key, :runtime_profile, :model_profile, "
                ":policy_version, :state)"
            ),
            {
                "id": "80b8c1bc-6b3c-5d8a-a3bc-29b1c80d3f0b",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_id": revision_id,
                "deployment_key": _DEPLOYMENT_KEY,
                "runtime_profile": _RUNTIME_PROFILE,
                "model_profile": _MODEL_PROFILE,
                "policy_version": _POLICY_VERSION,
                "state": "staged",
            },
        )
    elif any(
        deployment_row[key] != value for key, value in expected_deployment.items()
    ):
        raise RuntimeError("alpha_model_v2_deployment_conflict")

    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'paused' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'staged'"
        ),
        {
            "deployment_id": previous["id"],
            "workspace_id": workspace_id,
        },
    )


def downgrade() -> None:
    """Remove unused v2 and restore v1; fail closed once v2 has evidence."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
        return
    deployment = bind.execute(
        sa.text(
            "SELECT id, instruction_revision_id, state FROM agent_deployments "
            "WHERE workspace_id = :workspace_id AND deployment_key = :deployment_key"
        ),
        {"workspace_id": workspace_id, "deployment_key": _DEPLOYMENT_KEY},
    ).mappings().one_or_none()
    if deployment is None:
        return
    if deployment["state"] != "staged":
        raise RuntimeError("alpha_model_v2_deployment_not_staged")

    references = bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM agent_installations "
            " WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id) + "
            "(SELECT count(*) FROM agent_sessions "
            " WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id) + "
            "(SELECT count(*) FROM alpha_model_preflights "
            " WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id)"
        ),
        {"workspace_id": workspace_id, "deployment_id": deployment["id"]},
    )
    if references:
        raise RuntimeError("alpha_model_v2_deployment_in_use")

    bind.execute(
        sa.text(
            "DELETE FROM agent_deployments "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'staged'"
        ),
        {
            "deployment_id": deployment["id"],
            "workspace_id": workspace_id,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'staged' "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_key = :deployment_key AND state = 'paused'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": _PREVIOUS_DEPLOYMENT_KEY,
        },
    )

    revision_id = deployment["instruction_revision_id"]
    revision_references = bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM agent_deployments "
            " WHERE workspace_id = :workspace_id "
            " AND instruction_revision_id = :revision_id) + "
            "(SELECT count(*) FROM agent_installations "
            " WHERE workspace_id = :workspace_id "
            " AND instruction_revision_id = :revision_id) + "
            "(SELECT count(*) FROM agent_sessions "
            " WHERE workspace_id = :workspace_id "
            " AND instruction_revision_id = :revision_id) + "
            "(SELECT count(*) FROM agent_turns "
            " WHERE workspace_id = :workspace_id "
            " AND instruction_revision_id = :revision_id) + "
            "(SELECT count(*) FROM alpha_model_preflights "
            " WHERE workspace_id = :workspace_id "
            " AND instruction_revision_id = :revision_id)"
        ),
        {"workspace_id": workspace_id, "revision_id": revision_id},
    )
    if revision_references:
        raise RuntimeError("alpha_model_v2_revision_in_use")
    bind.execute(
        sa.text(
            "DELETE FROM instruction_revisions "
            "WHERE id = :revision_id AND workspace_id = :workspace_id "
            "AND revision_key = :revision_key"
        ),
        {
            "revision_id": revision_id,
            "workspace_id": workspace_id,
            "revision_key": _REVISION_KEY,
        },
    )

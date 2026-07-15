"""Stage the reviewed Alpha model release without activating provider egress.

Revision ID: 0023_alpha_model_release
Revises: 0022_alpha_bundle_digest
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_alpha_model_release"
down_revision: str | None = "0022_alpha_bundle_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_REVISION_KEY = "alpha-model-v1"
_DEPLOYMENT_KEY = "alpha-model-v1-openai"
_SOURCE_COMMIT_SHA = "7c2781602448ac140e1e31ea27cb93414e3333d2"
_CONTENT_SHA256 = "bdb610fd54e5444e9ed22df035418c6ba2217a620d427c2ae49a51adef917dff"
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_POLICY_VERSION = "alpha-model-v1"
_DEPLOYMENT_STATE = "staged"


def upgrade() -> None:
    """Insert an exact validated revision and a staged-only deployment."""

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
        raise RuntimeError("alpha_model_definition_state_ambiguous")
    definition_id = definitions[0]["id"]

    expected_revision = {
        "source_repository": "markmalina/Gate",
        "source_path": "alpha_model_v1",
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
                "id": "6d98a3b5-e537-55bd-86c3-d9b72131e190",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_key": _REVISION_KEY,
                **expected_revision,
            },
        )
    else:
        if any(revision_row[key] != value for key, value in expected_revision.items()):
            raise RuntimeError("alpha_model_revision_conflict")
        revision_id = revision_row["id"]

    expected_deployment = {
        "agent_definition_id": definition_id,
        "instruction_revision_id": revision_id,
        "runtime_profile": _RUNTIME_PROFILE,
        "model_profile": _MODEL_PROFILE,
        "policy_version": _POLICY_VERSION,
        "state": _DEPLOYMENT_STATE,
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
                "id": "d9d00b22-226b-58f3-8a9a-e109e62f095c",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_id": revision_id,
                "deployment_key": _DEPLOYMENT_KEY,
                "runtime_profile": _RUNTIME_PROFILE,
                "model_profile": _MODEL_PROFILE,
                "policy_version": _POLICY_VERSION,
                "state": _DEPLOYMENT_STATE,
            },
        )
        return
    if any(deployment_row[key] != value for key, value in expected_deployment.items()):
        raise RuntimeError("alpha_model_deployment_conflict")


def downgrade() -> None:
    """Remove only the unused staged release; never detach an installation."""

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
    if deployment["state"] != _DEPLOYMENT_STATE:
        raise RuntimeError("alpha_model_deployment_not_staged")

    deployment_references = bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM agent_installations "
            " WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id) + "
            "(SELECT count(*) FROM agent_sessions "
            " WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id)"
        ),
        {"workspace_id": workspace_id, "deployment_id": deployment["id"]},
    )
    if deployment_references:
        raise RuntimeError("alpha_model_deployment_in_use")
    bind.execute(
        sa.text(
            "DELETE FROM agent_deployments "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = :state"
        ),
        {
            "deployment_id": deployment["id"],
            "workspace_id": workspace_id,
            "state": _DEPLOYMENT_STATE,
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
            " AND instruction_revision_id = :revision_id)"
        ),
        {"workspace_id": workspace_id, "revision_id": revision_id},
    )
    if revision_references:
        raise RuntimeError("alpha_model_revision_in_use")
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

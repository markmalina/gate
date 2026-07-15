"""Promote the immutable Alpha canary release for the owner-approved group.

Revision ID: 0020_alpha_canary_release
Revises: 0019_alpha_lifecycle
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_alpha_canary_release"
down_revision: str | None = "0019_alpha_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_CONNECTOR_KEY = "telegram_alpha_group"
_REVISION_KEY = "alpha-canary-v1"
_DEPLOYMENT_KEY = "alpha-canary-v1-fixture"
_SOURCE_COMMIT_SHA = "0b5295606694c537c2b59915d0c91e789f1cbb51"
_CONTENT_SHA256 = "8a40570ed64fe29ba56b50975113a89bee5a59c22c4bf9cc56cf0abf75eba578"

_REQUIRED_OWNER_FLAGS = (
    "GATE_ALPHA_CONNECTOR_ENABLED",
    "GATE_TELEGRAM_WORKER_ENABLED",
    "GATE_ALPHA_SHADOW_WORKER_ENABLED",
    "GATE_ALPHA_CANARY_ENABLED",
    "GATE_ALPHA_DELIVERY_ENABLED",
    "GATE_ALPHA_PRIVACY_MODE_CONFIRMED",
)


def upgrade() -> None:
    """Seed one validated release and bind it only after explicit owner flags."""

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

    definition_id = bind.scalar(
        sa.text(
            "SELECT id FROM agent_definitions "
            "WHERE workspace_id = :workspace_id AND agent_key = 'alpha_group'"
        ),
        {"workspace_id": workspace_id},
    )
    if definition_id is None:
        definition_id = bind.scalar(
            sa.text(
                "INSERT INTO agent_definitions "
                "(id, workspace_id, agent_key, display_name) "
                "VALUES (:id, :workspace_id, 'alpha_group', 'Alpha') "
                "RETURNING id"
            ),
            {
                "id": "83b3e641-b441-55cd-bf02-74e0fa7aca00",
                "workspace_id": workspace_id,
            },
        )

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
    expected_revision = {
        "source_repository": "markmalina/Gate",
        "source_path": "alpha_canary_v1",
        "source_commit_sha": _SOURCE_COMMIT_SHA,
        "content_sha256": _CONTENT_SHA256,
        "schema_version": "agent-bundle-v1",
        "validation_state": "validated",
    }
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
                "id": "5420aa13-2099-52fc-a2b6-4ee0ba073acc",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_key": _REVISION_KEY,
                **expected_revision,
            },
        )
    else:
        if any(revision_row[key] != value for key, value in expected_revision.items()):
            raise RuntimeError("alpha_canary_revision_conflict")
        revision_id = revision_row["id"]

    deployment_row = bind.execute(
        sa.text(
            "SELECT id, agent_definition_id, instruction_revision_id, state "
            "FROM agent_deployments "
            "WHERE workspace_id = :workspace_id AND deployment_key = :deployment_key"
        ),
        {"workspace_id": workspace_id, "deployment_key": _DEPLOYMENT_KEY},
    ).mappings().one_or_none()
    if deployment_row is None:
        deployment_id = bind.scalar(
            sa.text(
                "INSERT INTO agent_deployments "
                "(id, workspace_id, agent_definition_id, instruction_revision_id, "
                "deployment_key, runtime_profile, model_profile, policy_version, state) "
                "VALUES (:id, :workspace_id, :definition_id, :revision_id, "
                ":deployment_key, 'fixture-only-v1', 'network-free-v1', "
                "'alpha-canary-v1', 'staged') RETURNING id"
            ),
            {
                "id": "a39d2486-c405-5f07-a7d4-605c5c1290cb",
                "workspace_id": workspace_id,
                "definition_id": definition_id,
                "revision_id": revision_id,
                "deployment_key": _DEPLOYMENT_KEY,
            },
        )
        deployment_state = "staged"
    else:
        if (
            deployment_row["agent_definition_id"] != definition_id
            or deployment_row["instruction_revision_id"] != revision_id
        ):
            raise RuntimeError("alpha_canary_deployment_conflict")
        deployment_id = deployment_row["id"]
        deployment_state = deployment_row["state"]

    if not _owner_activation_authorized():
        return
    if deployment_state == "paused":
        return

    connector_id = bind.scalar(
        sa.text(
            "SELECT id FROM transport_connectors "
            "WHERE workspace_id = :workspace_id AND connector_key = :connector_key "
            "AND connector_kind = 'telegram_bot' AND is_enabled IS TRUE"
        ),
        {"workspace_id": workspace_id, "connector_key": _CONNECTOR_KEY},
    )
    if connector_id is None:
        return
    installations = bind.execute(
        sa.text(
            "SELECT id, state, deployment_id, instruction_revision_id "
            "FROM agent_installations "
            "WHERE workspace_id = :workspace_id AND connector_id = :connector_id "
            "ORDER BY created_at, id"
        ),
        {"workspace_id": workspace_id, "connector_id": connector_id},
    ).mappings().all()
    if len(installations) > 1:
        raise RuntimeError("alpha_canary_group_state_ambiguous")
    if not installations:
        return
    installation = installations[0]
    if (
        installation["state"] == "active"
        and installation["deployment_id"] == deployment_id
        and installation["instruction_revision_id"] == revision_id
    ):
        return
    if installation["state"] not in {"discovered", "pending_approval"}:
        return

    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'active' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'staged'"
        ),
        {"deployment_id": deployment_id, "workspace_id": workspace_id},
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
            "deployment_id": deployment_id,
            "revision_id": revision_id,
            "installation_id": installation["id"],
            "workspace_id": workspace_id,
        },
    )


def downgrade() -> None:
    """Fail closed while preserving immutable release and audit metadata."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
        return
    deployment_id = bind.scalar(
        sa.text(
            "SELECT id FROM agent_deployments "
            "WHERE workspace_id = :workspace_id AND deployment_key = :deployment_key"
        ),
        {"workspace_id": workspace_id, "deployment_key": _DEPLOYMENT_KEY},
    )
    revision_id = bind.scalar(
        sa.text(
            "SELECT id FROM instruction_revisions "
            "WHERE workspace_id = :workspace_id AND revision_key = :revision_key"
        ),
        {"workspace_id": workspace_id, "revision_key": _REVISION_KEY},
    )
    if deployment_id is None or revision_id is None:
        return
    installation_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM agent_installations "
            "WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id "
            "AND instruction_revision_id = :revision_id"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": deployment_id,
            "revision_id": revision_id,
        },
    )
    if installation_count:
        bind.execute(
            sa.text(
                "UPDATE delivery_grants SET revoked_at = now() "
                "WHERE workspace_id = :workspace_id AND used_at IS NULL "
                "AND revoked_at IS NULL AND installation_id IN ("
                "SELECT id FROM agent_installations "
                "WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id "
                "AND instruction_revision_id = :revision_id)"
            ),
            {
                "workspace_id": workspace_id,
                "deployment_id": deployment_id,
                "revision_id": revision_id,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE agent_installations SET state = 'pending_approval', "
                "deployment_id = NULL, instruction_revision_id = NULL, updated_at = now() "
                "WHERE workspace_id = :workspace_id AND deployment_id = :deployment_id "
                "AND instruction_revision_id = :revision_id AND state = 'active'"
            ),
            {
                "workspace_id": workspace_id,
                "deployment_id": deployment_id,
                "revision_id": revision_id,
            },
        )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'staged' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'active'"
        ),
        {"deployment_id": deployment_id, "workspace_id": workspace_id},
    )


def _owner_activation_authorized() -> bool:
    return all(_enabled(os.getenv(name)) for name in _REQUIRED_OWNER_FLAGS)


def _enabled(value: str | None) -> bool:
    return value is not None and value.strip().casefold() in {"1", "true", "yes", "on"}

"""Release Universal Mayak for one owner-confirmed private dialog.

Revision ID: 0044_mayak_owner_release
Revises: 0043_alpha_model_v2_binding
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_mayak_owner_release"
down_revision: str | None = "0043_alpha_model_v2_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_ACCOUNT_ALIAS = "telegram_mark_by"
_PROJECT_ALIAS = "oko"
_AGENT_KEY = "oko"
_REVISION_KEY = "universal-mayak-v1"
_DEPLOYMENT_KEY = "universal-mayak-v1-openai"
_INSTALLATION_KEY = "universal-mayak-v1-owner-private"
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_POLICY_VERSION = "universal-mayak-v1"
_SOURCE_COMMIT_SHA = "96fff35b3304424a441be674accc12980741f9e3"
_CONTENT_SHA256 = (
    "c012a37bd566bfce08cde977db49b574bd8db22947a651f9892757dd8694cdcf"
)

# This is the content-free Gate task identifier for the exact owner message
# confirmed in Gate #300. It binds the release to that already-persisted route
# without committing a Telegram identifier, message text, or other payload.
_OWNER_ACCEPTANCE_TASK_ID = "4f060e00-0bb5-4701-8f38-80b06da9fc8e"

_DEFINITION_ID = "f01f7ac7-e4f0-5a28-8378-e526e62f9745"
_REVISION_ID = "f0e37371-d57a-58ad-9526-fa70c03bc51a"
_DEPLOYMENT_ID = "9d29f1f4-a81d-53f5-978f-0efbe5e42d84"
_INSTALLATION_ID = "2b605ee3-9a75-5770-bab8-148ba529e2bf"

_REQUIRED_OWNER_FLAGS = (
    "GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED",
    "GATE_UNIVERSAL_MAYAK_MODEL_DATA_GATE_APPROVED",
    "GATE_UNIVERSAL_MAYAK_DELIVERY_ENABLED",
)


def upgrade() -> None:
    """Create one reviewed release and activate only the confirmed owner route."""

    bind = op.get_bind()
    workspace_rows = bind.execute(
        sa.text(
            "SELECT id FROM workspaces "
            "WHERE key = :workspace_key AND is_active IS TRUE"
        ),
        {"workspace_key": _WORKSPACE_KEY},
    ).scalars().all()
    if not workspace_rows:
        return
    if len(workspace_rows) != 1:
        raise RuntimeError("mayak_owner_workspace_ambiguous")
    workspace_id = workspace_rows[0]

    definition_id = _definition(bind, workspace_id=workspace_id)
    revision_id = _revision(
        bind,
        workspace_id=workspace_id,
        definition_id=definition_id,
    )
    deployment_id, deployment_state = _deployment(
        bind,
        workspace_id=workspace_id,
        definition_id=definition_id,
        revision_id=revision_id,
    )

    if not _owner_activation_authorized():
        return
    if deployment_state == "paused":
        raise RuntimeError("mayak_owner_deployment_paused")

    target = _owner_target(bind, workspace_id=workspace_id)
    if target is None:
        return
    _bind_account_project(
        bind,
        account_id=target["account_id"],
        project_id=target["project_id"],
    )
    installation = _installation(
        bind,
        workspace_id=workspace_id,
        connector_id=target["connector_id"],
        dialog_id=target["dialog_id"],
    )

    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'active' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'staged'"
        ),
        {"deployment_id": deployment_id, "workspace_id": workspace_id},
    )

    if installation is None:
        bind.execute(
            sa.text(
                "INSERT INTO agent_installations "
                "(id, workspace_id, connector_id, dialog_id, deployment_id, "
                "instruction_revision_id, installation_key, state, "
                "max_turns_per_window, max_tokens_per_turn, "
                "rate_window_seconds, dialog_label) "
                "VALUES (:id, :workspace_id, :connector_id, :dialog_id, "
                ":deployment_id, :revision_id, :installation_key, 'active', "
                "5, 2048, 60, 'Око возрождения')"
            ),
            {
                "id": _INSTALLATION_ID,
                "workspace_id": workspace_id,
                "connector_id": target["connector_id"],
                "dialog_id": target["dialog_id"],
                "deployment_id": deployment_id,
                "revision_id": revision_id,
                "installation_key": _INSTALLATION_KEY,
            },
        )
        return

    if (
        installation["state"] == "active"
        and installation["deployment_id"] == deployment_id
        and installation["instruction_revision_id"] == revision_id
    ):
        return
    if installation["state"] not in {"discovered", "pending_approval"}:
        raise RuntimeError("mayak_owner_installation_state_conflict")
    if (
        installation["deployment_id"] is not None
        or installation["instruction_revision_id"] is not None
    ):
        raise RuntimeError("mayak_owner_installation_binding_conflict")
    bind.execute(
        sa.text(
            "UPDATE agent_installations "
            "SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, state = 'active', "
            "max_turns_per_window = 5, max_tokens_per_turn = 2048, "
            "rate_window_seconds = 60, dialog_label = 'Око возрождения', "
            "updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND state IN ('discovered', 'pending_approval') "
            "AND deployment_id IS NULL AND instruction_revision_id IS NULL"
        ),
        {
            "deployment_id": deployment_id,
            "revision_id": revision_id,
            "installation_id": installation["id"],
            "workspace_id": workspace_id,
        },
    )


def downgrade() -> None:
    """Fail closed while retaining immutable release and audit rows."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    if workspace_id is None:
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
            "deployment_id": _DEPLOYMENT_ID,
            "revision_id": _REVISION_ID,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_installations "
            "SET state = 'pending_approval', deployment_id = NULL, "
            "instruction_revision_id = NULL, updated_at = now() "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_id = :deployment_id "
            "AND instruction_revision_id = :revision_id "
            "AND state = 'active'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_id": _DEPLOYMENT_ID,
            "revision_id": _REVISION_ID,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'staged' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND state = 'active'"
        ),
        {"deployment_id": _DEPLOYMENT_ID, "workspace_id": workspace_id},
    )


def _definition(bind: sa.engine.Connection, *, workspace_id: object) -> object:
    rows = bind.execute(
        sa.text(
            "SELECT id FROM agent_definitions "
            "WHERE workspace_id = :workspace_id AND agent_key = :agent_key"
        ),
        {"workspace_id": workspace_id, "agent_key": _AGENT_KEY},
    ).scalars().all()
    if len(rows) > 1:
        raise RuntimeError("mayak_owner_definition_ambiguous")
    if rows:
        return rows[0]
    return bind.scalar(
        sa.text(
            "INSERT INTO agent_definitions "
            "(id, workspace_id, agent_key, display_name) "
            "VALUES (:id, :workspace_id, :agent_key, 'Око возрождения') "
            "RETURNING id"
        ),
        {
            "id": _DEFINITION_ID,
            "workspace_id": workspace_id,
            "agent_key": _AGENT_KEY,
        },
    )


def _revision(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    definition_id: object,
) -> object:
    row = bind.execute(
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
    expected = {
        "source_repository": "markmalina/Gate",
        "source_path": "universal_mayak_v1",
        "source_commit_sha": _SOURCE_COMMIT_SHA,
        "content_sha256": _CONTENT_SHA256,
        "schema_version": "agent-bundle-v1",
        "validation_state": "validated",
    }
    if row is not None:
        if any(row[key] != value for key, value in expected.items()):
            raise RuntimeError("mayak_owner_revision_conflict")
        return row["id"]
    return bind.scalar(
        sa.text(
            "INSERT INTO instruction_revisions "
            "(id, workspace_id, agent_definition_id, revision_key, "
            "source_repository, source_path, source_commit_sha, "
            "content_sha256, schema_version, validation_state) "
            "VALUES (:id, :workspace_id, :definition_id, :revision_key, "
            ":source_repository, :source_path, :source_commit_sha, "
            ":content_sha256, :schema_version, :validation_state) "
            "RETURNING id"
        ),
        {
            "id": _REVISION_ID,
            "workspace_id": workspace_id,
            "definition_id": definition_id,
            "revision_key": _REVISION_KEY,
            **expected,
        },
    )


def _deployment(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    definition_id: object,
    revision_id: object,
) -> tuple[object, str]:
    row = bind.execute(
        sa.text(
            "SELECT id, agent_definition_id, instruction_revision_id, "
            "runtime_profile, model_profile, policy_version, state "
            "FROM agent_deployments "
            "WHERE workspace_id = :workspace_id "
            "AND deployment_key = :deployment_key"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": _DEPLOYMENT_KEY,
        },
    ).mappings().one_or_none()
    if row is not None:
        expected = {
            "agent_definition_id": definition_id,
            "instruction_revision_id": revision_id,
            "runtime_profile": _RUNTIME_PROFILE,
            "model_profile": _MODEL_PROFILE,
            "policy_version": _POLICY_VERSION,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise RuntimeError("mayak_owner_deployment_conflict")
        return row["id"], row["state"]
    deployment_id = bind.scalar(
        sa.text(
            "INSERT INTO agent_deployments "
            "(id, workspace_id, agent_definition_id, instruction_revision_id, "
            "deployment_key, runtime_profile, model_profile, policy_version, state) "
            "VALUES (:id, :workspace_id, :definition_id, :revision_id, "
            ":deployment_key, :runtime_profile, :model_profile, "
            ":policy_version, 'staged') RETURNING id"
        ),
        {
            "id": _DEPLOYMENT_ID,
            "workspace_id": workspace_id,
            "definition_id": definition_id,
            "revision_id": revision_id,
            "deployment_key": _DEPLOYMENT_KEY,
            "runtime_profile": _RUNTIME_PROFILE,
            "model_profile": _MODEL_PROFILE,
            "policy_version": _POLICY_VERSION,
        },
    )
    return deployment_id, "staged"


def _owner_target(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
) -> sa.RowMapping | None:
    rows = bind.execute(
        sa.text(
            "SELECT d.id AS dialog_id, c.id AS connector_id, "
            "a.id AS account_id, p.id AS project_id "
            "FROM reply_tasks rt "
            "JOIN messages m ON m.id = rt.trigger_message_id "
            " AND m.account_id = rt.account_id "
            " AND m.project_id = rt.project_id "
            "JOIN accounts a ON a.id = m.account_id "
            "JOIN projects p ON p.id = m.project_id "
            "JOIN canonical_messages cm ON cm.id = m.canonical_message_id "
            "JOIN dialogs d ON d.id = cm.dialog_id "
            " AND d.workspace_id = cm.workspace_id "
            "JOIN transport_connectors c ON c.id = d.connector_id "
            " AND c.workspace_id = d.workspace_id "
            "JOIN telegram_business_connections bc "
            " ON bc.account_id = a.id "
            " AND bc.business_connection_id = m.business_connection_id "
            "WHERE rt.id = :task_id "
            " AND rt.status = 'needs_reply' "
            " AND a.alias = :account_alias "
            " AND a.owner = :workspace_key "
            " AND a.account_type = 'telegram_personal' "
            " AND a.is_active IS TRUE "
            " AND a.read_enabled IS TRUE "
            " AND a.send_enabled IS TRUE "
            " AND p.alias = :project_alias "
            " AND p.is_active IS TRUE "
            " AND cm.workspace_id = :workspace_id "
            " AND cm.direction = 'inbound' "
            " AND cm.provider_sender_id = d.provider_dialog_id "
            " AND m.direction = 'inbound' "
            " AND m.chat_id = d.provider_dialog_id "
            " AND m.sender_id = d.provider_dialog_id "
            " AND d.dialog_kind = 'private' "
            " AND c.connector_key = a.alias "
            " AND c.connector_kind = 'telegram_business' "
            " AND c.is_enabled IS TRUE "
            " AND bc.is_enabled IS TRUE "
            "FOR UPDATE OF rt, d, c"
        ),
        {
            "task_id": _OWNER_ACCEPTANCE_TASK_ID,
            "account_alias": _ACCOUNT_ALIAS,
            "workspace_key": _WORKSPACE_KEY,
            "project_alias": _PROJECT_ALIAS,
            "workspace_id": workspace_id,
        },
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("mayak_owner_target_ambiguous")
    return rows[0]


def _bind_account_project(
    bind: sa.engine.Connection,
    *,
    account_id: object,
    project_id: object,
) -> None:
    linked_project_id = bind.scalar(
        sa.text(
            "SELECT linked_project_id FROM accounts "
            "WHERE id = :account_id FOR UPDATE"
        ),
        {"account_id": account_id},
    )
    if linked_project_id is not None and linked_project_id != project_id:
        raise RuntimeError("mayak_owner_account_project_conflict")
    if linked_project_id is None:
        bind.execute(
            sa.text(
                "UPDATE accounts SET linked_project_id = :project_id "
                "WHERE id = :account_id AND linked_project_id IS NULL"
            ),
            {"account_id": account_id, "project_id": project_id},
        )


def _installation(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    connector_id: object,
    dialog_id: object,
) -> sa.RowMapping | None:
    rows = bind.execute(
        sa.text(
            "SELECT id, deployment_id, instruction_revision_id, state "
            "FROM agent_installations "
            "WHERE workspace_id = :workspace_id "
            "AND connector_id = :connector_id AND dialog_id = :dialog_id "
            "FOR UPDATE"
        ),
        {
            "workspace_id": workspace_id,
            "connector_id": connector_id,
            "dialog_id": dialog_id,
        },
    ).mappings().all()
    if len(rows) > 1:
        raise RuntimeError("mayak_owner_installation_ambiguous")
    return rows[0] if rows else None


def _owner_activation_authorized() -> bool:
    return all(_enabled(os.getenv(name)) for name in _REQUIRED_OWNER_FLAGS)


def _enabled(value: str | None) -> bool:
    return value is not None and value.strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }

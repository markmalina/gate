"""Reconcile the exact owner-private Mayak installation after task evolution.

Revision ID: 0046_mayak_installation_reconcile
Revises: 0045_mayak_owner_route
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_mayak_installation_reconcile"
down_revision: str | None = "0045_mayak_owner_route"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_ACCOUNT_ALIAS = "telegram_mark_by"
_SOURCE_PROJECT_ALIAS = "unclassified"
_TARGET_PROJECT_ALIAS = "oko"
_AGENT_KEY = "oko"
_REVISION_KEY = "universal-mayak-v1"
_DEPLOYMENT_KEY = "universal-mayak-v1-openai"
_INSTALLATION_KEY = "universal-mayak-v1-owner-private"
_OWNER_ACCEPTANCE_TASK_ID = "4f060e00-0bb5-4701-8f38-80b06da9fc8e"
_INSTALLATION_ID = "2b605ee3-9a75-5770-bab8-148ba529e2bf"


def upgrade() -> None:
    """Reconcile the accepted route without depending on mutable task status."""

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
        raise RuntimeError("mayak_owner_route_workspace_ambiguous")
    workspace_id = workspace_rows[0]

    target_project_rows = bind.execute(
        sa.text(
            "SELECT id FROM projects "
            "WHERE alias = :target_project_alias AND is_active IS TRUE"
        ),
        {"target_project_alias": _TARGET_PROJECT_ALIAS},
    ).scalars().all()
    if not target_project_rows:
        return
    if len(target_project_rows) != 1:
        raise RuntimeError("mayak_owner_route_project_ambiguous")
    target_project_id = target_project_rows[0]

    rows = bind.execute(
        sa.text(
            "SELECT rt.id AS task_id, rt.conversation_id, m.id AS message_id, "
            "m.project_id AS source_project_id, d.id AS dialog_id, "
            "c.id AS connector_id, a.id AS account_id "
            "FROM reply_tasks rt "
            "JOIN messages m ON m.id = rt.trigger_message_id "
            " AND m.account_id = rt.account_id "
            " AND m.project_id = rt.project_id "
            "JOIN projects source_project ON source_project.id = m.project_id "
            "JOIN accounts a ON a.id = m.account_id "
            "JOIN canonical_messages cm ON cm.id = m.canonical_message_id "
            "JOIN dialogs d ON d.id = cm.dialog_id "
            " AND d.workspace_id = cm.workspace_id "
            "JOIN transport_connectors c ON c.id = d.connector_id "
            " AND c.workspace_id = d.workspace_id "
            "JOIN telegram_business_connections bc "
            " ON bc.account_id = a.id "
            " AND bc.business_connection_id = m.business_connection_id "
            "WHERE rt.id = :task_id "
            " AND source_project.alias IN "
            " (:source_project_alias, :target_project_alias) "
            " AND source_project.is_active IS TRUE "
            " AND a.alias = :account_alias "
            " AND a.owner = :workspace_key "
            " AND a.account_type = 'telegram_personal' "
            " AND a.is_active IS TRUE "
            " AND a.read_enabled IS TRUE "
            " AND a.send_enabled IS TRUE "
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
            "FOR UPDATE OF rt, m, d, c"
        ),
        {
            "task_id": _OWNER_ACCEPTANCE_TASK_ID,
            "source_project_alias": _SOURCE_PROJECT_ALIAS,
            "target_project_alias": _TARGET_PROJECT_ALIAS,
            "account_alias": _ACCOUNT_ALIAS,
            "workspace_key": _WORKSPACE_KEY,
            "workspace_id": workspace_id,
        },
    ).mappings().all()
    if not rows:
        return
    if len(rows) != 1:
        raise RuntimeError("mayak_owner_route_target_ambiguous")
    target = rows[0]

    release_rows = bind.execute(
        sa.text(
            "SELECT ad.id AS deployment_id, ad.state AS deployment_state, "
            "ir.id AS revision_id "
            "FROM agent_deployments ad "
            "JOIN agent_definitions definition "
            " ON definition.id = ad.agent_definition_id "
            " AND definition.workspace_id = ad.workspace_id "
            "JOIN instruction_revisions ir "
            " ON ir.id = ad.instruction_revision_id "
            " AND ir.workspace_id = ad.workspace_id "
            "WHERE ad.workspace_id = :workspace_id "
            " AND ad.deployment_key = :deployment_key "
            " AND ad.state IN ('staged', 'active') "
            " AND definition.agent_key = :agent_key "
            " AND ir.revision_key = :revision_key "
            " AND ir.validation_state = 'validated' "
            "FOR UPDATE OF ad"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": _DEPLOYMENT_KEY,
            "agent_key": _AGENT_KEY,
            "revision_key": _REVISION_KEY,
        },
    ).mappings().all()
    if len(release_rows) != 1:
        raise RuntimeError("mayak_owner_route_release_invalid")
    release = release_rows[0]

    account_project = bind.scalar(
        sa.text("SELECT linked_project_id FROM accounts WHERE id = :id FOR UPDATE"),
        {"id": target["account_id"]},
    )
    if account_project is not None and account_project not in {
        target["source_project_id"],
        target_project_id,
    }:
        raise RuntimeError("mayak_owner_route_account_project_conflict")

    installation_rows = bind.execute(
        sa.text(
            "SELECT id, deployment_id, instruction_revision_id, "
            "installation_key, state "
            "FROM agent_installations "
            "WHERE workspace_id = :workspace_id "
            " AND connector_id = :connector_id AND dialog_id = :dialog_id "
            "FOR UPDATE"
        ),
        {
            "workspace_id": workspace_id,
            "connector_id": target["connector_id"],
            "dialog_id": target["dialog_id"],
        },
    ).mappings().all()
    if len(installation_rows) > 1:
        raise RuntimeError("mayak_owner_route_installation_ambiguous")

    bind.execute(
        sa.text(
            "UPDATE messages SET project_id = :target_project_id "
            "WHERE id = :message_id AND project_id = :source_project_id"
        ),
        {
            "target_project_id": target_project_id,
            "message_id": target["message_id"],
            "source_project_id": target["source_project_id"],
        },
    )
    bind.execute(
        sa.text(
            "UPDATE reply_tasks SET project_id = :target_project_id "
            "WHERE id = :task_id AND project_id = :source_project_id"
        ),
        {
            "target_project_id": target_project_id,
            "task_id": target["task_id"],
            "source_project_id": target["source_project_id"],
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'active' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            " AND state = 'staged'"
        ),
        {
            "deployment_id": release["deployment_id"],
            "workspace_id": workspace_id,
        },
    )

    if not installation_rows:
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
                "deployment_id": release["deployment_id"],
                "revision_id": release["revision_id"],
                "installation_key": _INSTALLATION_KEY,
            },
        )
        return

    installation = installation_rows[0]
    if (
        installation["state"] == "active"
        and installation["deployment_id"] == release["deployment_id"]
        and installation["instruction_revision_id"] == release["revision_id"]
        and installation["installation_key"] == _INSTALLATION_KEY
    ):
        return
    if installation["state"] not in {
        "discovered",
        "pending_approval",
        "active",
        "paused",
    }:
        raise RuntimeError("mayak_owner_route_installation_state_conflict")
    if installation["deployment_id"] not in {None, release["deployment_id"]}:
        raise RuntimeError("mayak_owner_route_installation_deployment_conflict")
    if installation["instruction_revision_id"] not in {None, release["revision_id"]}:
        raise RuntimeError("mayak_owner_route_installation_revision_conflict")
    bind.execute(
        sa.text(
            "UPDATE agent_installations SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, "
            "installation_key = :installation_key, state = 'active', "
            "max_turns_per_window = 5, max_tokens_per_turn = 2048, "
            "rate_window_seconds = 60, dialog_label = 'Око возрождения', "
            "updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id"
        ),
        {
            "deployment_id": release["deployment_id"],
            "revision_id": release["revision_id"],
            "installation_key": _INSTALLATION_KEY,
            "installation_id": installation["id"],
            "workspace_id": workspace_id,
        },
    )


def downgrade() -> None:
    """Pause the reconciled installation and restore the exact source route."""

    bind = op.get_bind()
    workspace_id = bind.scalar(
        sa.text("SELECT id FROM workspaces WHERE key = :workspace_key"),
        {"workspace_key": _WORKSPACE_KEY},
    )
    source_project_id = bind.scalar(
        sa.text("SELECT id FROM projects WHERE alias = :source_project_alias"),
        {"source_project_alias": _SOURCE_PROJECT_ALIAS},
    )
    target_project_id = bind.scalar(
        sa.text("SELECT id FROM projects WHERE alias = :target_project_alias"),
        {"target_project_alias": _TARGET_PROJECT_ALIAS},
    )
    if workspace_id is None:
        return

    bind.execute(
        sa.text(
            "UPDATE delivery_grants SET revoked_at = now() "
            "WHERE workspace_id = :workspace_id AND used_at IS NULL "
            " AND revoked_at IS NULL AND installation_id IN ("
            "SELECT id FROM agent_installations "
            "WHERE workspace_id = :workspace_id "
            " AND installation_key = :installation_key)"
        ),
        {
            "workspace_id": workspace_id,
            "installation_key": _INSTALLATION_KEY,
        },
    )
    bind.execute(
        sa.text(
            "UPDATE agent_installations SET state = 'paused', updated_at = now() "
            "WHERE workspace_id = :workspace_id "
            " AND installation_key = :installation_key AND state = 'active'"
        ),
        {
            "workspace_id": workspace_id,
            "installation_key": _INSTALLATION_KEY,
        },
    )
    if source_project_id is not None and target_project_id is not None:
        message_id = bind.scalar(
            sa.text(
                "SELECT trigger_message_id FROM reply_tasks "
                "WHERE id = :task_id AND project_id = :target_project_id"
            ),
            {
                "task_id": _OWNER_ACCEPTANCE_TASK_ID,
                "target_project_id": target_project_id,
            },
        )
        if message_id is not None:
            bind.execute(
                sa.text(
                    "UPDATE messages SET project_id = :source_project_id "
                    "WHERE id = :message_id AND project_id = :target_project_id"
                ),
                {
                    "source_project_id": source_project_id,
                    "target_project_id": target_project_id,
                    "message_id": message_id,
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE reply_tasks SET project_id = :source_project_id "
                    "WHERE id = :task_id AND project_id = :target_project_id"
                ),
                {
                    "source_project_id": source_project_id,
                    "target_project_id": target_project_id,
                    "task_id": _OWNER_ACCEPTANCE_TASK_ID,
                },
            )

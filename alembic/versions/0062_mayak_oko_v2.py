"""Release the accepted Oko LMS copy on the exact owner-private route.

Revision ID: 0062_mayak_oko_v2
Revises: 0061_voice_dead_letter_reconciliation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_mayak_oko_v2"
down_revision: str | None = "0061_voice_dead_letter_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_KEY = "mark-personal"
_AGENT_KEY = "oko"
_INSTALLATION_KEY = "universal-mayak-v1-owner-private"
_OLD_REVISION_KEY = "universal-mayak-v1"
_OLD_DEPLOYMENT_KEY = "universal-mayak-v1-openai"
_NEW_REVISION_KEY = "universal-mayak-oko-v2"
_NEW_DEPLOYMENT_KEY = "universal-mayak-oko-v2-openai"
_NEW_REVISION_ID = "5d365625-50ab-561c-8af2-08594911e617"
_NEW_DEPLOYMENT_ID = "5bbbb4f4-f9a6-5143-8708-43f216ba323e"
_SOURCE_REPOSITORY = "markmalina/mayaki"
_SOURCE_PATH = "lms/oko-vozrozhdeniya"
_SOURCE_COMMIT_SHA = "0a638b580fc514f73cfb166c60c57531875e4887"
_CONTENT_SHA256 = (
    "11f94e09de0f4b4b2e60ed69d2ca0ec9da89bc3f07f1ff4b5a7e479e1711ae77"
)
_RUNTIME_PROFILE = "openai-responses-v1"
_MODEL_PROFILE = "gpt-5.6-luna"
_POLICY_VERSION = "universal-mayak-oko-v2"


def upgrade() -> None:
    """Create an immutable v2 release and atomically rebind one installation."""

    bind = op.get_bind()
    scope = _scope(bind)
    if scope is None:
        return
    workspace_id, definition_id = scope
    old = _release(
        bind,
        workspace_id=workspace_id,
        deployment_key=_OLD_DEPLOYMENT_KEY,
        revision_key=_OLD_REVISION_KEY,
    )
    installation = _installation(bind, workspace_id=workspace_id)
    if (
        installation["state"] != "active"
        or installation["deployment_id"] != old["deployment_id"]
        or installation["instruction_revision_id"] != old["revision_id"]
    ):
        raise RuntimeError("mayak_oko_v2_source_binding_not_exact")
    _require_quiescent(
        bind,
        workspace_id=workspace_id,
        installation_id=installation["installation_id"],
        dialog_id=installation["dialog_id"],
    )
    revision_id = _create_revision(
        bind,
        workspace_id=workspace_id,
        definition_id=definition_id,
    )
    deployment_id = _create_deployment(
        bind,
        workspace_id=workspace_id,
        definition_id=definition_id,
        revision_id=revision_id,
    )
    result = bind.execute(
        sa.text(
            "UPDATE agent_installations "
            "SET deployment_id = :deployment_id, "
            "instruction_revision_id = :revision_id, updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND installation_key = :installation_key AND state = 'active' "
            "AND deployment_id = :old_deployment_id "
            "AND instruction_revision_id = :old_revision_id"
        ),
        {
            "deployment_id": deployment_id,
            "revision_id": revision_id,
            "installation_id": installation["installation_id"],
            "workspace_id": workspace_id,
            "installation_key": _INSTALLATION_KEY,
            "old_deployment_id": old["deployment_id"],
            "old_revision_id": old["revision_id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("mayak_oko_v2_rebind_failed")


def downgrade() -> None:
    """Rebind the exact installation to v1 and pause v2 without losing audit rows."""

    bind = op.get_bind()
    scope = _scope(bind)
    if scope is None:
        return
    workspace_id, _definition_id = scope
    old = _release(
        bind,
        workspace_id=workspace_id,
        deployment_key=_OLD_DEPLOYMENT_KEY,
        revision_key=_OLD_REVISION_KEY,
    )
    new = _release(
        bind,
        workspace_id=workspace_id,
        deployment_key=_NEW_DEPLOYMENT_KEY,
        revision_key=_NEW_REVISION_KEY,
    )
    installation = _installation(bind, workspace_id=workspace_id)
    if (
        installation["state"] != "active"
        or installation["deployment_id"] != new["deployment_id"]
        or installation["instruction_revision_id"] != new["revision_id"]
    ):
        raise RuntimeError("mayak_oko_v2_rollback_binding_not_exact")
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
            "instruction_revision_id = :revision_id, updated_at = now() "
            "WHERE id = :installation_id AND workspace_id = :workspace_id "
            "AND installation_key = :installation_key AND state = 'active' "
            "AND deployment_id = :new_deployment_id "
            "AND instruction_revision_id = :new_revision_id"
        ),
        {
            "deployment_id": old["deployment_id"],
            "revision_id": old["revision_id"],
            "installation_id": installation["installation_id"],
            "workspace_id": workspace_id,
            "installation_key": _INSTALLATION_KEY,
            "new_deployment_id": new["deployment_id"],
            "new_revision_id": new["revision_id"],
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("mayak_oko_v2_rollback_failed")
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET state = 'paused' "
            "WHERE id = :deployment_id AND workspace_id = :workspace_id "
            "AND deployment_key = :deployment_key AND state = 'active'"
        ),
        {
            "deployment_id": new["deployment_id"],
            "workspace_id": workspace_id,
            "deployment_key": _NEW_DEPLOYMENT_KEY,
        },
    )


def _scope(bind: sa.engine.Connection) -> tuple[object, object] | None:
    rows = bind.execute(
        sa.text(
            "SELECT workspace.id AS workspace_id, definition.id AS definition_id "
            "FROM workspaces workspace "
            "JOIN agent_definitions definition "
            "ON definition.workspace_id = workspace.id "
            "WHERE workspace.key = :workspace_key "
            "AND workspace.is_active IS TRUE "
            "AND definition.agent_key = :agent_key"
        ),
        {"workspace_key": _WORKSPACE_KEY, "agent_key": _AGENT_KEY},
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("mayak_oko_v2_scope_ambiguous")
    return rows[0]["workspace_id"], rows[0]["definition_id"]


def _release(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    deployment_key: str,
    revision_key: str,
) -> sa.RowMapping:
    rows = bind.execute(
        sa.text(
            "SELECT deployment.id AS deployment_id, "
            "revision.id AS revision_id "
            "FROM agent_deployments deployment "
            "JOIN instruction_revisions revision "
            "ON revision.id = deployment.instruction_revision_id "
            "AND revision.workspace_id = deployment.workspace_id "
            "WHERE deployment.workspace_id = :workspace_id "
            "AND deployment.deployment_key = :deployment_key "
            "AND deployment.state = 'active' "
            "AND revision.revision_key = :revision_key "
            "AND revision.validation_state = 'validated'"
        ),
        {
            "workspace_id": workspace_id,
            "deployment_key": deployment_key,
            "revision_key": revision_key,
        },
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("mayak_oko_v2_release_not_exact")
    return rows[0]


def _installation(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
) -> sa.RowMapping:
    rows = bind.execute(
        sa.text(
            "SELECT id AS installation_id, dialog_id, deployment_id, "
            "instruction_revision_id, state "
            "FROM agent_installations "
            "WHERE workspace_id = :workspace_id "
            "AND installation_key = :installation_key "
            "FOR UPDATE"
        ),
        {
            "workspace_id": workspace_id,
            "installation_key": _INSTALLATION_KEY,
        },
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("mayak_oko_v2_installation_not_exact")
    return rows[0]


def _create_revision(
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
            "revision_key": _NEW_REVISION_KEY,
        },
    ).mappings().one_or_none()
    expected = {
        "source_repository": _SOURCE_REPOSITORY,
        "source_path": _SOURCE_PATH,
        "source_commit_sha": _SOURCE_COMMIT_SHA,
        "content_sha256": _CONTENT_SHA256,
        "schema_version": "agent-bundle-v1",
        "validation_state": "validated",
    }
    if row is not None:
        if any(row[key] != value for key, value in expected.items()):
            raise RuntimeError("mayak_oko_v2_revision_conflict")
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
            "id": _NEW_REVISION_ID,
            "workspace_id": workspace_id,
            "definition_id": definition_id,
            "revision_key": _NEW_REVISION_KEY,
            **expected,
        },
    )


def _create_deployment(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    definition_id: object,
    revision_id: object,
) -> object:
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
            "deployment_key": _NEW_DEPLOYMENT_KEY,
        },
    ).mappings().one_or_none()
    expected = {
        "agent_definition_id": definition_id,
        "instruction_revision_id": revision_id,
        "runtime_profile": _RUNTIME_PROFILE,
        "model_profile": _MODEL_PROFILE,
        "policy_version": _POLICY_VERSION,
        "state": "active",
    }
    if row is not None:
        if any(row[key] != value for key, value in expected.items()):
            raise RuntimeError("mayak_oko_v2_deployment_conflict")
        return row["id"]
    return bind.scalar(
        sa.text(
            "INSERT INTO agent_deployments "
            "(id, workspace_id, agent_definition_id, instruction_revision_id, "
            "deployment_key, runtime_profile, model_profile, policy_version, state) "
            "VALUES (:id, :workspace_id, :definition_id, :revision_id, "
            ":deployment_key, :runtime_profile, :model_profile, "
            ":policy_version, 'active') RETURNING id"
        ),
        {
            "id": _NEW_DEPLOYMENT_ID,
            "workspace_id": workspace_id,
            "definition_id": definition_id,
            "revision_id": revision_id,
            "deployment_key": _NEW_DEPLOYMENT_KEY,
            "runtime_profile": _RUNTIME_PROFILE,
            "model_profile": _MODEL_PROFILE,
            "policy_version": _POLICY_VERSION,
        },
    )


def _require_quiescent(
    bind: sa.engine.Connection,
    *,
    workspace_id: object,
    installation_id: object,
    dialog_id: object,
) -> None:
    nonterminal_turns = bind.scalar(
        sa.text(
            "SELECT count(*) FROM agent_turns turn "
            "JOIN agent_sessions session ON session.id = turn.session_id "
            "AND session.workspace_id = turn.workspace_id "
            "WHERE turn.workspace_id = :workspace_id "
            "AND session.dialog_id = :dialog_id "
            "AND turn.status IN ('queued', 'running')"
        ),
        {"workspace_id": workspace_id, "dialog_id": dialog_id},
    )
    if nonterminal_turns:
        raise RuntimeError("mayak_oko_v2_nonterminal_turn_exists")
    unused_grants = bind.scalar(
        sa.text(
            "SELECT count(*) FROM delivery_grants "
            "WHERE workspace_id = :workspace_id "
            "AND installation_id = :installation_id "
            "AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {
            "workspace_id": workspace_id,
            "installation_id": installation_id,
        },
    )
    if unused_grants:
        raise RuntimeError("mayak_oko_v2_unused_grant_exists")

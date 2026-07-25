"""Safety and contract evidence for the exact owner Mayak successor."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

from app.api.mcp_schemas import GATE_STATUS_OUTPUT_SCHEMA
from app.core.config import Settings
from app.infrastructure.universal_mayak_private_runtime import (
    SessionFactoryApprovedPrivateBindingResolver,
)
from app.infrastructure.universal_mayak_status import (
    _has_input_digest,
    _redacted_code,
    universal_mayak_operational_status,
)
from jsonschema import Draft202012Validator

DIGEST = "a" * 64


class FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._rows)

    def mappings(self) -> SimpleNamespace:
        return SimpleNamespace(
            one=lambda: self._rows[0],
            one_or_none=lambda: self._rows[0] if self._rows else None,
        )


class FakeSession:
    def __init__(self, batches: list[list[object]]) -> None:
        self._batches = batches
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        rows = self._batches.pop(0)
        return rows[0] if rows else None

    async def execute(
        self,
        statement: object,
        _parameters: object | None = None,
    ) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self._batches.pop(0))


def test_status_is_redacted_and_proves_terminal_replay_without_provider() -> None:
    session = FakeSession(
        [
            ["11111111-1111-1111-1111-111111111111"],
            [
                {
                    "total": 1,
                    "discovered": 0,
                    "pending_approval": 0,
                    "active": 1,
                    "paused": 0,
                    "revoked": 0,
                    "exact_active": 1,
                }
            ],
            [
                {
                    "total": 1,
                    "active": 1,
                    "paused": 0,
                    "completed": 0,
                    "handoff_required": 0,
                }
            ],
            [{"total": 1, "queued": 0, "running": 0, "proposed": 1, "failed": 0}],
            [{"total": 1, "open": 0, "used": 1, "revoked": 0}],
            [
                {
                    "total": 1,
                    "reserved": 0,
                    "sending": 0,
                    "sent": 1,
                    "failed": 0,
                    "unknown": 0,
                    "blocked": 0,
                }
            ],
            [
                {
                    "status": "proposed",
                    "result_code": "mayak_demo_sent",
                    "context_message_ids": [f"mayak-input-sha256:{DIGEST}"],
                }
            ],
            [{"state": "sent", "error_code": None}],
            [
                {
                    "task_present": True,
                    "account_ready": True,
                    "trigger_message_present": True,
                    "projects_allowed": True,
                    "canonical_ready": True,
                    "private_inbound_ready": True,
                    "actor_dialog_ready": True,
                    "connector_ready": True,
                    "business_connection_ready": True,
                    "release_ready": True,
                }
            ],
        ]
    )
    settings = Settings(
        _env_file=None,
        GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED=True,
        GATE_UNIVERSAL_MAYAK_MODEL_DATA_GATE_APPROVED=True,
        GATE_UNIVERSAL_MAYAK_DELIVERY_ENABLED=True,
    )

    status = asyncio.run(
        universal_mayak_operational_status(
            session,
            workspace_key="mark-personal",
            settings=settings,
        )
    )

    assert status["state"] == "active_observed"
    assert status["installation"]["ready"] is True
    assert status["owner_route_probe"] == {
        "task_present": True,
        "account_ready": True,
        "trigger_message_present": True,
        "projects_allowed": True,
        "canonical_ready": True,
        "private_inbound_ready": True,
        "actor_dialog_ready": True,
        "connector_ready": True,
        "business_connection_ready": True,
        "release_ready": True,
        "ready": True,
    }
    assert status["provider_free_replay_verifier"] == {
        "available": True,
        "input_digest_bound": True,
        "terminal_state": True,
        "verified": True,
        "provider_call_required": False,
    }
    assert status["private_content_exposed"] is False
    assert status["provider_identifiers_exposed"] is False
    assert status["telegram_identifiers_exposed"] is False
    assert len(session.statements) == 9

    schema = GATE_STATUS_OUTPUT_SCHEMA["properties"]["universal_mayak"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(status)


def test_status_helpers_fail_closed_on_payload_like_codes_and_bad_digests() -> None:
    assert _redacted_code("mayak_demo_sent") == "mayak_demo_sent"
    assert _redacted_code("private person text") is None
    assert _redacted_code("mayak_bad value") is None
    assert _has_input_digest(
        {"context_message_ids": [f"mayak-input-sha256:{DIGEST}"]}
    )
    assert not _has_input_digest(
        {"context_message_ids": ["mayak-input-sha256:not-a-digest"]}
    )


def test_owner_route_migration_is_exact_reversible_and_not_account_global() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0045_mayak_owner_route.py"
    )
    spec = importlib.util.spec_from_file_location("mayak_owner_route", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0045_mayak_owner_route"
    assert module.down_revision == "0044_mayak_owner_release"
    source = path.read_text(encoding="utf-8")
    for guard in (
        "rt.id = :task_id",
        "source_project.alias = :source_project_alias",
        "a.alias = :account_alias",
        "cm.provider_sender_id = d.provider_dialog_id",
        "m.sender_id = d.provider_dialog_id",
        "d.dialog_kind = 'private'",
        "bc.is_enabled IS TRUE",
        "FOR UPDATE OF rt, m, d, c",
        "UPDATE messages SET project_id = :target_project_id",
        "UPDATE reply_tasks SET project_id = :target_project_id",
        "UPDATE delivery_grants SET revoked_at = now()",
        "state = 'paused'",
    ):
        assert guard in source
    assert "UPDATE accounts SET linked_project_id" not in source
    assert "provider_dialog_id =" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)



def test_successor_reconciles_after_reply_task_status_changes() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0046_mayak_installation_reconcile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mayak_installation_reconcile",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0046_mayak_reconcile"
    assert module.down_revision == "0045_mayak_owner_route"
    source = path.read_text(encoding="utf-8")
    assert "rt.status = 'needs_reply'" not in source
    for guard in (
        "rt.id = :task_id",
        "source_project.alias IN",
        "(:source_project_alias, :target_project_alias)",
        '"target_project_alias": _TARGET_PROJECT_ALIAS',
        "installation_key, state",
        "installation_key = :installation_key, state = 'active'",
        '"active",',
        "UPDATE delivery_grants SET revoked_at = now()",
        "UPDATE messages SET project_id = :source_project_id",
        "UPDATE reply_tasks SET project_id = :source_project_id",
    ):
        assert guard in source
    assert "UPDATE accounts SET linked_project_id" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_runtime_accepts_only_explicit_source_or_oko_project() -> None:
    source = inspect.getsource(SessionFactoryApprovedPrivateBindingResolver)
    assert 'source_project.alias.in_(("unclassified", AgentKey.OKO.value))' in source
    assert "oko_project.alias == AgentKey.OKO.value" in source
    assert "Message.project_id == source_project.id" in source
    assert "Account.linked_project_id == Project.id" not in source
def test_exact_identity_successor_allows_independent_legacy_projects() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0047_mayak_exact_identity_reconcile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mayak_exact_identity_reconcile",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0047_mayak_exact_route"
    assert module.down_revision == "0046_mayak_reconcile"
    source = path.read_text(encoding="utf-8")
    for exact_identity in (
        "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2",
        "e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0",
        "f2659ed6-9a1d-5f72-8bf1-fe45420cfef7",
        "4f060e00-0bb5-4701-8f38-80b06da9fc8e",
    ):
        assert exact_identity in source
    for guard in (
        "rt.account_id = :account_id",
        "m.account_id = :account_id",
        "rt.project_id IN (:source_project_id, :target_project_id)",
        "m.project_id IN (:source_project_id, :target_project_id)",
        "cm.provider_sender_id = d.provider_dialog_id",
        "m.sender_id = d.provider_dialog_id",
        "d.dialog_kind = 'private'",
        "c.connector_kind = 'telegram_business'",
        "bc.is_enabled IS TRUE",
        "FOR UPDATE OF rt, m, d, c",
        "UPDATE delivery_grants SET revoked_at = now()",
        "state = 'paused'",
    ):
        assert guard in source
    assert "m.project_id = rt.project_id" not in source
    assert "a.owner =" not in source
    assert "a.account_type =" not in source
    assert "UPDATE accounts SET linked_project_id" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
def test_connector_successor_uses_exact_canonical_dialog_binding() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0048_mayak_connector_binding.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mayak_connector_binding",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0048_mayak_connector"
    assert module.down_revision == "0047_mayak_exact_route"
    migration_source = path.read_text(encoding="utf-8")
    assert "c.connector_key = a.alias" not in migration_source
    assert "c.connector_kind = 'telegram_business'" in migration_source
    assert "c.is_enabled IS TRUE" in migration_source
    assert "d.id = cm.dialog_id" in migration_source
    assert "c.id = d.connector_id" in migration_source

    runtime_source = inspect.getsource(
        SessionFactoryApprovedPrivateBindingResolver
    )
    assert "TransportConnector.connector_key == Account.alias" not in runtime_source
    assert "Dialog.connector_id == TransportConnector.id" in runtime_source
    assert "TransportConnector.connector_kind" in runtime_source
    assert "ConnectorKind.TELEGRAM_BUSINESS.value" in runtime_source
    assert "TransportConnector.is_enabled.is_(True)" in runtime_source
    assert callable(module.upgrade)
    assert callable(module.downgrade)

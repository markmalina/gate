"""Fail-closed SQL binding tests for the owner-only private Mayak."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from app.application.universal_mayak_demo import UNIVERSAL_MAYAK_PROMPT_SHA256
from app.infrastructure.universal_mayak_private_runtime import (
    SessionFactoryApprovedPrivateBindingResolver,
)

ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
WORKSPACE_ID = "33333333-3333-3333-3333-333333333333"
INSTALLATION_ID = "44444444-4444-4444-4444-444444444444"
DIALOG_UUID = "55555555-5555-5555-5555-555555555555"
CANONICAL_MESSAGE_ID = "99999999-9999-9999-9999-999999999999"
BUSINESS_CONNECTION_ID = "owner_business_connection"
PROVIDER_DIALOG_ID = "700000001"


class Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.rows)


class Sessions:
    def __init__(self, rows):
        self.session = Session(rows)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.session


def row(*, digest: str = UNIVERSAL_MAYAK_PROMPT_SHA256):
    return (
        SimpleNamespace(id=UUID(WORKSPACE_ID)),
        SimpleNamespace(id=UUID(ACCOUNT_ID)),
        SimpleNamespace(id=UUID(PROJECT_ID)),
        SimpleNamespace(business_connection_id=BUSINESS_CONNECTION_ID),
        SimpleNamespace(id=UUID("66666666-6666-6666-6666-666666666666")),
        SimpleNamespace(
            id=UUID(DIALOG_UUID),
            provider_dialog_id=PROVIDER_DIALOG_ID,
        ),
        SimpleNamespace(id=UUID(CANONICAL_MESSAGE_ID)),
        SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        SimpleNamespace(id=UUID(INSTALLATION_ID)),
        SimpleNamespace(id=UUID("77777777-7777-7777-7777-777777777777")),
        SimpleNamespace(content_sha256=digest),
        SimpleNamespace(id=UUID("88888888-8888-8888-8888-888888888888")),
    )


def resolve(sessions: Sessions, **overrides):
    values = {
        "account_id": ACCOUNT_ID,
        "project_id": PROJECT_ID,
        "canonical_message_id": CANONICAL_MESSAGE_ID,
        "business_connection_id": BUSINESS_CONNECTION_ID,
        "provider_dialog_id": PROVIDER_DIALOG_ID,
        "provider_actor_id": PROVIDER_DIALOG_ID,
    }
    values.update(overrides)
    resolver = SessionFactoryApprovedPrivateBindingResolver(sessions)
    return asyncio.run(resolver.resolve(**values))


def test_exact_owner_business_binding_is_projected_without_provider_target() -> None:
    sessions = Sessions([row()])

    binding = resolve(sessions)

    assert binding is not None
    assert binding.account_id == ACCOUNT_ID
    assert binding.project_id == PROJECT_ID
    assert binding.business_connection_id == BUSINESS_CONNECTION_ID
    assert binding.provider_dialog_id == PROVIDER_DIALOG_ID
    assert binding.provider_actor_id == PROVIDER_DIALOG_ID
    assert binding.scope.workspace_ref == WORKSPACE_ID
    assert binding.scope.installation_ref == INSTALLATION_ID
    assert binding.scope.dialog_ref == DIALOG_UUID
    assert binding.actor_scope == "owner"
    statement = str(sessions.session.statements[0])
    for table in (
        "telegram_business_connections",
        "transport_connectors",
        "dialogs",
        "canonical_messages",
        "messages",
        "agent_installations",
        "agent_deployments",
        "instruction_revisions",
        "agent_definitions",
    ):
        assert table in statement


def test_invalid_or_non_owner_actor_fails_before_sql() -> None:
    invalid = Sessions([row()])
    mismatch = Sessions([row()])

    assert resolve(invalid, account_id="not-a-uuid") is None
    assert (
        resolve(mismatch, provider_actor_id="different-private-participant")
        is None
    )
    assert invalid.calls == 0
    assert mismatch.calls == 0


def test_missing_ambiguous_or_unreviewed_binding_fails_closed() -> None:
    assert resolve(Sessions([])) is None
    assert resolve(Sessions([row(), row()])) is None
    assert resolve(Sessions([row(digest="0" * 64)])) is None

"""The pre-migration probe remains read-only and its receipt stays redacted."""

import asyncio
from types import SimpleNamespace

import pytest

from app.worker import reply_sequence_baseline_probe as probe


class FakeResult:
    def __init__(self, values):
        self.values = values

    def one(self):
        return self.values

    def scalars(self):
        return self

    def all(self):
        return self.values


class FakeConnection:
    def __init__(self, revisions):
        self.revisions = revisions
        self.statements = []

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "current_database()" in sql:
            return FakeResult(
                ("private_database", "public", "private_role", "10.0.0.1", "5432")
            )
        if sql == "SELECT version_num FROM alembic_version":
            return FakeResult(self.revisions)
        raise AssertionError(sql)


def test_pre_0112_probe_reads_only_session_and_schema_and_redacts_identity():
    connection = FakeConnection(["0111_reply_documents"])
    digest, revision = asyncio.run(probe.read_session_identity(connection))
    assert revision == "0111_reply_documents"
    assert len(digest) == 64
    assert "private" not in digest
    assert len(connection.statements) == 2
    assert all(statement.startswith("SELECT ") for statement in connection.statements)
    assert not any(
        "reply_dispatch_control" in statement for statement in connection.statements
    )


@pytest.mark.parametrize("revisions", [[], ["0111_reply_documents", "0110_previous"]])
def test_ambiguous_schema_ledger_fails_closed(revisions):
    with pytest.raises(ValueError, match="invalid_schema_ledger"):
        asyncio.run(probe.read_session_identity(FakeConnection(revisions)))


def test_workspace_mismatch_does_not_open_database(monkeypatch):
    monkeypatch.setattr(
        probe,
        "get_settings",
        lambda: SimpleNamespace(gate_workspace_id="other", database_url="never-open"),
    )
    monkeypatch.setattr(
        probe, "create_async_engine", lambda *_a, **_kw: pytest.fail("opened DB")
    )
    with pytest.raises(ValueError, match="workspace_mismatch"):
        asyncio.run(
            probe.execute(
                operation_id="opaque-1",
                expected_workspace="mark-personal",
                expected_schema="0111_reply_documents",
            )
        )


@pytest.mark.parametrize("reference", ["", "with space", "../bad", "x" * 129])
def test_operation_reference_rejects_unbounded_or_shell_like_input(reference):
    with pytest.raises(ValueError, match="invalid_reference"):
        asyncio.run(
            probe.execute(
                operation_id=reference,
                expected_workspace="mark-personal",
                expected_schema="0111_reply_documents",
            )
        )

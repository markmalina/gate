"""Idempotency and redaction tests for the Alpha provider preflight."""

import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.application.agent_kernel import AgentResultKind, AgentRuntimeResult
from app.core.config import Settings
from app.infrastructure import alpha_model_preflight as preflight
from app.infrastructure.openai_agent_runtime import OpenAIAgentRuntimeError
from app.models.alpha_preflight import AlphaModelPreflightModel
from pydantic import SecretStr


class FakeSession:
    def __init__(self) -> None:
        self.row: AlphaModelPreflightModel | None = None
        self.commits = 0
        self.rollbacks = 0

    def add(self, row: AlphaModelPreflightModel) -> None:
        if row.id is None:
            row.id = uuid.uuid4()
        self.row = row

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        gate_alpha_model_runtime_enabled=True,
        gate_alpha_model_data_gate_approved=True,
        openai_api_key=SecretStr("test-key"),
    )


@pytest.mark.anyio
async def test_preflight_calls_provider_once_and_never_persists_candidate_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    existing_results: list[AlphaModelPreflightModel | None] = [None, None]

    async def existing(*_args: Any, **_kwargs: Any) -> AlphaModelPreflightModel | None:
        return existing_results.pop(0)

    async def binding(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"workspace": SimpleNamespace(id=uuid.uuid4())}

    async def budget(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def locked(*_args: Any, **_kwargs: Any) -> AlphaModelPreflightModel:
        assert session.row is not None
        return session.row

    class Runtime:
        calls = 0

        async def propose(self, _command: object) -> AgentRuntimeResult:
            self.calls += 1
            return AgentRuntimeResult(
                kind=AgentResultKind.REPLY,
                result_code="alpha_preflight_ok",
                candidate_text="готово",
                input_tokens=120,
                output_tokens=4,
            )

    runtime = Runtime()
    monkeypatch.setattr(preflight, "_existing_preflight", existing)
    monkeypatch.setattr(preflight, "_exact_binding", binding)
    monkeypatch.setattr(preflight, "_require_budget", budget)
    monkeypatch.setattr(preflight, "_locked_preflight", locked)
    monkeypatch.setattr(
        preflight,
        "_runtime_and_command",
        lambda **_kwargs: (runtime, object()),
    )

    result = await preflight.run_alpha_model_preflight(
        session,  # type: ignore[arg-type]
        settings=_settings(),
        http_client=SimpleNamespace(),  # type: ignore[arg-type]
        workspace_key="mark-personal",
    )

    assert runtime.calls == 1
    assert result["state"] == "succeeded"
    assert result["provider_attempts"] == 1
    assert result["telegram_delivery_blocked"] is True
    assert result["candidate_sha256"] == hashlib.sha256(
        "готово".encode()
    ).hexdigest()
    assert "candidate_text" not in result
    assert session.row is not None
    assert not hasattr(session.row, "candidate_text")


@pytest.mark.anyio
async def test_existing_preflight_is_terminal_and_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = AlphaModelPreflightModel(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        instruction_revision_id=uuid.uuid4(),
        preflight_key=preflight.PREFLIGHT_KEY,
        state="failed",
        reserved_tokens=preflight.PREFLIGHT_RESERVED_TOKENS,
        error_code="openai_credentials_rejected",
    )

    async def existing(*_args: Any, **_kwargs: Any) -> AlphaModelPreflightModel:
        return row

    monkeypatch.setattr(preflight, "_existing_preflight", existing)
    result = await preflight.run_alpha_model_preflight(
        FakeSession(),  # type: ignore[arg-type]
        settings=_settings(),
        http_client=SimpleNamespace(),  # type: ignore[arg-type]
        workspace_key="mark-personal",
    )

    assert result["state"] == "failed"
    assert result["replay"] is True
    assert result["error_code"] == "openai_credentials_rejected"
    assert result["provider_attempts"] == 1


@pytest.mark.anyio
async def test_provider_failure_is_bounded_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    existing_results: list[AlphaModelPreflightModel | None] = [None, None]

    async def existing(*_args: Any, **_kwargs: Any) -> AlphaModelPreflightModel | None:
        return existing_results.pop(0)

    async def binding(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"workspace": SimpleNamespace(id=uuid.uuid4())}

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def locked(*_args: Any, **_kwargs: Any) -> AlphaModelPreflightModel:
        assert session.row is not None
        return session.row

    class Runtime:
        async def propose(self, _command: object) -> AgentRuntimeResult:
            raise OpenAIAgentRuntimeError("openai_rate_limited")

    monkeypatch.setattr(preflight, "_existing_preflight", existing)
    monkeypatch.setattr(preflight, "_exact_binding", binding)
    monkeypatch.setattr(preflight, "_require_budget", noop)
    monkeypatch.setattr(preflight, "_locked_preflight", locked)
    monkeypatch.setattr(
        preflight,
        "_runtime_and_command",
        lambda **_kwargs: (Runtime(), object()),
    )

    result = await preflight.run_alpha_model_preflight(
        session,  # type: ignore[arg-type]
        settings=_settings(),
        http_client=SimpleNamespace(),  # type: ignore[arg-type]
        workspace_key="mark-personal",
    )

    assert result["state"] == "failed"
    assert result["error_code"] == "openai_rate_limited"
    assert "provider_body" not in result

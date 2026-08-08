from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from app.infrastructure.beta_checkpoint_dispatch import (
    BETA_CHECKPOINT_DISPATCH_EVENT,
    BETA_CHECKPOINT_DISPATCH_SCHEMA,
    BetaCheckpointDispatchError,
    GitHubAppBetaCheckpointDispatcher,
    build_beta_checkpoint_dispatch,
)

NOW = datetime(2026, 8, 8, 17, 10, tzinfo=UTC)
SPACE_ID = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"


def _private_key() -> tuple[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private_pem, key.public_key()


def test_dispatch_envelope_is_exact_content_free_and_deterministic() -> None:
    first = build_beta_checkpoint_dispatch(
        space_id=SPACE_ID,
        accepted_sequence=17,
    )
    second = build_beta_checkpoint_dispatch(
        space_id=SPACE_ID,
        accepted_sequence=17,
    )

    assert first == second
    assert first.as_client_payload() == {
        "schema_version": BETA_CHECKPOINT_DISPATCH_SCHEMA,
        "space_id": SPACE_ID,
        "accepted_sequence": 17,
        "dispatch_id": first.dispatch_id,
    }
    assert first.dispatch_id.startswith("dispatch-")
    assert len(first.dispatch_id) == 73
    encoded = json.dumps(first.as_client_payload())
    for forbidden in ("telegram", "message", "text", "payload", "chat_id"):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("space_id", "sequence", "code"),
    [
        ("wedding-minsk", 1, "beta_checkpoint_dispatch_space_invalid"),
        (SPACE_ID, 0, "beta_checkpoint_dispatch_sequence_invalid"),
        (SPACE_ID, 1_000_000_000_000, "beta_checkpoint_dispatch_sequence_invalid"),
        (SPACE_ID, True, "beta_checkpoint_dispatch_sequence_invalid"),
    ],
)
def test_dispatch_envelope_rejects_invalid_scope(
    space_id: str, sequence: int, code: str
) -> None:
    with pytest.raises(BetaCheckpointDispatchError) as caught:
        build_beta_checkpoint_dispatch(
            space_id=space_id,
            accepted_sequence=sequence,
        )
    assert caught.value.code == code


def test_github_app_sender_mints_short_lived_token_then_dispatches_once() -> None:
    private_pem, public_key = _private_key()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/app/installations/2468/access_tokens":
            encoded = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(
                encoded,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            assert claims["iss"] == "1357"
            assert claims["exp"] - claims["iat"] == 570
            assert request.content == b""
            return httpx.Response(201, json={"token": "ghs_synthetic_installation"})
        assert request.url.path == "/repos/AlphaCenter/beta/dispatches"
        assert request.headers["Authorization"] == (
            "Bearer ghs_synthetic_installation"
        )
        assert json.loads(request.content) == {
            "event_type": BETA_CHECKPOINT_DISPATCH_EVENT,
            "client_payload": build_beta_checkpoint_dispatch(
                space_id=SPACE_ID,
                accepted_sequence=17,
            ).as_client_payload(),
        }
        return httpx.Response(204)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            sender = GitHubAppBetaCheckpointDispatcher(
                client=client,
                app_id="1357",
                installation_id="2468",
                private_key_pem=private_pem,
                now=lambda: NOW,
            )
            result = await sender.dispatch(
                space_id=SPACE_ID,
                accepted_sequence=17,
            )
        assert result.space_id == SPACE_ID

    asyncio.run(exercise())
    assert [request.url.path for request in requests] == [
        "/app/installations/2468/access_tokens",
        "/repos/AlphaCenter/beta/dispatches",
    ]


@pytest.mark.parametrize(
    ("token_status", "token_body", "expected"),
    [
        (403, {"message": "synthetic"}, "beta_checkpoint_dispatch_token_rejected"),
        (201, {}, "beta_checkpoint_dispatch_token_invalid"),
        (
            201,
            {"token": "space separated token"},
            "beta_checkpoint_dispatch_token_invalid",
        ),
    ],
)
def test_sender_fails_closed_without_dispatching_on_bad_token_response(
    token_status: int,
    token_body: dict[str, str],
    expected: str,
) -> None:
    private_pem, _public_key = _private_key()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(token_status, json=token_body)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            sender = GitHubAppBetaCheckpointDispatcher(
                client=client,
                app_id="1357",
                installation_id="2468",
                private_key_pem=private_pem,
                now=lambda: NOW,
            )
            with pytest.raises(BetaCheckpointDispatchError) as caught:
                await sender.dispatch(
                    space_id=SPACE_ID,
                    accepted_sequence=17,
                )
            assert caught.value.code == expected

    asyncio.run(exercise())
    assert calls == 1


def test_sender_requires_explicit_github_app_identity() -> None:
    with pytest.raises(BetaCheckpointDispatchError) as caught:
        GitHubAppBetaCheckpointDispatcher(
            client=httpx.AsyncClient(),
            app_id="",
            installation_id="",
            private_key_pem="",
        )
    assert caught.value.code == "beta_checkpoint_dispatch_configuration_invalid"

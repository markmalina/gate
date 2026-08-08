from __future__ import annotations

import pytest
from app.core.config import Settings
from app.core.github_actions_oidc import GitHubOidcError, _authorize_claims
from app.infrastructure.beta_checkpoint_export import _build_portable_checkpoint
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def _claims(
    settings: Settings,
    *,
    standard_subject: bool = False,
) -> dict[str, object]:
    repository_owner, repository_name = (
        settings.gate_beta_checkpoint_oidc_repository.split("/", maxsplit=1)
    )
    return {
        "sub": (
            (
                f"repo:{settings.gate_beta_checkpoint_oidc_repository}:"
                f"ref:{settings.gate_beta_checkpoint_oidc_ref}"
            )
            if standard_subject
            else (
                f"repo:{repository_owner}@"
                f"{settings.gate_beta_checkpoint_oidc_repository_owner_id}"
                f"/{repository_name}@"
                f"{settings.gate_beta_checkpoint_oidc_repository_id}:"
                f"ref:{settings.gate_beta_checkpoint_oidc_ref}"
            )
        ),
        "repository": settings.gate_beta_checkpoint_oidc_repository,
        "repository_id": settings.gate_beta_checkpoint_oidc_repository_id,
        "repository_owner_id": (
            settings.gate_beta_checkpoint_oidc_repository_owner_id
        ),
        "repository_visibility": "private",
        "ref": settings.gate_beta_checkpoint_oidc_ref,
        "workflow_ref": settings.gate_beta_checkpoint_oidc_workflow_ref,
        "event_name": "workflow_dispatch",
        "aud": settings.gate_beta_checkpoint_oidc_audience,
    }


def test_exact_private_beta_workflow_claims_are_accepted() -> None:
    settings = Settings(_env_file=None)
    _authorize_claims(_claims(settings), settings)


def test_exact_standard_private_beta_workflow_claims_are_accepted() -> None:
    settings = Settings(_env_file=None)
    _authorize_claims(_claims(settings, standard_subject=True), settings)


def test_malformed_checkpoint_repository_setting_fails_closed() -> None:
    settings = Settings(
        _env_file=None,
        gate_beta_checkpoint_oidc_repository="AlphaCenter/beta/extra",
    )
    with pytest.raises(
        GitHubOidcError,
        match="beta_checkpoint_oidc_configuration_invalid",
    ):
        _authorize_claims(_claims(settings), settings)


def test_checkpoint_oidc_defaults_target_transferred_repository() -> None:
    settings = Settings(_env_file=None)
    assert settings.gate_beta_checkpoint_oidc_repository == "AlphaCenter/beta"
    assert settings.gate_beta_checkpoint_oidc_repository_id == "1312239432"
    assert settings.gate_beta_checkpoint_oidc_repository_owner_id == "310893851"
    assert settings.gate_beta_checkpoint_oidc_workflow_ref == (
        "AlphaCenter/beta/.github/workflows/"
        "checkpoint-context.yml@refs/heads/main"
    )


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("sub", "repo:attacker@9/beta@9:ref:refs/heads/main"),
        ("repository", "attacker/beta"),
        ("repository_id", "9"),
        ("repository_owner_id", "9"),
        ("repository_visibility", "public"),
        ("ref", "refs/pull/9/merge"),
        (
            "workflow_ref",
            "attacker/beta/.github/workflows/"
            "checkpoint-context.yml@refs/heads/main",
        ),
        ("event_name", "pull_request"),
    ],
)
def test_oidc_claim_drift_fails_closed(claim: str, value: str) -> None:
    settings = Settings(_env_file=None)
    claims = _claims(settings)
    claims[claim] = value
    with pytest.raises(GitHubOidcError):
        _authorize_claims(claims, settings)


def test_incremental_checkpoint_range_can_start_after_one() -> None:
    group_id = "beta-group-stroi-alphav-001"
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    events = [
        {
            "schema_version": "beta.context-event@1",
            "event_id": f"event-{sequence}",
            "group_id": group_id,
            "sequence": sequence,
            "kind": "message.inbound",
            "occurred_at": "2026-07-26T14:00:00.000Z",
            "recorded_at": "2026-07-26T14:00:01.000Z",
            "actor_ref": "actor-safe",
            "content": {
                "format": "structured_text",
                "text": "synthetic",
                "language": "ru",
            },
            "provenance": {
                "source": "telegram",
                "source_event_ref": f"gate-event-{sequence}",
                "content_sha256": "a" * 64,
                "idempotency_key": f"gate.context.{sequence}",
            },
            "supersedes": None,
            "tombstones": None,
        }
        for sequence in (51, 52)
    ]
    checkpoint = _build_portable_checkpoint(
        group_id=group_id,
        events=events,
        recipient_public_key=public,
        created_at="2026-07-26T14:01:00.000Z",
    )
    assert checkpoint["manifest"]["sequence_start"] == 51
    assert checkpoint["manifest"]["sequence_end"] == 52

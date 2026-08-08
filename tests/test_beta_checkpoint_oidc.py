from __future__ import annotations

import hashlib

import pytest
from app.core.config import Settings
from app.core.github_actions_oidc import GitHubOidcError, _authorize_claims
from app.infrastructure import beta_checkpoint_export
from app.infrastructure.beta_checkpoint_export import (
    _build_portable_checkpoint,
    _is_canonical_timestamp,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

GROUP_ID = "beta-group-stroi-alphav-001"
BETA_CONTRACT_FIXTURE_VERSION = "beta.context-event-conformance@1"
BETA_CONTRACT_FIXTURE_SHA256 = (
    "8ab71cecedb71822f199e39e6ee43fe7f5580cd2584f5075ae5b2c38c77c01aa"
)
BETA_BOUNDARY_CONTRACT_REVISION = "e58d25000a5151a53f6400c4c45dd9c5344e4d0d"
BETA_BOUNDARY_FIXTURE_VERSION = "beta.gate-python-to-node-boundaries@1"
GATE_SOURCE_BASE_REVISION = "46045e8f662f7fd6671a91117ee26a68fe4438ca"
GATE_PATCH_FIXTURE_VERSION = "gate.python-portable-checkpoint-boundary-builder@1"


def _event(sequence: int, *, text: str = "synthetic") -> dict[str, object]:
    return {
        "schema_version": "beta.context-event@1",
        "event_id": f"beta-event-{sequence:032x}",
        "group_id": GROUP_ID,
        "sequence": sequence,
        "kind": "message.inbound",
        "occurred_at": "2026-07-26T14:00:00.000Z",
        "recorded_at": "2026-07-26T14:00:01.000Z",
        "actor_ref": "actor-0000000000000000",
        "content": {
            "format": "structured_text",
            "text": text,
            "language": "ru",
        },
        "provenance": {
            "source": "telegram",
            "source_event_ref": f"gate-event-{sequence:064x}",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "idempotency_key": f"gate.context.{sequence:032x}",
        },
        "supersedes": None,
        "tombstones": None,
    }


def _conformance_event(
    sequence: int,
    *,
    text: str = "",
) -> dict[str, object]:
    counter = f"{sequence:032x}"
    return {
        "schema_version": "beta.context-event@1",
        "event_id": f"beta-event-{counter}",
        "group_id": "beta-group-conformance-001",
        "sequence": sequence,
        "kind": "boundary",
        "occurred_at": "2026-08-08T00:00:00.000Z",
        "recorded_at": "2026-08-08T00:00:00.000Z",
        "actor_ref": "actor-0000000000000001",
        "content": {"format": "system", "text": text, "language": None},
        "provenance": {
            "source": "beta",
            "source_event_ref": f"conformance-event-{counter}",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "idempotency_key": f"conformance.event.{sequence:016d}",
        },
        "supersedes": None,
        "tombstones": None,
    }


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


def test_exact_repository_dispatch_claims_are_accepted() -> None:
    settings = Settings(_env_file=None)
    claims = _claims(settings)
    claims["event_name"] = "repository_dispatch"
    _authorize_claims(claims, settings)


def test_malformed_checkpoint_repository_setting_fails_closed() -> None:
    settings = Settings(
        _env_file=None,
        GATE_BETA_CHECKPOINT_OIDC_REPOSITORY="AlphaCenter/beta/extra",
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


def test_manual_checkpoint_limit_matches_beta_v1_manifest() -> None:
    assert beta_checkpoint_export.MAX_CHECKPOINT_EVENTS == 50
    assert beta_checkpoint_export.MAX_EXPORT_EVENTS == 50


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0000-02-29T00:00:00.000Z", True),
        ("2026-08-08T23:59:59.999Z", True),
        ("2026-02-29T00:00:00.000Z", False),
        ("2026-08-08T24:00:00.000Z", False),
        ("٢٠٢٦-08-08T00:00:00.000Z", False),
    ],
)
def test_canonical_timestamp_matches_beta_v1(value: str, expected: bool) -> None:
    assert _is_canonical_timestamp(value) is expected


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
        ("event_name", "push"),
    ],
)
def test_oidc_claim_drift_fails_closed(claim: str, value: str) -> None:
    settings = Settings(_env_file=None)
    claims = _claims(settings)
    claims[claim] = value
    with pytest.raises(GitHubOidcError):
        _authorize_claims(claims, settings)


def test_incremental_checkpoint_range_can_start_after_one() -> None:
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    events = [_event(sequence) for sequence in (51, 52)]
    checkpoint = _build_portable_checkpoint(
        group_id=GROUP_ID,
        events=events,
        recipient_public_key=public,
        created_at="2026-07-26T14:01:00.000Z",
    )
    assert checkpoint["manifest"]["sequence_start"] == 51
    assert checkpoint["manifest"]["sequence_end"] == 52


def test_v1_ciphertext_bytes_are_unchanged_for_valid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FixedPrivateFactory:
        @staticmethod
        def generate() -> X25519PrivateKey:
            return X25519PrivateKey.from_private_bytes(bytes(range(32)))

    recipient_private = X25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    recipient_public = recipient_private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    text = "Согласовали площадку."
    event = _event(1, text=text)
    event["group_id"] = "beta-group-wedding-minsk-001"
    event["occurred_at"] = "2026-08-08T20:00:00.000Z"
    event["recorded_at"] = "2026-08-08T20:00:01.000Z"
    monkeypatch.setattr(
        beta_checkpoint_export,
        "X25519PrivateKey",
        _FixedPrivateFactory,
    )
    monkeypatch.setattr(
        beta_checkpoint_export.os,
        "urandom",
        lambda size: b"\x07" * size,
    )

    checkpoint = _build_portable_checkpoint(
        group_id="beta-group-wedding-minsk-001",
        events=[event],
        recipient_public_key=recipient_public,
        created_at="2026-08-08T20:01:00.000Z",
    )

    assert checkpoint["manifest"]["ciphertext_sha256"] == (
        "d90ab324308c44aeba9abad579bd3780c"
        "81bc5734ce546e03e9009a230c07480"
    )


@pytest.mark.parametrize(
    ("case", "expected_bytes", "expected_sha256"),
    [
        (
            "unicode-codepoints-65536",
            350_695,
            "dd80397373771ac739f1cee38e85db76ef1ea96682e946f7c455aacd153e6ed8",
        ),
        (
            "event-count-50",
            41_929,
            "628a53704582a3cbdd11dee63d45c39b86a79a39ca8f9f2c154e3c8dde284292",
        ),
    ],
)
def test_gate_reproduces_beta_boundary_fixture_bytes(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    """Pin Gate bytes consumed by the exact Beta contract revision above."""

    class _FixedPrivateFactory:
        @staticmethod
        def generate() -> X25519PrivateKey:
            return X25519PrivateKey.from_private_bytes(bytes(range(32)))

    if case == "unicode-codepoints-65536":
        events = [_conformance_event(1, text="🟦" * 65_536)]
    else:
        events = [
            _conformance_event(sequence)
            for sequence in range(1, 51)
        ]

    recipient_private = X25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    recipient_public = recipient_private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(
        beta_checkpoint_export,
        "X25519PrivateKey",
        _FixedPrivateFactory,
    )
    monkeypatch.setattr(
        beta_checkpoint_export.os,
        "urandom",
        lambda size: b"\x07" * size,
    )

    checkpoint = _build_portable_checkpoint(
        group_id="beta-group-conformance-001",
        events=events,
        recipient_public_key=recipient_public,
        created_at="2026-08-08T00:00:00.000Z",
    )

    assert BETA_CONTRACT_FIXTURE_VERSION == "beta.context-event-conformance@1"
    assert BETA_CONTRACT_FIXTURE_SHA256 == (
        "8ab71cecedb71822f199e39e6ee43fe7f5580cd2584f5075ae5b2c38c77c01aa"
    )
    assert BETA_BOUNDARY_CONTRACT_REVISION == (
        "e58d25000a5151a53f6400c4c45dd9c5344e4d0d"
    )
    assert BETA_BOUNDARY_FIXTURE_VERSION == (
        "beta.gate-python-to-node-boundaries@1"
    )
    assert GATE_SOURCE_BASE_REVISION == (
        "46045e8f662f7fd6671a91117ee26a68fe4438ca"
    )
    assert GATE_PATCH_FIXTURE_VERSION == (
        "gate.python-portable-checkpoint-boundary-builder@1"
    )
    assert len(checkpoint["container"]) == expected_bytes
    assert checkpoint["manifest"]["ciphertext_sha256"] == expected_sha256

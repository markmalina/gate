"""Synthetic contract tests for the default-off TAKE shadow adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.take_shadow_intake import (
    MAX_TELEGRAM_MEDIA_BYTES,
    IntakeState,
    MemoryTakeShadowIntakeStore,
    RehearseTakeShadowIntakeService,
    TakeShadowIntakeCommand,
    TakeShadowIntakeError,
)
from jsonschema import Draft202012Validator, FormatChecker

NOW = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
TOKEN = "synthetic-token-for-tests-only-0001"


def _update(*, media_bytes: int = 1024, kind: str = "photo") -> dict[str, object]:
    return {
        "contract": "gate.telegram-media-update.synthetic",
        "version": "1.0.0",
        "update_ref": "update_synthetic_001",
        "submission_ref": "submission_synthetic_001",
        "subject_class": "adult_confirmed",
        "media": {
            "kind": kind,
            "delivery_mode": "document" if kind == "photo" else "native",
            "file_ref": "telegram_file_synthetic_001",
            "bytes": media_bytes,
            "mime": "image/jpeg" if kind == "photo" else "video/mp4",
            "sha256": "a" * 64,
        },
        "dropbox_reference": None,
    }


def _ready_store() -> MemoryTakeShadowIntakeStore:
    store = MemoryTakeShadowIntakeStore()
    digest = store.register_token(
        intake_token=TOKEN,
        scope_ref="scope_take_capture_owner_media",
        expires_at=NOW + timedelta(hours=1),
    )
    assert digest != TOKEN
    assert TOKEN not in store.token_digests
    assert store.grant_consent(
        intake_token=TOKEN,
        evidence_ref="consent_synthetic_001",
        subject_class="adult_confirmed",
        now=NOW,
    ) is IntakeState.CONSENTED
    return store


def _command(
    update: dict[str, object] | None = None,
    *,
    enabled: bool = True,
) -> TakeShadowIntakeCommand:
    return TakeShadowIntakeCommand(
        workspace_key="workspace_synthetic_gate",
        intake_token=TOKEN,
        synthetic_update=update or _update(),
        now=NOW,
        enabled=enabled,
    )


def test_adapter_is_default_off_and_performs_no_effect() -> None:
    service = RehearseTakeShadowIntakeService(store=_ready_store())

    with pytest.raises(TakeShadowIntakeError, match="disabled"):
        service.execute(_command(enabled=False))


def test_photo_is_received_as_document_and_matches_closed_schema() -> None:
    service = RehearseTakeShadowIntakeService(store=_ready_store())

    result = service.execute(_command())

    assert result.status == "received"
    assert result.external_effects is False
    assert result.boundary_event is not None
    payload = result.boundary_event["payload"]
    assert isinstance(payload, dict)
    assert payload["source_media_kind"] == "photo"
    assert payload["normalized_media_kind"] == "document"
    assert payload["take_contract"] == "take.capture-manifest@1"
    assert payload["publication_authorized"] is False
    assert payload["personal_identifiers_included"] is False
    schema_path = (
        Path(__file__).parents[1]
        / "contracts/alphavia/v1/schemas/gate-media-received.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        result.boundary_event
    )


def test_photo_native_delivery_fails_closed() -> None:
    update = _update()
    media = update["media"]
    assert isinstance(media, dict)
    media["delivery_mode"] = "native"

    with pytest.raises(TakeShadowIntakeError, match="photo_must_be_document"):
        RehearseTakeShadowIntakeService(store=_ready_store()).execute(_command(update))


def test_large_media_returns_only_dormant_dropbox_request_contract() -> None:
    store = _ready_store()
    service = RehearseTakeShadowIntakeService(store=store)
    update = _update(media_bytes=MAX_TELEGRAM_MEDIA_BYTES + 1, kind="video")

    result = service.execute(_command(update))

    assert result.status == "awaiting_dropbox"
    assert result.boundary_event is None
    assert result.upload_request is not None
    assert result.upload_request["external_request_created"] is False
    assert result.upload_request["personal_identifiers_included"] is False
    assert store.status(intake_token=TOKEN, now=NOW) is IntakeState.CONSENTED


def test_fake_dropbox_reference_completes_large_media_once() -> None:
    store = _ready_store()
    service = RehearseTakeShadowIntakeService(store=store)
    first = _update(media_bytes=MAX_TELEGRAM_MEDIA_BYTES + 1, kind="video")
    service.execute(_command(first))
    completed = deepcopy(first)
    completed["update_ref"] = "update_synthetic_002"
    completed["dropbox_reference"] = {
        "request_ref": "upload_synthetic_001",
        "file_ref": "dropbox_file_synthetic_001",
    }

    result = service.execute(_command(completed))

    assert result.boundary_event is not None
    payload = result.boundary_event["payload"]
    assert isinstance(payload, dict)
    assert payload["transport"] == "dropbox_reference_synthetic"
    assert payload["source_file_ref"] == "dropbox_file_synthetic_001"
    assert store.status(intake_token=TOKEN, now=NOW) is IntakeState.CONSUMED


def test_exact_update_replay_is_idempotent_but_changed_payload_conflicts() -> None:
    service = RehearseTakeShadowIntakeService(store=_ready_store())
    first = service.execute(_command())
    replay = service.execute(_command())
    assert replay.boundary_event == first.boundary_event
    assert replay.idempotent_replay is True

    changed = _update()
    media = changed["media"]
    assert isinstance(media, dict)
    media["bytes"] = 2048
    with pytest.raises(TakeShadowIntakeError, match="idempotency_conflict"):
        service.execute(_command(changed))


@pytest.mark.parametrize("raw_key", ["chat_id", "user_id", "message_id", "username", "phone"])
def test_raw_telegram_identifiers_are_forbidden(raw_key: str) -> None:
    update = _update()
    update[raw_key] = 123

    with pytest.raises(TakeShadowIntakeError, match="raw_telegram_identifier"):
        RehearseTakeShadowIntakeService(store=_ready_store()).execute(_command(update))


def test_minor_stops_at_separate_guardian_gate() -> None:
    store = MemoryTakeShadowIntakeStore()
    store.register_token(
        intake_token=TOKEN,
        scope_ref="scope_take_capture_minor",
        expires_at=NOW + timedelta(hours=1),
    )
    assert store.grant_consent(
        intake_token=TOKEN,
        evidence_ref="consent_unused_minor",
        subject_class="minor",
        now=NOW,
    ) is IntakeState.GUARDIAN_REQUIRED

    with pytest.raises(TakeShadowIntakeError, match="guardian_authorization_required"):
        RehearseTakeShadowIntakeService(store=store).execute(_command())


def test_revoked_and_expired_tokens_fail_closed() -> None:
    revoked = _ready_store()
    assert revoked.revoke(intake_token=TOKEN, now=NOW) is IntakeState.REVOKED
    with pytest.raises(TakeShadowIntakeError, match="revoked"):
        RehearseTakeShadowIntakeService(store=revoked).execute(_command())

    expired = MemoryTakeShadowIntakeStore()
    expired.register_token(
        intake_token=TOKEN,
        scope_ref="scope_take_capture_expired",
        expires_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(TakeShadowIntakeError, match="expired"):
        expired.grant_consent(
            intake_token=TOKEN,
            evidence_ref="consent_synthetic_expired",
            subject_class="adult_confirmed",
            now=NOW,
        )

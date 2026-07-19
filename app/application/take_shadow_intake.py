"""Default-off, synthetic Gate transport boundary for TAKE Capture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

MAX_TELEGRAM_MEDIA_BYTES = 20 * 1024 * 1024
OPAQUE_REF_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)
FORBIDDEN_TELEGRAM_ID_KEYS = frozenset(
    {"chat_id", "user_id", "telegram_id", "message_id", "username", "phone"}
)


class TakeShadowIntakeError(RuntimeError):
    """A bounded fail-closed refusal from the synthetic intake boundary."""


class IntakeState(StrEnum):
    ISSUED = "issued"
    CONSENTED = "consented"
    GUARDIAN_REQUIRED = "guardian_required"
    REVOKED = "revoked"
    CONSUMED = "consumed"
    EXPIRED = "expired"


@dataclass(slots=True)
class _TokenRecord:
    token_digest: str
    scope_ref: str
    expires_at: datetime
    state: IntakeState = IntakeState.ISSUED
    consent_evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class TakeShadowIntakeCommand:
    workspace_key: str
    intake_token: str
    synthetic_update: dict[str, object]
    now: datetime
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class TakeShadowIntakeResult:
    status: str
    boundary_event: dict[str, object] | None
    upload_request: dict[str, object] | None
    idempotent_replay: bool
    external_effects: bool = False


@dataclass(frozen=True, slots=True)
class _ReplayRecord:
    fingerprint: str
    result: TakeShadowIntakeResult


@dataclass(frozen=True, slots=True)
class _ParsedMedia:
    kind: str
    normalized_kind: str
    file_ref: str
    bytes: int
    mime: str
    sha256: str


@dataclass(frozen=True, slots=True)
class _ParsedUpdate:
    update_ref: str
    submission_ref: str
    media: _ParsedMedia
    dropbox_reference: dict[str, str] | None


class MemoryTakeShadowIntakeStore:
    """Digest-only token and replay store for deterministic repository tests."""

    def __init__(self) -> None:
        self._tokens: dict[str, _TokenRecord] = {}
        self._replays: dict[str, _ReplayRecord] = {}

    @property
    def token_digests(self) -> frozenset[str]:
        return frozenset(self._tokens)

    def register_token(
        self,
        *,
        intake_token: str,
        scope_ref: str,
        expires_at: datetime,
    ) -> str:
        digest = _token_digest(intake_token)
        _require_opaque_ref(scope_ref, "scope_ref")
        if expires_at.tzinfo is None:
            raise TakeShadowIntakeError("take_shadow_token_expiry_invalid")
        if digest in self._tokens:
            raise TakeShadowIntakeError("take_shadow_token_duplicate")
        self._tokens[digest] = _TokenRecord(digest, scope_ref, expires_at)
        return digest

    def grant_consent(
        self,
        *,
        intake_token: str,
        evidence_ref: str,
        subject_class: str,
        now: datetime,
    ) -> IntakeState:
        record = self._active_token(intake_token, now=now)
        if record.state is not IntakeState.ISSUED:
            raise TakeShadowIntakeError("take_shadow_consent_transition_invalid")
        if subject_class == "minor":
            record.state = IntakeState.GUARDIAN_REQUIRED
            return record.state
        if subject_class != "adult_confirmed":
            raise TakeShadowIntakeError("take_shadow_adult_confirmation_required")
        record.consent_evidence_ref = _require_opaque_ref(evidence_ref, "consent_evidence_ref")
        record.state = IntakeState.CONSENTED
        return record.state

    def revoke(self, *, intake_token: str, now: datetime) -> IntakeState:
        record = self._active_token(intake_token, now=now)
        if record.state not in {IntakeState.ISSUED, IntakeState.CONSENTED}:
            raise TakeShadowIntakeError("take_shadow_revoke_transition_invalid")
        record.state = IntakeState.REVOKED
        record.consent_evidence_ref = None
        return record.state

    def status(self, *, intake_token: str, now: datetime) -> IntakeState:
        return self._token(intake_token, now=now).state

    def token_for_submission(self, *, intake_token: str, now: datetime) -> _TokenRecord:
        record = self._active_token(intake_token, now=now)
        if record.state is IntakeState.GUARDIAN_REQUIRED:
            raise TakeShadowIntakeError("take_shadow_guardian_authorization_required")
        if record.state is not IntakeState.CONSENTED:
            raise TakeShadowIntakeError("take_shadow_consent_required")
        return record

    def consume(self, *, intake_token: str, now: datetime) -> None:
        record = self.token_for_submission(intake_token=intake_token, now=now)
        record.state = IntakeState.CONSUMED

    def replay(self, update_ref: str) -> _ReplayRecord | None:
        return self._replays.get(update_ref)

    def remember(self, update_ref: str, record: _ReplayRecord) -> None:
        if update_ref in self._replays:
            raise TakeShadowIntakeError("take_shadow_replay_race")
        self._replays[update_ref] = record

    def _token(self, intake_token: str, *, now: datetime) -> _TokenRecord:
        digest = _token_digest(intake_token)
        record = self._tokens.get(digest)
        if record is None:
            raise TakeShadowIntakeError("take_shadow_token_unknown")
        if now.tzinfo is None:
            raise TakeShadowIntakeError("take_shadow_now_invalid")
        if record.state not in {
            IntakeState.CONSUMED,
            IntakeState.REVOKED,
            IntakeState.GUARDIAN_REQUIRED,
            IntakeState.EXPIRED,
        } and record.expires_at <= now:
            record.state = IntakeState.EXPIRED
            record.consent_evidence_ref = None
        return record

    def _active_token(self, intake_token: str, *, now: datetime) -> _TokenRecord:
        record = self._token(intake_token, now=now)
        if record.state is IntakeState.EXPIRED:
            raise TakeShadowIntakeError("take_shadow_token_expired")
        if record.state is IntakeState.REVOKED:
            raise TakeShadowIntakeError("take_shadow_token_revoked")
        if record.state is IntakeState.CONSUMED:
            raise TakeShadowIntakeError("take_shadow_token_consumed")
        return record


class RehearseTakeShadowIntakeService:
    """Validate synthetic media intake and emit no external effect."""

    def __init__(self, *, store: MemoryTakeShadowIntakeStore) -> None:
        self._store = store

    def execute(self, command: TakeShadowIntakeCommand) -> TakeShadowIntakeResult:
        if not command.enabled:
            raise TakeShadowIntakeError("take_shadow_intake_disabled")
        _require_opaque_ref(command.workspace_key, "workspace_key")
        if command.now.tzinfo is None:
            raise TakeShadowIntakeError("take_shadow_now_invalid")
        parsed = _parse_synthetic_update(command.synthetic_update)
        token_digest = _token_digest(command.intake_token)
        fingerprint = _digest(
            {
                "workspace_key": command.workspace_key,
                "token_digest": token_digest,
                "synthetic_update": command.synthetic_update,
            }
        )
        previous = self._store.replay(parsed.update_ref)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise TakeShadowIntakeError("take_shadow_idempotency_conflict")
            return TakeShadowIntakeResult(
                status=previous.result.status,
                boundary_event=previous.result.boundary_event,
                upload_request=previous.result.upload_request,
                idempotent_replay=True,
            )

        token = self._store.token_for_submission(
            intake_token=command.intake_token,
            now=command.now,
        )
        if parsed.media.bytes > MAX_TELEGRAM_MEDIA_BYTES and parsed.dropbox_reference is None:
            result = TakeShadowIntakeResult(
                status="awaiting_dropbox",
                boundary_event=None,
                upload_request=_upload_request(parsed, token_digest=token.token_digest),
                idempotent_replay=False,
            )
            self._store.remember(parsed.update_ref, _ReplayRecord(fingerprint, result))
            return result

        transport = "telegram_synthetic"
        source_file_ref = parsed.media.file_ref
        if parsed.dropbox_reference is not None:
            if parsed.media.bytes <= MAX_TELEGRAM_MEDIA_BYTES:
                raise TakeShadowIntakeError("take_shadow_dropbox_fallback_not_required")
            transport = "dropbox_reference_synthetic"
            source_file_ref = parsed.dropbox_reference["file_ref"]

        assert token.consent_evidence_ref is not None
        event = _media_received_event(
            command=command,
            parsed=parsed,
            token=token,
            transport=transport,
            source_file_ref=source_file_ref,
        )
        self._store.consume(intake_token=command.intake_token, now=command.now)
        result = TakeShadowIntakeResult(
            status="received",
            boundary_event=event,
            upload_request=None,
            idempotent_replay=False,
        )
        self._store.remember(parsed.update_ref, _ReplayRecord(fingerprint, result))
        return result


def _parse_synthetic_update(value: dict[str, object]) -> _ParsedUpdate:
    _reject_raw_telegram_identifiers(value)
    required = {
        "contract",
        "version",
        "update_ref",
        "submission_ref",
        "subject_class",
        "media",
        "dropbox_reference",
    }
    if set(value) != required:
        raise TakeShadowIntakeError("take_shadow_update_envelope_invalid")
    if (
        value.get("contract") != "gate.telegram-media-update.synthetic"
        or value.get("version") != "1.0.0"
    ):
        raise TakeShadowIntakeError("take_shadow_update_contract_invalid")
    if value.get("subject_class") == "minor":
        raise TakeShadowIntakeError("take_shadow_guardian_authorization_required")
    if value.get("subject_class") != "adult_confirmed":
        raise TakeShadowIntakeError("take_shadow_adult_confirmation_required")
    update_ref = _require_opaque_ref(value.get("update_ref"), "update_ref")
    submission_ref = _require_opaque_ref(value.get("submission_ref"), "submission_ref")

    media = _require_dict(value.get("media"), "media")
    if set(media) != {"kind", "delivery_mode", "file_ref", "bytes", "mime", "sha256"}:
        raise TakeShadowIntakeError("take_shadow_media_invalid")
    kind = media.get("kind")
    if kind not in {"photo", "video", "audio", "document"}:
        raise TakeShadowIntakeError("take_shadow_media_kind_invalid")
    delivery_mode = media.get("delivery_mode")
    if kind == "photo" and delivery_mode != "document":
        raise TakeShadowIntakeError("take_shadow_photo_must_be_document")
    if kind != "photo" and delivery_mode != "native":
        raise TakeShadowIntakeError("take_shadow_delivery_mode_invalid")
    media_bytes = media.get("bytes")
    if type(media_bytes) is not int or media_bytes < 1:
        raise TakeShadowIntakeError("take_shadow_media_bytes_invalid")
    mime = media.get("mime")
    if not isinstance(mime, str) or mime not in {
        "image/jpeg",
        "image/png",
        "video/mp4",
        "video/quicktime",
        "audio/mpeg",
        "audio/wav",
        "application/octet-stream",
    }:
        raise TakeShadowIntakeError("take_shadow_media_mime_invalid")
    parsed_media = _ParsedMedia(
        kind=str(kind),
        normalized_kind="document" if kind == "photo" else str(kind),
        file_ref=_require_opaque_ref(media.get("file_ref"), "media_file_ref"),
        bytes=media_bytes,
        mime=mime,
        sha256=_require_sha256(media.get("sha256"), "media_sha256"),
    )

    dropbox_reference_value = value.get("dropbox_reference")
    dropbox_reference: dict[str, str] | None = None
    if dropbox_reference_value is not None:
        dropbox = _require_dict(dropbox_reference_value, "dropbox_reference")
        if set(dropbox) != {"request_ref", "file_ref"}:
            raise TakeShadowIntakeError("take_shadow_dropbox_reference_invalid")
        dropbox_reference = {
            "request_ref": _require_opaque_ref(
                dropbox.get("request_ref"), "dropbox_request_ref"
            ),
            "file_ref": _require_opaque_ref(dropbox.get("file_ref"), "dropbox_file_ref"),
        }
    return _ParsedUpdate(update_ref, submission_ref, parsed_media, dropbox_reference)


def _upload_request(parsed: _ParsedUpdate, *, token_digest: str) -> dict[str, object]:
    request_digest = _digest(
        {
            "submission_ref": parsed.submission_ref,
            "media_sha256": parsed.media.sha256,
            "token_digest": token_digest,
        }
    )
    return {
        "contract": "gate.dropbox-upload-request",
        "version": "1.0.0",
        "request_ref": f"upload_{request_digest[:24]}",
        "submission_ref": parsed.submission_ref,
        "provider": "dropbox",
        "state": "request_required",
        "external_request_created": False,
        "personal_identifiers_included": False,
    }


def _media_received_event(
    *,
    command: TakeShadowIntakeCommand,
    parsed: _ParsedUpdate,
    token: _TokenRecord,
    transport: str,
    source_file_ref: str,
) -> dict[str, object]:
    event_digest = _digest(
        {
            "workspace_key": command.workspace_key,
            "update_ref": parsed.update_ref,
            "submission_ref": parsed.submission_ref,
            "sha256": parsed.media.sha256,
        }
    )
    assert token.consent_evidence_ref is not None
    return {
        "contract": "gate.media-received",
        "version": "1.0.0",
        "event_id": f"evt_media_{event_digest[:24]}",
        "occurred_at": command.now.isoformat().replace("+00:00", "Z"),
        "idempotency_key": f"media_{parsed.update_ref}",
        "correlation_id": parsed.submission_ref,
        "trace_id": f"trace_{event_digest[:24]}",
        "payload": {
            "submission_ref": parsed.submission_ref,
            "source_file_ref": source_file_ref,
            "take_contract": "take.capture-manifest@1",
            "transport": transport,
            "source_media_kind": parsed.media.kind,
            "normalized_media_kind": parsed.media.normalized_kind,
            "detected_mime": parsed.media.mime,
            "bytes": parsed.media.bytes,
            "sha256": parsed.media.sha256,
            "consent_scope_ref": token.scope_ref,
            "consent_evidence_ref": token.consent_evidence_ref,
            "adult_confirmed": True,
            "publication_authorized": False,
            "personal_identifiers_included": False,
        },
    }


def _token_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) < 16 or value != value.strip():
        raise TakeShadowIntakeError("take_shadow_token_invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _require_opaque_ref(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value) <= 128
        or value != value.strip()
        or any(character not in OPAQUE_REF_CHARACTERS for character in value)
    ):
        raise TakeShadowIntakeError(f"take_shadow_{field_name}_invalid")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TakeShadowIntakeError(f"take_shadow_{field_name}_invalid")
    return value


def _require_dict(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TakeShadowIntakeError(f"take_shadow_{field_name}_invalid")
    return value


def _reject_raw_telegram_identifiers(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in FORBIDDEN_TELEGRAM_ID_KEYS:
                raise TakeShadowIntakeError("take_shadow_raw_telegram_identifier_forbidden")
            _reject_raw_telegram_identifiers(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_raw_telegram_identifiers(nested)


def _digest(value: object) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(wire.encode()).hexdigest()

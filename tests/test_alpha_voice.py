"""Contract tests for Alpha Telegram voice before provider integration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.alpha_access import (
    AlphaAccessFacts,
    AlphaAccessInstallation,
    AlphaAccessReason,
    AlphaContentKind,
    AlphaEventKind,
    AlphaInvocationKind,
    AlphaMessageOrigin,
    AlphaSenderKind,
    AlphaUserConsent,
)
from app.application.alpha_canary import ALPHA_DISCLOSURE_PREFIX
from app.application.alpha_consent import ALPHA_PUBLIC_CONSENT_VERSION
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_voice import (
    EMPTY_TRANSCRIPT_CLARIFICATION,
    MAX_ALPHA_VOICE_RESPONSE_CHARS,
    MAX_TELEGRAM_VOICE_BYTES,
    MAX_TELEGRAM_VOICE_DURATION_SECONDS,
    AlphaVoiceDomainError,
    AlphaVoiceIntakeCode,
    AlphaVoiceIntakeDisposition,
    AlphaVoiceResponseKind,
    DeterministicFixtureTranscriptionPort,
    EvaluateAlphaVoiceCommand,
    TelegramVoiceMetadata,
    TranscriptionJobKey,
    TranscriptionJobState,
    TranscriptionOutcome,
    VoiceTranscriptionRequest,
    VoiceTranscriptionResult,
    build_alpha_voice_response,
    evaluate_alpha_voice,
    mark_ambiguous_provider_outcome,
    new_queued_transcription_job,
    settle_transcription_job,
    start_transcription_job,
)
from app.application.canonical_messaging import DialogKind

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _voice(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "file_id": "telegram-file-id-sensitive",
        "file_unique_id": "telegram-file-unique-sensitive",
        "duration": 45,
        "mime_type": "audio/ogg; codecs=opus",
        "file_size": 5,
    }
    payload.update(changes)
    return payload


def _installation(*, private: bool = False, **changes: object) -> AlphaAccessInstallation:
    values: dict[str, object] = {
        "installation_ref": (
            "private-installation-ref" if private else "group-installation-ref"
        ),
        "dialog_label": "Direct Alpha user" if private else "Alpha Group",
        "dialog_kind": DialogKind.PRIVATE if private else DialogKind.SUPERGROUP,
        "state": AlphaInstallationState.ACTIVE,
        "owner_approved": True,
        "required_consent_version": (
            ALPHA_PUBLIC_CONSENT_VERSION if private else None
        ),
    }
    values.update(changes)
    return AlphaAccessInstallation(**values)  # type: ignore[arg-type]


def _access(
    *,
    private: bool = False,
    installation: AlphaAccessInstallation | None = None,
    **changes: object,
) -> AlphaAccessFacts:
    selected = installation or _installation(private=private)
    values: dict[str, object] = {
        "installation": selected,
        "dialog_kind": selected.dialog_kind,
        "event_kind": AlphaEventKind.MESSAGE,
        "origin": AlphaMessageOrigin.INBOUND,
        "sender_kind": AlphaSenderKind.HUMAN,
        "content_kind": AlphaContentKind.VOICE,
        "connector_enabled": True,
        "invocation_kind": (
            AlphaInvocationKind.NONE
            if selected.dialog_kind is DialogKind.PRIVATE
            else AlphaInvocationKind.REPLY_TO_ALPHA
        ),
        "consent": (
            AlphaUserConsent(
                granted=True,
                policy_version=ALPHA_PUBLIC_CONSENT_VERSION,
            )
            if selected.dialog_kind is DialogKind.PRIVATE
            else None
        ),
    }
    values.update(changes)
    return AlphaAccessFacts(**values)  # type: ignore[arg-type]


def _command(**changes: object) -> EvaluateAlphaVoiceCommand:
    values: dict[str, object] = {
        "workspace_key": "workspace-mark",
        "canonical_message_id": "canonical-message-sensitive",
        "access": _access(),
        "media_kind": "voice",
        "voice": _voice(),
    }
    values.update(changes)
    return EvaluateAlphaVoiceCommand(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"media_kind": "audio"}, AlphaVoiceIntakeCode.MEDIA_UNSUPPORTED),
        ({"media_kind": None}, AlphaVoiceIntakeCode.METADATA_MISSING),
        ({"voice": None}, AlphaVoiceIntakeCode.METADATA_MISSING),
        ({"voice": _voice(file_size="5")}, AlphaVoiceIntakeCode.METADATA_MALFORMED),
        ({"voice": _voice(duration=True)}, AlphaVoiceIntakeCode.METADATA_MALFORMED),
        ({"voice": _voice(file_id="")}, AlphaVoiceIntakeCode.METADATA_MALFORMED),
        (
            {"voice": _voice(file_size=MAX_TELEGRAM_VOICE_BYTES + 1)},
            AlphaVoiceIntakeCode.FILE_TOO_LARGE,
        ),
        (
            {"voice": _voice(duration=MAX_TELEGRAM_VOICE_DURATION_SECONDS + 1)},
            AlphaVoiceIntakeCode.DURATION_TOO_LONG,
        ),
        ({"voice": _voice(mime_type="audio/mpeg")}, AlphaVoiceIntakeCode.MIME_UNSUPPORTED),
    ],
)
def test_unsupported_missing_oversize_and_malformed_are_deterministic_terminal(
    changes: dict[str, object], code: AlphaVoiceIntakeCode
) -> None:
    first = evaluate_alpha_voice(_command(**changes))
    second = evaluate_alpha_voice(_command(**changes))

    assert first == second
    assert first.disposition is AlphaVoiceIntakeDisposition.TERMINAL
    assert first.code is code
    assert first.terminal is True
    assert first.retryable is False
    assert first.job_key is None
    assert first.access_reason is AlphaAccessReason.ALLOWED


def test_caps_are_effectively_inclusive_at_19mb_and_600_seconds() -> None:
    decision = evaluate_alpha_voice(
        _command(
            voice=_voice(
                file_size=MAX_TELEGRAM_VOICE_BYTES,
                duration=MAX_TELEGRAM_VOICE_DURATION_SECONDS,
            )
        )
    )

    assert decision.disposition is AlphaVoiceIntakeDisposition.QUEUED
    assert decision.metadata is not None
    assert decision.metadata.file_size_bytes == 19_000_000
    assert decision.metadata.duration_seconds == 600


@pytest.mark.parametrize(
    ("mime_type", "accepted"),
    [
        (None, True),
        ("audio/ogg", True),
        (" Audio/Ogg ; codecs=opus", True),
        ("audio/opus; codec=opus", True),
        ("audio/oggish", False),
        ("audio/opus-extra", False),
        ("audio/mpeg", False),
    ],
)
def test_mime_validation_matches_the_exact_base_type_before_parameters(
    mime_type: str | None, accepted: bool
) -> None:
    decision = evaluate_alpha_voice(
        _command(voice=_voice(mime_type=mime_type))
    )

    assert (decision.code is AlphaVoiceIntakeCode.QUEUED) is accepted
    if not accepted:
        assert decision.code is AlphaVoiceIntakeCode.MIME_UNSUPPORTED


def test_optional_telegram_file_size_is_accepted_but_download_cap_still_applies() -> None:
    decision = evaluate_alpha_voice(_command(voice=_voice(file_size=None)))

    assert decision.disposition is AlphaVoiceIntakeDisposition.QUEUED
    assert decision.metadata is not None
    assert decision.metadata.file_size_bytes is None

    request = VoiceTranscriptionRequest(
        job_key=decision.job_key,  # type: ignore[arg-type]
        metadata=decision.metadata,
        audio_bytes=b"voice",
    )
    assert request.audio_bytes == b"voice"


@pytest.mark.parametrize(
    ("access", "reason"),
    [
        (
            _access(invocation_kind=AlphaInvocationKind.NONE),
            AlphaAccessReason.VOICE_REPLY_REQUIRED,
        ),
        (
            _access(sender_kind=AlphaSenderKind.BOT),
            AlphaAccessReason.SENDER_NOT_HUMAN,
        ),
        (
            replace(
                _access(),
                connector_enabled="true",  # type: ignore[arg-type]
            ),
            AlphaAccessReason.INVALID_FACTS,
        ),
        (
            _access(
                installation=_installation(owner_approved=False),
            ),
            AlphaAccessReason.OWNER_APPROVAL_REQUIRED,
        ),
        (
            _access(private=True, consent=None),
            AlphaAccessReason.CONSENT_REQUIRED,
        ),
        (
            _access(
                private=True,
                installation=_installation(
                    private=True,
                    state=AlphaInstallationState.REVOKED,
                    owner_approved=False,
                ),
                consent=None,
            ),
            AlphaAccessReason.INSTALLATION_REVOKED,
        ),
    ],
)
def test_authoritative_access_denial_is_terminal_and_happens_before_metadata_parse(
    access: AlphaAccessFacts, reason: AlphaAccessReason
) -> None:
    decision = evaluate_alpha_voice(
        _command(
            access=access,
            media_kind=object(),
            voice=object(),
        )
    )

    assert decision.code is AlphaVoiceIntakeCode.ACCESS_DENIED
    assert decision.access_reason is reason
    assert decision.terminal is True
    assert decision.retryable is False


def test_group_voice_authorization_comes_only_from_verified_reply_access_policy() -> None:
    rejected = evaluate_alpha_voice(
        _command(
            access=_access(invocation_kind=AlphaInvocationKind.MENTION),
        )
    )
    accepted = evaluate_alpha_voice(
        _command(
            access=_access(invocation_kind=AlphaInvocationKind.REPLY_TO_ALPHA),
        )
    )

    assert rejected.code is AlphaVoiceIntakeCode.ACCESS_DENIED
    assert rejected.access_reason is AlphaAccessReason.VOICE_REPLY_REQUIRED
    assert accepted.code is AlphaVoiceIntakeCode.QUEUED
    assert accepted.access_reason is AlphaAccessReason.ALLOWED


def test_private_voice_uses_shared_active_owner_approval_and_versioned_consent_policy() -> None:
    allowed = evaluate_alpha_voice(_command(access=_access(private=True)))
    stale = evaluate_alpha_voice(
        _command(
            access=_access(
                private=True,
                consent=AlphaUserConsent(
                    granted=True,
                    policy_version="older-consent-v0",
                ),
            )
        )
    )

    assert allowed.code is AlphaVoiceIntakeCode.QUEUED
    assert allowed.access_reason is AlphaAccessReason.ALLOWED
    assert stale.code is AlphaVoiceIntakeCode.ACCESS_DENIED
    assert stale.access_reason is AlphaAccessReason.CONSENT_VERSION_MISMATCH


def test_voice_requires_both_provider_route_and_voice_content_kind() -> None:
    decision = evaluate_alpha_voice(
        _command(access=_access(content_kind=AlphaContentKind.TEXT))
    )

    assert decision.code is AlphaVoiceIntakeCode.MEDIA_UNSUPPORTED
    assert decision.access_reason is AlphaAccessReason.ALLOWED


def test_authorized_private_voice_gets_exact_idempotency_key() -> None:
    decision = evaluate_alpha_voice(_command(access=_access(private=True)))

    assert decision.disposition is AlphaVoiceIntakeDisposition.QUEUED
    assert decision.job_key is not None
    assert decision.job_key.as_tuple() == (
        "workspace-mark",
        "canonical-message-sensitive",
        "transcription",
    )


def test_durable_job_has_closed_states_and_running_must_be_persisted_before_call() -> None:
    assert {state.value for state in TranscriptionJobState} == {
        "queued",
        "running",
        "completed",
        "failed",
        "unknown",
    }
    key = TranscriptionJobKey("workspace-mark", "canonical-message-sensitive")
    queued = new_queued_transcription_job(key, now=NOW)
    running = start_transcription_job(
        queued,
        now=NOW + timedelta(seconds=1),
        provider_operation_ref="provider-op-1",
    )
    completed = settle_transcription_job(
        running,
        VoiceTranscriptionResult(
            outcome=TranscriptionOutcome.COMPLETED,
            transcript="Распознанный текст",
        ),
        now=NOW + timedelta(seconds=2),
    )

    assert [queued.state, running.state, completed.state] == [
        TranscriptionJobState.QUEUED,
        TranscriptionJobState.RUNNING,
        TranscriptionJobState.COMPLETED,
    ]
    assert queued.automatic_provider_call_allowed is True
    assert running.automatic_provider_call_allowed is False
    assert completed.automatic_provider_call_allowed is False
    assert completed.attempts == 1
    with pytest.raises(AlphaVoiceDomainError, match="voice_job_not_startable"):
        start_transcription_job(
            running,
            now=NOW + timedelta(seconds=3),
            provider_operation_ref="provider-op-2",
        )


def test_ambiguous_provider_outcome_becomes_unknown_and_never_auto_retries() -> None:
    queued = new_queued_transcription_job(
        TranscriptionJobKey("workspace-mark", "canonical-message-sensitive"),
        now=NOW,
    )
    running = start_transcription_job(
        queued,
        now=NOW + timedelta(seconds=1),
        provider_operation_ref="provider-op-sensitive",
    )
    unknown = mark_ambiguous_provider_outcome(running, now=NOW + timedelta(minutes=5))

    assert unknown.state is TranscriptionJobState.UNKNOWN
    assert unknown.error_code == "alpha_voice_provider_outcome_ambiguous"
    assert unknown.automatic_provider_call_allowed is False
    with pytest.raises(AlphaVoiceDomainError, match="voice_job_not_startable"):
        start_transcription_job(
            unknown,
            now=NOW + timedelta(minutes=6),
            provider_operation_ref="second-op-forbidden",
        )


def test_job_transitions_reject_time_regression_and_provider_identity_change() -> None:
    queued = new_queued_transcription_job(
        TranscriptionJobKey("workspace-mark", "canonical-message-sensitive"),
        now=NOW,
    )
    with pytest.raises(AlphaVoiceDomainError, match="voice_job_time_regression"):
        start_transcription_job(
            queued,
            now=NOW - timedelta(seconds=1),
            provider_operation_ref="provider-op-1",
        )

    running = start_transcription_job(
        queued,
        now=NOW + timedelta(seconds=1),
        provider_operation_ref="provider-op-1",
    )
    with pytest.raises(
        AlphaVoiceDomainError,
        match="voice_provider_operation_ref_mismatch",
    ):
        settle_transcription_job(
            running,
            VoiceTranscriptionResult(
                outcome=TranscriptionOutcome.FAILED,
                error_code="provider_rejected",
                provider_operation_ref="different-provider-op",
            ),
            now=NOW + timedelta(seconds=2),
        )


def test_fixture_transcription_is_deterministic_and_has_no_network() -> None:
    metadata = TelegramVoiceMetadata(
        file_id="file-id-sensitive",
        file_unique_id="unique-id-sensitive",
        duration_seconds=1,
        file_size_bytes=5,
        mime_type="audio/ogg",
    )
    request = VoiceTranscriptionRequest(
        job_key=TranscriptionJobKey("workspace-mark", "canonical-message-sensitive"),
        metadata=metadata,
        audio_bytes=b"voice",
    )
    fixture = DeterministicFixtureTranscriptionPort("Распознанный текст")

    first = asyncio.run(fixture.transcribe(request))
    second = asyncio.run(fixture.transcribe(request))

    assert first == second
    assert first.outcome is TranscriptionOutcome.COMPLETED
    assert first.transcript == "Распознанный текст"
    assert first.provider_operation_ref is not None
    assert first.provider_operation_ref.startswith("fixture-")


def test_audio_download_is_revalidated_against_cap_and_metadata() -> None:
    metadata = TelegramVoiceMetadata(
        file_id="file-id",
        file_unique_id="unique-id",
        duration_seconds=1,
        file_size_bytes=5,
    )
    with pytest.raises(AlphaVoiceDomainError, match="voice_audio_size_mismatch"):
        VoiceTranscriptionRequest(
            job_key=TranscriptionJobKey("workspace-mark", "canonical-message"),
            metadata=metadata,
            audio_bytes=b"four",
        )
    with pytest.raises(AlphaVoiceDomainError, match="voice_audio_too_large"):
        VoiceTranscriptionRequest(
            job_key=TranscriptionJobKey("workspace-mark", "canonical-message"),
            metadata=replace(metadata, file_size_bytes=MAX_TELEGRAM_VOICE_BYTES + 1),
            audio_bytes=b"x" * (MAX_TELEGRAM_VOICE_BYTES + 1),
        )


def test_structured_voice_response_has_exact_contract_and_empty_clarification() -> None:
    response = build_alpha_voice_response(
        transcript="Пожалуйста, назови статус проекта",
        understanding="вы хотите узнать текущий статус проекта",
        answer="Проект готов к следующему этапу.",
    )
    empty = build_alpha_voice_response(
        transcript="  \n",
        understanding="must not be used",
        answer="must not be used",
    )

    assert response.kind is AlphaVoiceResponseKind.ANSWER
    assert response.render() == (
        "Я понял так: вы хотите узнать текущий статус проекта\n\n"
        "Ответ: Проект готов к следующему этапу."
    )
    assert empty.kind is AlphaVoiceResponseKind.CLARIFICATION
    assert empty.render() == EMPTY_TRANSCRIPT_CLARIFICATION


def test_raw_audio_transcripts_answers_and_provider_ids_are_hidden_from_repr() -> None:
    metadata = TelegramVoiceMetadata(
        file_id="secret-file-id",
        file_unique_id="secret-unique-id",
        duration_seconds=1,
        file_size_bytes=12,
        mime_type="audio/ogg",
    )
    request = VoiceTranscriptionRequest(
        job_key=TranscriptionJobKey("workspace-mark", "secret-canonical-message"),
        metadata=metadata,
        audio_bytes=b"secret-audio",
    )
    result = VoiceTranscriptionResult(
        outcome=TranscriptionOutcome.COMPLETED,
        transcript="secret-transcript",
        provider_operation_ref="secret-provider-operation",
    )
    response = build_alpha_voice_response(
        transcript="secret-transcript",
        understanding="secret-understanding",
        answer="secret-answer",
    )
    fixture = DeterministicFixtureTranscriptionPort("secret-fixture-transcript")
    command = _command(
        canonical_message_id="secret-canonical-message",
        voice={
            "file_id": "secret-file-id",
            "file_unique_id": "secret-unique-id",
            "duration": 1,
            "file_size": 12,
        },
    )

    combined = " ".join(
        map(repr, (metadata, request, result, response, fixture, command))
    )
    for secret in (
        "secret-file-id",
        "secret-unique-id",
        "secret-canonical-message",
        "secret-audio",
        "secret-transcript",
        "secret-provider-operation",
        "secret-understanding",
        "secret-answer",
        "secret-fixture-transcript",
    ):
        assert secret not in combined


def test_rendered_voice_response_is_bounded_to_one_telegram_send() -> None:
    prefix_length = len("Я понял так: \n\nОтвет: ")
    fitting_answer = "а" * (MAX_ALPHA_VOICE_RESPONSE_CHARS - prefix_length - 1)
    fitting = build_alpha_voice_response(
        transcript="голос",
        understanding="у",
        answer=fitting_answer,
    )

    assert len(fitting.render()) == MAX_ALPHA_VOICE_RESPONSE_CHARS
    assert len(ALPHA_DISCLOSURE_PREFIX + fitting.render()) == 4_096
    with pytest.raises(AlphaVoiceDomainError, match="voice_response_too_large"):
        build_alpha_voice_response(
            transcript="голос",
            understanding="уу",
            answer=fitting_answer,
        )

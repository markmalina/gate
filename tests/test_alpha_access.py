"""Pure policy tests for channel-neutral Alpha access."""

from dataclasses import replace

import pytest
from app.application.alpha_access import (
    MAX_ALPHA_RATE_WINDOW_SECONDS,
    MAX_ALPHA_TOKENS_PER_TURN,
    MAX_ALPHA_TURNS_PER_WINDOW,
    AlphaAccessFacts,
    AlphaAccessInstallation,
    AlphaAccessReason,
    AlphaAccessRoute,
    AlphaContentKind,
    AlphaDataScope,
    AlphaEventKind,
    AlphaInstallationQuota,
    AlphaInvocationKind,
    AlphaLifecycleAction,
    AlphaMessageOrigin,
    AlphaProfile,
    AlphaProfileScope,
    AlphaSenderKind,
    AlphaTransitionReason,
    AlphaUserConsent,
    TransitionAlphaInstallationCommand,
    evaluate_alpha_access,
    transition_alpha_installation,
)
from app.application.alpha_consent import ALPHA_PUBLIC_CONSENT_VERSION
from app.application.alpha_group import AlphaInstallationState
from app.application.canonical_messaging import DialogKind


def _group_installation(**changes: object) -> AlphaAccessInstallation:
    values: dict[str, object] = {
        "installation_ref": "installation-opaque-ref",
        "dialog_label": "Alpha Group",
        "dialog_kind": DialogKind.SUPERGROUP,
        "state": AlphaInstallationState.ACTIVE,
        "owner_approved": True,
    }
    values.update(changes)
    return AlphaAccessInstallation(**values)  # type: ignore[arg-type]


def _private_installation(**changes: object) -> AlphaAccessInstallation:
    values: dict[str, object] = {
        "installation_ref": "private-installation-ref",
        "dialog_label": "Direct Alpha user",
        "dialog_kind": DialogKind.PRIVATE,
        "state": AlphaInstallationState.ACTIVE,
        "owner_approved": True,
        "required_consent_version": ALPHA_PUBLIC_CONSENT_VERSION,
    }
    values.update(changes)
    return AlphaAccessInstallation(**values)  # type: ignore[arg-type]


def _facts(
    installation: AlphaAccessInstallation,
    **changes: object,
) -> AlphaAccessFacts:
    values: dict[str, object] = {
        "installation": installation,
        "dialog_kind": installation.dialog_kind,
        "event_kind": AlphaEventKind.MESSAGE,
        "origin": AlphaMessageOrigin.INBOUND,
        "sender_kind": AlphaSenderKind.HUMAN,
        "content_kind": AlphaContentKind.TEXT,
        "connector_enabled": True,
        "invocation_kind": AlphaInvocationKind.NONE,
        "consent": AlphaUserConsent(
            granted=True,
            policy_version=installation.required_consent_version,
        )
        if installation.dialog_kind is DialogKind.PRIVATE
        else None,
    }
    values.update(changes)
    return AlphaAccessFacts(**values)  # type: ignore[arg-type]


def test_access_defaults_fail_closed_and_inactive_states_have_exact_reasons() -> None:
    discovered = _group_installation(
        state=AlphaInstallationState.DISCOVERED,
        owner_approved=False,
    )

    connector_off = evaluate_alpha_access(
        AlphaAccessFacts(
            installation=discovered,
            dialog_kind=DialogKind.SUPERGROUP,
            event_kind=AlphaEventKind.MESSAGE,
            origin=AlphaMessageOrigin.INBOUND,
            sender_kind=AlphaSenderKind.HUMAN,
            content_kind=AlphaContentKind.TEXT,
        )
    )
    discovered_result = evaluate_alpha_access(
        _facts(
            discovered,
            invocation_kind=AlphaInvocationKind.COMMAND,
        )
    )

    assert connector_off.route is AlphaAccessRoute.BLOCKED
    assert connector_off.reason is AlphaAccessReason.CONNECTOR_DISABLED
    assert discovered_result.reason is AlphaAccessReason.INSTALLATION_DISCOVERED
    assert connector_off.provider_allowed is False


@pytest.mark.parametrize(
    "invocation",
    [
        AlphaInvocationKind.COMMAND,
        AlphaInvocationKind.MENTION,
        AlphaInvocationKind.REPLY_TO_ALPHA,
    ],
)
def test_group_accepts_only_explicit_command_mention_or_reply(
    invocation: AlphaInvocationKind,
) -> None:
    installation = _group_installation()

    allowed = evaluate_alpha_access(
        _facts(installation, invocation_kind=invocation)
    )
    uninvoked = evaluate_alpha_access(_facts(installation))

    assert allowed.route is AlphaAccessRoute.PROVIDER
    assert allowed.reason is AlphaAccessReason.ALLOWED
    assert uninvoked.route is AlphaAccessRoute.BLOCKED
    assert uninvoked.reason is AlphaAccessReason.NOT_INVOKED


def test_group_voice_is_allowed_only_as_an_explicit_reply() -> None:
    installation = _group_installation()

    allowed = evaluate_alpha_access(
        _facts(
            installation,
            content_kind=AlphaContentKind.VOICE,
            invocation_kind=AlphaInvocationKind.REPLY_TO_ALPHA,
        )
    )
    uninvoked = evaluate_alpha_access(
        _facts(installation, content_kind=AlphaContentKind.VOICE)
    )
    command = evaluate_alpha_access(
        _facts(
            installation,
            content_kind=AlphaContentKind.VOICE,
            invocation_kind=AlphaInvocationKind.COMMAND,
        )
    )
    mention = evaluate_alpha_access(
        _facts(
            installation,
            content_kind=AlphaContentKind.VOICE,
            invocation_kind=AlphaInvocationKind.MENTION,
        )
    )

    assert allowed.provider_allowed is True
    assert uninvoked.reason is AlphaAccessReason.VOICE_REPLY_REQUIRED
    assert command.reason is AlphaAccessReason.VOICE_REPLY_REQUIRED
    assert mention.reason is AlphaAccessReason.VOICE_REPLY_REQUIRED


@pytest.mark.parametrize("content_kind", [AlphaContentKind.TEXT, AlphaContentKind.VOICE])
def test_private_human_text_and_voice_require_active_owner_approval_and_exact_consent(
    content_kind: AlphaContentKind,
) -> None:
    installation = _private_installation()
    allowed = evaluate_alpha_access(
        _facts(installation, content_kind=content_kind)
    )
    unapproved = evaluate_alpha_access(
        _facts(
            replace(installation, owner_approved=False),
            content_kind=content_kind,
        )
    )
    no_consent = evaluate_alpha_access(
        _facts(installation, content_kind=content_kind, consent=None)
    )
    stale_consent = evaluate_alpha_access(
        _facts(
            installation,
            content_kind=content_kind,
            consent=AlphaUserConsent(granted=True, policy_version="older-v1"),
        )
    )

    assert allowed.provider_allowed is True
    assert unapproved.reason is AlphaAccessReason.OWNER_APPROVAL_REQUIRED
    assert no_consent.route is AlphaAccessRoute.LOCAL_ONLY
    assert no_consent.reason is AlphaAccessReason.CONSENT_REQUIRED
    assert stale_consent.reason is AlphaAccessReason.CONSENT_VERSION_MISMATCH
    assert stale_consent.provider_allowed is False


def test_start_and_discovery_are_never_provider_input() -> None:
    pending = _private_installation(
        state=AlphaInstallationState.PENDING_APPROVAL,
        owner_approved=False,
    )
    active = _private_installation()

    start_before_consent = evaluate_alpha_access(
        _facts(pending, is_start=True, consent=None)
    )
    start_after_consent = evaluate_alpha_access(_facts(active, is_start=True))
    discovery = evaluate_alpha_access(
        _facts(
            active,
            event_kind=AlphaEventKind.DISCOVERY,
            sender_kind=AlphaSenderKind.SYSTEM,
            content_kind=AlphaContentKind.NONE,
            consent=None,
        )
    )

    assert start_before_consent.route is AlphaAccessRoute.LOCAL_ONLY
    assert start_before_consent.reason is AlphaAccessReason.START_LOCAL_ONLY
    assert start_after_consent.route is AlphaAccessRoute.LOCAL_ONLY
    assert discovery.route is AlphaAccessRoute.BLOCKED
    assert discovery.reason is AlphaAccessReason.DISCOVERY_ONLY
    assert not any(
        decision.provider_allowed
        for decision in (start_before_consent, start_after_consent, discovery)
    )


def test_revoked_private_installation_never_responds_even_to_start() -> None:
    revoked = _private_installation(
        state=AlphaInstallationState.REVOKED,
        owner_approved=False,
    )

    decision = evaluate_alpha_access(_facts(revoked, is_start=True, consent=None))

    assert decision.route is AlphaAccessRoute.BLOCKED
    assert decision.reason is AlphaAccessReason.INSTALLATION_REVOKED


def test_non_humans_unsupported_content_and_proactive_origin_are_denied() -> None:
    installation = _private_installation()

    bot = evaluate_alpha_access(
        _facts(installation, sender_kind=AlphaSenderKind.BOT)
    )
    unsupported = evaluate_alpha_access(
        _facts(installation, content_kind=AlphaContentKind.UNSUPPORTED)
    )
    proactive = evaluate_alpha_access(
        _facts(installation, origin=AlphaMessageOrigin.PROACTIVE)
    )

    assert bot.reason is AlphaAccessReason.SENDER_NOT_HUMAN
    assert unsupported.reason is AlphaAccessReason.CONTENT_UNSUPPORTED
    assert proactive.reason is AlphaAccessReason.PROACTIVE_FORBIDDEN
    assert proactive.local_response_allowed is False


def test_mark_private_profile_is_owner_only_and_cannot_be_attached_to_a_group() -> None:
    scope = AlphaProfileScope(
        profile=AlphaProfile.MARK_PRIVATE,
        data_scope=AlphaDataScope.MARK_PRIVATE,
    )
    private = _private_installation(profile_scope=scope)

    external_user = evaluate_alpha_access(
        _facts(private, actor_is_owner=False)
    )
    owner = evaluate_alpha_access(_facts(private, actor_is_owner=True))

    assert external_user.reason is AlphaAccessReason.MARK_PRIVATE_REQUIRES_OWNER
    assert owner.provider_allowed is True
    assert owner.data_scope is AlphaDataScope.MARK_PRIVATE
    with pytest.raises(ValueError, match="private installation"):
        _group_installation(profile_scope=scope)
    with pytest.raises(ValueError, match="data_scope_mismatch"):
        AlphaProfileScope(
            profile=AlphaProfile.PUBLIC,
            data_scope=AlphaDataScope.MARK_PRIVATE,
        )


def test_public_profile_is_scoped_to_one_installation() -> None:
    scope = AlphaProfileScope()

    assert scope.profile is AlphaProfile.PUBLIC
    assert scope.data_scope is AlphaDataScope.INSTALLATION


@pytest.mark.parametrize(
    ("profile", "data_scope", "error"),
    [
        ("public", AlphaDataScope.INSTALLATION, "profile must be"),
        ("mark_private", AlphaDataScope.MARK_PRIVATE, "profile must be"),
        (AlphaProfile.PUBLIC, "installation", "data_scope must be"),
        (AlphaProfile.MARK_PRIVATE, object(), "data_scope must be"),
    ],
)
def test_profile_scope_rejects_untyped_nested_values(
    profile: object,
    data_scope: object,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        AlphaProfileScope(
            profile=profile,  # type: ignore[arg-type]
            data_scope=data_scope,  # type: ignore[arg-type]
        )


def test_owner_view_has_only_opaque_reference_label_policy_and_quotas() -> None:
    quota = AlphaInstallationQuota(
        max_turns_per_window=7,
        rate_window_seconds=120,
        max_tokens_per_turn=4096,
    )
    installation = _group_installation(quota=quota)
    decision = evaluate_alpha_access(
        _facts(
            installation,
            invocation_kind=AlphaInvocationKind.MENTION,
        )
    )

    assert installation.installation_ref == "installation-opaque-ref"
    assert installation.dialog_label == "Alpha Group"
    assert decision.quota == quota
    assert "provider" not in repr(installation).lower()
    assert "dialog_id" not in repr(installation)
    with pytest.raises(TypeError):
        AlphaAccessInstallation(  # type: ignore[call-arg]
            installation_ref="installation-opaque-ref",
            dialog_label="Alpha Group",
            dialog_kind=DialogKind.GROUP,
            provider_dialog_id="-1007001",
        )
    with pytest.raises(ValueError, match="opaque reference"):
        _group_installation(installation_ref="-1007001")
    with pytest.raises(ValueError, match="opaque reference"):
        _group_installation(installation_ref="7001234567890123")
    numeric_leading_uuid = _group_installation(
        installation_ref="7f739770-3a80-4ef7-a598-672540336137"
    )
    assert numeric_leading_uuid.installation_ref.startswith("7")


def test_installation_quotas_have_inclusive_upper_bounds_and_reject_bool_confusion() -> None:
    maximum = AlphaInstallationQuota(
        max_turns_per_window=MAX_ALPHA_TURNS_PER_WINDOW,
        rate_window_seconds=MAX_ALPHA_RATE_WINDOW_SECONDS,
        max_tokens_per_turn=MAX_ALPHA_TOKENS_PER_TURN,
    )

    assert maximum.max_turns_per_window == MAX_ALPHA_TURNS_PER_WINDOW
    assert maximum.rate_window_seconds == MAX_ALPHA_RATE_WINDOW_SECONDS
    assert maximum.max_tokens_per_turn == MAX_ALPHA_TOKENS_PER_TURN
    for changes in (
        {"max_turns_per_window": MAX_ALPHA_TURNS_PER_WINDOW + 1},
        {"rate_window_seconds": MAX_ALPHA_RATE_WINDOW_SECONDS + 1},
        {"max_tokens_per_turn": MAX_ALPHA_TOKENS_PER_TURN + 1},
        {"max_turns_per_window": True},
        {"rate_window_seconds": True},
        {"max_tokens_per_turn": True},
    ):
        with pytest.raises(ValueError, match="must be between"):
            AlphaInstallationQuota(**changes)  # type: ignore[arg-type]


def test_lifecycle_requires_confirmation_and_exact_expected_state() -> None:
    installation = _group_installation(
        state=AlphaInstallationState.DISCOVERED,
        owner_approved=False,
    )

    unconfirmed = transition_alpha_installation(
        installation,
        TransitionAlphaInstallationCommand(
            action=AlphaLifecycleAction.REQUEST_APPROVAL,
            expected_state=AlphaInstallationState.DISCOVERED,
        ),
    )
    stale = transition_alpha_installation(
        installation,
        TransitionAlphaInstallationCommand(
            action=AlphaLifecycleAction.REQUEST_APPROVAL,
            expected_state=AlphaInstallationState.PENDING_APPROVAL,
            confirmed=True,
        ),
    )
    invalid = transition_alpha_installation(
        installation,
        TransitionAlphaInstallationCommand(
            action=AlphaLifecycleAction.APPROVE,
            expected_state=AlphaInstallationState.DISCOVERED,
            confirmed=True,
        ),
    )

    assert unconfirmed.reason is AlphaTransitionReason.CONFIRMATION_REQUIRED
    assert stale.reason is AlphaTransitionReason.EXPECTED_STATE_MISMATCH
    assert invalid.reason is AlphaTransitionReason.INVALID_TRANSITION
    assert unconfirmed.installation is installation


def test_full_lifecycle_is_explicit_owner_approved_and_revoked_is_terminal() -> None:
    current = _group_installation(
        state=AlphaInstallationState.DISCOVERED,
        owner_approved=False,
    )

    actions = [
        (
            AlphaLifecycleAction.REQUEST_APPROVAL,
            AlphaInstallationState.DISCOVERED,
            AlphaInstallationState.PENDING_APPROVAL,
        ),
        (
            AlphaLifecycleAction.APPROVE,
            AlphaInstallationState.PENDING_APPROVAL,
            AlphaInstallationState.ACTIVE,
        ),
        (
            AlphaLifecycleAction.PAUSE,
            AlphaInstallationState.ACTIVE,
            AlphaInstallationState.PAUSED,
        ),
        (
            AlphaLifecycleAction.RESUME,
            AlphaInstallationState.PAUSED,
            AlphaInstallationState.ACTIVE,
        ),
        (
            AlphaLifecycleAction.REVOKE,
            AlphaInstallationState.ACTIVE,
            AlphaInstallationState.REVOKED,
        ),
    ]
    for action, expected, target in actions:
        result = transition_alpha_installation(
            current,
            TransitionAlphaInstallationCommand(
                action=action,
                expected_state=expected,
                confirmed=True,
            ),
        )
        assert result.applied is True
        assert result.reason is AlphaTransitionReason.APPLIED
        assert result.installation.state is target
        current = result.installation

    assert current.owner_approved is False
    terminal = transition_alpha_installation(
        current,
        TransitionAlphaInstallationCommand(
            action=AlphaLifecycleAction.REVOKE,
            expected_state=AlphaInstallationState.REVOKED,
            confirmed=True,
        ),
    )
    assert terminal.applied is False
    assert terminal.reason is AlphaTransitionReason.INVALID_TRANSITION


def test_policy_decision_is_deterministic_for_the_same_facts() -> None:
    facts = _facts(
        _group_installation(),
        invocation_kind=AlphaInvocationKind.REPLY_TO_ALPHA,
    )

    assert evaluate_alpha_access(facts) == evaluate_alpha_access(facts)


def test_runtime_type_confusion_is_denied_instead_of_becoming_an_invocation() -> None:
    installation = _group_installation()
    facts = _facts(installation)
    raw_invocation = replace(
        facts,
        invocation_kind="mention",  # type: ignore[arg-type]
    )
    raw_connector = replace(
        facts,
        connector_enabled="true",  # type: ignore[arg-type]
        invocation_kind=AlphaInvocationKind.MENTION,
    )
    raw_confirmation = TransitionAlphaInstallationCommand(
        action=AlphaLifecycleAction.PAUSE,
        expected_state=AlphaInstallationState.ACTIVE,
        confirmed="yes",  # type: ignore[arg-type]
    )

    assert evaluate_alpha_access(raw_invocation).reason is AlphaAccessReason.INVALID_FACTS
    assert evaluate_alpha_access(raw_connector).reason is AlphaAccessReason.INVALID_FACTS
    assert (
        transition_alpha_installation(installation, raw_confirmation).reason
        is AlphaTransitionReason.CONFIRMATION_REQUIRED
    )

    with pytest.raises(ValueError, match="AlphaAccessInstallation"):
        replace(facts, installation="not-an-installation")  # type: ignore[arg-type]

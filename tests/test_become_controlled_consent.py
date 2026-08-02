import asyncio
from datetime import UTC, datetime

import pytest

from app.application.become_controlled_consent import (
    APPROVED_BECOME_CONSENT_COPY,
    BECOME_CONSENT_POLICY_VERSION,
    CONSENT_INTENT,
    CONTROLLED_CONSENT_INTAKE_SCHEMA,
    QUEST_REF,
    BecomeConsentContext,
    PersistBecomeControlledConsentCommand,
    PersistedBecomeControlledConsent,
    ProcessBecomeConsentCommand,
    ProcessBecomeConsentService,
    build_consent_receipt,
    consent_correlation_ref,
    consent_evidence,
)
from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
    opaque_subject_ref,
)


class _Presence:
    def __init__(self, member_count: int | None) -> None:
        self.member_count_value = member_count
        self.calls: list[tuple[str, str]] = []

    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None:
        self.calls.append((installation_ref, provider_dialog_id))
        return self.member_count_value


class _Store:
    def __init__(self, context: BecomeConsentContext | None) -> None:
        self.context = context
        self.commands: list[PersistBecomeControlledConsentCommand] = []

    async def resolve_context(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
        subject_ref: str,
    ) -> BecomeConsentContext | None:
        assert workspace_key == "mark-personal"
        assert provider_dialog_id == "-1007002"
        if self.context is not None:
            assert subject_ref == self.context.subject_ref
        return self.context

    async def persist(
        self,
        command: PersistBecomeControlledConsentCommand,
    ) -> PersistedBecomeControlledConsent:
        self.commands.append(command)
        return PersistedBecomeControlledConsent(
            command.receipt.receipt_key,
            command.receipt.digest,
            False,
        )


def _update(text: str = "/become consent") -> dict[str, object]:
    return {
        "update_id": 43,
        "message": {
            "message_id": 8,
            "chat": {
                "id": -1007002,
                "type": "supergroup",
                "title": "BECOME owner pilot",
            },
            "from": {"id": 700000001, "is_bot": False},
            "text": text,
        },
    }


def _context() -> BecomeConsentContext:
    return BecomeConsentContext(
        installation_ref="installation-fixture-owner",
        subject_ref=opaque_subject_ref(
            provider_actor_id="700000001",
            pepper="fixture-private-pepper",
        ),
        quest_ref=QUEST_REF,
        correlation_ref=consent_correlation_ref(
            registration_receipt_digest="a" * 64,
        ),
    )


def _receipt(*, member_count: int | None = 2):
    return build_consent_receipt(
        context=_context(),
        source_receipt_id="00000000-0000-0000-0000-000000000043",
        accepted_at=datetime(2026, 8, 2, 5, 0, tzinfo=UTC),
        member_count=member_count,
    )


def test_consent_receipt_matches_relations_contract_exactly() -> None:
    receipt = _receipt()
    payload = receipt.payload()

    assert set(payload) == {
        "schema",
        "connector_key",
        "installation_ref",
        "group_state",
        "member_count",
        "quest_ref",
        "correlation_ref",
        "subject_ref",
        "action_ref",
        "accepted_at",
        "intent",
        "policy_version",
        "policy_digest",
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
    }
    assert payload["schema"] == CONTROLLED_CONSENT_INTAKE_SCHEMA
    assert payload["connector_key"] == BECOME_CONNECTOR_KEY
    assert payload["intent"] == CONSENT_INTENT
    assert payload["policy_version"] == BECOME_CONSENT_POLICY_VERSION
    assert payload["group_state"] == "owner_only"
    assert payload["member_count"] == 2
    assert payload["external_effects"] == 0
    assert payload["provider_calls"] == 0
    assert payload["messages_or_publications"] == 0
    assert payload["media_bytes"] == 0
    assert payload["real_payments"] == 0
    assert len(str(payload["policy_digest"])) == 64
    assert len(receipt.digest) == 64
    wire = str(payload)
    assert APPROVED_BECOME_CONSENT_COPY not in wire
    assert "700000001" not in wire
    assert "-1007002" not in wire


@pytest.mark.parametrize(
    "update",
    [
        _update("/become consent extra"),
        _update("/alpha consent"),
        _update("/become register"),
        {**_update(), "callback_query": {"id": "unexpected"}},
    ],
)
def test_consent_requires_exact_explicit_human_command(
    update: dict[str, object],
) -> None:
    with pytest.raises(BecomeControlledIntakeError):
        consent_evidence(
            update,
            invocation_command="become",
            bot_username="BecomeNowBot",
        )


def test_consent_fails_closed_without_registration() -> None:
    service = ProcessBecomeConsentService(
        store=_Store(None),
        presence=_Presence(2),
        subject_pepper="fixture-private-pepper",
    )
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_consent_registration_required",
    ):
        asyncio.run(
            service.execute(
                ProcessBecomeConsentCommand(
                    workspace_key="mark-personal",
                    source_receipt_id="00000000-0000-0000-0000-000000000043",
                    update=_update(),
                    received_at=datetime(2026, 8, 2, 5, 0, tzinfo=UTC),
                )
            )
        )


@pytest.mark.parametrize("member_count", [None, 0, 1, 3])
def test_consent_fails_closed_unless_group_remains_owner_only(
    member_count: int | None,
) -> None:
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_consent_owner_only_required",
    ):
        _receipt(member_count=member_count)


def test_consent_service_persists_only_content_free_receipt() -> None:
    store = _Store(_context())
    presence = _Presence(2)
    service = ProcessBecomeConsentService(
        store=store,
        presence=presence,
        subject_pepper="fixture-private-pepper",
    )

    result = asyncio.run(
        service.execute(
            ProcessBecomeConsentCommand(
                workspace_key="mark-personal",
                source_receipt_id="00000000-0000-0000-0000-000000000043",
                update=_update(),
                received_at=datetime(2026, 8, 2, 5, 0, tzinfo=UTC),
            )
        )
    )

    assert result.duplicate is False
    assert presence.calls == [
        ("installation-fixture-owner", "-1007002"),
    ]
    assert len(store.commands) == 1
    payload = store.commands[0].receipt.payload()
    assert payload["subject_ref"] == _context().subject_ref
    assert payload["quest_ref"] == QUEST_REF
    assert payload["intent"] == "consent_accepted"
    assert payload["external_effects"] == 0
    assert "700000001" not in str(payload)
    assert "-1007002" not in str(payload)

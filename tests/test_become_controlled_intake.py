import asyncio
from datetime import UTC, datetime

import pytest
from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    CONTROLLED_INTAKE_SCHEMA,
    BecomeControlledIntakeError,
    PersistBecomeControlledIntakeCommand,
    PersistedBecomeControlledIntake,
    build_registration_receipt,
    opaque_subject_ref,
    registration_evidence,
)


class _MemoryStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}

    async def persist(
        self,
        command: PersistBecomeControlledIntakeCommand,
    ) -> PersistedBecomeControlledIntake:
        key = (command.source_receipt_id, command.receipt.intent)
        payload = command.receipt.payload()
        existing = self._rows.get(key)
        if existing is not None:
            digest, stored = existing
            if digest != command.receipt.digest or stored != payload:
                raise BecomeControlledIntakeError(
                    "become_registration_mutated_replay"
                )
            return PersistedBecomeControlledIntake(
                command.receipt.receipt_key,
                command.receipt.digest,
                True,
            )
        self._rows[key] = (command.receipt.digest, payload)
        return PersistedBecomeControlledIntake(
            command.receipt.receipt_key,
            command.receipt.digest,
            False,
        )


def _update(text: str = "/become register") -> dict[str, object]:
    return {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "chat": {
                "id": -1007002,
                "type": "supergroup",
                "title": "BECOME owner pilot",
            },
            "from": {"id": 700000001, "is_bot": False},
            "text": text,
        },
    }


def _receipt(*, member_count: int | None = 2):
    return build_registration_receipt(
        installation_ref="installation-fixture-owner",
        subject_ref=opaque_subject_ref(
            provider_actor_id="700000001",
            pepper="fixture-private-pepper",
        ),
        source_receipt_id="00000000-0000-0000-0000-000000000042",
        received_at=datetime(2026, 8, 2, 3, 30, tzinfo=UTC),
        member_count=member_count,
    )


def test_registration_receipt_matches_downstream_contract_without_content() -> None:
    receipt = _receipt()
    payload = receipt.payload()

    assert payload["schema"] == CONTROLLED_INTAKE_SCHEMA
    assert payload["connector_key"] == BECOME_CONNECTOR_KEY
    assert payload["group_state"] == "owner_only"
    assert payload["member_count"] == 2
    assert payload["intent"] == "registration_confirmed"
    assert payload["external_effects"] == 0
    assert payload["provider_calls"] == 0
    assert payload["messages_or_publications"] == 0
    assert payload["media_bytes"] == 0
    assert payload["real_payments"] == 0
    assert len(receipt.digest) == 64
    assert receipt.receipt_key.startswith("become_intake_")
    wire = str(payload).casefold()
    for forbidden in (
        "message_text",
        "chat_id",
        "telegram_id",
        "username",
        "token",
        "secret",
        "transcript",
        "media",
    ):
        assert forbidden not in wire


def test_exact_registration_shape_and_subject_pseudonymization() -> None:
    evidence = registration_evidence(
        _update(),
        invocation_command="become",
        bot_username="BecomeNowBot",
    )
    assert evidence.provider_dialog_id == "-1007002"
    first = opaque_subject_ref(
        provider_actor_id=evidence.provider_actor_id,
        pepper="fixture-private-pepper",
    )
    second = opaque_subject_ref(
        provider_actor_id=evidence.provider_actor_id,
        pepper="fixture-private-pepper",
    )
    other = opaque_subject_ref(
        provider_actor_id="700000002",
        pepper="fixture-private-pepper",
    )
    assert first == second
    assert first != other
    assert "700000001" not in first


@pytest.mark.parametrize(
    "update",
    [
        _update("/become register extra"),
        _update("/alpha register"),
        {**_update(), "callback_query": {"id": "unexpected"}},
    ],
)
def test_unapproved_registration_shape_fails_closed(
    update: dict[str, object],
) -> None:
    with pytest.raises(BecomeControlledIntakeError):
        registration_evidence(
            update,
            invocation_command="become",
            bot_username="BecomeNowBot",
        )


def test_non_owner_presence_fails_before_persistence() -> None:
    for count in (None, 0, 1, 3):
        with pytest.raises(
            BecomeControlledIntakeError,
            match="become_registration_owner_only_required",
        ):
            _receipt(member_count=count)


def test_exact_replay_is_idempotent_and_mutation_is_rejected() -> None:
    store = _MemoryStore()
    receipt = _receipt()
    command = PersistBecomeControlledIntakeCommand(
        workspace_key="mark-personal",
        source_receipt_id="00000000-0000-0000-0000-000000000042",
        provider_dialog_id="-1007002",
        receipt=receipt,
    )
    first = asyncio.run(store.persist(command))
    second = asyncio.run(store.persist(command))
    assert first.duplicate is False
    assert second.duplicate is True

    mutated = build_registration_receipt(
        installation_ref=receipt.installation_ref,
        subject_ref=opaque_subject_ref(
            provider_actor_id="700000002",
            pepper="fixture-private-pepper",
        ),
        source_receipt_id=command.source_receipt_id,
        received_at=datetime(2026, 8, 2, 3, 30, tzinfo=UTC),
        member_count=2,
    )
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_registration_mutated_replay",
    ):
        asyncio.run(
            store.persist(
                PersistBecomeControlledIntakeCommand(
                    workspace_key=command.workspace_key,
                    source_receipt_id=command.source_receipt_id,
                    provider_dialog_id=command.provider_dialog_id,
                    receipt=mutated,
                )
            )
        )

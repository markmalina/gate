"""Contract tests for the persistence-independent Personal CRM foundation."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.personal_crm import (
    ChannelIdentityRecord,
    ConversationAttentionState,
    InteractionDirection,
    PersonalCrmDomainError,
    PersonalCrmExportV1,
    PersonalInteraction,
    PersonalNote,
    PersonalNoteKind,
    PersonalWorkItem,
    PersonalWorkItemStatus,
    PersonRecord,
    ResponsibleParty,
    SetConversationAttentionCommand,
    apply_attention_command,
)

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def _attention_command(**changes: object) -> SetConversationAttentionCommand:
    values: dict[str, object] = {
        "command_key": "attention-command-1",
        "workspace_key": "mark-personal",
        "person_key": "person-anna",
        "conversation_key": "conversation-telegram-1",
        "state": ConversationAttentionState.WAITING_ON_THEM,
        "expected_revision": 0,
        "actor_key": "owner-mark",
        "confirmed": True,
        "occurred_at": NOW,
        "next_review_at": NOW + timedelta(days=2),
        "note": "Жду обещанные материалы",
    }
    values.update(changes)
    return SetConversationAttentionCommand(**values)  # type: ignore[arg-type]


def test_attention_taxonomy_is_explicit_and_has_no_answered_binary() -> None:
    assert {state.value for state in ConversationAttentionState} == {
        "untriaged",
        "action_on_me",
        "waiting_on_them",
        "scheduled",
        "no_action",
        "archived",
    }
    assert "answered" not in {state.value for state in ConversationAttentionState}
    assert "unanswered" not in {state.value for state in ConversationAttentionState}


def test_only_confirmed_attention_command_changes_state_and_replays_safely() -> None:
    with pytest.raises(PersonalCrmDomainError, match="attention_confirmation_required"):
        _attention_command(confirmed=False)

    waiting = apply_attention_command(None, _attention_command())
    assert waiting.state is ConversationAttentionState.WAITING_ON_THEM
    assert waiting.revision == 1
    assert apply_attention_command(waiting, _attention_command()) is waiting

    action = _attention_command(
        command_key="attention-command-2",
        state=ConversationAttentionState.ACTION_ON_ME,
        expected_revision=1,
        occurred_at=NOW + timedelta(hours=1),
        next_review_at=None,
        note="Подготовить ответ",
    )
    updated = apply_attention_command(waiting, action)
    assert updated.state is ConversationAttentionState.ACTION_ON_ME
    assert updated.revision == 2

    with pytest.raises(PersonalCrmDomainError, match="attention_revision_conflict"):
        apply_attention_command(updated, replace(action, command_key="stale-command"))
    with pytest.raises(PersonalCrmDomainError, match="attention_scope_mismatch"):
        apply_attention_command(
            updated,
            replace(
                action,
                command_key="wrong-scope",
                expected_revision=2,
                person_key="person-boris",
            ),
        )


def test_outbound_interaction_does_not_infer_attention_or_close_waiting() -> None:
    waiting = apply_attention_command(None, _attention_command())
    outbound = PersonalInteraction(
        workspace_key=waiting.workspace_key,
        person_key=waiting.person_key,
        conversation_key=waiting.conversation_key,
        interaction_key="interaction-outbound-ok",
        channel_identity_key="telegram-anna",
        direction=InteractionDirection.OUTBOUND,
        occurred_at=NOW + timedelta(minutes=5),
        summary="Ок, хорошо",
    )

    assert outbound.direction is InteractionDirection.OUTBOUND
    assert waiting.state is ConversationAttentionState.WAITING_ON_THEM


def test_notes_and_thoughts_exist_without_reply_tasks() -> None:
    note = PersonalNote(
        note_key="note-1",
        workspace_key="mark-personal",
        person_key="person-anna",
        kind=PersonalNoteKind.THOUGHT,
        body="Спросить о новом проекте при следующем разговоре",
        created_at=NOW,
    )

    assert note.conversation_key is None
    assert "reply" not in {field_name for field_name in note.__dataclass_fields__}
    assert "Спросить" not in repr(note)


def test_work_item_requires_responsible_party_review_time_and_valid_timestamps() -> None:
    work_item = PersonalWorkItem(
        work_item_key="follow-up-1",
        workspace_key="mark-personal",
        person_key="person-anna",
        conversation_key="conversation-telegram-1",
        title="Вернуться к предложению",
        details="После того как Анна пришлёт материалы",
        responsible_party=ResponsibleParty.PERSON,
        next_review_at=NOW + timedelta(days=2),
        status=PersonalWorkItemStatus.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )

    assert work_item.responsible_party is ResponsibleParty.PERSON
    assert work_item.next_review_at > work_item.created_at

    with pytest.raises(PersonalCrmDomainError, match="next_review_at_timezone_required"):
        replace(work_item, next_review_at=datetime(2026, 7, 18, 8, 0))


def test_export_v1_is_scoped_deterministic_json_and_keeps_channel_identity_separate() -> None:
    person = PersonRecord(
        workspace_key="mark-personal",
        person_key="person-anna",
        display_name="Анна",
        created_at=NOW,
    )
    identity = ChannelIdentityRecord(
        workspace_key=person.workspace_key,
        person_key=person.person_key,
        channel_identity_key="telegram-anna",
        channel="telegram",
        connector_key="telegram-business-ru",
        external_subject_ref="provider-user-991",
        linked_at=NOW,
    )
    interaction = PersonalInteraction(
        workspace_key=person.workspace_key,
        person_key=person.person_key,
        conversation_key="conversation-telegram-1",
        interaction_key="interaction-1",
        channel_identity_key=identity.channel_identity_key,
        direction=InteractionDirection.INBOUND,
        occurred_at=NOW,
        summary="Обсуждение проекта",
    )
    attention = apply_attention_command(None, _attention_command())
    note = PersonalNote(
        note_key="note-1",
        workspace_key=person.workspace_key,
        person_key=person.person_key,
        conversation_key=None,
        kind=PersonalNoteKind.NOTE,
        body="Предпочитает голосовые сообщения",
        created_at=NOW,
    )
    work_item = PersonalWorkItem(
        work_item_key="work-1",
        workspace_key=person.workspace_key,
        person_key=person.person_key,
        conversation_key=attention.conversation_key,
        title="Проверить материалы",
        responsible_party=ResponsibleParty.WORKSPACE_OWNER,
        next_review_at=NOW + timedelta(days=2),
        status=PersonalWorkItemStatus.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )
    export = PersonalCrmExportV1(
        workspace_key=person.workspace_key,
        person=person,
        exported_at=NOW,
        channel_identities=(identity,),
        interactions=(interaction,),
        attention=(attention,),
        notes=(note,),
        work_items=(work_item,),
    )

    payload = export.to_portable_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["schema_version"] == "gate.personal_crm.export.v1"
    assert payload["exported_at"] == "2026-07-16T08:00:00Z"
    assert payload["person"] == {
        "person_key": "person-anna",
        "display_name": "Анна",
        "created_at": "2026-07-16T08:00:00Z",
    }
    assert "provider-user-991" in encoded
    assert "reply_task" not in encoded
    assert "answered" not in encoded
    assert "Анна" not in repr(export)

    with pytest.raises(PersonalCrmDomainError, match="export_person_scope_mismatch"):
        replace(export, notes=(replace(note, person_key="person-boris"),))

    with pytest.raises(PersonalCrmDomainError, match="export_channel_identity_missing"):
        replace(export, channel_identities=())


def test_scheduled_attention_requires_review_time() -> None:
    with pytest.raises(
        PersonalCrmDomainError,
        match="scheduled_attention_requires_next_review_at",
    ):
        _attention_command(
            state=ConversationAttentionState.SCHEDULED,
            next_review_at=None,
        )

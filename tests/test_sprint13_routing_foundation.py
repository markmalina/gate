# mypy: ignore-errors
"""Sprint 13 multi-account and routing foundation tests."""

from app.models.domain import (
    ALPHA_PERSONAL_PROJECT_ID,
    DEFAULT_ACCOUNT_ID,
    DEFAULT_PROJECT_ID,
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TELEGRAM_MARK_RU_ACCOUNT_ID,
    UNCLASSIFIED_PROJECT_ID,
    Account,
    Conversation,
    ConversationContextBinding,
    Message,
    MessageContextAssignment,
    Person,
    PersonIdentity,
    PersonProjectRelation,
    Project,
    ReplyDraft,
    ReplyTask,
    TelegramBusinessConnection,
)


def test_seed_identity_constants_are_stable_and_isolated() -> None:
    assert DEFAULT_ACCOUNT_ID == TELEGRAM_MARK_RU_ACCOUNT_ID
    assert TELEGRAM_MARK_RU_ACCOUNT_ID != TELEGRAM_MARK_BY_ACCOUNT_ID
    assert DEFAULT_PROJECT_ID == UNCLASSIFIED_PROJECT_ID
    assert DEFAULT_PROJECT_ID != ALPHA_PERSONAL_PROJECT_ID


def test_sprint13_tables_are_registered() -> None:
    expected = {
        Account.__tablename__,
        Project.__tablename__,
        TelegramBusinessConnection.__tablename__,
        Person.__tablename__,
        PersonIdentity.__tablename__,
        PersonProjectRelation.__tablename__,
        ConversationContextBinding.__tablename__,
        MessageContextAssignment.__tablename__,
    }

    assert expected == {
        "accounts",
        "projects",
        "telegram_business_connections",
        "people",
        "person_identities",
        "person_project_relations",
        "conversation_context_bindings",
        "message_context_assignments",
    }


def test_new_messages_and_reply_objects_require_account_and_project_context() -> None:
    for model in (Message, ReplyTask, ReplyDraft):
        assert model.__table__.c.account_id.nullable is False
        assert model.__table__.c.project_id.nullable is False
        assert model.__table__.c.account_id.server_default is not None
        assert model.__table__.c.project_id.server_default is not None


def test_conversation_and_message_have_person_and_context_links() -> None:
    for model in (Conversation, Message, ReplyTask, ReplyDraft):
        assert "person_id" in model.__table__.c
        assert "context_binding_id" in model.__table__.c


def test_message_identity_lookup_is_scoped_by_account() -> None:
    index = next(
        item
        for item in Message.__table__.indexes
        if item.name == "ix_messages_telegram_identity_lookup"
    )

    assert index.unique is False
    expression_text = " ".join(str(expression) for expression in index.expressions)
    assert "account_id" in expression_text
    assert "business_connection_id" in expression_text
    assert "chat_id" in expression_text
    assert "telegram_message_id" in expression_text

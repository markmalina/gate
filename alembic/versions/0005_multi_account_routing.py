"""Add multi-account and routing data foundation.

Revision ID: 0005_multi_account_routing
Revises: 0004_reply_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_multi_account_routing"
down_revision: str | None = "0004_reply_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_RU_ID = "0d8e025f-323d-5e86-a44c-a620f690ae4f"
ACCOUNT_BY_ID = "0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2"
ALPHA_PERSONAL_ID = "9d2010e7-9583-5aa5-b7a9-ce73ac5e22eb"
OKO_ID = "f2659ed6-9a1d-5f72-8bf1-fe45420cfef7"
ALPHA_MAYAKI_ID = "329d252f-f839-5ddd-963e-50b82469cf39"
UNCLASSIFIED_ID = "e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0"

CONTEXT_FK_NAMES = {
    "messages": "fk_messages_context_binding_id",
    "conversations": "fk_conversations_context_binding_id",
    "reply_tasks": "fk_reply_tasks_context_binding_id",
    "reply_drafts": "fk_reply_drafts_context_binding_id",
}


def _uuid_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::uuid")


def upgrade() -> None:
    _extend_accounts()
    _extend_projects()
    _seed_accounts_and_projects()
    _create_identity_tables()
    _create_business_connection_table()
    _extend_messages()
    _extend_conversations()
    _extend_reply_objects()
    _create_context_tables()


def downgrade() -> None:
    _drop_context_tables()
    _drop_reply_object_extensions()
    _drop_conversation_extensions()
    _drop_message_extensions()
    _drop_business_connection_table()
    _drop_identity_tables()
    op.drop_index("ix_projects_alias", table_name="projects")
    op.drop_column("projects", "alias")
    op.drop_index("ix_accounts_alias", table_name="accounts")
    for column in (
        "is_active",
        "send_enabled",
        "read_enabled",
        "phone_masked",
        "telegram_user_id",
        "country_code",
        "alias",
    ):
        op.drop_column("accounts", column)


def _extend_accounts() -> None:
    op.add_column("accounts", sa.Column("alias", sa.String(length=128), nullable=True))
    op.add_column("accounts", sa.Column("country_code", sa.String(length=8), nullable=True))
    op.add_column("accounts", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column("accounts", sa.Column("phone_masked", sa.String(length=64), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("read_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "accounts",
        sa.Column("send_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "accounts",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.execute(
        """
        UPDATE accounts
        SET alias = 'legacy_' || replace(id::text, '-', '')
        WHERE alias IS NULL
        """
    )
    op.alter_column("accounts", "alias", nullable=False)
    op.create_index("ix_accounts_alias", "accounts", ["alias"], unique=True)


def _extend_projects() -> None:
    op.add_column("projects", sa.Column("alias", sa.String(length=128), nullable=True))
    op.execute(
        """
        UPDATE projects
        SET alias = 'legacy_' || replace(id::text, '-', '')
        WHERE alias IS NULL
        """
    )
    op.alter_column("projects", "alias", nullable=False)
    op.create_index("ix_projects_alias", "projects", ["alias"], unique=True)


def _seed_accounts_and_projects() -> None:
    op.execute(
        f"""
        INSERT INTO projects (id, alias, name, adapter_type, memory_target, is_active)
        VALUES
          ('{ALPHA_PERSONAL_ID}'::uuid, 'alpha_personal', 'Alpha Personal', 'project', NULL, true),
          ('{OKO_ID}'::uuid, 'oko', 'Oko', 'project', NULL, true),
          ('{ALPHA_MAYAKI_ID}'::uuid, 'alpha_mayaki', 'Alpha Mayaki', 'project', NULL, true),
          ('{UNCLASSIFIED_ID}'::uuid, 'unclassified', 'Unclassified', 'project', NULL, true)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO accounts (
            id, alias, owner, country_code, telegram_user_id, phone_masked,
            account_type, linked_project_id, permissions, reply_mode,
            audit_log_enabled, read_enabled, send_enabled, is_active
        )
        VALUES
          (
            '{ACCOUNT_RU_ID}'::uuid, 'telegram_mark_ru', 'Mark', 'RU', NULL, NULL,
            'telegram_personal', NULL, '{{}}'::jsonb, 'manual', true, true, true, true
          ),
          (
            '{ACCOUNT_BY_ID}'::uuid, 'telegram_mark_by', 'Mark', 'BY', NULL, NULL,
            'telegram_personal', NULL, '{{}}'::jsonb, 'manual', true, true, true, true
          )
        ON CONFLICT DO NOTHING
        """
    )


def _create_identity_tables() -> None:
    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_people_display_name", "people", ["display_name"])
    op.create_table(
        "person_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), server_default="telegram", nullable=False),
        sa.Column("platform_user_id", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("phone_masked", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.UniqueConstraint(
            "account_id",
            "platform",
            "platform_user_id",
            name="uq_person_identities_account_platform_user",
        ),
    )
    op.create_index("ix_person_identities_person_id", "person_identities", ["person_id"])
    op.create_index("ix_person_identities_account_id", "person_identities", ["account_id"])
    op.create_table(
        "person_project_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index(
        "ix_person_project_relations_person_id", "person_project_relations", ["person_id"]
    )
    op.create_index(
        "ix_person_project_relations_project_id", "person_project_relations", ["project_id"]
    )
    op.create_index(
        "ix_person_project_relations_active",
        "person_project_relations",
        ["person_id", "project_id", "status"],
    )


def _create_business_connection_table() -> None:
    op.create_table(
        "telegram_business_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_connection_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("user_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "rights", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_update_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.UniqueConstraint(
            "business_connection_id",
            name="uq_telegram_business_connections_business_connection_id",
        ),
    )
    op.create_index(
        "ix_telegram_business_connections_account_id",
        "telegram_business_connections",
        ["account_id"],
    )
    op.create_index(
        "ix_telegram_business_connections_business_connection_id",
        "telegram_business_connections",
        ["business_connection_id"],
    )
    op.create_index(
        "uq_telegram_business_connections_enabled_account",
        "telegram_business_connections",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_enabled"),
    )


def _extend_messages() -> None:
    op.add_column("messages", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_messages_person_id_people", "messages", "people", ["person_id"], ["id"]
    )
    op.create_index("ix_messages_person_id", "messages", ["person_id"])
    op.execute(f"UPDATE messages SET account_id = '{ACCOUNT_RU_ID}'::uuid WHERE account_id IS NULL")
    op.execute(
        f"UPDATE messages SET project_id = '{UNCLASSIFIED_ID}'::uuid WHERE project_id IS NULL"
    )
    op.alter_column(
        "messages",
        "account_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=_uuid_default(ACCOUNT_RU_ID),
    )
    op.alter_column(
        "messages",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=_uuid_default(UNCLASSIFIED_ID),
    )
    op.create_index("ix_messages_account_id", "messages", ["account_id"])
    op.create_index("ix_messages_project_id", "messages", ["project_id"])
    op.create_index(
        "uq_messages_telegram_identity",
        "messages",
        [
            "account_id",
            sa.text("coalesce(business_connection_id, '')"),
            "chat_id",
            "telegram_message_id",
        ],
        unique=True,
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )


def _extend_conversations() -> None:
    op.add_column("conversations", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_conversations_person_id_people",
        "conversations",
        "people",
        ["person_id"],
        ["id"],
    )
    op.create_index("ix_conversations_person_id", "conversations", ["person_id"])
    op.create_index("ix_conversations_account_id", "conversations", ["account_id"])
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    op.execute(
        f"UPDATE conversations SET account_id = '{ACCOUNT_RU_ID}'::uuid WHERE account_id IS NULL"
    )
    op.execute(
        f"""
        UPDATE conversations
        SET project_id = '{UNCLASSIFIED_ID}'::uuid
        WHERE project_id IS NULL
        """
    )


def _extend_reply_objects() -> None:
    for table_name in ("reply_tasks", "reply_drafts"):
        op.add_column(
            table_name,
            sa.Column(
                "account_id",
                postgresql.UUID(as_uuid=True),
                server_default=_uuid_default(ACCOUNT_RU_ID),
                nullable=True,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                server_default=_uuid_default(UNCLASSIFIED_ID),
                nullable=True,
            ),
        )
        op.add_column(table_name, sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table_name}_account_id_accounts",
            table_name,
            "accounts",
            ["account_id"],
            ["id"],
        )
        op.create_foreign_key(
            f"fk_{table_name}_project_id_projects",
            table_name,
            "projects",
            ["project_id"],
            ["id"],
        )
        op.create_foreign_key(
            f"fk_{table_name}_person_id_people",
            table_name,
            "people",
            ["person_id"],
            ["id"],
        )
        op.create_index(f"ix_{table_name}_account_id", table_name, ["account_id"])
        op.create_index(f"ix_{table_name}_project_id", table_name, ["project_id"])
        op.create_index(f"ix_{table_name}_person_id", table_name, ["person_id"])
    op.execute(
        f"""
        UPDATE reply_tasks AS task
        SET account_id = COALESCE(conversation.account_id, '{ACCOUNT_RU_ID}'::uuid),
            project_id = COALESCE(conversation.project_id, '{UNCLASSIFIED_ID}'::uuid)
        FROM conversations AS conversation
        WHERE conversation.id = task.conversation_id
        """
    )
    op.execute(
        f"""
        UPDATE reply_drafts AS draft
        SET account_id = COALESCE(conversation.account_id, '{ACCOUNT_RU_ID}'::uuid),
            project_id = COALESCE(conversation.project_id, '{UNCLASSIFIED_ID}'::uuid)
        FROM conversations AS conversation
        WHERE conversation.id = draft.conversation_id
        """
    )
    for table_name in ("reply_tasks", "reply_drafts"):
        op.alter_column(
            table_name,
            "account_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=_uuid_default(ACCOUNT_RU_ID),
        )
        op.alter_column(
            table_name,
            "project_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=_uuid_default(UNCLASSIFIED_ID),
        )


def _create_context_tables() -> None:
    op.create_table(
        "conversation_context_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_type", sa.String(length=64), server_default="conversation", nullable=False),
        sa.Column("effective_from_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
        sa.Column("assignment_source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["effective_from_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["effective_to_message_id"], ["messages.id"]),
    )
    op.create_index(
        "ix_conversation_context_bindings_conversation_id",
        "conversation_context_bindings",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_context_bindings_project_id",
        "conversation_context_bindings",
        ["project_id"],
    )
    op.create_index(
        "ix_conversation_context_bindings_active",
        "conversation_context_bindings",
        ["conversation_id", "status", "created_at"],
    )
    for table_name in CONTEXT_FK_NAMES:
        op.add_column(
            table_name,
            sa.Column("context_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            CONTEXT_FK_NAMES[table_name],
            table_name,
            "conversation_context_bindings",
            ["context_binding_id"],
            ["id"],
        )
        op.create_index(f"ix_{table_name}_context_binding_id", table_name, ["context_binding_id"])
    op.create_table(
        "message_context_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
        sa.Column("assignment_source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["context_binding_id"], ["conversation_context_bindings.id"]),
    )
    op.create_index(
        "ix_message_context_assignments_message_id",
        "message_context_assignments",
        ["message_id"],
    )
    op.create_index(
        "ix_message_context_assignments_project_id",
        "message_context_assignments",
        ["project_id"],
    )
    op.create_index(
        "ix_message_context_assignments_message_created",
        "message_context_assignments",
        ["message_id", "created_at"],
    )


def _drop_context_tables() -> None:
    op.drop_index(
        "ix_message_context_assignments_message_created",
        table_name="message_context_assignments",
    )
    op.drop_index(
        "ix_message_context_assignments_project_id",
        table_name="message_context_assignments",
    )
    op.drop_index(
        "ix_message_context_assignments_message_id",
        table_name="message_context_assignments",
    )
    op.drop_table("message_context_assignments")
    for table_name in ("reply_drafts", "reply_tasks", "conversations", "messages"):
        op.drop_index(f"ix_{table_name}_context_binding_id", table_name=table_name)
        op.drop_constraint(CONTEXT_FK_NAMES[table_name], table_name, type_="foreignkey")
        op.drop_column(table_name, "context_binding_id")
    op.drop_index(
        "ix_conversation_context_bindings_active",
        table_name="conversation_context_bindings",
    )
    op.drop_index(
        "ix_conversation_context_bindings_project_id",
        table_name="conversation_context_bindings",
    )
    op.drop_index(
        "ix_conversation_context_bindings_conversation_id",
        table_name="conversation_context_bindings",
    )
    op.drop_table("conversation_context_bindings")


def _drop_reply_object_extensions() -> None:
    for table_name in ("reply_drafts", "reply_tasks"):
        op.drop_index(f"ix_{table_name}_person_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_project_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_account_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_person_id_people", table_name, type_="foreignkey")
        op.drop_constraint(f"fk_{table_name}_project_id_projects", table_name, type_="foreignkey")
        op.drop_constraint(f"fk_{table_name}_account_id_accounts", table_name, type_="foreignkey")
        op.drop_column(table_name, "person_id")
        op.drop_column(table_name, "project_id")
        op.drop_column(table_name, "account_id")


def _drop_conversation_extensions() -> None:
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_index("ix_conversations_account_id", table_name="conversations")
    op.drop_index("ix_conversations_person_id", table_name="conversations")
    op.drop_constraint(
        "fk_conversations_person_id_people", "conversations", type_="foreignkey"
    )
    op.drop_column("conversations", "person_id")


def _drop_message_extensions() -> None:
    op.drop_index("uq_messages_telegram_identity", table_name="messages")
    op.drop_index("ix_messages_project_id", table_name="messages")
    op.drop_index("ix_messages_account_id", table_name="messages")
    op.alter_column(
        "messages",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "messages",
        "account_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        server_default=None,
    )
    op.drop_index("ix_messages_person_id", table_name="messages")
    op.drop_constraint("fk_messages_person_id_people", "messages", type_="foreignkey")
    op.drop_column("messages", "person_id")


def _drop_business_connection_table() -> None:
    op.drop_index(
        "uq_telegram_business_connections_enabled_account",
        table_name="telegram_business_connections",
    )
    op.drop_index(
        "ix_telegram_business_connections_business_connection_id",
        table_name="telegram_business_connections",
    )
    op.drop_index(
        "ix_telegram_business_connections_account_id",
        table_name="telegram_business_connections",
    )
    op.drop_table("telegram_business_connections")


def _drop_identity_tables() -> None:
    op.drop_index(
        "ix_person_project_relations_active", table_name="person_project_relations"
    )
    op.drop_index(
        "ix_person_project_relations_project_id", table_name="person_project_relations"
    )
    op.drop_index(
        "ix_person_project_relations_person_id", table_name="person_project_relations"
    )
    op.drop_table("person_project_relations")
    op.drop_index("ix_person_identities_account_id", table_name="person_identities")
    op.drop_index("ix_person_identities_person_id", table_name="person_identities")
    op.drop_table("person_identities")
    op.drop_index("ix_people_display_name", table_name="people")
    op.drop_table("people")

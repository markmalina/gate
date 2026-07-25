"""PostgreSQL projection from canonical Business text to the current human inbox."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.telegram_business_text_inbox import (
    TelegramBusinessTextInboxCommand,
    TelegramBusinessTextInboxProjector,
    TelegramBusinessTextInboxResult,
)
from app.models.domain import (
    Account,
    Bot,
    Conversation,
    ConversationStatus,
    Message,
    Person,
    PersonIdentity,
    Project,
    ReplyTask,
    ReplyTaskStatus,
)


class TelegramBusinessTextInboxConflictError(RuntimeError):
    """Reject reuse of one canonical identity with incompatible inbox facts."""


class SqlTelegramBusinessTextInboxProjector(TelegramBusinessTextInboxProjector):
    """Write one idempotent compatibility read model inside the caller transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project(
        self,
        command: TelegramBusinessTextInboxCommand,
    ) -> TelegramBusinessTextInboxResult:
        canonical_message_id = uuid.UUID(command.canonical_message_id)
        existing = await self._session.scalar(
            select(Message).where(Message.canonical_message_id == canonical_message_id)
        )
        if existing is not None:
            return await self._existing_result(existing, command=command)

        account_id = uuid.UUID(command.account_id)
        project_id = uuid.UUID(command.project_id)
        await self._require_scope(account_id=account_id, project_id=project_id)
        bot = await self._bot(command.bot_username)
        person_id, title = await self._counterparty(
            command,
            account_id=account_id,
        )
        conversation = await self._conversation(
            command,
            bot_id=bot.id,
            account_id=account_id,
            project_id=project_id,
            person_id=person_id,
            title=title,
        )
        direction = command.direction.value
        message = Message(
            bot_id=bot.id,
            account_id=account_id,
            project_id=project_id,
            person_id=person_id,
            canonical_message_id=canonical_message_id,
            direction=direction,
            source="telegram",
            event_type=command.event_type,
            chat_id=command.provider_dialog_id,
            sender_id=command.provider_sender_id,
            recipient_id=(
                command.provider_dialog_id
                if command.direction is CanonicalMessageDirection.OUTBOUND
                else None
            ),
            text=command.text_content,
            telegram_update_id=command.update_id,
            telegram_message_id=int(command.provider_message_id),
            business_connection_id=command.business_connection_id,
            payload={
                "canonical_source": True,
                "_gate_counterparty": {"display_name": title},
            },
            received_at=(
                command.occurred_at
                if command.direction is CanonicalMessageDirection.INBOUND
                else None
            ),
            sent_at=(
                command.occurred_at
                if command.direction is CanonicalMessageDirection.OUTBOUND
                else None
            ),
        )
        self._session.add(message)
        await self._session.flush()

        reply_task_created = False
        if command.route is WorkRoute.HUMAN_REPLY_TASK:
            conversation.status = ConversationStatus.OPEN.value
            conversation.requires_reply = True
            conversation.last_inbound_at = command.occurred_at
            self._session.add(
                ReplyTask(
                    account_id=account_id,
                    project_id=project_id,
                    person_id=person_id,
                    conversation_id=conversation.id,
                    trigger_message_id=message.id,
                    status=ReplyTaskStatus.NEEDS_REPLY.value,
                    reason="inbound_human_text",
                )
            )
            reply_task_created = True
        elif command.direction is CanonicalMessageDirection.OUTBOUND:
            conversation.status = ConversationStatus.REPLIED.value
            conversation.requires_reply = False
            conversation.last_outbound_at = command.occurred_at
            await self._satisfy_reply_tasks(conversation_id=conversation.id)
        conversation.last_message_at = command.occurred_at
        await self._session.flush()
        return TelegramBusinessTextInboxResult(
            duplicate=False,
            message_created=True,
            reply_task_created=reply_task_created,
        )

    async def _existing_result(
        self,
        message: Message,
        *,
        command: TelegramBusinessTextInboxCommand,
    ) -> TelegramBusinessTextInboxResult:
        expected = (
            uuid.UUID(command.account_id),
            uuid.UUID(command.project_id),
            command.direction.value,
            command.provider_dialog_id,
            command.provider_sender_id,
            command.text_content,
            command.update_id,
            int(command.provider_message_id),
            command.business_connection_id,
        )
        actual = (
            message.account_id,
            message.project_id,
            message.direction,
            message.chat_id,
            message.sender_id,
            message.text,
            message.telegram_update_id,
            message.telegram_message_id,
            message.business_connection_id,
        )
        if actual != expected:
            raise TelegramBusinessTextInboxConflictError(
                "telegram_business_text_inbox_identity_conflict"
            )
        reply_task_count = int(
            (
                await self._session.scalar(
                    select(ReplyTask.id).where(ReplyTask.trigger_message_id == message.id)
                )
            )
            is not None
        )
        expected_reply_task_count = int(command.route is WorkRoute.HUMAN_REPLY_TASK)
        reply_task_created = False
        if reply_task_count != expected_reply_task_count:
            if reply_task_count == 0 and expected_reply_task_count == 1:
                await self._promote_to_human_reply(message, command=command)
                reply_task_created = True
            else:
                raise TelegramBusinessTextInboxConflictError(
                    "telegram_business_text_inbox_reply_task_conflict"
                )
        return TelegramBusinessTextInboxResult(
            duplicate=True,
            message_created=False,
            reply_task_created=reply_task_created,
        )

    async def _promote_to_human_reply(
        self,
        message: Message,
        *,
        command: TelegramBusinessTextInboxCommand,
    ) -> None:
        conversation = await self._session.scalar(
            select(Conversation).where(
                Conversation.bot_id == message.bot_id,
                Conversation.account_id == message.account_id,
                Conversation.project_id == message.project_id,
                Conversation.chat_id == command.provider_dialog_id,
                Conversation.business_connection_id == command.business_connection_id,
            )
        )
        if conversation is None:
            raise TelegramBusinessTextInboxConflictError(
                "telegram_business_text_inbox_conversation_missing"
            )
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
        conversation.last_inbound_at = command.occurred_at
        if (
            conversation.last_message_at is None
            or conversation.last_message_at < command.occurred_at
        ):
            conversation.last_message_at = command.occurred_at
        self._session.add(
            ReplyTask(
                account_id=message.account_id,
                project_id=message.project_id,
                person_id=message.person_id,
                conversation_id=conversation.id,
                trigger_message_id=message.id,
                status=ReplyTaskStatus.NEEDS_REPLY.value,
                reason="inbound_human_text",
            )
        )
        await self._session.flush()

    async def _require_scope(
        self,
        *,
        account_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        account = await self._session.scalar(
            select(Account.id).where(
                Account.id == account_id,
                Account.is_active.is_(True),
            )
        )
        project = await self._session.scalar(
            select(Project.id).where(
                Project.id == project_id,
                Project.is_active.is_(True),
            )
        )
        if account is None or project is None:
            raise TelegramBusinessTextInboxConflictError(
                "telegram_business_text_inbox_scope_invalid"
            )

    async def _bot(self, username: str) -> Bot:
        bot = await self._session.scalar(select(Bot).where(Bot.username == username))
        if bot is not None:
            return bot
        bot = Bot(
            username=username,
            token_secret_ref="runtime_env",
            is_active=True,
        )
        self._session.add(bot)
        await self._session.flush()
        return bot

    async def _counterparty(
        self,
        command: TelegramBusinessTextInboxCommand,
        *,
        account_id: uuid.UUID,
    ) -> tuple[uuid.UUID, str]:
        result = await self._session.execute(
            select(PersonIdentity, Person)
            .join(Person, PersonIdentity.person_id == Person.id)
            .where(
                PersonIdentity.account_id == account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == command.counterparty_user_id,
            )
        )
        row = result.one_or_none()
        profile_name = command.counterparty_display_name
        username = command.counterparty_username
        if row is None:
            fallback_name = (
                profile_name
                or (f"@{username}" if username else None)
                or command.counterparty_user_id
            )
            person = Person(display_name=fallback_name)
            self._session.add(person)
            await self._session.flush()
            self._session.add(
                PersonIdentity(
                    person_id=person.id,
                    account_id=account_id,
                    platform="telegram",
                    platform_user_id=command.counterparty_user_id,
                    username=username,
                    display_name=profile_name,
                    is_verified=True,
                    metadata_={
                        "name_source": "telegram_profile",
                        "telegram_profile_name": profile_name,
                    },
                )
            )
            return person.id, fallback_name

        identity, person = row
        metadata = dict(identity.metadata_ or {})
        saved_name = _optional_text(metadata.get("telegram_contact_name"))
        manual_name = _optional_text(metadata.get("manual_name"))
        identity.username = username
        identity.display_name = profile_name
        metadata["telegram_profile_name"] = profile_name
        if saved_name is not None:
            metadata["name_source"] = "telegram_contact"
            person.display_name = saved_name
        elif manual_name is not None:
            metadata["name_source"] = "manual"
            person.display_name = manual_name
        elif profile_name is not None:
            metadata["name_source"] = "telegram_profile"
            person.display_name = profile_name
        identity.metadata_ = metadata
        return person.id, person.display_name

    async def _conversation(
        self,
        command: TelegramBusinessTextInboxCommand,
        *,
        bot_id: uuid.UUID,
        account_id: uuid.UUID,
        project_id: uuid.UUID,
        person_id: uuid.UUID,
        title: str,
    ) -> Conversation:
        conversation = await self._session.scalar(
            select(Conversation).where(
                Conversation.bot_id == bot_id,
                Conversation.account_id == account_id,
                Conversation.project_id == project_id,
                Conversation.chat_id == command.provider_dialog_id,
                Conversation.business_connection_id == command.business_connection_id,
            )
        )
        if conversation is not None:
            conversation.person_id = person_id
            conversation.title = title
            return conversation
        inbound = command.direction is CanonicalMessageDirection.INBOUND
        conversation = Conversation(
            bot_id=bot_id,
            account_id=account_id,
            project_id=project_id,
            person_id=person_id,
            chat_id=command.provider_dialog_id,
            business_connection_id=command.business_connection_id,
            title=title,
            status=(ConversationStatus.OPEN.value if inbound else ConversationStatus.REPLIED.value),
            requires_reply=False,
            last_message_at=command.occurred_at,
            last_inbound_at=command.occurred_at if inbound else None,
            last_outbound_at=command.occurred_at if not inbound else None,
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def _satisfy_reply_tasks(self, *, conversation_id: uuid.UUID) -> None:
        tasks = (
            await self._session.execute(
                select(ReplyTask).where(
                    ReplyTask.conversation_id == conversation_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                )
            )
        ).scalars()
        for task in tasks:
            task.status = ReplyTaskStatus.REPLIED.value


class SessionFactoryTelegramBusinessTextInboxProjector(TelegramBusinessTextInboxProjector):
    """Own one short atomic inbox projection transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def project(
        self,
        command: TelegramBusinessTextInboxCommand,
    ) -> TelegramBusinessTextInboxResult:
        async with self._sessions.begin() as session:
            return await SqlTelegramBusinessTextInboxProjector(session).project(command)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

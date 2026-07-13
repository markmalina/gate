"""Read-only identity resolution for canonical Telegram ingress shadow parity."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TelegramIngressIdentitySnapshot:
    """Account and owner identities loaded without workflow mutations."""

    account_id: str
    account_alias: str
    project_id: str
    owner_identity_ids: frozenset[str]


@dataclass(frozen=True)
class ResolveTelegramIngressIdentityCommand:
    """Inputs required to determine direction and reply-task intent."""

    business_connection_id: str
    sender_id: str
    text_present: bool


@dataclass(frozen=True)
class TelegramIngressIdentityResolution:
    """Redacted identity parity result for shadow comparison."""

    resolved: bool
    account_id: str | None
    account_alias: str | None
    project_id: str | None
    direction: str
    reply_task_intent: str
    resolution: str


class TelegramIngressIdentityReader(Protocol):
    """Read identity snapshots without commits, flushes or writes."""

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        """Return the enabled connection snapshot, if known."""


class ResolveTelegramIngressIdentityService:
    """Resolve ingress direction through an injected read-only snapshot boundary."""

    def __init__(self, reader: TelegramIngressIdentityReader) -> None:
        self._reader = reader

    async def execute(
        self,
        command: ResolveTelegramIngressIdentityCommand,
    ) -> TelegramIngressIdentityResolution:
        snapshot = await self._reader.read(
            business_connection_id=command.business_connection_id,
        )
        if snapshot is None:
            return TelegramIngressIdentityResolution(
                resolved=False,
                account_id=None,
                account_alias=None,
                project_id=None,
                direction="unresolved",
                reply_task_intent="unresolved",
                resolution="connection_not_found",
            )

        is_outbound = command.sender_id in snapshot.owner_identity_ids
        if is_outbound:
            direction = "outbound"
            reply_task_intent = "satisfy_existing"
        else:
            direction = "inbound"
            reply_task_intent = (
                "create_inbound_human_text" if command.text_present else "none"
            )
        return TelegramIngressIdentityResolution(
            resolved=True,
            account_id=snapshot.account_id,
            account_alias=snapshot.account_alias,
            project_id=snapshot.project_id,
            direction=direction,
            reply_task_intent=reply_task_intent,
            resolution="database_snapshot",
        )

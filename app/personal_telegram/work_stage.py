"""Composition and privileged setup of the isolated S1 command journal."""

from __future__ import annotations

import asyncio
import base64
import hmac
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import AwareDatetime, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.database import database_runtime_lifespan
from app.models.domain import TELEGRAM_MARK_RU_ACCOUNT_ID
from app.personal_telegram.command_scope import (
    CommandDelegation,
    CommandRuntimeError,
    PeerIdentity,
    RuCommandScope,
    oauth_client_namespace,
    personal_client_namespace,
    utc_now,
)
from app.personal_telegram.command_service import PersonalCommandService
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.grant_registry import SqlGrantRegistry

FIXTURE_PEER = PeerIdentity("self", 1)


class StageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATE_PERSONAL_STAGE_", hide_input_in_errors=True)

    environment: Literal["staging", "test"] = Field(validation_alias="GATE_ENV")
    database_url: SecretStr
    master_key: SecretStr
    resource: str
    issuer: str
    jwks_url: str
    client_id: str
    workspace: str
    owner: str
    binding_id: UUID
    grant_id: UUID
    grant_expires_at: AwareDatetime
    approval_ref: str
    file_host: str = ""
    workspace_claim: str = "gate_workspace_id"
    account_ids_claim: str = "gate_account_ids"
    project_ids_claim: str = "gate_project_ids"

    @classmethod
    def from_environment(cls) -> StageSettings:
        # BaseSettings resolves all required fields from the isolated environment.
        return cls()  # type: ignore[call-arg]

    @model_validator(mode="after")
    def validate_stage(self) -> StageSettings:
        if not self.database_url.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("A separate PostgreSQL stage DSN is required")
        for url in (self.resource, self.issuer, self.jwks_url):
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Configure explicit HTTPS staging OAuth endpoints")
        if (
            urlsplit(self.resource).path != "/mcp"
            or urlsplit(self.resource).hostname == "gate-production-bab1.up.railway.app"
        ):
            raise ValueError("Stage resource must have path /mcp")
        if self.file_host and not (
            self.file_host == self.file_host.lower()
            and all(part and part.replace("-", "").isalnum() for part in self.file_host.split("."))
            and self.file_host.endswith((".oaiusercontent.com", ".blob.core.windows.net"))
        ):
            raise ValueError("Expected one exact Work file host")
        Fernet(self.master_key.get_secret_value().encode())
        self.scope().validate()
        oauth_client_namespace(self.issuer, self.client_id)
        return self

    def scope(self) -> RuCommandScope:
        return RuCommandScope(
            self.workspace, TELEGRAM_MARK_RU_ACCOUNT_ID, self.binding_id, self.owner
        )

    def key(self, purpose: str) -> bytes:
        master = base64.urlsafe_b64decode(self.master_key.get_secret_value())
        return base64.urlsafe_b64encode(
            hmac.digest(master, ("gate.s1.v1/" + purpose).encode(), "sha256")
        )

    def oauth_settings(self) -> Settings:
        return Settings.model_validate(
            {
                "GATE_MCP_ENABLED": True,
                "GATE_PERSONAL_COMMAND_TOOLS_ENABLED": True,
                "MCP_AUTH_MODE": "oauth",
                "MCP_AUTHORIZATION_SERVER": self.issuer,
                "MCP_PUBLIC_RESOURCE_URL": self.resource,
                "MCP_OAUTH_JWKS_URL": self.jwks_url,
                "MCP_OAUTH_WORKSPACE_CLAIM": self.workspace_claim,
                "MCP_OAUTH_ACCOUNT_IDS_CLAIM": self.account_ids_claim,
                "MCP_OAUTH_PROJECT_IDS_CLAIM": self.project_ids_claim,
            }
        )


class StageJournal(PersonalCommandService):
    def __init__(self, sessions: async_sessionmaker[AsyncSession], config: StageSettings) -> None:
        scope = config.scope()
        grants = SqlGrantRegistry(
            sessions, scope=scope, policy_key=config.key("grant"), key_version="s1-v1"
        )
        store = SqlCommandStore(
            sessions,
            scope=scope,
            payload_cipher=CommandPayloadCipher(config.key("payload")),
            grants=grants,
        )
        super().__init__(store, grants, peer_reference_key=config.key("peer"))
        self.grant = CommandDelegation(
            config.grant_id,
            scope,
            oauth_client_namespace(config.issuer, config.client_id),
            frozenset({"send_text"}),
            frozenset({FIXTURE_PEER.key}),
            config.grant_expires_at,
        )

    def for_client(self, principal: Principal) -> StageJournal:
        if personal_client_namespace(principal) != self.grant.client_namespace:
            raise CommandRuntimeError("client_scope_denied")
        return self

    def require_file_access(self, principal: Principal) -> None:
        self._principal(principal, "gate:personal:jobs:read")

    async def readiness(self, *, principal: Principal) -> dict[str, Any]:
        result = await super().readiness(principal=principal)
        await self.check_binding()
        result["journal_ready"] = True
        if "gate:personal:send_text" in principal.scopes:
            try:
                peer = await self.issue_peer_reference(
                    principal=principal, delegation=self.grant, peer=FIXTURE_PEER, now=utc_now()
                )
            except CommandRuntimeError as exc:
                if str(exc) not in {"delegation_inactive", "delegation_not_found"}:
                    raise
                result["blockers"].append(str(exc))
            else:
                result["test_target"] = {
                    "grant_ref": "grant_" + self.grant.delegation_id.hex,
                    "peer_ref": peer,
                }
        return result

    async def check_binding(self) -> None:
        async with self.store.sessions() as session:
            await self.require_synthetic_binding(session)

    async def require_synthetic_binding(self, session: AsyncSession) -> None:
        row = await session.get(RuntimeBindingRow, self.scope.binding_id)
        if not (
            row
            and row.workspace == self.scope.workspace
            and row.owner_subject == self.scope.owner_subject
            and row.account_id == str(self.scope.account_id)
            and row.custody_state == "synthetic"
            and row.ciphertext == b""
            and row.lease_owner is None
        ):
            raise CommandRuntimeError("synthetic_binding_required")


async def provision(config: StageSettings) -> None:
    """Idempotent operator action. Web requests cannot create bindings or grants."""
    async with database_runtime_lifespan(config.database_url.get_secret_value()) as db:
        journal = StageJournal(db.session_factory, config)
        async with db.session_factory.begin() as session:
            await session.execute(
                insert(RuntimeBindingRow)
                .values(
                    id=config.binding_id,
                    workspace=config.workspace,
                    account_id=str(journal.scope.account_id),
                    owner_subject=config.owner,
                    session_generation=1,
                    ciphertext=b"",
                    revision=0,
                    key_version="synthetic",
                    custody_state="synthetic",
                    lease_generation=0,
                    health_state="disconnected",
                )
                .on_conflict_do_nothing()
            )
            await journal.require_synthetic_binding(session)
        await journal.grants.install_approved_grant(
            journal.grant, approval_ref=config.approval_ref, now=utc_now()
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["provision"])
    parser.parse_args()
    asyncio.run(provision(StageSettings.from_environment()))

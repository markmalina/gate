"""One long-lived, command-only RU connection; not wired to web startup.

This first activation boundary has no Telegram mutation or history dispatcher.
The V2 all-writer guard and fresh delegation registry must precede those paths.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.personal_telegram.command_scope import CommandRuntimeError, utc_now
from app.personal_telegram.command_store import LeaseToken, SqlCommandStore
from app.personal_telegram.session_persistence import SessionCipher, open_encrypted_session


class RuntimeClient(Protocol):
    async def connect(self) -> None: ...
    async def is_user_authorized(self) -> bool: ...
    async def owner_id(self) -> int: ...
    async def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...


@dataclass(frozen=True, slots=True, repr=False)
class TelegramApplication:
    api_id: int
    api_hash: str

    def validate(self) -> None:
        if (
            type(self.api_id) is not int
            or self.api_id <= 0
            or not re.fullmatch(r"[0-9a-fA-F]{32}", self.api_hash)
        ):
            raise CommandRuntimeError("telegram_application_unavailable")


class TelethonRuntimeClient:
    """Small private client port: no raw RPC, login, send or update listeners."""

    def __init__(self, session: Any, application: TelegramApplication) -> None:
        application.validate()
        from telethon import TelegramClient  # type: ignore[import-untyped]

        self._client = TelegramClient(
            session,
            application.api_id,
            application.api_hash,
            receive_updates=False,
            catch_up=False,
            auto_reconnect=False,
            request_retries=0,
            connection_retries=1,
            flood_sleep_threshold=0,
            timeout=5,
        )

    async def connect(self) -> None:
        await self._client.connect()

    async def is_user_authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def owner_id(self) -> int:
        owner = await self._client.get_me()
        if owner is None or not isinstance(owner.id, int):
            raise CommandRuntimeError("telegram_owner_unavailable")
        return int(owner.id)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    def is_connected(self) -> bool:
        return bool(self._client.is_connected())


@dataclass(frozen=True, slots=True)
class WorkerExit:
    state: str
    code: str | None = None


class RuCloudWorker:
    def __init__(
        self,
        *,
        store: SqlCommandStore,
        cipher: SessionCipher,
        session_generation: int,
        expected_provider_user_id: int,
        client_factory: Callable[[Any], RuntimeClient],
        enabled: bool = False,
        lease_ttl: timedelta = timedelta(seconds=30),
        heartbeat_seconds: float = 5,
        startup_timeout: float = 10,
        disconnect_timeout: float = 10,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        store.scope.validate()
        if (
            type(expected_provider_user_id) is not int
            or expected_provider_user_id <= 0
            or not 5 <= lease_ttl.total_seconds() <= 300
            or not 0 < heartbeat_seconds <= lease_ttl.total_seconds() / 3
            or not 0 < startup_timeout < lease_ttl.total_seconds() / 2
            or disconnect_timeout <= 0
        ):
            raise CommandRuntimeError("worker_configuration_invalid")
        self.store = store
        self.cipher = cipher
        self.session_generation = session_generation
        self.expected_owner = expected_provider_user_id
        self.client_factory = client_factory
        self.enabled = enabled
        self.ttl = lease_ttl
        self.heartbeat = heartbeat_seconds
        self.startup_timeout = startup_timeout
        self.disconnect_timeout = disconnect_timeout
        self.clock = clock

    async def run_until(self, stop: asyncio.Event) -> WorkerExit:
        if not self.enabled:
            return WorkerExit("disabled")
        token = await self.store.acquire_owner(now=self.clock(), ttl=self.ttl)
        client: RuntimeClient | None = None
        result = WorkerExit("disconnected")
        try:

            async def startup() -> None:
                nonlocal client
                session = await open_encrypted_session(
                    scope=self.store.scope,
                    generation=self.session_generation,
                    cipher=self.cipher,
                    repository=self.store,
                )
                await self.store.renew_owner(
                    token,
                    now=self.clock(),
                    ttl=self.ttl,
                    health="connecting",
                )
                client = self.client_factory(session)
                await self._connect_verified(client)
                await self.store.renew_owner(token, now=self.clock(), ttl=self.ttl, health="ready")

            await asyncio.wait_for(startup(), self.startup_timeout)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), self.heartbeat)
                except TimeoutError:
                    if client is None or not client.is_connected():
                        raise CommandRuntimeError("telegram_connection_lost") from None
                    await self.store.renew_owner(
                        token,
                        now=self.clock(),
                        ttl=self.ttl,
                        health="ready",
                    )
        except CommandRuntimeError as exc:
            result = WorkerExit("failed", str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            result = WorkerExit("failed", "telegram_runtime_failed")
        finally:
            # Even an expired lease cannot authorize replacement while this
            # process may still have a live TCP connection. Failed cleanup keeps
            # ownership latched for a separate reviewed termination operation.
            cleanup = await self._disconnect_and_release(client, token)
            if not cleanup:
                result = WorkerExit("blocked", "session_disconnect_unconfirmed")
        return result

    async def _connect_verified(self, client: RuntimeClient) -> None:
        await client.connect()
        if not await client.is_user_authorized():
            raise CommandRuntimeError("ru_session_login_required")
        if await client.owner_id() != self.expected_owner:
            raise CommandRuntimeError("ru_session_owner_mismatch")

    async def _disconnect_and_release(
        self,
        client: RuntimeClient | None,
        token: LeaseToken,
    ) -> bool:
        try:
            if client is not None:
                await asyncio.wait_for(client.disconnect(), self.disconnect_timeout)
                if client.is_connected():
                    return False
            await asyncio.wait_for(
                self.store.release_disconnected_owner(token),
                self.disconnect_timeout,
            )
            return True
        except Exception:
            return False

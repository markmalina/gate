"""Minimal official Meta Instagram API adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.instagram.commands import (
    InstagramCommandType,
    InstagramProviderCommand,
    ProviderFollowState,
)
from app.instagram.config import InstagramAccountConfig, InstagramRuntimeConfig
from app.instagram.contracts import PublishedReel, PublishState, ReelInsights


class MetaInstagramError(RuntimeError):
    """Provider failure with a safe public message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MetaInstagramClient:
    def __init__(self, *, config: InstagramRuntimeConfig, http: httpx.AsyncClient) -> None:
        self.config = config
        self.http = http

    def _url(self, path: str) -> str:
        return f"{self.config.graph_base_url}/{self.config.graph_version}/{path.lstrip('/')}"

    async def publish_reel(
        self, *, account: InstagramAccountConfig, video_url: str, caption: str
    ) -> PublishedReel:
        created = await self._post(
            account,
            account.instagram_user_id + "/media",
            {
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
            },
        )
        container_id = str(created.get("id", ""))
        if not container_id:
            raise MetaInstagramError(
                "provider_contract", "Meta did not return a media container id"
            )
        await self._wait_until_ready(account=account, container_id=container_id)
        published = await self._post(
            account,
            account.instagram_user_id + "/media_publish",
            {"creation_id": container_id},
        )
        media_id = str(published.get("id", ""))
        if not media_id:
            raise MetaInstagramError(
                "unknown", "Meta publish outcome is unknown; do not retry automatically"
            )
        permalink = await self._permalink(account=account, media_id=media_id)
        return PublishedReel(media_id=media_id, permalink=permalink, state=PublishState.PUBLISHED)

    async def insights(self, *, account: InstagramAccountConfig, media_id: str) -> ReelInsights:
        data = await self._get(
            account,
            media_id + "/insights",
            {"metric": "views,reach,likes,comments,saved,shares,total_interactions"},
        )
        metrics: dict[str, int | float | None] = {}
        for item in data.get("data", []):
            name = item.get("name")
            values = item.get("values") or []
            if isinstance(name, str) and values and isinstance(values[0], dict):
                value = values[0].get("value")
                metrics[name] = value if isinstance(value, (int, float)) else None
        return ReelInsights(media_id=media_id, captured_at=datetime.now(UTC), metrics=metrics)

    async def reply_to_comment(
        self,
        *,
        account: InstagramAccountConfig,
        comment_id: str,
        text: str,
    ) -> str:
        """Publish one public reply to an Instagram comment."""
        result = await self._request(
            "POST",
            account,
            comment_id + "/replies",
            json={"message": text},
        )
        reply_id = result.get("id")
        if not isinstance(reply_id, str) or not reply_id:
            raise MetaInstagramError("provider_contract", "Meta did not return the public reply id")
        return reply_id

    async def private_reply_to_comment(
        self,
        *,
        account: InstagramAccountConfig,
        comment_id: str,
        text: str,
        quick_reply: dict[str, str] | None = None,
    ) -> str:
        """Send the single private-reply opening message permitted for a comment."""
        message: dict[str, Any] = {"text": text}
        if quick_reply is not None:
            message["quick_replies"] = [
                {
                    "content_type": "text",
                    "title": quick_reply["title"],
                    "payload": quick_reply["payload"],
                }
            ]
        result = await self._request(
            "POST",
            account,
            account.instagram_user_id + "/messages",
            json={
                "recipient": {"comment_id": comment_id},
                "message": message,
            },
        )
        recipient_id = result.get("recipient_id")
        if not isinstance(recipient_id, str) or not recipient_id:
            raise MetaInstagramError(
                "provider_contract",
                "Meta did not return the private-reply recipient id",
            )
        return recipient_id

    async def send_message(
        self,
        *,
        account: InstagramAccountConfig,
        instagram_scoped_user_id: str,
        text: str,
        quick_reply: dict[str, str] | None = None,
    ) -> str:
        """Send a message inside an already-open Instagram conversation window."""
        message: dict[str, Any] = {"text": text}
        if quick_reply is not None:
            message["quick_replies"] = [
                {
                    "content_type": "text",
                    "title": quick_reply["title"],
                    "payload": quick_reply["payload"],
                }
            ]
        result = await self._request(
            "POST",
            account,
            account.instagram_user_id + "/messages",
            json={
                "recipient": {"id": instagram_scoped_user_id},
                "message": message,
            },
        )
        message_id = result.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise MetaInstagramError("provider_contract", "Meta did not return the message id")
        return message_id

    async def follow_state(
        self,
        *,
        account: InstagramAccountConfig,
        instagram_scoped_user_id: str,
    ) -> ProviderFollowState:
        """Read follow state after the Instagram user has granted profile consent."""
        try:
            result = await self._get(
                account,
                instagram_scoped_user_id,
                {"fields": "is_user_follow_business"},
            )
        except MetaInstagramError as exc:
            if exc.code == "unknown":
                return ProviderFollowState.UNKNOWN
            return ProviderFollowState.NOT_PERMITTED
        value = result.get("is_user_follow_business")
        if value is True:
            return ProviderFollowState.FOLLOWING
        if value is False:
            return ProviderFollowState.NOT_FOLLOWING
        return ProviderFollowState.UNKNOWN

    async def _wait_until_ready(
        self, *, account: InstagramAccountConfig, container_id: str
    ) -> None:
        for _ in range(20):
            result = await self._get(account, container_id, {"fields": "status_code"})
            state = result.get("status_code")
            if state == "FINISHED":
                return
            if state in {"ERROR", "EXPIRED"}:
                raise MetaInstagramError(
                    "processing_failed", f"Meta media processing ended with {state}"
                )
            await asyncio.sleep(3)
        raise MetaInstagramError(
            "processing_timeout", "Meta media processing did not finish in time"
        )

    async def _permalink(self, *, account: InstagramAccountConfig, media_id: str) -> str | None:
        result = await self._get(account, media_id, {"fields": "permalink"})
        value = result.get("permalink")
        return value if isinstance(value, str) else None

    async def _post(
        self, account: InstagramAccountConfig, path: str, data: dict[str, str]
    ) -> dict[str, Any]:
        return await self._request("POST", account, path, data=data)

    async def _get(
        self, account: InstagramAccountConfig, path: str, params: dict[str, str]
    ) -> dict[str, Any]:
        return await self._request("GET", account, path, params=params)

    async def _request(
        self, method: str, account: InstagramAccountConfig, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            response = await self.http.request(
                method,
                self._url(path),
                headers={"Authorization": f"Bearer {account.access_token}"},
                timeout=30.0,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MetaInstagramError("provider_contract", "Meta returned an invalid response")
            return payload
        except MetaInstagramError:
            raise
        except httpx.TimeoutException as exc:
            raise MetaInstagramError(
                "unknown", "Meta request timed out; do not retry automatically"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MetaInstagramError(
                "provider_error", "Meta rejected the Instagram request"
            ) from exc


class MetaInstagramCommandExecutor:
    """Execute validated MAYAKI commands with process-local replay protection."""

    def __init__(self, *, client: MetaInstagramClient) -> None:
        self.client = client
        self._results: dict[str, dict[str, Any]] = {}

    async def execute(self, value: Any) -> dict[str, Any]:
        command = InstagramProviderCommand.from_mapping(value)
        previous = self._results.get(command.idempotency_key)
        if previous is not None:
            return {**previous, "idempotent_replay": True}
        account = self.client.config.account(command.account_alias)
        if account is None:
            raise MetaInstagramError(
                "account_not_found", "Instagram account alias is not configured"
            )
        result: dict[str, Any] = {
            "status": "delivered",
            "command_type": command.command_type.value,
            "idempotency_key": command.idempotency_key,
            "idempotent_replay": False,
            "external_delivery": True,
        }
        if command.command_type is InstagramCommandType.PUBLIC_REPLY:
            result["provider_reply_id"] = await self.client.reply_to_comment(
                account=account,
                comment_id=str(command.provider_comment_id),
                text=str(command.text),
            )
        elif command.command_type is InstagramCommandType.PRIVATE_REPLY:
            result["instagram_user_id"] = await self.client.private_reply_to_comment(
                account=account,
                comment_id=str(command.provider_comment_id),
                text=str(command.text),
                quick_reply=command.quick_reply,
            )
        elif command.command_type is InstagramCommandType.FOLLOW_CHECK:
            state = await self.client.follow_state(
                account=account,
                instagram_scoped_user_id=str(command.instagram_user_id),
            )
            result["follow_state"] = state.value
            result["external_delivery"] = False
            result["status"] = "observed"
        else:
            result["provider_message_id"] = await self.client.send_message(
                account=account,
                instagram_scoped_user_id=str(command.instagram_user_id),
                text=str(command.text),
                quick_reply=command.quick_reply,
            )
        self._results[command.idempotency_key] = result
        return result

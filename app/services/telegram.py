"""Telegram Bot API client for safe outbound messages."""

import hashlib
import hmac
import os
import time
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import PurePosixPath
from typing import Any, cast

import httpx

from app.services.process_send_fence import process_send_fence
from app.services.telegram_delivery_gate import (
    canary_intent,
    delivery_gate,
    registered_gate,
    require_reserved_start_window,
)


def build_telegram_http_client() -> httpx.AsyncClient:
    """Build the bounded process-owned HTTP transport for Telegram API calls."""

    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=10.0,
            write=5.0,
            pool=2.0,
        ),
        limits=httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=30.0,
        ),
        follow_redirects=False,
        trust_env=False,
    )


class TelegramApiError(RuntimeError):
    """Sanitized Telegram Bot API failure safe to persist and expose."""

    def __init__(
        self,
        *,
        error_code: int | None,
        description: str,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(description)
        self.error_code = error_code
        self.description = description
        self.http_status = http_status
        self.retry_after_seconds = (
            retry_after_seconds
            if type(retry_after_seconds) is int and retry_after_seconds >= 0
            else None
        )


class TelegramClient:
    """Small async Telegram Bot API client."""

    def __init__(
        self,
        credential: str,
        api_base_url: str = "https://api.telegram.org",
        *,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._credential = credential
        self._http_client = http_client
        self._api_base_url = api_base_url.rstrip("/")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send a Telegram text message and return the Telegram API response."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id
        if reply_to_message_id is not None:
            if type(reply_to_message_id) is not int or reply_to_message_id < 1:
                raise ValueError("Telegram reply message id is invalid")
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        elif _looks_like_html_message(text):
            payload["parse_mode"] = "HTML"

        effective_reply_markup = (
            reply_markup if reply_markup is not None else _build_internal_reply_markup(text)
        )
        if effective_reply_markup is not None:
            payload["reply_markup"] = effective_reply_markup

        url = f"{self._api_base_url}/bot{self._credential}/sendMessage"
        return await self._post_telegram_json(url=url, payload=payload)

    async def send_plain_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send literal text without parse mode or Telegram reply markup."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id
        if reply_to_message_id is not None:
            if type(reply_to_message_id) is not int or reply_to_message_id < 1:
                raise ValueError("Telegram reply message id is invalid")
            payload["reply_parameters"] = {"message_id": reply_to_message_id}

        url = f"{self._api_base_url}/bot{self._credential}/sendMessage"
        return await self._post_telegram_json(url=url, payload=payload)

    async def send_business_document(
        self,
        *,
        chat_id: str,
        business_connection_id: str,
        filename: str,
        media_type: str,
        data: bytes,
        caption: str = "",
    ) -> dict[str, Any]:
        """Upload one literal PDF or ZIP as a Business document.

        The caller owns recipient authorization, durable reservation and replay.
        This transport never retries an ambiguous provider outcome.
        """

        if not self._credential or not chat_id or not business_connection_id:
            raise ValueError("Telegram Business destination is required")
        if (
            not filename
            or filename in {".", ".."}
            or PurePosixPath(filename).name != filename
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
            or len(filename.encode("utf-8")) > 255
        ):
            raise ValueError("Invalid document filename")
        allowed = {
            ".pdf": "application/pdf",
            ".zip": "application/zip",
        }
        if allowed.get(PurePosixPath(filename).suffix.lower()) != media_type:
            raise ValueError("Document extension and MIME type do not match")
        if not isinstance(data, bytes) or not 0 < len(data) <= 8 * 1024 * 1024:
            raise ValueError("Document must contain at most 8 MiB")
        if not isinstance(caption, str) or len(caption) > 1024:
            raise ValueError("Document caption is too long")

        url = f"{self._api_base_url}/bot{self._credential}/sendDocument"
        return await self._post_telegram_multipart(
            url=url,
            payload={
                "chat_id": chat_id,
                "business_connection_id": business_connection_id,
                "caption": caption,
            },
            file=("document", (filename, data, media_type)),
        )

    async def _post_telegram_multipart(
        self,
        *,
        url: str,
        payload: dict[str, str],
        file: tuple[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        await process_send_fence().require_write_admission(self._credential.split(":", 1)[0])
        # Multipart has no scoped canary grant. Keep this guard at the shared
        # HTTP boundary so delegated document paths cannot bypass admission.
        from app.core.config import get_settings

        if getattr(get_settings(), "gate_reply_sequence_canary_enabled", False):
            raise TelegramApiError(error_code=403, description="Canary method blocked")
        owner = registered_gate(self._credential.split(":", 1)[0])
        guard = getattr(owner, "guard_send", None)
        if guard is not None:
            await guard(url.rsplit("/", 1)[-1], canary_intent())
        gate = delivery_gate(self._credential)
        if gate is not None:
            admitted = await gate.before(payload)
            if time.monotonic() - admitted > 0.1:
                raise TelegramApiError(
                    error_code=429,
                    description="Transport start window expired",
                    retry_after_seconds=1,
                )
        require_reserved_start_window(self._credential)
        with process_send_fence().admitted():
            response = await self._http_client.post(
                url,
                data=payload,
                files={file[0]: file[1]},
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=2.0),
            )
        try:
            return self._parse_telegram_response(response)
        except TelegramApiError as exc:
            if gate is not None and exc.error_code == 429:
                await gate.failed(payload, exc.retry_after_seconds)
            raise

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        business_connection_id: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit a Telegram text message and return the Telegram API response."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        elif _looks_like_html_message(text):
            payload["parse_mode"] = "HTML"

        effective_reply_markup = (
            reply_markup if reply_markup is not None else _build_internal_reply_markup(text)
        )
        if effective_reply_markup is not None:
            payload["reply_markup"] = effective_reply_markup

        url = f"{self._api_base_url}/bot{self._credential}/editMessageText"
        return await self._post_telegram_json(url=url, payload=payload)

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: str,
        message_id: int,
        business_connection_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace or clear a Telegram message's inline keyboard."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        effective_reply_markup = (
            reply_markup if reply_markup is not None else {"inline_keyboard": []}
        )
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": effective_reply_markup,
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id

        url = f"{self._api_base_url}/bot{self._credential}/editMessageReplyMarkup"
        return await self._post_telegram_json(url=url, payload=payload)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        """Acknowledge a Telegram inline-keyboard callback query."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            payload["text"] = text

        url = f"{self._api_base_url}/bot{self._credential}/answerCallbackQuery"
        return await self._post_telegram_json(url=url, payload=payload)

    async def get_chat_member(
        self,
        *,
        chat_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Return one current chat member for callback-owner verification."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")
        if not chat_id or not user_id:
            raise RuntimeError("Telegram chat and user ids are required")

        url = f"{self._api_base_url}/bot{self._credential}/getChatMember"
        return await self._post_telegram_json(
            url=url,
            payload={"chat_id": chat_id, "user_id": user_id},
        )

    async def get_chat_administrators(
        self,
        *,
        chat_id: str,
    ) -> dict[str, Any]:
        """Return current group administrators for bounded owner resolution."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")
        if not chat_id:
            raise RuntimeError("Telegram chat id is required")

        url = f"{self._api_base_url}/bot{self._credential}/getChatAdministrators"
        return await self._post_telegram_json(
            url=url,
            payload={"chat_id": chat_id},
        )

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, Any]:
        """Return Telegram's current view of one Business connection."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")

        url = f"{self._api_base_url}/bot{self._credential}/getBusinessConnection"
        return await self._post_telegram_json(
            url=url,
            payload={"business_connection_id": business_connection_id},
        )

    async def _post_telegram_json(self, *, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        method = url.rsplit("/", 1)[-1]
        sending = method == "sendMessage"
        writing = method not in {"getBusinessConnection", "getChatMember", "getChatAdministrators"}
        if writing:
            await process_send_fence().require_write_admission(self._credential.split(":", 1)[0])
        if writing:
            owner = registered_gate(self._credential.split(":", 1)[0])
            guard = getattr(owner, "guard_send", None)
            if guard is not None:
                await guard(method, canary_intent())
        if method.startswith("send") and not sending:
            from app.core.config import get_settings

            if getattr(get_settings(), "gate_reply_sequence_canary_enabled", False):
                raise TelegramApiError(error_code=403, description="Canary method blocked")
        gate = delivery_gate(self._credential) if sending else None
        admitted: float | None = None
        if gate is not None:
            admitted = await gate.before(payload)
            if time.monotonic() - admitted > 0.1:
                raise TelegramApiError(
                    error_code=429,
                    description="Transport start window expired",
                    retry_after_seconds=1,
                )
        if sending:
            from app.core.config import get_settings

            intent = canary_intent()
            if getattr(get_settings(), "gate_reply_sequence_canary_enabled", False) or (
                intent is not None and intent.execution_id is not None
            ):
                owner = registered_gate(self._credential.split(":", 1)[0])
                if intent is None or owner is None:
                    raise TelegramApiError(error_code=403, description="Canary admission closed")
                await owner.reserve_canary(intent, payload)
        if admitted is not None and time.monotonic() - admitted > 0.1:
            raise TelegramApiError(
                error_code=429,
                description="Transport start window expired",
                retry_after_seconds=1,
            )
        require_reserved_start_window(self._credential)
        with process_send_fence().admitted() if writing else nullcontext():
            response = await self._http_client.post(url, json=payload)
        try:
            return self._parse_telegram_response(response)
        except TelegramApiError as exc:
            if gate is not None and exc.error_code == 429:
                await gate.failed(payload, exc.retry_after_seconds)
            raise

    @staticmethod
    def _parse_telegram_response(response: httpx.Response) -> dict[str, Any]:
        try:
            result = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError("Telegram API returned invalid JSON") from None

        if not isinstance(result, dict):
            raise RuntimeError("Telegram API returned a non-object response")

        if response.is_error or result.get("ok") is not True:
            raw_code = result.get("error_code")
            error_code = raw_code if isinstance(raw_code, int) else None
            raw_description = result.get("description")
            description = (
                raw_description.strip()
                if isinstance(raw_description, str) and raw_description.strip()
                else "Telegram API did not confirm delivery"
            )
            raise TelegramApiError(
                error_code=error_code,
                description=description[:512],
                http_status=response.status_code,
                retry_after_seconds=_telegram_retry_after(result.get("parameters")),
            )

        return cast(dict[str, Any], result)


def _telegram_retry_after(parameters: object) -> int | None:
    """Read only a non-negative integer; never coerce or shorten provider cooldowns."""

    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    return value if type(value) is int and value >= 0 else None


def _looks_like_html_message(text: str) -> bool:
    """Return true for internal messages that intentionally use Telegram HTML."""

    return "<code>" in text or "<pre>" in text


def _build_internal_reply_markup(
    text: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Build inline actions for Gate's internal draft messages."""

    draft_ref = _extract_line_value(text=text, prefix="Draft created:")
    if draft_ref:
        runtime_environ = os.environ if environ is None else environ
        buttons: list[dict[str, str]] = []
        edit_url = _build_draft_edit_url(draft_ref=draft_ref, environ=runtime_environ)
        if edit_url is not None:
            buttons.append({"text": "✏️ Редактировать", "url": edit_url})
        buttons.append(
            {
                "text": "✅ Отправить",
                "callback_data": f"gate:send:{draft_ref}",
            }
        )
        return {"inline_keyboard": [buttons]}

    edit_url = _extract_https_line(text)
    if text.startswith("Edit draft:") and edit_url:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✏️ Открыть редактор",
                        "url": edit_url,
                    }
                ]
            ]
        }

    return None


def _build_draft_edit_url(
    *,
    draft_ref: str,
    environ: Mapping[str, str],
) -> str | None:
    raw_base_url = (
        environ.get("PUBLIC_BASE_URL")
        or environ.get("RAILWAY_PUBLIC_DOMAIN")
        or environ.get("RAILWAY_STATIC_URL")
        or ""
    ).strip()
    secret = (
        environ.get("ADMIN_BEARER_TOKEN") or environ.get("TELEGRAM_WEBHOOK_SECRET") or ""
    ).strip()
    if not raw_base_url or not secret:
        return None

    base_url = raw_base_url if "://" in raw_base_url else f"https://{raw_base_url}"
    token = hmac.new(
        secret.encode(),
        draft_ref.encode(),
        hashlib.sha256,
    ).hexdigest()[:40]
    return f"{base_url.rstrip('/')}/admin/drafts/{draft_ref}/web-edit?token={token}"


def _extract_line_value(*, text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line.removeprefix(prefix).strip()
            return value or None
    return None


def _extract_https_line(text: str) -> str | None:
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("https://"):
            return value
    return None

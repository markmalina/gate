"""Telegram Bot API client for safe outbound messages."""

import hashlib
import hmac
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx


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
    ) -> None:
        super().__init__(description)
        self.error_code = error_code
        self.description = description
        self.http_status = http_status


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

        url = f"{self._api_base_url}/bot{self._credential}/sendMessage"
        return await self._post_telegram_json(url=url, payload=payload)

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

    async def _post_telegram_json(
        self, *, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._http_client.post(url, json=payload)
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
            )

        return cast(dict[str, Any], result)


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
        environ.get("ADMIN_BEARER_TOKEN")
        or environ.get("TELEGRAM_WEBHOOK_SECRET")
        or ""
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

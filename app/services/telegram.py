"""Telegram Bot API client for safe outbound messages."""

from typing import Any, cast

import httpx


class TelegramClient:
    """Small async Telegram Bot API client."""

    def __init__(
        self,
        credential: str,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._credential = credential
        self._api_base_url = api_base_url.rstrip("/")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        business_connection_id: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
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
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        elif _looks_like_html_message(text):
            payload["parse_mode"] = "HTML"

        effective_reply_markup = reply_markup or _build_internal_reply_markup(text)
        if effective_reply_markup is not None:
            payload["reply_markup"] = effective_reply_markup

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

        effective_reply_markup = reply_markup or _build_internal_reply_markup(text)
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

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup or {"inline_keyboard": []},
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

    async def _post_telegram_json(
        self, *, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

        if not isinstance(result, dict):
            raise RuntimeError("Telegram API returned a non-object response")

        return cast(dict[str, Any], result)


def _looks_like_html_message(text: str) -> bool:
    """Return true for internal messages that intentionally use Telegram HTML."""

    return "<code>" in text or "<pre>" in text


def _build_internal_reply_markup(text: str) -> dict[str, Any] | None:
    """Build inline actions for Gate's internal draft messages."""

    draft_ref = _extract_line_value(text=text, prefix="Draft created:")
    if draft_ref:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✏️ Редактировать",
                        "callback_data": f"gate:edit:{draft_ref}",
                    },
                    {
                        "text": "✅ Отправить",
                        "callback_data": f"gate:send:{draft_ref}",
                    },
                ]
            ]
        }

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

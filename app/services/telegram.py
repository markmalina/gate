"""Telegram Bot API client for safe outbound messages."""

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

import httpx

from app.application.telegram_formatting import validate_entities


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

        if business_connection_id is not None:
            # Legacy auto/admin routes have no immutable reviewed snapshot or
            # durable send attempt. All Business replies use the shared sender's
            # literal/formatted/document/media methods after final admission.
            from app.application.reply_sends import ReplySendError

            raise ReplySendError(
                code="context_snapshot_required",
                message="Use a snapshot-bound Gate draft and shared final admission",
            )

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

    async def send_formatted_message(
        self,
        *,
        chat_id: str,
        text: str,
        entities: list[dict[str, Any]],
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send exact saved text and entities without parse-mode guessing or retries."""

        if not self._credential:
            raise RuntimeError("Telegram credential is required")
        checked = validate_entities(text, entities)
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "entities": list(checked),
        }
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id
        if reply_to_message_id is not None:
            if type(reply_to_message_id) is not int or reply_to_message_id < 1:
                raise ValueError("Telegram reply message id is invalid")
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        url = f"{self._api_base_url}/bot{self._credential}/sendMessage"
        return await self._post_telegram_json(url=url, payload=payload)

    async def send_rich_message(
        self,
        *,
        chat_id: str,
        business_connection_id: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One blocks-only rich write on the shared bounded HTTP client; no retry."""
        if not self._credential or not chat_id or not business_connection_id:
            raise ValueError("Telegram Business destination is required")
        from app.application.telegram_rich import validate_provider_blocks

        validate_provider_blocks(blocks)
        payload = {
            "chat_id": chat_id,
            "business_connection_id": business_connection_id,
            "rich_message": {"blocks": blocks, "skip_entity_detection": True},
        }
        url = f"{self._api_base_url}/bot{self._credential}/sendRichMessage"
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
        caption_entities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Upload one original supported file as a Business document.

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
        from app.application.document_formats import DOCUMENT_TYPES

        allowed = DOCUMENT_TYPES
        if allowed.get(PurePosixPath(filename).suffix.lower()) != media_type:
            raise ValueError("Document extension and MIME type do not match")
        if not isinstance(data, bytes) or not 0 < len(data) <= 8 * 1024 * 1024:
            raise ValueError("Document must contain at most 8 MiB")
        if not isinstance(caption, str) or len(caption) > 1024:
            raise ValueError("Document caption is too long")

        payload = {
            "chat_id": chat_id,
            "business_connection_id": business_connection_id,
            "caption": caption,
        }
        if PurePosixPath(filename).suffix.lower() not in {".pdf", ".zip"}:
            payload["disable_content_type_detection"] = "true"
        if caption_entities is not None:
            payload["caption_entities"] = json.dumps(
                list(validate_entities(caption, caption_entities)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        url = f"{self._api_base_url}/bot{self._credential}/sendDocument"
        return await self._post_telegram_multipart(
            url=url,
            payload=payload,
            file=("document", (filename, data, media_type)),
        )

    async def send_business_photo(
        self,
        *,
        chat_id: str,
        business_connection_id: str,
        filename: str,
        media_type: str,
        data: bytes,
        caption: str = "",
        caption_entities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """One approved photo through the same bounded multipart transport."""
        files = _checked_photo_files([("photo_1", filename, media_type, data)])
        self._require_business_destination(chat_id, business_connection_id)
        payload = {
            "chat_id": chat_id,
            "business_connection_id": business_connection_id,
            "photo": "attach://photo_1",
        }
        entities_json = _caption_entities_json(caption, caption_entities or [])
        if caption:
            payload.update(caption=caption, caption_entities=entities_json)
        return await self._post_telegram_multipart(
            url=f"{self._api_base_url}/bot{self._credential}/sendPhoto",
            payload=payload,
            files=files,
        )

    async def send_business_album(
        self,
        *,
        chat_id: str,
        business_connection_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One native album POST; no splitting, retry or fallback."""
        self._require_business_destination(chat_id, business_connection_id)
        if not 2 <= len(items) <= 10:
            raise ValueError("Album requires 2-10 photos")
        files = _checked_photo_files(
            [
                (f"photo_{index}", item["filename"], item["media_type"], item["data"])
                for index, item in enumerate(items, 1)
            ]
        )
        media = []
        for index, item in enumerate(items, 1):
            caption = item["caption"]
            entities = item.get("caption_entities", [])
            _caption_entities_json(caption, entities)
            media.append(
                {
                    "type": "photo",
                    "media": f"attach://photo_{index}",
                    **({"caption": caption, "caption_entities": entities} if caption else {}),
                }
            )
        return await self._post_telegram_multipart(
            url=f"{self._api_base_url}/bot{self._credential}/sendMediaGroup",
            payload={
                "chat_id": chat_id,
                "business_connection_id": business_connection_id,
                "media": json.dumps(media, ensure_ascii=False, separators=(",", ":")),
            },
            files=files,
        )

    async def send_business_rich_media(
        self,
        *,
        chat_id: str,
        business_connection_id: str,
        blocks: list[dict[str, Any]],
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One native blocks-only rich write, local multipart attachments only."""
        from app.application.telegram_rich_media import validate_rich_media_attachments

        self._require_business_destination(chat_id, business_connection_id)
        files = _checked_photo_files(
            [
                (f"photo_{index}", item["filename"], item["media_type"], item["data"])
                for index, item in enumerate(items, 1)
            ]
        )
        validate_rich_media_attachments(blocks, set(files))
        return await self._post_telegram_multipart(
            url=f"{self._api_base_url}/bot{self._credential}/sendRichMessage",
            payload={
                "chat_id": chat_id,
                "business_connection_id": business_connection_id,
                "rich_message": json.dumps(
                    {"blocks": blocks, "skip_entity_detection": True},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
            files=files,
        )

    def _require_business_destination(self, chat_id: str, business_connection_id: str) -> None:
        if not self._credential or not chat_id or not business_connection_id:
            raise ValueError("Telegram Business destination is required")

    async def _post_telegram_multipart(
        self,
        *,
        url: str,
        payload: dict[str, str],
        file: tuple[str, tuple[str, bytes, str]] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        if (file is None) == (files is None):
            raise ValueError("Exactly one multipart attachment mapping is required")
        attachments = {file[0]: file[1]} if file is not None else files
        response = await self._http_client.post(
            url,
            data=payload,
            files=attachments,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=2.0),
        )
        return self._parse_telegram_response(response)

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
        response = await self._http_client.post(url, json=payload)
        return self._parse_telegram_response(response)

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
            )

        return cast(dict[str, Any], result)


def _checked_photo_files(
    items: list[tuple[str, str, str, bytes]],
) -> dict[str, tuple[str, bytes, str]]:
    """Defense in depth; full decode/transform belongs to immutable preparation."""
    if not 1 <= len(items) <= 10:
        raise ValueError("Media requires 1-10 photos")
    files = {}
    total = 0
    for name, filename, media_type, data in items:
        if (
            not filename
            or PurePosixPath(filename).name != filename
            or "\\" in filename
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
            or len(filename.encode()) > 255
        ):
            raise ValueError("Invalid photo filename")
        extension = PurePosixPath(filename).suffix.lower()
        if (
            {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(extension)
            != media_type
            or not isinstance(data, bytes)
            or not 0 < len(data) <= 8 * 1024 * 1024
        ):
            raise ValueError("Photo MIME, extension or bytes are invalid")
        if (media_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n")) or (
            media_type == "image/jpeg" and not data.startswith(b"\xff\xd8")
        ):
            raise ValueError("Photo signature is invalid")
        total += len(data)
        if total > 24 * 1024 * 1024 or name in files:
            raise ValueError("Photo attachment mapping or total size is invalid")
        files[name] = (filename, data, media_type)
    return files


def _caption_entities_json(caption: str, entities: list[dict[str, Any]]) -> str:
    if not isinstance(caption, str) or len(caption.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Photo caption exceeds 1024 UTF-16 units")
    return json.dumps(
        list(validate_entities(caption, entities)), ensure_ascii=False, separators=(",", ":")
    )


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

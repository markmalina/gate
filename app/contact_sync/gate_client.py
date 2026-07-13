"""Privacy-bounded HTTP publisher for Gate contact snapshots."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.contact_sync.models import ContactSnapshotItem, SnapshotImportCounts


class GatePublishError(Exception):
    """Safe Gate publishing failure that never includes request contact data."""


class GateSnapshotPublisher:
    """Publish sanitized chunks to the protected Gate import boundary."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        parsed_url = urlparse(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise GatePublishError("Gate base URL must be an absolute HTTP(S) URL")
        if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "test"}:
            raise GatePublishError("Gate base URL must use HTTPS outside local tests")
        if not bearer_token:
            raise GatePublishError("Gate contact-sync bearer token is required")
        self._endpoint = f"{normalized_url}/admin/contact-snapshots"
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def publish(
        self,
        *,
        account_alias: str,
        revision: str,
        source: str,
        source_account_telegram_user_id: str | None,
        contacts: tuple[ContactSnapshotItem, ...],
    ) -> SnapshotImportCounts:
        payload = {
            "account_alias": account_alias,
            "revision": revision,
            "source": source,
            "source_account_telegram_user_id": source_account_telegram_user_id,
            "contacts": [
                {
                    "telegram_user_id": contact.telegram_user_id,
                    "display_name": contact.display_name,
                    "username": contact.username,
                }
                for contact in contacts
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._bearer_token}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise GatePublishError("Gate contact snapshot request failed") from exc

        if response.status_code != 200:
            error_code = _safe_error_code(response)
            suffix = f" ({error_code})" if error_code else ""
            raise GatePublishError(
                f"Gate rejected contact snapshot with HTTP {response.status_code}{suffix}"
            )

        try:
            body = response.json()
            counts = body["counts"]
            return SnapshotImportCounts(
                received=_non_negative_int(counts, "received"),
                created=_non_negative_int(counts, "created"),
                updated=_non_negative_int(counts, "updated"),
                unchanged=_non_negative_int(counts, "unchanged"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GatePublishError("Gate returned an invalid aggregate response") from exc


def _safe_error_code(response: httpx.Response) -> str | None:
    try:
        body: Any = response.json()
        code = body.get("detail", {}).get("code")
    except (ValueError, AttributeError):
        return None
    if not isinstance(code, str) or not code.replace("_", "").isalnum():
        return None
    return code[:64]


def _non_negative_int(mapping: Any, key: str) -> int:
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Invalid aggregate field: {key}")
    return value

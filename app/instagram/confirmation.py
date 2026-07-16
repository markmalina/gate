"""Short-lived exact confirmation tokens for Reel publication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from app.instagram.contracts import InstagramContractError, ReelDraft


def issue_confirmation(draft: ReelDraft, *, secret: str) -> str:
    if not secret:
        raise InstagramContractError("Instagram signing secret is not configured")
    body = json.dumps(
        {
            "account_alias": draft.account_alias,
            "caption": draft.caption,
            "download_url": draft.video.download_url,
            "file_id": draft.video.file_id,
            "file_name": draft.video.file_name,
            "mime_type": draft.video.mime_type,
            "expires_at": draft.expires_at.isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")


def verify_confirmation(token: str, *, secret: str) -> ReelDraft:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        body, signature = raw[:-32], raw[-32:]
        expected = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InstagramContractError("confirmation token is invalid")
        payload = json.loads(body)
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at <= datetime.now(UTC):
            raise InstagramContractError("confirmation token has expired")
        return ReelDraft.build(
            account_alias=payload["account_alias"],
            caption=payload["caption"],
            video=payload,
            expires_at=expires_at,
        )
    except InstagramContractError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InstagramContractError("confirmation token is invalid") from exc

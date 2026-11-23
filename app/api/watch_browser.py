"""Owner browser access to one Watch recording through the existing OAuth issuer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from html import escape
from urllib.parse import parse_qs, urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app.api.deps import get_database_runtime
from app.core.config import Settings, get_settings
from app.core.mcp_oauth import McpOAuthError, authenticate_mcp_oauth
from app.core.watch_browser import CALLBACK_PATH, WatchBrowser, watch_browser, watch_path
from app.models.domain import UNCLASSIFIED_PROJECT_ID
from app.models.watch_voice import WatchEarlyCard, WatchVoiceNote

router = APIRouter(include_in_schema=False)
settings_dependency = Depends(get_settings)
_FLOW_COOKIE = "__Host-gate-watch-flow"
_SESSION_COOKIE = "__Host-gate-watch-session"
_SLOT = re.compile(r"[A-Za-z0-9_-]{43}")
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


def _error(status: int) -> Response:
    return Response(status_code=status, headers=_HEADERS)


async def _is_owner(token: str, settings: Settings, config: WatchBrowser) -> bool:
    if len(token) > 3600 or not _JWT.fullmatch(token):
        return False
    try:
        principal = await authenticate_mcp_oauth(
            authorization=f"Bearer {token}", settings=settings, required_scope="gate:inbox:read"
        )
    except McpOAuthError:
        return False
    return (
        principal.subject == config.owner_subject
        and principal.access_scope.workspace_id == settings.gate_workspace_id
        and UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids
    )


def _login(config: WatchBrowser, event_id: str) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    query = urlencode({
        "response_type": "code",
        "response_mode": "form_post",
        "client_id": config.client_id,
        "redirect_uri": config.callback,
        "audience": config.audience,
        "scope": "gate:inbox:read",
        "code_challenge_method": "S256",
        "code_challenge": challenge.rstrip(b"=").decode(),
        "state": state,
    })
    response = RedirectResponse(config.issuer + "authorize?" + query, 303, headers=_HEADERS)
    # The IdP returns a cross-site POST. State and PKCE bind it to this browser.
    response.set_cookie(
        _FLOW_COOKIE, f"{state}.{verifier}.{event_id}",
        max_age=300, secure=True, httponly=True, samesite="none",
    )
    return response


async def _callback_fields(request: Request) -> dict[str, list[str]]:
    if request.headers.get("content-type", "").split(";", 1)[0] != (
        "application/x-www-form-urlencoded"
    ):
        return {}
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4096:
            return {}
        data.extend(chunk)
    try:
        return parse_qs(data.decode("ascii"), max_num_fields=12, strict_parsing=True)
    except ValueError:
        return {}


async def _exchange(config: WatchBrowser, code: str, verifier: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(
                config.issuer + "oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": config.client_id,
                    "redirect_uri": config.callback,
                    "code": code,
                    "code_verifier": verifier,
                },
                follow_redirects=False,
            )
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token") if isinstance(data, dict) else None
            if isinstance(token, str):
                return token
    except (httpx.HTTPError, ValueError):
        pass
    return ""


@router.post(CALLBACK_PATH)
async def watch_callback(request: Request, settings: Settings = settings_dependency) -> Response:
    config = watch_browser(settings)
    if config is None:
        return _error(404)
    state, _, rest = request.cookies.get(_FLOW_COOKIE, "").partition(".")
    verifier, _, event_id = rest.partition(".")
    fields = await _callback_fields(request)
    returned_state = fields.get("state", [])
    codes = fields.get("code", [])
    if (
        not _SLOT.fullmatch(state)
        or not _SLOT.fullmatch(verifier)
        or len(returned_state) != 1
        or not _SLOT.fullmatch(returned_state[0])
        or not hmac.compare_digest(state, returned_state[0])
        or len(codes) != 1
        or len(codes[0]) > 2048
    ):
        return _error(403)
    try:
        event_id = str(UUID(event_id))
    except ValueError:
        return _error(403)
    token = await _exchange(config, codes[0], verifier)
    response: Response
    if await _is_owner(token, settings, config):
        response = RedirectResponse(watch_path(event_id), 303, headers=_HEADERS)
        response.set_cookie(
            _SESSION_COOKIE, token, max_age=900, secure=True, httponly=True, samesite="lax"
        )
    else:
        response = _error(403)
    response.delete_cookie(_FLOW_COOKIE, secure=True, httponly=True, samesite="none")
    return response


@router.get("/watch/records/{event_id}")
@router.get("/watch/records/{event_id}/original")
async def watch_record(
    event_id: str, request: Request, settings: Settings = settings_dependency
) -> Response:
    config = watch_browser(settings)
    if config is None:
        return _error(404)
    try:
        event_id = str(UUID(event_id))
    except ValueError:
        return _error(404)
    original = request.url.path.endswith("/original")
    token = request.cookies.get(_SESSION_COOKIE)
    if not token:
        return _error(401) if original else _login(config, event_id)
    if not await _is_owner(token, settings, config):
        response = _error(403)
        response.delete_cookie(_SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    async with get_database_runtime(request).session_factory() as session:
        card = await session.scalar(
            select(WatchEarlyCard).where(
                WatchEarlyCard.id == event_id,
                WatchEarlyCard.workspace_key == settings.gate_workspace_id,
                WatchEarlyCard.account_id == settings.dropbox_call_account_id,
            )
        )
        if card is None:
            return _error(404)
        query = select(WatchVoiceNote).where(
            WatchVoiceNote.id == card.id,
            WatchVoiceNote.workspace_key == card.workspace_key,
            WatchVoiceNote.account_id == card.account_id,
            WatchVoiceNote.file_id == card.file_id,
            WatchVoiceNote.revision == card.revision,
        )
        if original:
            note = await session.scalar(query)
            if (
                note is None
                or len(note.data) != note.size_bytes
                or hashlib.sha256(note.data).hexdigest() != note.sha256
            ):
                return _error(404)
            return Response(
                note.data,
                media_type="application/octet-stream",
                headers={
                    **_HEADERS,
                    "Content-Disposition": f'attachment; filename="watch-{event_id}"',
                },
            )
        summary = (await session.execute(query.with_only_columns(
            WatchVoiceNote.filename, WatchVoiceNote.transcription_state, WatchVoiceNote.transcript
        ))).one_or_none()

    content = "<p>Оригинал ожидает сохранения в Gate.</p>"
    if summary is not None:
        filename, state, transcript = summary
        content = (
            f"<p>{escape(filename)}</p>"
            f'<p><a href="{watch_path(event_id)}/original">Скачать оригинал</a></p>'
        )
        if state == "transcribed" and transcript:
            content += f"<h2>Расшифровка</h2><pre>{escape(transcript)}</pre>"
        else:
            content += {
                "rejected": "<p>Расшифровка завершилась отказом.</p>",
                "unknown": "<p>Исход расшифровки не подтверждён.</p>",
            }.get(state, "<p>Расшифровка готовится.</p>")
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Запись Watch</title>'
        f"<h1>Запись Watch</h1><p>{event_id}</p>{content}</html>",
        headers=_HEADERS,
    )

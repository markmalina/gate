import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from app.api import watch_browser as browser
from app.core import mcp_oauth
from app.core.config import Settings, get_settings
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.watch_voice_early_cards import card_values
from app.infrastructure.watch_voice_github import marker
from app.models.domain import UNCLASSIFIED_PROJECT_ID
from app.models.watch_voice import WatchEarlyCard, WatchVoiceNote
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ORIGIN = "https://gate.example"
ISSUER = "https://auth.example/"
CALLBACK = "/watch/oauth/callback"
RECORDING = b"synthetic original"


@pytest.mark.anyio
async def test_owner_link_round_trip_and_fail_closed(monkeypatch):
    settings = Settings(
        GATE_MCP_ENABLED=True, MCP_AUTH_MODE="oauth", MCP_PUBLIC_RESOURCE_URL=ORIGIN + "/mcp",
        MCP_AUTHORIZATION_SERVER=ISSUER, MCP_OAUTH_JWKS_URL=ISSUER + ".well-known/jwks.json",
        GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="test",
        GATE_WATCH_BROWSER_CLIENT_ID="browser-test", GATE_WATCH_BROWSER_OWNER_SUBJECT="auth|owner",
    )
    now = datetime.now(UTC)
    values = card_values(
        settings, {"id": "id:synthetic", "rev": "abc", "server_modified": now.isoformat()}, now
    )
    path = f"/watch/records/{values['id']}"
    assert ORIGIN + path in values["body"]
    assert marker(values["id"], 0) in values["body"]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(mcp_oauth, "_jwks_client", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    ))

    def token(subject, workspace, *, project=True):
        return jwt.encode({
            "sub": subject, "iss": ISSUER, "aud": ORIGIN + "/mcp",
            "iat": int(now.timestamp()), "exp": int(now.timestamp()) + 300,
            "scope": "gate:inbox:read", "gate_workspace_id": workspace,
            "gate_project_ids": [str(UNCLASSIFIED_PROJECT_ID)] if project else [],
        }, key, algorithm="RS256")

    approved = token("auth|owner", "mark-personal")
    credentials = {
        "owner-code": approved,
        "other-subject": token("auth|other", "mark-personal"),
        "other-workspace": token("auth|owner", "foreign"),
        "no-project": token("auth|owner", "mark-personal", project=False),
    }
    exchanges = []

    async def exchange(config, code, verifier):
        assert config.callback == ORIGIN + CALLBACK
        assert len(verifier) == 43
        exchanges.append(code)
        return credentials[code]

    monkeypatch.setattr(browser, "_exchange", exchange)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as db:
        await db.run_sync(WatchEarlyCard.__table__.create)
        await db.run_sync(WatchVoiceNote.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        session.add(WatchEarlyCard(**values))

    app = FastAPI()
    app.include_router(browser.router)
    app.state.database_runtime = DatabaseRuntime(engine, factory)
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            async def start():
                response = await client.get(path)
                assert response.status_code == 303
                assert response.headers["referrer-policy"] == "no-referrer"
                assert values["id"] not in response.headers["location"]
                query = parse_qs(urlsplit(response.headers["location"]).query)
                assert query["response_mode"] == ["form_post"]
                assert query["code_challenge_method"] == ["S256"]
                return query["state"][0]

            assert (await client.get(path + "/original")).status_code == 401
            assert (await client.get("/watch/records/invalid")).status_code == 404
            state = await start()
            rejected = await client.post(CALLBACK, data={"state": "a" * 43, "code": "owner-code"})
            assert rejected.status_code == 403 and not exchanges
            for code in ("other-subject", "other-workspace", "no-project"):
                state = await start()
                denied = await client.post(CALLBACK, data={"state": state, "code": code})
                assert denied.status_code == 403 and not client.cookies.get(browser._SESSION_COOKIE)
            state = await start()
            returned = await client.post(CALLBACK, data={"state": state, "code": "owner-code"})
            assert returned.status_code == 303 and returned.headers["location"] == path
            cookies = returned.headers.get_list("set-cookie")
            assert "Secure" in cookies[0] and "HttpOnly" in cookies[0]
            assert not client.cookies.get(browser._FLOW_COOKIE)
            page = await client.get(path)
            assert page.status_code == 200 and "ожидает сохранения" in page.text
            assert "no-store" in page.headers["cache-control"]
            async with factory.begin() as session:
                session.add(WatchVoiceNote(
                    id=values["id"], workspace_key="mark-personal", account_id="test",
                    file_id="id:synthetic", revision="abc", source_path="/Alpha/Voice/test.m4a",
                    filename="test.m4a", media_type="audio/mp4", size_bytes=len(RECORDING),
                    sha256=hashlib.sha256(RECORDING).hexdigest(), data=RECORDING,
                    received_at=now, dropbox_modified_at=now,
                    transcription_state="transcribed", transcript="<b>synthetic</b>",
                ))
            result = await client.get(path)
            assert "&lt;b&gt;synthetic&lt;/b&gt;" in result.text
            original = await client.get(path + "/original")
            assert original.content == RECORDING and "attachment" in original.headers[
                "content-disposition"
            ]
            assert "no-store" in original.headers["cache-control"]
            other = "/watch/records/a617bee2-1b12-40e4-8bdd-8b4c4d20d153"
            assert (await client.get(other)).status_code == 404
    finally:
        await engine.dispose()

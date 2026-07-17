import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.instagram.command_gateway import (
    InstagramCommandGatewayError,
    execute_authenticated_command,
    sign_instagram_command,
    verify_instagram_command,
)
from app.instagram.automation import (
    AutomationDecision,
    AutomationFacts,
    FollowState,
    admit_direct_delivery,
)
from app.instagram.commands import (
    FakeInstagramCommandExecutor,
    JsonInstagramCommandResultStore,
    ProviderFollowState,
)
from app.instagram.config import InstagramAccountConfig, InstagramRuntimeConfig
from app.instagram.confirmation import issue_confirmation, verify_confirmation
from app.instagram.contracts import InstagramContractError, ReelDraft
from app.instagram.diagnostics import diagnose_instagram_transport
from app.instagram.events import InstagramEvent, InstagramEventType
from app.instagram.mcp import instagram_mcp_tool_names
from app.instagram.meta import MetaInstagramClient, MetaInstagramCommandExecutor
from app.instagram.routes import router as instagram_webhook_router
from app.instagram.sink import MayakiInstagramEventSink, sign_mayaki_intake
from app.instagram.webhooks import normalize_webhook, verify_meta_signature
from fastapi import FastAPI

VIDEO = {
    "download_url": "https://files.example/reel.mp4",
    "file_id": "file-1",
    "mime_type": "video/mp4",
    "file_name": "reel.mp4",
}


def test_confirmation_round_trip_binds_exact_reel() -> None:
    draft = ReelDraft.build(
        account_alias="main",
        caption="Hello",
        video=VIDEO,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    restored = verify_confirmation(issue_confirmation(draft, secret="secret"), secret="secret")
    assert restored.account_alias == "main"
    assert restored.caption == "Hello"
    assert restored.video.file_id == "file-1"


def test_confirmation_rejects_tampering() -> None:
    draft = ReelDraft.build(
        account_alias="main",
        caption="Hello",
        video=VIDEO,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    token = issue_confirmation(draft, secret="secret")
    with pytest.raises(InstagramContractError, match="invalid"):
        verify_confirmation(token[:-2] + "aa", secret="secret")


def test_reel_contract_rejects_non_video_file() -> None:
    with pytest.raises(InstagramContractError, match="MP4"):
        ReelDraft.build(
            account_alias="main",
            caption="Hello",
            video={**VIDEO, "mime_type": "image/png"},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


def test_unknown_follow_state_never_unlocks_follow_gated_delivery() -> None:
    facts = AutomationFacts(
        automation_enabled=True,
        kill_switch_active=False,
        approved_copy=True,
        event_is_deduplicated=True,
        messaging_window_open=True,
        follow_state=FollowState.UNKNOWN,
    )
    assert admit_direct_delivery(facts, requires_follow=True) is AutomationDecision.REQUIRE_HUMAN


def test_automation_kill_switch_blocks_delivery() -> None:
    facts = AutomationFacts(
        automation_enabled=True,
        kill_switch_active=True,
        approved_copy=True,
        event_is_deduplicated=True,
        messaging_window_open=True,
        follow_state=FollowState.FOLLOWING,
    )
    assert admit_direct_delivery(facts, requires_follow=False) is AutomationDecision.BLOCK


def test_verified_comment_webhook_is_normalized_and_scoped() -> None:
    body = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": "ig-1",
                    "time": 1_700_000_000,
                    "changes": [
                        {
                            "field": "comments",
                            "value": {"id": "comment-1", "text": "21"},
                        }
                    ],
                }
            ],
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    verify_meta_signature(body=body, signature_header=signature, app_secret="secret")
    events = normalize_webhook(
        body=body,
        workspace_key="mark-personal",
        account_alias_by_provider_id={"ig-1": "main"},
    )
    assert len(events) == 1
    assert events[0].event_type is InstagramEventType.COMMENT_RECEIVED
    assert events[0].account_alias == "main"
    assert events[0].deduplication_key


def test_comment_event_adapts_to_authenticated_mayaki_intake() -> None:
    event = InstagramEvent(
        event_type=InstagramEventType.COMMENT_RECEIVED,
        workspace_key="mark-personal",
        account_alias="main",
        provider_event_id="comment-1",
        occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
        payload={
            "from": {"id": "ig-user-1", "username": "anna"},
            "text": "21",
        },
    )
    payload = event.mayaki_gate_intake_payload()
    assert payload["provider"] == "instagram"
    assert payload["provider_user_id"] == "ig-user-1"
    assert payload["message_text"] == "21"
    assert payload["raw"]["account_alias"] == "main"


@pytest.mark.anyio
async def test_gate_signs_and_delivers_instagram_event_to_mayaki() -> None:
    event = InstagramEvent(
        event_type=InstagramEventType.COMMENT_RECEIVED,
        workspace_key="mark-personal",
        account_alias="main",
        provider_event_id="comment-1",
        occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
        payload={"from": {"id": "ig-user-1"}, "text": "21"},
    )
    now = datetime(2026, 7, 17, 1, tzinfo=UTC)

    async def handler(request: httpx.Request) -> httpx.Response:
        raw_body = request.content.decode()
        expected = sign_mayaki_intake(
            method="POST",
            path="/intake/gate",
            timestamp=now.isoformat(),
            raw_body=raw_body,
            secret="hmac-secret",
        )
        assert request.headers["authorization"] == "Bearer bearer-secret"
        assert request.headers["x-mayaki-signature"] == expected
        assert request.headers["x-mayaki-idempotency-key"] == event.deduplication_key
        return httpx.Response(200, json={"ok": True})

    config = InstagramRuntimeConfig(
        enabled=False,
        delivery_enabled=False,
        webhook_enabled=True,
        webhook_delivery_enabled=True,
        graph_base_url="https://graph.instagram.com",
        graph_version="",
        signing_secret="",
        app_secret="app-secret",
        webhook_verify_token="verify-token",
        mayaki_intake_url="https://mayaki.example/intake/gate",
        mayaki_intake_bearer_token="bearer-secret",
        mayaki_intake_hmac_secret="hmac-secret",
        accounts={
            "main": InstagramAccountConfig(
                alias="main",
                instagram_user_id="ig-account-1",
                access_token="secret",
            )
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        delivered = await MayakiInstagramEventSink(
            config=config,
            http=http,
            now=lambda: now,
        ).deliver([event])
    assert delivered == 1


@pytest.mark.anyio
async def test_meta_webhook_verification_returns_plain_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_INSTAGRAM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("GATE_INSTAGRAM_APP_SECRET", "app-secret")
    monkeypatch.setenv("GATE_INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "verify-token")
    app = FastAPI()
    app.include_router(instagram_webhook_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as http:
        response = await http.get(
            "/webhooks/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge-123",
                "hub.verify_token": "verify-token",
            },
        )
    assert response.status_code == 200
    assert response.text == "challenge-123"
    assert response.headers["content-type"].startswith("text/plain")


def test_mayaki_private_reply_and_follow_check_commands_run_on_fake_adapter() -> None:
    executor = FakeInstagramCommandExecutor(follow_state=ProviderFollowState.FOLLOWING)
    private_reply = {
        "type": "gate.instagram.private_reply.requested.v1",
        "idempotency_key": "session-1:opening-dm",
        "account_alias": "main",
        "provider_comment_id": "comment-1",
        "text": "Нажми «Получить».",
        "quick_reply": {
            "title": "Получить",
            "payload": "ig_consent:session-1:1",
        },
    }
    first = executor.execute(private_reply)
    replay = executor.execute(private_reply)
    follow = executor.execute(
        {
            "type": "gate.instagram.follow_check.requested.v1",
            "idempotency_key": "session-1:follow-check:3",
            "account_alias": "main",
            "instagram_user_id": "ig-user-1",
        }
    )
    assert first["external_delivery"] is False
    assert replay["idempotent_replay"] is True
    assert follow["follow_state"] == "following"


def instagram_meta_config() -> InstagramRuntimeConfig:
    return InstagramRuntimeConfig(
        enabled=True,
        delivery_enabled=True,
        webhook_enabled=False,
        webhook_delivery_enabled=False,
        graph_base_url="https://graph.instagram.com",
        graph_version="v23.0",
        signing_secret="signing-secret",
        app_secret="",
        webhook_verify_token="",
        mayaki_intake_url="",
        mayaki_intake_bearer_token="",
        mayaki_intake_hmac_secret="",
        accounts={},
    )


@pytest.mark.anyio
async def test_meta_public_and_private_reply_contracts() -> None:
    account = InstagramAccountConfig(
        alias="main",
        instagram_user_id="ig-account-1",
        access_token="secret",
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/comment-1/replies"):
            return httpx.Response(200, json={"id": "reply-1"})
        return httpx.Response(200, json={"recipient_id": "ig-user-1", "message_id": "mid-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MetaInstagramClient(config=instagram_meta_config(), http=http)
        public_id = await client.reply_to_comment(
            account=account,
            comment_id="comment-1",
            text="Отправил в личку",
        )
        recipient_id = await client.private_reply_to_comment(
            account=account,
            comment_id="comment-1",
            text="Нажми «Получить».",
            quick_reply={"title": "Получить", "payload": "ig_consent:session-1:1"},
        )

    assert public_id == "reply-1"
    assert recipient_id == "ig-user-1"
    assert requests[0].url.path == "/v23.0/comment-1/replies"
    private_body = json.loads(requests[1].content)
    assert private_body["recipient"] == {"comment_id": "comment-1"}
    assert private_body["message"]["quick_replies"][0]["payload"].startswith("ig_consent:")


@pytest.mark.anyio
async def test_meta_follow_state_is_fail_closed() -> None:
    account = InstagramAccountConfig(
        alias="main",
        instagram_user_id="ig-account-1",
        access_token="secret",
    )
    responses = iter(
        [
            httpx.Response(200, json={"is_user_follow_business": True}),
            httpx.Response(200, json={}),
            httpx.Response(403, json={"error": {"message": "User consent is required"}}),
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MetaInstagramClient(config=instagram_meta_config(), http=http)
        following = await client.follow_state(
            account=account,
            instagram_scoped_user_id="ig-user-1",
        )
        unknown = await client.follow_state(
            account=account,
            instagram_scoped_user_id="ig-user-2",
        )
        not_permitted = await client.follow_state(
            account=account,
            instagram_scoped_user_id="ig-user-3",
        )

    assert following is ProviderFollowState.FOLLOWING
    assert unknown is ProviderFollowState.UNKNOWN
    assert not_permitted is ProviderFollowState.NOT_PERMITTED


@pytest.mark.anyio
async def test_live_command_executor_maps_validated_commands_to_meta() -> None:
    account = InstagramAccountConfig(
        alias="main",
        instagram_user_id="ig-account-1",
        access_token="secret",
    )
    config = instagram_meta_config()
    config.accounts["main"] = account
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/comment-1/replies"):
            return httpx.Response(200, json={"id": "reply-1"})
        if body.get("recipient") == {"comment_id": "comment-1"}:
            return httpx.Response(200, json={"recipient_id": "ig-user-1"})
        return httpx.Response(200, json={"message_id": "message-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        executor = MetaInstagramCommandExecutor(
            client=MetaInstagramClient(config=config, http=http)
        )
        public = await executor.execute(
            {
                "type": "gate.instagram.public_reply.requested.v1",
                "idempotency_key": "session-1:public-reply",
                "account_alias": "main",
                "provider_comment_id": "comment-1",
                "text": "Отправил в личку",
            }
        )
        replay = await executor.execute(
            {
                "type": "gate.instagram.public_reply.requested.v1",
                "idempotency_key": "session-1:public-reply",
                "account_alias": "main",
                "provider_comment_id": "comment-1",
                "text": "Отправил в личку",
            }
        )
        prompt = await executor.execute(
            {
                "type": "gate.instagram.follow_prompt.requested.v1",
                "idempotency_key": "session-1:follow-prompt:3",
                "account_alias": "main",
                "instagram_user_id": "ig-user-1",
                "text": "Подпишись и проверь ещё раз.",
                "quick_reply": {
                    "title": "Проверить ещё раз",
                    "payload": "ig_follow_recheck:session-1:3",
                },
            }
        )

    assert public["provider_reply_id"] == "reply-1"
    assert replay["idempotent_replay"] is True
    assert prompt["provider_message_id"] == "message-1"
    assert len(requests) == 2


def test_setup_diagnostics_are_available_while_instagram_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_INSTAGRAM_ENABLED", raising=False)
    monkeypatch.setenv("GATE_INSTAGRAM_SIGNING_SECRET", "secret-not-for-output")
    assert instagram_mcp_tool_names() == ["gate_instagram_diagnose_setup"]
    report = diagnose_instagram_transport(InstagramRuntimeConfig.from_env())
    assert report["status"] == "configuration_required"
    assert report["external_delivery"] is False
    assert "account_registry" in report["missing"]
    assert "secret-not-for-output" not in json.dumps(report)


def test_setup_diagnostics_report_capabilities_independently() -> None:
    config = instagram_meta_config()
    config.accounts["main"] = InstagramAccountConfig(
        alias="main",
        instagram_user_id="ig-account-1",
        access_token="secret",
    )
    report = diagnose_instagram_transport(config)
    assert report["capabilities"]["publishing"] is True
    assert report["capabilities"]["external_publish"] is True
    assert report["capabilities"]["webhook_verification"] is False


def test_signed_command_gateway_rejects_tampering_and_expired_requests() -> None:
    raw_body = json.dumps(
        {
            "type": "gate.instagram.follow_check.requested.v1",
            "idempotency_key": "session-1:follow-check:1",
            "account_alias": "main",
            "instagram_user_id": "ig-user-1",
        },
        separators=(",", ":"),
    )
    timestamp = "2026-07-17T08:00:00+00:00"
    signature = sign_instagram_command(
        timestamp=timestamp,
        idempotency_key="session-1:follow-check:1",
        raw_body=raw_body,
        secret="hmac-secret",
    )
    verified = verify_instagram_command(
        raw_body=raw_body,
        authorization="Bearer bearer-secret",
        timestamp=timestamp,
        signature=signature,
        idempotency_key="session-1:follow-check:1",
        bearer_token="bearer-secret",
        hmac_secret="hmac-secret",
        now=lambda: datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
    )
    assert verified["account_alias"] == "main"
    with pytest.raises(InstagramCommandGatewayError, match="signature_invalid"):
        verify_instagram_command(
            raw_body=raw_body + " ",
            authorization="Bearer bearer-secret",
            timestamp=timestamp,
            signature=signature,
            idempotency_key="session-1:follow-check:1",
            bearer_token="bearer-secret",
            hmac_secret="hmac-secret",
            now=lambda: datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
        )
    with pytest.raises(InstagramCommandGatewayError, match="timestamp_expired"):
        verify_instagram_command(
            raw_body=raw_body,
            authorization="Bearer bearer-secret",
            timestamp=timestamp,
            signature=signature,
            idempotency_key="session-1:follow-check:1",
            bearer_token="bearer-secret",
            hmac_secret="hmac-secret",
            now=lambda: datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
        )


@pytest.mark.anyio
async def test_command_result_store_survives_executor_restart(tmp_path) -> None:
    account = InstagramAccountConfig("main", "ig-account-1", "secret")
    config = instagram_meta_config()
    config.accounts["main"] = account
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"id": "reply-1"})

    command = {
        "type": "gate.instagram.public_reply.requested.v1",
        "idempotency_key": "session-1:public-reply:persisted",
        "account_alias": "main",
        "provider_comment_id": "comment-1",
        "text": "Отправил в личку",
    }
    store = JsonInstagramCommandResultStore(str(tmp_path / "results.json"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = MetaInstagramCommandExecutor(
            client=MetaInstagramClient(config=config, http=http),
            result_store=store,
        )
        await first.execute(command)
        restarted = MetaInstagramCommandExecutor(
            client=MetaInstagramClient(config=config, http=http),
            result_store=JsonInstagramCommandResultStore(str(tmp_path / "results.json")),
        )
        replay = await restarted.execute(command)
    assert replay["idempotent_replay"] is True
    assert calls == 1


@pytest.mark.anyio
async def test_authenticated_command_maps_provider_timeout_to_unknown() -> None:
    account = InstagramAccountConfig("main", "ig-account-1", "secret")
    config = instagram_meta_config()
    config.accounts["main"] = account
    command = {
        "type": "gate.instagram.public_reply.requested.v1",
        "idempotency_key": "session-1:public-reply:timeout",
        "account_alias": "main",
        "provider_comment_id": "comment-1",
        "text": "Отправил в личку",
    }
    raw_body = json.dumps(command, separators=(",", ":"))
    timestamp = "2026-07-17T08:00:00+00:00"
    signature = sign_instagram_command(
        timestamp=timestamp,
        idempotency_key=command["idempotency_key"],
        raw_body=raw_body,
        secret="hmac-secret",
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await execute_authenticated_command(
            executor=MetaInstagramCommandExecutor(
                client=MetaInstagramClient(config=config, http=http)
            ),
            raw_body=raw_body,
            authorization="Bearer bearer-secret",
            timestamp=timestamp,
            signature=signature,
            idempotency_key=command["idempotency_key"],
            bearer_token="bearer-secret",
            hmac_secret="hmac-secret",
            now=lambda: datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
        )
    assert result["status"] == "unknown"
    assert result["external_delivery"] is False


@pytest.mark.anyio
async def test_unknown_provider_result_is_replayed_without_second_call(tmp_path) -> None:
    account = InstagramAccountConfig("main", "ig-account-1", "secret")
    config = instagram_meta_config()
    config.accounts["main"] = account
    calls = 0
    command = {
        "type": "gate.instagram.public_reply.requested.v1",
        "idempotency_key": "session-1:public-reply:unknown-persisted",
        "account_alias": "main",
        "provider_comment_id": "comment-1",
        "text": "Отправил в личку",
    }
    raw_body = json.dumps(command, separators=(",", ":"))
    timestamp = "2026-07-17T08:00:00+00:00"
    signature = sign_instagram_command(
        timestamp=timestamp,
        idempotency_key=command["idempotency_key"],
        raw_body=raw_body,
        secret="hmac-secret",
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    store = JsonInstagramCommandResultStore(str(tmp_path / "unknown-results.json"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await execute_authenticated_command(
            executor=MetaInstagramCommandExecutor(
                client=MetaInstagramClient(config=config, http=http), result_store=store
            ),
            raw_body=raw_body,
            authorization="Bearer bearer-secret",
            timestamp=timestamp,
            signature=signature,
            idempotency_key=command["idempotency_key"],
            bearer_token="bearer-secret",
            hmac_secret="hmac-secret",
            now=lambda: datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
        )
        replay = await MetaInstagramCommandExecutor(
            client=MetaInstagramClient(config=config, http=http), result_store=store
        ).execute(command)
    assert first["status"] == "unknown"
    assert replay["idempotent_replay"] is True
    assert calls == 1

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.instagram.automation import (
    AutomationDecision,
    AutomationFacts,
    FollowState,
    admit_direct_delivery,
)
from app.instagram.confirmation import issue_confirmation, verify_confirmation
from app.instagram.contracts import InstagramContractError, ReelDraft
from app.instagram.events import InstagramEventType
from app.instagram.webhooks import normalize_webhook, verify_meta_signature

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
                    "changes": [{"field": "comments", "value": {"id": "comment-1", "text": "21"}}],
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

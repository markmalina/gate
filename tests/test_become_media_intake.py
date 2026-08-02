from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest
from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)
from app.application.become_media_intake import (
    CONFIRMED_TRANSCRIPT_RETENTION,
    RAW_MEDIA_RETENTION,
    AuthorizeBecomeMediaCommand,
    AuthorizedBecomeMediaIntake,
    BecomeMediaEvidence,
    DownloadedBecomeMedia,
    PersistBecomeRawMediaCommand,
    PersistedBecomeRawMedia,
    ProcessBecomeMediaCommand,
    ProcessBecomeMediaService,
    is_become_media_update,
    media_evidence,
)


def _update(kind: str = "voice", *, size: int = 1234) -> dict[str, object]:
    media: dict[str, object] = {
        "file_id": "provider-file-private",
        "file_unique_id": "provider-file-unique-private",
        "duration": 17,
        "file_size": size,
        "mime_type": "audio/ogg" if kind == "voice" else "video/mp4",
    }
    if kind == "video":
        media.update({"width": 720, "height": 1280})
    return {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "chat": {"id": -1007002, "type": "supergroup"},
            "from": {"id": 700000001, "is_bot": False},
            kind: media,
        },
    }


class FakePresence:
    def __init__(self, count: int | None = 2) -> None:
        self.count = count

    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None:
        assert installation_ref == "installation-become-fixture"
        assert provider_dialog_id == "-1007002"
        return self.count


class FakeDownloader:
    def __init__(self, data: bytes = b"bounded-media") -> None:
        self.data = data
        self.calls = 0

    async def download(
        self,
        *,
        evidence: BecomeMediaEvidence,
    ) -> DownloadedBecomeMedia:
        self.calls += 1
        return DownloadedBecomeMedia(
            data=self.data,
            media_type=(
                "audio/ogg"
                if evidence.kind.value == "voice"
                else "video/mp4"
            ),
        )


class FakeStore:
    def __init__(self, *, consent_ready: bool = True) -> None:
        self.consent_ready = consent_ready
        self.archived = False
        self.authorizations: list[AuthorizeBecomeMediaCommand] = []
        self.raw: list[PersistBecomeRawMediaCommand] = []

    async def installation_ref(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
    ) -> str | None:
        assert workspace_key == "mark-personal"
        assert provider_dialog_id == "-1007002"
        return "installation-become-fixture"

    async def authorize(
        self,
        command: AuthorizeBecomeMediaCommand,
    ) -> AuthorizedBecomeMediaIntake:
        if not self.consent_ready:
            raise BecomeControlledIntakeError(
                "become_media_executed_consent_required"
            )
        self.authorizations.append(command)
        return AuthorizedBecomeMediaIntake(
            intake_id="11111111-1111-1111-1111-111111111111",
            intake_key="become_media_fixture",
            duplicate=len(self.authorizations) > 1,
            archived=self.archived,
        )

    async def persist_raw(
        self,
        command: PersistBecomeRawMediaCommand,
    ) -> PersistedBecomeRawMedia:
        self.raw.append(command)
        duplicate = self.archived
        self.archived = True
        return PersistedBecomeRawMedia(
            intake_id=command.intake_id,
            duplicate=duplicate,
            size_bytes=len(command.data),
        )


def _service(
    *,
    store: FakeStore | None = None,
    presence: FakePresence | None = None,
    downloader: FakeDownloader | None = None,
) -> tuple[
    ProcessBecomeMediaService,
    FakeStore,
    FakeDownloader,
]:
    actual_store = store or FakeStore()
    actual_downloader = downloader or FakeDownloader()
    return (
        ProcessBecomeMediaService(
            store=actual_store,
            downloader=actual_downloader,
            presence=presence or FakePresence(),
            subject_pepper="private-become-pepper",
        ),
        actual_store,
        actual_downloader,
    )


def _command(kind: str = "voice", *, size: int = 1234) -> ProcessBecomeMediaCommand:
    return ProcessBecomeMediaCommand(
        workspace_key="mark-personal",
        source_receipt_id="22222222-2222-2222-2222-222222222222",
        update=_update(kind, size=size),
        received_at=datetime(2026, 8, 2, 8, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize("kind", ["voice", "video"])
def test_real_group_media_is_archived_after_executed_consent(kind: str) -> None:
    service, store, downloader = _service()

    result = asyncio.run(service.execute(_command(kind)))

    assert result.archived is True
    assert result.kind.value == kind
    assert result.size_bytes == len(b"bounded-media")
    assert downloader.calls == 1
    assert len(store.authorizations) == 1
    authorization = store.authorizations[0]
    assert authorization.raw_expires_at - authorization.received_at == (
        RAW_MEDIA_RETENTION
    )
    assert authorization.file_unique_digest == hashlib.sha256(
        b"provider-file-unique-private"
    ).hexdigest()
    assert "provider-file" not in repr(authorization)
    assert len(store.raw) == 1


def test_exact_archived_replay_does_not_download_again() -> None:
    service, _store, downloader = _service()
    first = asyncio.run(service.execute(_command()))
    second = asyncio.run(service.execute(_command()))

    assert first.archived is True
    assert second.duplicate is True
    assert second.archived is True
    assert downloader.calls == 1


def test_missing_executed_consent_fails_before_download() -> None:
    service, _store, downloader = _service(
        store=FakeStore(consent_ready=False)
    )

    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_executed_consent_required",
    ):
        asyncio.run(service.execute(_command()))

    assert downloader.calls == 0


def test_owner_only_presence_is_rechecked_before_authorization() -> None:
    service, store, downloader = _service(presence=FakePresence(3))

    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_owner_only_required",
    ):
        asyncio.run(service.execute(_command()))

    assert store.authorizations == []
    assert downloader.calls == 0


def test_telegram_download_limit_fails_closed() -> None:
    service, store, downloader = _service()

    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_download_limit_exceeded",
    ):
        asyncio.run(service.execute(_command(size=20_000_001)))

    assert store.authorizations == []
    assert downloader.calls == 0


def test_media_shape_rejects_ambiguous_or_nonhuman_updates() -> None:
    ambiguous = _update()
    message = ambiguous["message"]
    assert isinstance(message, dict)
    message["video"] = {
        "file_id": "other",
        "file_unique_id": "other-unique",
        "duration": 1,
    }
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_kind_ambiguous",
    ):
        media_evidence(ambiguous)

    bot_update = _update()
    bot_message = bot_update["message"]
    assert isinstance(bot_message, dict)
    bot_message["from"] = {"id": 1, "is_bot": True}
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_media_human_required",
    ):
        media_evidence(bot_update)


def test_media_detection_and_retention_contract_are_exact() -> None:
    assert is_become_media_update(_update("voice")) is True
    assert is_become_media_update(_update("video")) is True
    assert RAW_MEDIA_RETENTION.days == 7
    assert CONFIRMED_TRANSCRIPT_RETENTION.days == 30

"""Metadata-only publisher using the existing GitHub App installation."""

import httpx

from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError

APP_WRITER = "markmalina-n8n-upload[bot]"
LEGACY_WRITER = "github-actions[bot]"
REPOSITORY = "markmalina/mark"


class AppInboxClient(MarkInboxClient):
    """Issue writes use the App; uncertain older Actions writes remain reconcilable."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        super().__init__(http, token, APP_WRITER)
        self.legacy = MarkInboxClient(http, token, LEGACY_WRITER)

    async def preflight(self) -> None:
        repo = await self._get(f"/repos/{REPOSITORY}")
        if (not isinstance(repo, dict) or repo.get("full_name") != REPOSITORY
                or repo.get("id") != 1336157811 or repo.get("private") is not True
                or repo.get("archived") is not False or repo.get("has_issues") is not True
                or not isinstance(repo.get("owner"), dict)
                or repo["owner"].get("id") != 23397294):
            raise WatchVoiceError("github_private_target_mismatch")

    async def reconcile(self, *, part: int, issue_number: int | None, body: str):
        app_receipt = await super().reconcile(part=part, issue_number=issue_number, body=body)
        legacy_receipt = await self.legacy.reconcile(part=part, issue_number=issue_number, body=body)
        if app_receipt is not None and legacy_receipt is not None:
            raise WatchVoiceError("github_duplicate_receipts")
        return app_receipt or legacy_receipt

"""Watch publisher using a repository-scoped GitHub App token."""

import httpx

from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError

LEGACY_WRITER = "github-actions[bot]"
REPOSITORY = "markmalina/mark"


class AppInboxClient(MarkInboxClient):
    """The App writes issues and comments; older issue writes remain reconcilable."""

    def __init__(self, http: httpx.AsyncClient, token: str, *, app_slug: str) -> None:
        super().__init__(
            http,
            token,
            f"{app_slug}[bot]",
            legacy_writer=LEGACY_WRITER,
            reconciliation_pages=8,
        )

    async def preflight(self) -> None:
        repo = await self._get(f"/repos/{REPOSITORY}")
        if (
            not isinstance(repo, dict)
            or repo.get("full_name") != REPOSITORY
            or repo.get("id") != 1336157811
            or repo.get("private") is not True
            or repo.get("archived") is not False
            or repo.get("has_issues") is not True
            or not isinstance(repo.get("owner"), dict)
            or repo["owner"].get("id") != 23397294
        ):
            raise WatchVoiceError("github_private_target_mismatch")

    async def verify_early_issue(self, number: int, body: str) -> None:
        issue = await self._get(f"/repos/{REPOSITORY}/issues/{number}")
        if not isinstance(issue, dict) or issue.get("number") != number:
            raise WatchVoiceError("github_early_issue_invalid")
        try:
            self.receipt(
                issue, part=0, issue_number=None, expected_body=body, reconciliation=True
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise WatchVoiceError("github_early_issue_mismatch") from None

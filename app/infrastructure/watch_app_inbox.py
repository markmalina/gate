"""Metadata-only publisher using a repository-scoped GitHub App token."""

import httpx

from app.infrastructure.watch_voice_github import API, MarkInboxClient, WatchVoiceError, marker

LEGACY_WRITER = "github-actions[bot]"
REPOSITORY = "markmalina/mark"


class AppInboxClient(MarkInboxClient):
    """Issue writes use the App; uncertain older Actions writes remain reconcilable."""

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


    async def watch_issue_body(self, number: int, event_id: str) -> str:
        issue = await self._get(f"/repos/{REPOSITORY}/issues/{number}")
        body = issue.get("body") if isinstance(issue, dict) else None
        user = issue.get("user") if isinstance(issue, dict) else None
        if (
            not isinstance(body, str)
            or not isinstance(user, dict)
            or user.get("login") not in (self.writer, self.legacy_writer)
            or issue.get("number") != number
            or issue.get("html_url") != f"https://github.com/{REPOSITORY}/issues/{number}"
            or "pull_request" in issue
            or body.split("\n").count(marker(event_id, 0)) != 1
        ):
            raise WatchVoiceError("github_watch_issue_mismatch")
        return body

    async def update_watch_issue(self, number: int, body: str) -> None:
        try:
            response = await self.http.patch(
                f"{API}/repos/{REPOSITORY}/issues/{number}",
                headers=self.headers, json={"body": body}, follow_redirects=False,
            )
        except httpx.HTTPError:
            raise WatchVoiceError("github_watch_patch_unknown", unknown=True) from None
        if response.status_code != 200:
            raise WatchVoiceError("github_watch_patch_deferred")


    async def watch_issue_comments(self, number: int) -> list[str]:
        path = f"/repos/{REPOSITORY}/issues/{number}/comments?per_page=100"
        url = f"https://github.com/{REPOSITORY}/issues/{number}#issuecomment-"
        bodies: list[str] = []
        for page in range(1, self.reconciliation_pages + 1):
            values = await self._get(f"{path}&page={page}")
            if not isinstance(values, list):
                raise WatchVoiceError("github_watch_comment_read_invalid")
            for comment in values:
                if not isinstance(comment, dict) or not isinstance(comment.get("user"), dict):
                    raise WatchVoiceError("github_watch_comment_read_invalid")
                if comment["user"].get("login") not in (self.writer, self.legacy_writer):
                    continue
                body, location = comment.get("body"), comment.get("html_url")
                if (
                    not isinstance(body, str)
                    or not isinstance(location, str)
                    or not location.startswith(url)
                ):
                    raise WatchVoiceError("github_watch_comment_read_invalid")
                bodies.append(body)
            if len(values) < 100:
                return bodies
        raise WatchVoiceError("github_reconciliation_page_limit")

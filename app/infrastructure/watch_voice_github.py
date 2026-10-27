"""Fixed-private-target GitHub publisher; unknown POSTs are read-reconciled only."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.infrastructure.watch_voice_journal import pulse

REPOSITORY = "markmalina/mark"
API = "https://api.github.com"
MAX_BODY_BYTES = 24_000


class WatchVoiceError(RuntimeError):
    def __init__(self, code: str, *, unknown: bool = False) -> None:
        self.code = code
        self.unknown = unknown
        super().__init__(code)


@dataclass(frozen=True)
class GithubReceipt:
    number: int
    url: str
    received_at: datetime


def transcript_parts(text: str) -> list[str]:
    """UTF-8 bounded parts preserve every source character."""
    chunks: list[str] = []
    part: list[str] = []
    size = 0
    for char in text:
        length = len(char.encode("utf-8"))
        if size + length > MAX_BODY_BYTES:
            chunks.append("".join(part))
            part, size = [], 0
        part.append(char)
        size += length
    if part or not chunks:
        chunks.append("".join(part))
    return chunks


def marker(event_id: str, part: int) -> str:
    return f"<!-- gate-inbox:v1 event_id={event_id} part={part} -->"


class MarkInboxClient:
    def __init__(self, http: httpx.AsyncClient, token: str, writer: str) -> None:
        self.http = http
        self.writer = writer
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str) -> Any:
        await pulse("github_read", seconds=90)
        try:
            response = await self.http.get(API + path, headers=self.headers, follow_redirects=False)
        except httpx.HTTPError:
            raise WatchVoiceError("github_read_unavailable") from None
        if response.status_code != 200:
            raise WatchVoiceError("github_read_rejected")
        try:
            return response.json()
        except ValueError:
            raise WatchVoiceError("github_read_invalid") from None

    async def preflight(self) -> None:
        repo = await self._get(f"/repos/{REPOSITORY}")
        user = await self._get("/user")
        if not isinstance(repo, dict) or not isinstance(user, dict):
            raise WatchVoiceError("github_identity_invalid")
        if (
            repo.get("full_name") != REPOSITORY
            or repo.get("private") is not True
            or repo.get("archived") is True
            or repo.get("has_issues") is not True
            or user.get("login") != self.writer
        ):
            raise WatchVoiceError("github_private_target_or_writer_mismatch")
        # Read preflight proves scope/privacy, not token write permission.
        # The exact authorized POST and its receipt prove the write capability.

    async def post(
        self, *, part: int, issue_number: int | None, title: str, body: str
    ) -> GithubReceipt:
        await pulse("github_write", seconds=90)
        path = f"/repos/{REPOSITORY}/issues"
        payload: dict[str, str] = {"title": title, "body": body}
        if part:
            if issue_number is None:
                raise WatchVoiceError("github_issue_receipt_missing")
            path += f"/{issue_number}/comments"
            payload = {"body": body}
        try:
            response = await self.http.post(
                API + path, headers=self.headers, json=payload, follow_redirects=False
            )
        except httpx.HTTPError:
            raise WatchVoiceError("github_post_unknown", unknown=True) from None
        if response.status_code != 201:
            definite = response.status_code in {400, 401, 403, 404, 410, 422, 429}
            raise WatchVoiceError(
                "github_post_rejected" if definite else "github_post_unknown", unknown=not definite
            )
        try:
            obj = response.json()
            return self.receipt(obj, part=part, issue_number=issue_number, expected_body=body)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise WatchVoiceError("github_receipt_unknown", unknown=True) from None

    def receipt(
        self, obj: dict[str, Any], *, part: int, issue_number: int | None, expected_body: str,
        expected_writer: str | None = None,
    ) -> GithubReceipt:
        number = issue_number if part else obj["number"]
        expected_url = f"https://github.com/{REPOSITORY}/issues/{number}"
        valid_url = (
            obj["html_url"].startswith(expected_url + "#issuecomment-")
            if part
            else obj["html_url"] == expected_url
        )
        # Verify exact body and writer, not a forgeable marker alone.
        if (
            type(number) is not int
            or number <= 0
            or not valid_url
            or obj.get("user", {}).get("login") != (
                self.writer if expected_writer is None else expected_writer
            )
            or obj.get("body") != expected_body
            or "pull_request" in obj
        ):
            raise ValueError("receipt mismatch")
        received_at = datetime.fromisoformat(obj["created_at"].replace("Z", "+00:00"))
        if received_at.tzinfo is None:
            raise ValueError("receipt time missing")
        return GithubReceipt(number, expected_url, received_at)

    async def reconcile(
        self, *, part: int, issue_number: int | None, body: str,
        expected_writer: str | None = None,
    ) -> GithubReceipt | None:
        path = f"/repos/{REPOSITORY}/issues?state=all&per_page=100"
        if part:
            if issue_number is None:
                raise WatchVoiceError("github_issue_receipt_missing")
            path = f"/repos/{REPOSITORY}/issues/{issue_number}/comments?per_page=100"
        found: list[GithubReceipt] = []
        for page in range(1, 101):
            objects = await self._get(path + f"&page={page}")
            if not isinstance(objects, list):
                raise WatchVoiceError("github_reconciliation_invalid")
            for obj in objects:
                if not isinstance(obj, dict) or obj.get("body") != body:
                    continue
                try:
                    found.append(
                        self.receipt(obj, part=part, issue_number=issue_number, expected_body=body,
                                     expected_writer=expected_writer)
                    )
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            if len(objects) < 100:
                break
        else:
            raise WatchVoiceError("github_reconciliation_page_limit")
        if len(found) > 1:
            raise WatchVoiceError("github_duplicate_receipts")
        return found[0] if found else None


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


import asyncio
import unittest

import httpx
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_github import WatchVoiceError


def run_case(authors):
    def handler(req):
        if req.url.path == "/repos/markmalina/mark":
            return httpx.Response(
                200,
                json={
                    "id": 1336157811,
                    "full_name": "markmalina/mark",
                    "private": True,
                    "archived": False,
                    "has_issues": True,
                    "owner": {"id": 23397294},
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "number": i + 1,
                    "html_url": f"https://github.com/markmalina/mark/issues/{i + 1}",
                    "user": {"login": author},
                    "body": "same body",
                    "created_at": "2026-11-15T00:00:00Z",
                }
                for i, author in enumerate(authors)
            ],
        )

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AppInboxClient(http, "fake-token")
            await client.preflight()
            return await client.reconcile(part=0, issue_number=None, body="same body")

    return asyncio.run(call())


class TestAppInbox(unittest.TestCase):
    def test_reconcile_old_and_new_writer(self):
        for author in ("github-actions[bot]", "markmalina-n8n-upload[bot]"):
            with self.subTest(author=author):
                self.assertEqual(run_case([author]).number, 1)

    def test_duplicate_across_writers_stops(self):
        with self.assertRaises(WatchVoiceError):
            run_case(["github-actions[bot]", "markmalina-n8n-upload[bot]"])


if __name__ == "__main__":
    unittest.main()

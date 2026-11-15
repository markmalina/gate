import asyncio

import httpx
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from watch_app_auth import AppAuthError, issue_mark_token


def private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def run_case(*, wrong_owner=False, broad=False, wrong_repo=False):
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/app/installations/179245428"):
            return httpx.Response(200, json={"id": 179245428, "app_id": 5641721,
                "app_slug": "markmalina-n8n-upload", "account": {"id": 1 if wrong_owner else 23397294},
                "suspended_at": None})
        if req.url.path.endswith("/access_tokens"):
            import json
            payload = json.loads(req.content)
            assert payload == {"repository_ids": [1336157811], "permissions": {"issues": "write"}}
            perms = {"issues": "write", "metadata": "read"}
            if broad:
                perms["contents"] = "write"
            return httpx.Response(201, json={"token": "secret-placeholder-1234567890", "permissions": perms})
        return httpx.Response(200, json={"id": 1 if wrong_repo else 1336157811,
            "private": True, "archived": False, "has_issues": True, "owner": {"id": 23397294}})

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await issue_mark_token(http, private_key())
    return asyncio.run(call()), calls


class TestAppAuth(unittest.TestCase):
    def test_scoped_token_and_target(self):
        token, calls = run_case()
        self.assertEqual(token, "secret-placeholder-1234567890")
        self.assertEqual(len(calls), 3)

    def test_reject_invalid_identity_scope_or_target(self):
        for options in ({"wrong_owner": True}, {"broad": True}, {"wrong_repo": True}):
            with self.subTest(options=options), self.assertRaises(AppAuthError):
                run_case(**options)


if __name__ == "__main__":
    unittest.main()

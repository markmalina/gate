import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w2.entry_adapter import (  # noqa: E402
    BlindRetryForbidden,
    ConsentRequired,
    FakeRelations,
    GateEntryAdapter,
    ReplayMutation,
    sign_synthetic_webhook,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "proof-loop" / "v2" / "fixtures" / "gate-entry-w2.json"


class GateEntryAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        relations = FakeRelations(
            {
                "consent_receipt_allowed_fixture": "ALLOWED",
                "consent_receipt_denied_fixture": "DENIED",
            }
        )
        self.adapter = GateEntryAdapter(Path(self.tmp.name) / "entry.sqlite", relations)
        self.adapter.bind_bot(
            "binding_become_fixture",
            "bot_become_fixture",
            "profile_link_become_fixture",
            "entry_subject_become_fixture",
        )
        self.fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def tearDown(self):
        self.adapter.close()
        self.tmp.cleanup()

    def _ingest(self, fixture):
        payload = fixture["payload"]
        self.assertEqual(fixture["signature"], sign_synthetic_webhook(payload))
        return self.adapter.ingest_signed_entry(
            fixture["webhook_ref"],
            fixture["channel"],
            fixture["binding_ref"],
            payload,
            fixture["signature"],
        )

    def test_two_signed_entry_fixtures_create_one_intent(self):
        first = self._ingest(self.fixtures["entries"][0])
        second = self._ingest(self.fixtures["entries"][1])
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.intent_ref, second.intent_ref)
        self.assertEqual(self.adapter.counts()["become_entry_intent"], 1)
        self.assertEqual(self.adapter.counts()["become_provider_webhook"], 2)

    def test_exact_webhook_replay_is_noop_and_mutation_fails(self):
        fixture = self.fixtures["entries"][0]
        first = self._ingest(fixture)
        second = self._ingest(fixture)
        self.assertEqual(first.intent_ref, second.intent_ref)
        self.assertFalse(second.created)
        changed = {**fixture["payload"], "touchpoint_ref": "touchpoint_changed_fixture"}
        with self.assertRaises(ReplayMutation):
            self.adapter.ingest_signed_entry(
                fixture["webhook_ref"],
                fixture["channel"],
                fixture["binding_ref"],
                changed,
                sign_synthetic_webhook(changed),
            )

    def test_invalid_signature_and_sensitive_payload_fail_closed(self):
        fixture = self.fixtures["entries"][0]
        with self.assertRaises(ValueError):
            self.adapter.ingest_signed_entry(
                fixture["webhook_ref"],
                fixture["channel"],
                fixture["binding_ref"],
                fixture["payload"],
                "0" * 64,
            )
        for field in ("provider_user_id", "signed_url", "transcript_text", "media_bytes"):
            payload = {**fixture["payload"], field: "forbidden"}
            with self.subTest(field=field), self.assertRaises(ValueError):
                sign_synthetic_webhook(payload)

    def test_controlled_instagram_reply_is_local_claim_and_unknown_has_no_retry(self):
        fixture = self.fixtures["entries"][1]
        self._ingest(fixture)
        claim = self.adapter.reserve_private_reply(fixture["webhook_ref"])
        self.assertTrue(claim.startswith("reply_claim_"))
        self.adapter.record_provider_state(fixture["webhook_ref"], "unknown")
        with self.assertRaises(BlindRetryForbidden):
            self.adapter.reserve_private_reply(fixture["webhook_ref"])

    def test_no_consent_means_no_download_claim(self):
        intent = self._ingest(self.fixtures["entries"][0]).intent_ref
        with self.assertRaises(ConsentRequired):
            self.adapter.preflight_voice_media_download(
                "download_request_missing_fixture", intent, None
            )
        denied = self.adapter.preflight_voice_media_download(
            "download_request_denied_fixture", intent, "consent_receipt_denied_fixture"
        )
        self.assertFalse(denied.download_permitted)
        with self.assertRaises(ConsentRequired):
            self.adapter.claim_download(denied.request_ref)

    def test_allowed_consent_returns_opaque_claim_without_bytes_or_url(self):
        intent = self._ingest(self.fixtures["entries"][0]).intent_ref
        receipt = self.adapter.preflight_voice_media_download(
            "download_request_allowed_fixture", intent, "consent_receipt_allowed_fixture"
        )
        self.assertTrue(receipt.download_permitted)
        claim = self.adapter.claim_download(receipt.request_ref)
        self.assertTrue(claim.startswith("download_claim_"))
        self.assertNotIn("http", claim)


if __name__ == "__main__":
    unittest.main()

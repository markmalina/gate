import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w3.voice_text_intake import (  # noqa: E402
    BlindRetryForbidden,
    QuarantineBlocked,
    ReplayMutation,
    VoiceTextIntakeStore,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts" / "proof-loop" / "v3" / "fixtures" / "gate-intake-w3.json"


class VoiceTextIntakeStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = VoiceTextIntakeStore(Path(self.tmp.name) / "intake.sqlite")
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _voice(self):
        voice = self.fixture["voice"]
        return self.store.record_voice(**voice)

    def test_text_persists_digest_not_content(self):
        receipt = self.store.record_text(**self.fixture["text"])
        self.assertEqual(len(receipt.content_sha256), 64)
        self.assertIsNone(receipt.asset_ref)
        self.assertEqual(self.store.counts(), {"intakes": 1, "assets": 0})

    def test_voice_exact_replay_is_noop_and_mutation_rejected(self):
        first = self._voice()
        second = self._voice()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.asset_ref, second.asset_ref)
        voice = {**self.fixture["voice"], "telegram_file_ref": "telegram_file_changed_fixture"}
        with self.assertRaises(ReplayMutation):
            self.store.record_voice(**voice)

    def test_quarantine_must_release_before_take_request(self):
        asset_ref = self._voice().asset_ref
        with self.assertRaises(QuarantineBlocked):
            self.store.make_take_request(asset_ref)
        self.store.decide_quarantine(asset_ref, "released")
        request = self.store.make_take_request(asset_ref)
        self.assertEqual(request["transcriber"], "TAKE")
        self.assertNotIn("telegram_file_ref", request)
        self.assertNotIn("bytes", json.dumps(request))

    def test_unknown_state_forbids_blind_retry(self):
        asset_ref = self._voice().asset_ref
        self.store.decide_quarantine(asset_ref, "released")
        self.store.record_provider_state(asset_ref, "unknown")
        with self.assertRaises(BlindRetryForbidden):
            self.store.make_take_request(asset_ref)

    def test_raw_media_and_non_synthetic_text_fail_closed(self):
        with self.assertRaises(ValueError):
            self.store.record_text("intake_bad", "quest_fixture", "real-looking text")
        voice = {**self.fixture["voice"], "telegram_file_ref": "not_opaque"}
        with self.assertRaises(ValueError):
            self.store.record_voice(**voice)


if __name__ == "__main__":
    unittest.main()

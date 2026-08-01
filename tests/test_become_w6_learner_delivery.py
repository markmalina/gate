import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w6.learner_delivery import (  # noqa: E402
    BlindRetryForbidden,
    LearnerDeliveryStore,
    ReplayMutation,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts" / "proof-loop" / "v6" / "fixtures" / "gate-delivery-w6.json"
T0 = datetime(2026, 8, 1, 5, 1, tzinfo=UTC)


class LearnerDeliveryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LearnerDeliveryStore(Path(self.tmp.name) / "delivery.sqlite")
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_exercise_delivery_is_exact_and_duplicate_safe(self):
        one = self.store.reserve(self.fixture["exercise_delivery"], T0)
        two = self.store.reserve(self.fixture["exercise_delivery"], T0)
        self.assertTrue(one.created)
        self.assertFalse(two.created)
        self.assertEqual(
            one.delivery_receipt_sha256, self.fixture["expected_exercise_delivery_sha256"]
        )
        self.assertEqual(self.store.counts()["deliveries"], 1)

    def test_mutated_version_replay_fails_closed(self):
        self.store.reserve(self.fixture["exercise_delivery"], T0)
        with self.assertRaises(ReplayMutation):
            self.store.reserve(
                {
                    **self.fixture["exercise_delivery"],
                    "exercise_version_ref": "exercise_version_changed_fixture",
                },
                T0,
            )

    def test_result_receipt_delivery_is_independent(self):
        claim = self.store.reserve(self.fixture["result_delivery"], T0)
        self.store.begin_mock(claim.delivery_ref)
        self.assertTrue(
            self.store.settle_mock(claim.delivery_ref).startswith("delivery_mock_receipt_")
        )

    def test_non_opaque_or_extra_invoice_field_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.reserve(
                {**self.fixture["exercise_delivery"], "invoice_ref": "invoice_forbidden"}, T0
            )
        with self.assertRaises(ValueError):
            self.store.reserve(
                {**self.fixture["exercise_delivery"], "learner_ref": "person@example.test"}, T0
            )

    def test_unknown_requires_reconciliation_and_forbids_blind_retry(self):
        claim = self.store.reserve(self.fixture["exercise_delivery"], T0)
        self.store.begin_mock(claim.delivery_ref)
        self.store.mark_unknown(claim.delivery_ref, T0)
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim.delivery_ref)
        self.store.reconcile(
            claim.delivery_ref, "reconciliation_receipt_delivery_fixture", True, T0
        )


if __name__ == "__main__":
    unittest.main()

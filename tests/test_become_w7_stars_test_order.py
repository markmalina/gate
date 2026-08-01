import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w7.stars_test_order import (  # noqa: E402
    BlindRetryForbidden,
    ReplayMutation,
    StarsTestOrderStore,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts" / "proof-loop" / "v7" / "fixtures" / "gate-stars-test-w7.json"
T0 = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)


class StarsTestOrderStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StarsTestOrderStore(Path(self.tmp.name) / "stars.sqlite")
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _reserve(self):
        return self.store.reserve_order(self.fixture["order"], T0)

    def _approve(self):
        claim = self._reserve()
        self.store.enqueue_precheckout(claim.order_ref, self.fixture["precheckout_query_ref"], T0)
        self.assertEqual(self.store.resolve_precheckout(self.fixture["precheckout_query_ref"], True, T0), "approved")
        return claim

    def test_exact_pre_authorized_one_xtr_test_order(self):
        one = self._reserve()
        two = self._reserve()
        self.assertTrue(one.created)
        self.assertFalse(two.created)
        self.assertEqual(one.order_receipt_sha256, self.fixture["expected_order_receipt_sha256"])
        with self.assertRaises(ValueError):
            self.store.reserve_order({**self.fixture["order"], "amount": 2}, T0)

    def test_async_precheckout_times_out_without_become_callback(self):
        claim = self._reserve()
        self.store.enqueue_precheckout(claim.order_ref, self.fixture["precheckout_query_ref"], T0, timeout_seconds=10)
        self.assertEqual(
            self.store.resolve_precheckout(self.fixture["precheckout_query_ref"], True, T0 + timedelta(seconds=11)),
            "timed_out",
        )

    def test_successful_payment_duplicate_webhook_is_noop_and_mutation_fails(self):
        claim = self._approve()
        args = (self.fixture["webhook_ref"], claim.order_ref, self.fixture["successful_payment_ref"], T0)
        self.assertTrue(self.store.record_successful_payment(*args))
        self.assertFalse(self.store.record_successful_payment(*args))
        with self.assertRaises(ReplayMutation):
            self.store.record_successful_payment(
                self.fixture["webhook_ref"], claim.order_ref, "successful_payment_test_changed_fixture", T0
            )
        self.assertEqual(self.store.counts()["webhooks"], 1)

    def test_lost_payment_response_becomes_unknown_and_never_blindly_retries(self):
        claim = self._approve()
        self.store.mark_payment_unknown(claim.order_ref, T0)
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim.order_ref, "payment")
        self.store.reconcile(claim.order_ref, "payment", "reconciliation_receipt_payment_fixture", True, T0)

    def test_refund_support_unknown_requires_reconciliation(self):
        claim = self._approve()
        self.store.record_successful_payment(
            self.fixture["webhook_ref"], claim.order_ref, self.fixture["successful_payment_ref"], T0
        )
        self.store.reserve_refund(self.fixture["refund_ref"], self.fixture["support_ref"], claim.order_ref, T0)
        self.store.begin_refund_mock(self.fixture["refund_ref"])
        self.store.mark_refund_unknown(self.fixture["refund_ref"], T0)
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(self.fixture["refund_ref"], "refund")
        self.store.reconcile(
            self.fixture["refund_ref"], "refund", "reconciliation_receipt_refund_fixture", True, T0
        )


if __name__ == "__main__":
    unittest.main()

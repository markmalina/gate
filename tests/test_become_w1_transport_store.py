import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w1.transport_store import (  # noqa: E402
    FROZEN_DIGEST,
    BlindRetryForbidden,
    GateTransportStore,
    ReplayMutation,
)

T0 = datetime(2026, 8, 1, 1, 30, tzinfo=UTC)


class GateTransportStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "transport.sqlite"
        self.store = GateTransportStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_duplicate_callback_has_at_most_one_effect_claim(self):
        payload = {
            "mode": "SIMULATED",
            "frozen_digest": FROZEN_DIGEST,
            "touchpoint_ref": "touchpoint_fixture_a",
        }
        first, created = self.store.record_callback("callback_fixture_a", payload, T0)
        second, duplicate_created = self.store.record_callback("callback_fixture_a", payload, T0)
        self.assertEqual(first, second)
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(
            self.store.counts(),
            {
                "become_transport_inbox": 1,
                "become_effect_claim": 1,
                "become_transport_outbox": 1,
                "become_reconciliation": 0,
            },
        )

    def test_mutated_replay_fails_closed(self):
        self.store.record_callback("callback_fixture_b", {"mode": "SIMULATED", "n": 1}, T0)
        with self.assertRaises(ReplayMutation):
            self.store.record_callback("callback_fixture_b", {"mode": "SIMULATED", "n": 2}, T0)

    def test_kill_restart_and_expired_lease_recovery(self):
        self.store.record_callback("callback_fixture_c", {"mode": "SIMULATED"}, T0)
        original = self.store.claim_next("worker_fixture_a", T0, lease_seconds=10)
        self.assertIsNotNone(original)
        self.store.close()  # synthetic kill after durable claim
        self.store = GateTransportStore(self.path)
        self.assertIsNone(self.store.claim_next("worker_fixture_b", T0 + timedelta(seconds=9)))
        recovered = self.store.claim_next("worker_fixture_b", T0 + timedelta(seconds=11))
        self.assertEqual(recovered.claim_ref, original.claim_ref)
        self.assertEqual(recovered.lease_owner, "worker_fixture_b")

    def test_unknown_requires_reconciliation_and_forbids_retry(self):
        claim, _ = self.store.record_callback("callback_fixture_d", {"mode": "SIMULATED"}, T0)
        self.store.claim_next("worker_fixture_a", T0)
        self.store.begin_effect(claim, "worker_fixture_a", T0)
        self.store.record_outcome(claim, "unknown", T0)
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim, T0)
        self.assertEqual(self.store.counts()["become_reconciliation"], 1)

    def test_expired_in_flight_is_unknown_not_reclaimed(self):
        claim, _ = self.store.record_callback("callback_fixture_e", {"mode": "SIMULATED"}, T0)
        self.store.claim_next("worker_fixture_a", T0, lease_seconds=10)
        self.store.begin_effect(claim, "worker_fixture_a", T0)
        self.assertEqual(self.store.reconcile_expired_in_flight(T0 + timedelta(seconds=11)), 1)
        self.assertIsNone(self.store.claim_next("worker_fixture_b", T0 + timedelta(seconds=12)))
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim, T0 + timedelta(seconds=12))

    def test_raw_or_provider_identity_fields_are_rejected(self):
        for field in ("transcript_text", "signed_url", "provider_identity", "media_bytes"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.record_callback(
                    f"callback_{field}", {"mode": "SIMULATED", field: "forbidden"}, T0
                )


if __name__ == "__main__":
    unittest.main()

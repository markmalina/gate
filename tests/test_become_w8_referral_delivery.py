import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from become_w8.referral_delivery import BlindRetryForbidden, ReferralDeliveryStore, ReplayMutation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts/proof-loop/v8/fixtures/gate-referral-w8.json"
T0 = datetime(2026, 8, 1, 4, 40, tzinfo=UTC)


class TestReferralDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "delivery.sqlite"
        self.store = ReferralDeliveryStore(self.path)
        self.fixture = json.loads(FIXTURE.read_text())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reserve(self, suffix="fixture"):
        event = json.loads(json.dumps(self.fixture["reserve"]))
        event["delivery_ref"] = f"referral_delivery_{suffix}"
        event["idempotency_key"] = f"idempotency_{suffix}"
        return event, self.store.reserve(event, T0)

    def test_exact_referrer_tuple_and_provider_free_allowlist(self):
        event, claim = self.reserve()
        result = self.store.observe(
            "provider_event_fixture",
            claim.delivery_ref,
            "delivered",
            "synthetic_provider_fixture",
            T0,
        )
        self.assertEqual(result["referrer_person_ref"], event["attribution"]["referrer_person_ref"])
        self.assertEqual(result["candidate_person_ref"], event["link"]["candidate_person_ref"])
        self.assertEqual(set(result), set(self.fixture["acceptance_allowlist"]))
        self.assertFalse(any("provider" in key or "media" in key for key in result))

    def test_reserve_and_status_replay_are_idempotent_but_mutation_fails(self):
        event, claim = self.reserve()
        self.assertFalse(self.store.reserve(event, T0).created)
        self.store.observe(
            "provider_event_fixture",
            claim.delivery_ref,
            "accepted",
            "synthetic_provider_fixture",
            T0,
        )
        self.store.observe(
            "provider_event_fixture",
            claim.delivery_ref,
            "accepted",
            "synthetic_provider_fixture",
            T0,
        )
        with self.assertRaises(ReplayMutation):
            self.store.observe(
                "provider_event_fixture",
                claim.delivery_ref,
                "delivered",
                "synthetic_provider_fixture",
                T0,
            )

    def test_link_attribution_mismatch_and_expiry_fail_closed(self):
        mismatched = json.loads(json.dumps(self.fixture["reserve"]))
        mismatched["link"]["referrer_person_ref"] = "person_other_referrer"
        with self.assertRaisesRegex(ValueError, "tuple mismatch"):
            self.store.reserve(mismatched, T0)
        expired = json.loads(json.dumps(self.fixture["reserve"]))
        expired["link"]["expires_at"] = "2026-08-01T04:00:00Z"
        with self.assertRaisesRegex(ValueError, "unexpired"):
            self.store.reserve(expired, T0)

    def test_terminal_status_cannot_regress_or_retry(self):
        _, claim = self.reserve()
        self.store.observe(
            "provider_event_delivered",
            claim.delivery_ref,
            "delivered",
            "synthetic_provider_fixture",
            T0,
        )
        with self.assertRaisesRegex(ValueError, "terminal"):
            self.store.observe(
                "provider_event_late",
                claim.delivery_ref,
                "accepted",
                "synthetic_provider_fixture",
                T0,
            )
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim.delivery_ref)

    def test_unknown_blocks_retry_and_requires_exact_reconciliation(self):
        _, claim = self.reserve("effect")
        unknown = self.store.observe(
            "provider_event_unknown",
            claim.delivery_ref,
            "unknown",
            "synthetic_provider_fixture",
            T0,
        )
        self.assertTrue(unknown["reconciliation_required"])
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim.delivery_ref)
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.reconcile(claim.delivery_ref, "reconciliation_receipt_bad", "MAYBE", T0)
        resolved = self.store.reconcile(
            claim.delivery_ref, "reconciliation_receipt_effect", "EFFECT_PROVEN", T0
        )
        self.assertEqual(
            (resolved["effect_state"], resolved["delivery_outcome"]), ("settled", "delivered")
        )
        self.assertFalse(resolved["reconciliation_required"])

    def test_reopen_is_safe_and_preserves_receipt(self):
        _, claim = self.reserve()
        expected = self.store.observe(
            "provider_event_fixture",
            claim.delivery_ref,
            "accepted",
            "synthetic_provider_fixture",
            T0,
        )
        self.store.close()
        self.store = ReferralDeliveryStore(self.path)
        self.assertEqual(self.store.projection(claim.delivery_ref), expected)

    def test_raw_provider_media_and_extra_fields_fail_closed(self):
        for key in ("provider_id", "chat_id", "message_id", "media_content", "transcript", "url"):
            event = json.loads(json.dumps(self.fixture["reserve"]))
            event[key] = "forbidden"
            with self.assertRaises(ValueError):
                self.store.reserve(event, T0)


if __name__ == "__main__":
    unittest.main()

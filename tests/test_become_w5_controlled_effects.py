import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w5.controlled_effects import (  # noqa: E402
    BlindRetryForbidden,
    ControlledEffectStore,
    MissingPermission,
    ReplayMutation,
    verify_staging_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts" / "proof-loop" / "v5" / "fixtures" / "gate-effects-w5.json"
T0 = datetime(2026, 8, 1, 4, 1, tzinfo=UTC)


class ControlledEffectStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ControlledEffectStore(Path(self.tmp.name) / "effects.sqlite")
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.store.register_grant(self.fixture["grant"], T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _reserve(self, operation="instagram_publish", suffix="publish"):
        return self.store.reserve(
            f"effect_{suffix}_fixture",
            f"idempotency_{suffix}_fixture",
            operation,
            self.fixture["grant"]["grant_ref"],
            self.fixture["staging_receipt"],
            T0,
        )

    def test_staging_receipt_and_exact_grant_are_valid(self):
        self.assertTrue(verify_staging_receipt(self.fixture["staging_receipt"]))
        one = self._reserve()
        two = self._reserve()
        self.assertTrue(one.created)
        self.assertFalse(two.created)
        self.assertEqual(self.store.counts()["effects"], 1)

    def test_mutated_effect_replay_fails_closed(self):
        self._reserve()
        with self.assertRaises(ReplayMutation):
            self.store.reserve(
                "effect_changed_fixture",
                "idempotency_publish_fixture",
                "instagram_publish",
                self.fixture["grant"]["grant_ref"],
                self.fixture["staging_receipt"],
                T0,
            )

    def test_missing_permission_is_explicit_blocker_and_no_effect(self):
        limited = {
            **self.fixture["grant"],
            "grant_ref": "mode_grant_limited_fixture",
            "operations": ["instagram_publish"],
        }
        self.store.register_grant(limited, T0)
        receipt = {**self.fixture["staging_receipt"], "mode_grant_ref": limited["grant_ref"]}
        body = {k: v for k, v in receipt.items() if k != "staging_receipt_sha256"}
        from become_w5.controlled_effects import _sha

        receipt["staging_receipt_sha256"] = _sha(body)
        with self.assertRaises(MissingPermission):
            self.store.reserve(
                "effect_pin_fixture",
                "idempotency_pin_fixture",
                "group_pin",
                limited["grant_ref"],
                receipt,
                T0,
            )
        self.assertEqual(self.store.counts(), {"effects": 0, "blockers": 1, "reconciliation": 0})

    def test_group_rights_cover_invite_welcome_and_pin(self):
        for operation in ("group_invite", "group_welcome", "group_pin"):
            claim = self._reserve(operation, operation)
            self.store.begin_mock(claim.effect_ref)
            self.assertTrue(
                self.store.settle_mock(claim.effect_ref).startswith("provider_mock_receipt_")
            )
        self.assertEqual(self.store.counts()["effects"], 3)

    def test_unknown_never_blindly_retries_and_reconciles(self):
        claim = self._reserve()
        self.store.begin_mock(claim.effect_ref)
        self.store.mark_unknown(claim.effect_ref, T0)
        with self.assertRaises(BlindRetryForbidden):
            self.store.retry(claim.effect_ref)
        self.store.reconcile(
            claim.effect_ref, "reconciliation_receipt_fixture", no_effect_proven=True, at=T0
        )


if __name__ == "__main__":
    unittest.main()

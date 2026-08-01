import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from become_w9.chaos_recovery import (  # noqa: E402
    BlindRetry,
    ChaosStore,
    FROZEN,
    Mutation,
    build_reconciliation_receipt,
    sha,
)


class TestChaos(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ChaosStore(Path(self.tempdir.name) / "chaos.sqlite")
        self.store.reserve("chaos_effect_fixture", "webhook")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _unknown(self, effect_ref: str = "chaos_effect_fixture", event_ref: str = "event_fixture") -> dict[str, str]:
        payload = {
            "mode": "SIMULATED",
            "status": "unknown",
            "receipt_ref": "mock_unknown",
        }
        self.assertTrue(self.store.ingest(event_ref, effect_ref, payload))
        return payload

    def _receipt(
        self,
        payload: dict[str, str],
        *,
        reconciliation_ref: str = "reconciliation_fixture",
        effect_ref: str = "chaos_effect_fixture",
        source_event_ref: str = "event_fixture",
        outcome: str = "NO_EFFECT_PROVEN",
        mode: str = "SIMULATED",
    ) -> dict[str, str]:
        return build_reconciliation_receipt(
            reconciliation_ref=reconciliation_ref,
            effect_ref=effect_ref,
            source_event_ref=source_event_ref,
            source_event_digest=sha(payload),
            evidence_digest=sha({"fixture": "provider_lookup", "effect": effect_ref}),
            mode=mode,
            outcome=outcome,
        )

    def test_duplicate_webhook_is_exact_replay_safe_and_mutation_fails(self) -> None:
        payload = {
            "mode": "SIMULATED",
            "status": "success",
            "receipt_ref": "mock_receipt",
        }
        self.assertTrue(self.store.ingest("event_fixture", "chaos_effect_fixture", payload))
        self.assertFalse(self.store.ingest("event_fixture", "chaos_effect_fixture", payload))
        with self.assertRaises(Mutation):
            self.store.ingest(
                "event_fixture",
                "chaos_effect_fixture",
                {**payload, "status": "failed"},
            )
        self.assertEqual(1, self.store.db.execute("SELECT count(*) FROM event").fetchone()[0])

    def test_orphan_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "orphan event"):
            self.store.ingest(
                "event_orphan",
                "chaos_effect_missing",
                {"mode": "SIMULATED", "status": "success", "receipt_ref": "mock_orphan"},
            )
        self.assertEqual(0, self.store.db.execute("SELECT count(*) FROM event").fetchone()[0])

    def test_ingest_insert_failure_rolls_back_effect_transition(self) -> None:
        self.store.db.execute(
            """
            CREATE TRIGGER fail_event BEFORE INSERT ON event
            WHEN NEW.event_ref='event_rollback_fixture'
            BEGIN SELECT RAISE(ABORT, 'synthetic event failure'); END
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingest(
                "event_rollback_fixture",
                "chaos_effect_fixture",
                {"mode": "SIMULATED", "status": "success", "receipt_ref": "mock_rollback"},
            )
        state = self.store.db.execute(
            "SELECT state FROM effect WHERE ref='chaos_effect_fixture'"
        ).fetchone()[0]
        self.assertEqual("reserved", state)
        self.assertEqual(0, self.store.db.execute("SELECT count(*) FROM event").fetchone()[0])

    def test_unknown_rejects_blind_retry_and_accepts_one_exact_receipt(self) -> None:
        payload = self._unknown()
        with self.assertRaises(BlindRetry):
            self.store.retry("chaos_effect_fixture")
        receipt = self._receipt(payload)
        self.assertTrue(self.store.reconcile(receipt))
        self.assertFalse(self.store.reconcile(receipt))
        self.assertEqual(1, self.store.db.execute("SELECT count(*) FROM reconcile").fetchone()[0])
        self.assertEqual(
            "reserved",
            self.store.db.execute(
                "SELECT state FROM effect WHERE ref='chaos_effect_fixture'"
            ).fetchone()[0],
        )

    def test_arbitrary_or_digest_mutated_reconciliation_is_rejected(self) -> None:
        payload = self._unknown()
        with self.assertRaisesRegex(ValueError, "exact typed"):
            self.store.reconcile("reconciliation_fixture")  # type: ignore[arg-type]
        receipt = self._receipt(payload)
        receipt["frozen_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "frozen digest"):
            self.store.reconcile(receipt)
        self.assertEqual(0, self.store.db.execute("SELECT count(*) FROM reconcile").fetchone()[0])

    def test_mutated_duplicate_reconciliation_ref_is_rejected(self) -> None:
        payload = self._unknown()
        receipt = self._receipt(payload)
        self.assertTrue(self.store.reconcile(receipt))
        mutated = self._receipt(payload, outcome="EFFECT_PROVEN")
        with self.assertRaises(Mutation):
            self.store.reconcile(mutated)

    def test_second_receipt_for_same_effect_is_rejected(self) -> None:
        payload = self._unknown()
        self.assertTrue(self.store.reconcile(self._receipt(payload)))
        duplicate = self._receipt(payload, reconciliation_ref="reconciliation_duplicate")
        with self.assertRaises(Mutation):
            self.store.reconcile(duplicate)

    def test_orphan_reconciliation_source_event_is_rejected(self) -> None:
        payload = self._unknown()
        orphan = self._receipt(payload, source_event_ref="event_missing")
        with self.assertRaisesRegex(ValueError, "orphan reconciliation event"):
            self.store.reconcile(orphan)
        self.assertEqual("unknown", self.store.db.execute("SELECT state FROM effect").fetchone()[0])

    def test_reconciliation_insert_failure_rolls_back_effect_transition(self) -> None:
        payload = self._unknown()
        receipt = self._receipt(payload)
        self.store.db.execute(
            """
            CREATE TRIGGER fail_reconciliation BEFORE INSERT ON reconcile
            BEGIN SELECT RAISE(ABORT, 'synthetic reconciliation failure'); END
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reconcile(receipt)
        self.assertEqual("unknown", self.store.db.execute("SELECT state FROM effect").fetchone()[0])
        self.assertEqual(0, self.store.db.execute("SELECT count(*) FROM reconcile").fetchone()[0])

    def test_test_payment_recovery_requires_bound_effect_evidence(self) -> None:
        self.store.reserve("chaos_effect_payment_fixture", "payment_test")
        payload = {"mode": "TEST", "status": "unknown", "receipt_ref": "mock_test_lost"}
        self.store.ingest("event_payment_fixture", "chaos_effect_payment_fixture", payload)
        receipt = self._receipt(
            payload,
            reconciliation_ref="reconciliation_payment_fixture",
            effect_ref="chaos_effect_payment_fixture",
            source_event_ref="event_payment_fixture",
            outcome="EFFECT_PROVEN",
            mode="TEST",
        )
        self.assertTrue(self.store.reconcile(receipt))
        state = self.store.db.execute(
            "SELECT state FROM effect WHERE ref='chaos_effect_payment_fixture'"
        ).fetchone()[0]
        self.assertEqual("settled", state)

    def test_mode_is_bound_to_effect_kind_and_source_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind and mode"):
            self.store.ingest(
                "event_wrong_mode",
                "chaos_effect_fixture",
                {"mode": "TEST", "status": "unknown", "receipt_ref": "mock_wrong_mode"},
            )

    def test_receipt_is_bound_to_current_frozen_digest(self) -> None:
        payload = self._unknown()
        receipt = self._receipt(payload)
        self.assertEqual(FROZEN, receipt["frozen_digest"])
        receipt["source_event_digest"] = "f" * 64
        receipt["receipt_digest"] = sha({key: value for key, value in receipt.items() if key != "receipt_digest"})
        with self.assertRaisesRegex(ValueError, "source evidence mismatch"):
            self.store.reconcile(receipt)

    def test_deploy_parity_evidence(self) -> None:
        config = {"mode": "SIMULATED", "retry": "reconcile_only", "digest": FROZEN}
        self.assertTrue(self.store.deploy_parity(config, dict(config)))
        self.assertFalse(self.store.deploy_parity(config, {**config, "retry": "blind"}))


if __name__ == "__main__":
    unittest.main()

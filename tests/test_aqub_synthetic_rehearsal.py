"""Executable synthetic crash matrix for the AQUB -> Gate provider boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.application.aqub_synthetic_rehearsal import (
    SyntheticCrash,
    SyntheticGateError,
    SyntheticGateMode,
    SyntheticGateRehearsal,
)

SECRET = "work-i-test-only-hmac-secret-0000000000000000"
NOW = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)


def command(label: str) -> dict[str, object]:
    suffix = hashlib.sha256(label.encode()).hexdigest()[:12]
    return {
        "schema_version": "aqub.delivery-command.v2",
        "command_id": f"40000000-0000-4000-8000-{suffix}",
        "idempotency_key": f"work-i/{label}/synthetic",
        "installation_ref": "installation:synthetic_installation_01",
        "cube_ref": "cube:synthetic_food_cube_01",
        "route_generation": 1,
        "conversation_ref": "conversation:synthetic_conversation_01",
        "requested_at": "2026-08-19T18:30:00.000Z",
        "payload": {
            "delivery_type": "food.fact_reply",
            "kind": "text",
            "media_type": "text/plain",
            "text": f"Synthetic Food result {label}",
            "reply_to_message_ref": "message:synthetic_message_01",
        },
    }


def raw(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def service(
    path: Path,
    *,
    route_state: dict[str, bool] | None = None,
    checkpoint=lambda _name, _value: None,
) -> SyntheticGateRehearsal:
    state = route_state or {"active": True}
    return SyntheticGateRehearsal(
        database_path=path,
        secret=SECRET,
        mode=SyntheticGateMode.REHEARSAL,
        route_active=lambda _command: state["active"],
        checkpoint=checkpoint,
        clock=lambda: NOW,
    )


class SyntheticGateCrashMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gate-work-i.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_machine_readable_manifest_and_shared_fixture_digest(self) -> None:
        root = Path(__file__).parents[1]
        manifest = json.loads(
            (
                root
                / "contracts/aqub/v2/rehearsal/food-synthetic-rehearsal-manifest.v1.json"
            ).read_text()
        )
        fixture = (root / "contracts/aqub/v2/rehearsal/food-synthetic-command.v1.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(fixture).hexdigest(), manifest["shared_fixture"]["sha256"]
        )
        self.assertEqual(
            manifest["exact_dependencies"]["aqub_work_h"],
            "b9448e247207f9d0db159fe142857bc80db48b20",
        )
        self.assertEqual(
            manifest["exact_dependencies"]["gate_work_h"],
            "74c572f73fb6d78756d125f08f66af7a4adb2eeb",
        )
        self.assertEqual(len(manifest["crash_matrix"]), 8)
        self.assertTrue(all(item["provider_spy_calls"] <= 1 for item in manifest["crash_matrix"]))
        self.assertEqual(manifest["runtime_surface"]["migrations_added"], 0)

    def test_default_off_has_no_entrypoint_or_database_effect(self) -> None:
        disabled = SyntheticGateRehearsal(database_path=self.path)
        value = command("default-off")
        body = raw(value)
        with self.assertRaisesRegex(SyntheticGateError, "GATE_SYNTHETIC_REHEARSAL_DISABLED"):
            disabled.accept_raw(body, signature(body))
        self.assertFalse(self.path.exists())

    def test_signature_covers_exact_raw_bytes_before_ledger_accept(self) -> None:
        gate = service(self.path)
        value = command("exact-raw")
        body = raw(value)
        tampered = body.replace(b"Synthetic Food", b"Alteredxx Food")
        with self.assertRaisesRegex(SyntheticGateError, "GATE_SYNTHETIC_SIGNATURE_INVALID"):
            gate.accept_raw(tampered, signature(body))
        accepted = gate.accept_raw(body, signature(body))
        self.assertEqual(accepted["ack"]["status"], "committed")
        self.assertEqual(gate.snapshot(str(value["command_id"]))["provider_spy_calls"], 0)

    def test_before_accept_and_after_accept_restart_converge_without_second_send(self) -> None:
        for label, accept_before_restart in (("before-accept", False), ("after-accept", True)):
            with self.subTest(label=label):
                path = Path(self.temporary.name) / f"{label}.sqlite3"
                value = command(label)
                body = raw(value)
                first = service(path)
                if accept_before_restart:
                    accepted = first.accept_raw(body, signature(body))
                    self.assertEqual(accepted["ack"]["status"], "committed")
                restarted = service(path)
                accepted = restarted.accept_raw(body, signature(body), provider_outcome="sent")
                expected_ack = "duplicate" if accept_before_restart else "committed"
                self.assertEqual(accepted["ack"]["status"], expected_ack)
                self.assertEqual(restarted.dispatch(str(value["command_id"]))["kind"], "sent")
                snapshot = restarted.snapshot(str(value["command_id"]))
                self.assertEqual(snapshot["state"], "sent")
                self.assertEqual(snapshot["provider_spy_calls"], 1)

    def test_provider_boundary_and_result_settle_crashes_become_reconcile_only(self) -> None:
        scenarios = (
            ("before-provider", "sent", "before_provider", 0, "unknown"),
            ("after-provider-acceptance", "crash_after_acceptance", None, 1, "sent"),
            ("after-provider-result", "crash_after_result", "after_provider_result", 1, "sent"),
        )
        for label, outcome, crash_boundary, expected_calls, reconciled_state in scenarios:
            with self.subTest(label=label):
                path = Path(self.temporary.name) / f"{label}.sqlite3"
                value = command(label)
                body = raw(value)

                def checkpoint(name: str, _command: object) -> None:
                    if name == crash_boundary:
                        raise SyntheticCrash

                gate = service(path, checkpoint=checkpoint)
                gate.accept_raw(body, signature(body), provider_outcome=outcome)
                with self.assertRaises(SyntheticCrash):
                    gate.dispatch(str(value["command_id"]))
                restarted = service(path)
                unknown = restarted.recover_sending(str(value["command_id"]))
                self.assertEqual(unknown["receipt"]["status"], "unknown")
                self.assertEqual(unknown["receipt"]["retry_disposition"], "reconcile_only")
                snapshot = restarted.snapshot(str(value["command_id"]))
                self.assertEqual(snapshot["provider_spy_calls"], expected_calls)
                reconciled = restarted.reconcile(str(value["command_id"]))
                self.assertEqual(reconciled["receipt"]["status"], reconciled_state)
                snapshot = restarted.snapshot(str(value["command_id"]))
                self.assertEqual(snapshot["provider_spy_calls"], expected_calls)

    def test_provider_unknown_later_reconciles_terminal_without_resend(self) -> None:
        value = command("unknown-later-terminal")
        body = raw(value)
        gate = service(self.path)
        gate.accept_raw(body, signature(body), provider_outcome="unknown")
        result = gate.dispatch(str(value["command_id"]))
        self.assertEqual(result["receipt"]["status"], "unknown")
        self.assertEqual(gate.reconcile(str(value["command_id"]))["kind"], "unknown")
        gate.provider.resolve(str(value["idempotency_key"]), "sent")
        restarted = service(self.path)
        terminal = restarted.reconcile(str(value["command_id"]))
        self.assertEqual(terminal["receipt"]["status"], "sent")
        self.assertEqual(restarted.snapshot(str(value["command_id"]))["provider_spy_calls"], 1)

    def test_terminal_survives_restart_and_duplicate_accept_returns_receipt(self) -> None:
        value = command("terminal-before-aqub")
        body = raw(value)
        gate = service(self.path)
        gate.accept_raw(body, signature(body), provider_outcome="failed")
        self.assertEqual(gate.dispatch(str(value["command_id"]))["receipt"]["status"], "failed")
        restarted = service(self.path)
        duplicate = restarted.accept_raw(body, signature(body))
        self.assertEqual(duplicate["ack"]["status"], "duplicate")
        self.assertEqual(duplicate["receipt"]["status"], "failed")
        self.assertEqual(restarted.snapshot(str(value["command_id"]))["provider_spy_calls"], 1)

    def test_revoke_before_dispatch_fails_terminal_with_zero_provider_calls(self) -> None:
        route = {"active": True}
        value = command("revoke-before-dispatch")
        body = raw(value)
        gate = service(self.path, route_state=route)
        gate.accept_raw(body, signature(body))
        route["active"] = False
        terminal = gate.dispatch(str(value["command_id"]))
        self.assertEqual(terminal["receipt"]["status"], "failed")
        self.assertEqual(terminal["receipt"]["failure_code"], "AQUB_V2_ROUTE_NOT_ACTIVE")
        self.assertEqual(gate.snapshot(str(value["command_id"]))["provider_spy_calls"], 0)

    def test_delete_redaction_makes_replay_impossible(self) -> None:
        value = command("delete-no-replay")
        body = raw(value)
        gate = service(self.path)
        gate.accept_raw(body, signature(body))
        gate.dispatch(str(value["command_id"]))
        gate.redact(str(value["command_id"]))
        snapshot = gate.snapshot(str(value["command_id"]))
        self.assertEqual(snapshot["state"], "redacted")
        self.assertEqual(snapshot["provider_spy_calls"], 1)
        replay = service(self.path).accept_raw(body, signature(body))
        self.assertEqual(replay["ack"]["status"], "rejected")
        self.assertEqual(replay["ack"]["reason_code"], "AQUB_V2_CONTENT_REDACTED")
        with self.assertRaisesRegex(SyntheticGateError, "GATE_SYNTHETIC_CONTENT_REDACTED"):
            service(self.path).dispatch(str(value["command_id"]))
        snapshot = service(self.path).snapshot(str(value["command_id"]))
        self.assertEqual(snapshot["provider_spy_calls"], 1)


if __name__ == "__main__":
    unittest.main()

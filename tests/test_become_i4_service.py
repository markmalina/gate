import http.client
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.become_i4_service import (  # noqa: E402
    CONTRACT_DIGEST, IC9_DIGEST, build_receipt, canonical_digest, create_server,
    validate_receipt, validate_request,
)

ROOT = Path(__file__).resolve().parents[1]
REQUEST = json.loads((ROOT / "contracts/proof-loop/i4/fixtures/gate-service-request.json").read_text())
SOURCE_SHA = "3ae45f95f263b467b134289dc34943e0e11dfc00"
ENV = {
    "BECOME_INTERNAL_ONLY": "true", "BECOME_RUNTIME_MODE": "SIMULATED_TEST",
    "BECOME_LIVE_ENABLED": "false", "BECOME_PROVIDER_SEND_ENABLED": "false",
    "BECOME_PAYMENT_ENABLED": "false", "BECOME_PUBLICATION_ENABLED": "false",
    "BECOME_MEDIA_DOWNLOAD_ENABLED": "false", "BECOME_EXTERNAL_EFFECTS_ENABLED": "false",
    "BECOME_I4_CONTRACT_DIGEST": CONTRACT_DIGEST, "BECOME_IC9_DIGEST": IC9_DIGEST,
    "BECOME_SOURCE_SHA": SOURCE_SHA,
}


class TestGateI4Service(unittest.TestCase):
    def test_common_request_and_response_contract(self):
        receipt = build_receipt(REQUEST, source_sha=SOURCE_SHA)
        self.assertEqual(set(receipt), {"schema", "service", "source_sha", "contract_digest",
            "mode", "quest_ref", "correlation_ref", "subject_ref", "external_effects",
            "output", "receipt_digest"})
        self.assertTrue(validate_receipt(receipt, request=REQUEST, expected_source_sha=SOURCE_SHA))

    def test_domain_output_is_exact_and_provider_disabled(self):
        output = build_receipt(REQUEST, source_sha=SOURCE_SHA)["output"]
        self.assertEqual(output["amount"], 1)
        self.assertEqual(output["currency"], "XTR")
        self.assertTrue(output["successful_payment"])
        self.assertTrue(output["result_before_payment"])
        self.assertEqual(output["provider_states"], ["reserved", "in_flight", "unknown", "settled"])
        self.assertEqual(output["blind_retry_attempts"], 0)

    def test_receipt_is_deterministic(self):
        self.assertEqual(build_receipt(REQUEST, source_sha=SOURCE_SHA),
                         build_receipt(REQUEST, source_sha=SOURCE_SHA))

    def test_contract_digest_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "contract digest mismatch"):
            validate_request({**REQUEST, "contract_digest": "0" * 64})

    def test_nested_sensitive_key_rejected(self):
        request = dict(REQUEST); request["input"] = dict(REQUEST["input"])
        request["input"]["delivery_ref"] = {"provider_payload": "synthetic"}
        with self.assertRaisesRegex(ValueError, "sensitive field rejected"):
            validate_request(request)

    def test_extra_domain_input_rejected(self):
        request = dict(REQUEST); request["input"] = {**REQUEST["input"], "retry": True}
        with self.assertRaisesRegex(ValueError, "exact Gate I4 input"):
            validate_request(request)

    def test_non_exact_stars_amount_rejected(self):
        request = dict(REQUEST); request["input"] = {**REQUEST["input"], "amount": 2}
        with self.assertRaisesRegex(ValueError, "exactly 1 XTR"):
            validate_request(request)

    def test_forged_receipt_with_recomputed_hash_rejected(self):
        receipt = build_receipt(REQUEST, source_sha=SOURCE_SHA)
        receipt["output"] = {**receipt["output"], "blind_retry_attempts": 1}
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        receipt["receipt_digest"] = canonical_digest(unsigned)
        with self.assertRaisesRegex(ValueError, "does not match frozen request"):
            validate_receipt(receipt, request=REQUEST, expected_source_sha=SOURCE_SHA)

    def test_runtime_refuses_enabled_provider(self):
        with patch.dict(os.environ, {**ENV, "BECOME_PAYMENT_ENABLED": "true"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "provider-disabled runtime"):
                create_server()

    def test_health_and_internal_post(self):
        with patch.dict(os.environ, ENV, clear=True):
            server = create_server(); thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                connection.request("GET", "/healthz"); health = connection.getresponse()
                self.assertEqual(health.status, 200); self.assertEqual(json.loads(health.read())["external_effects"], 0)
                payload = json.dumps(REQUEST, separators=(",", ":"))
                connection.request("POST", "/internal/become/i4", body=payload,
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse(); self.assertEqual(response.status, 200)
                receipt = json.loads(response.read())
                self.assertTrue(validate_receipt(receipt, request=REQUEST, expected_source_sha=SOURCE_SHA))
                connection.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__": unittest.main()

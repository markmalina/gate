"""Hermetic local process tests, no Railway or database access."""

import importlib.util
import os
import signal
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "fixture_runtime", Path(__file__).with_name("runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_finite_cgroup_limits(self):
        self.assertEqual(
            runtime.parse_caps("100000 100000\n", "536870912\n"),
            {"cpu": 1.0, "memoryBytes": 536870912},
        )
        for cpu, mem in [
            ("max 100000", "1"),
            ("100001 100000", "1"),
            ("1 100000", "max"),
            ("1 100000", "536870913"),
            ("-1 100000", "1"),
            ("0 100000", "1"),
        ]:
            with self.assertRaises(ValueError):
                runtime.parse_caps(cpu, mem)

    def test_real_waitpid_waits_for_delayed_children(self):
        def child(fd, index):
            stop = False

            def handler(*_):
                nonlocal stop
                stop = True

            signal.signal(signal.SIGTERM, handler)
            os.write(fd, b"R")
            os.close(fd)
            while not stop:
                time.sleep(0.01)
            time.sleep(0.15 if index else 0.02)

        children = runtime.supervise(child)
        start = time.monotonic()
        self.assertEqual(runtime.reap(children), 2)
        self.assertGreaterEqual(time.monotonic() - start, 0.14)
        for pid in children:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_forced_shutdown_reaps_stubborn_children(self):
        def child(fd, _index):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.write(fd, b"R")
            os.close(fd)
            while True:
                time.sleep(0.01)

        children = runtime.supervise(child)
        self.assertEqual(runtime.reap(children, timeout=0.1), 2)

    def test_production_identity_refuses_before_any_effect(self):
        env = {
            "GATE781_DISPOSABLE": "synthetic-data-only",
            "GATE781_NONCE": "00000000-0000-4000-8000-000000000001",
            "GATE781_SOURCE_SHA": "a" * 40,
            "RAILWAY_PROJECT_ID": next(iter(runtime.PROD)),
        }
        with self.assertRaises(ValueError):
            runtime.identity(env)

    def test_synthetic_originals_claims_and_journal_are_exact(self):
        rows = runtime.expected_rows()
        self.assertEqual(
            {r["transcription_state"] for r in rows["notes"]},
            {"processing", "transcribed", "unknown"},
        )
        self.assertEqual(
            {r["delivery_state"] for r in rows["notes"]}, {"pending", "sending", "unknown"}
        )
        self.assertTrue(all(r["provider_request_count"] == 1 for r in rows["notes"]))
        self.assertEqual(len(rows["journal"]), 1)


if __name__ == "__main__":
    unittest.main()

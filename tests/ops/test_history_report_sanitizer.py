import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "history_sanitizer", Path("scripts/sanitize_history_secret_report.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProjectionTests(unittest.TestCase):
    def project(self, raw, **kwargs):
        return module.project(raw, exit_code=kwargs.get("exit_code", 23),
                              source_sha=kwargs.get("source_sha", "a" * 40),
                              complete_history=kwargs.get("complete_history", True),
                              ref_count=kwargs.get("ref_count", 3), commit_count=7,
                              refs_digest="c" * 64)

    def finding(self, **kwargs):
        marker = "SYNTHETIC_PRIVATE_VALUE_MUST_NOT_ESCAPE"
        return {"Commit": "b" * 40, "StartLine": 3, "EndLine": 3,
                "File": marker + "/file.py", "RuleID": "generic-api-key",
                "Secret": marker, "Match": marker, "Message": marker,
                "Author": marker, "Email": marker, "Fingerprint": marker, **kwargs}

    def test_only_safe_projection_survives(self):
        result = self.project([self.finding()])
        self.assertEqual(result["result"], "FINDINGS")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(result))
        self.assertEqual(
            set(result["findings"][0]),
            {"commit", "path_sha256", "start_line", "end_line", "class"},
        )

    def test_rule_text_is_not_a_log_channel(self):
        result = self.project([self.finding(RuleID="SYNTHETIC_PRIVATE_VALUE")])
        self.assertEqual(result["findings"][0]["class"], "other")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(result))

    def test_failures_and_partial_history_are_incomplete(self):
        for code in (1, 2, 124, 137):
            self.assertEqual(self.project([], exit_code=code)["result"], "INCOMPLETE")
        partial = self.project([], exit_code=0, complete_history=False)
        self.assertEqual(partial["result"], "INCOMPLETE")

    def test_cli_distinguishes_valid_null_from_missing_or_malformed_report(self):
        marker = "SYNTHETIC_PRIVATE_VALUE_MUST_NOT_ESCAPE"
        for raw, code, result in [("null", 0, "NO_FINDINGS"),
                                  (marker, 1, "INCOMPLETE"),
                                  (None, 1, "INCOMPLETE")]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "raw.json"
                if raw is not None:
                    path.write_text(raw)
                output = root / "safe.json"
                run = subprocess.run(
                    [sys.executable, "scripts/sanitize_history_secret_report.py",
                     "--raw", str(path), "--output", str(output), "--exit-code", "0",
                     "--source-sha", "a" * 40, "--complete-history", "true",
                     "--ref-count", "3", "--commit-count", "7", "--refs-digest", "c" * 64],
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(run.returncode, code)
                self.assertEqual(json.loads(output.read_text())["result"], result)
                self.assertNotIn(marker, run.stdout + run.stderr + output.read_text())

    def test_history_stream_includes_merge_only_addition(self):
        marker = "SYNTHETIC_MERGE_ONLY_FINDING"
        workflow = Path(".github/workflows/history-secret-scan.yml").read_text()
        configured = re.search(r"--log-opts='([^']+)'", workflow)
        self.assertIsNotNone(configured)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args], check=True,
                    capture_output=True, text=True,
                ).stdout

            git("init", "--initial-branch=main")
            git("config", "user.name", "Synthetic Test")
            git("config", "user.email", "synthetic@example.invalid")
            (root / "base.txt").write_text("base")
            git("add", ".")
            git("commit", "-m", "base")
            git("checkout", "-b", "side")
            (root / "side.txt").write_text("side")
            git("add", ".")
            git("commit", "-m", "side")
            git("checkout", "main")
            (root / "main.txt").write_text("main")
            git("add", ".")
            git("commit", "-m", "main")
            git("merge", "--no-ff", "--no-commit", "side")
            (root / "merge-only.txt").write_text(marker)
            git("add", ".")
            git("commit", "-m", "merge")
            old = git("log", "-p", "-U0", "--all", "--full-history")
            actual = git("log", "-p", "-U0", *shlex.split(configured.group(1)))
            self.assertNotIn(marker, old)
            self.assertIn(marker, actual)

    def test_missing_coverage_is_incomplete(self):
        self.assertEqual(self.project([], exit_code=0, ref_count=0)["result"], "INCOMPLETE")

    def test_clean_empty_success_is_distinct_from_inconsistent_report(self):
        self.assertEqual(self.project([], exit_code=0)["result"], "NO_FINDINGS")
        self.assertEqual(self.project([], exit_code=23)["result"], "INCOMPLETE")
        self.assertEqual(self.project([self.finding()], exit_code=0)["result"], "INCOMPLETE")

    def test_malformed_shapes_and_untrusted_fields_fail_closed(self):
        for raw in (None, {}, ["bad"], [self.finding(Commit="secret")],
                    [self.finding(StartLine=True)], [self.finding(EndLine=0)]):
            self.assertEqual(self.project(raw)["result"], "INCOMPLETE")
        self.assertEqual(self.project([], exit_code=0, source_sha="secret")["result"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()


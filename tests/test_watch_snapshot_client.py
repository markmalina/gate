"""Source-only admission checks; no provider or Actions requests."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPS = "5c86c479f6581c5922519382ffcd953d63629084"


def test_watch_snapshot_exact_pins_and_pure_terminal():
    raw = (ROOT / ".github/workflows/gate-snapshot-cutover.yml").read_bytes()
    workflow = yaml.safe_load(raw)
    for path in ("agent-ops.watch-snapshot.lock.json", "agent-ops.gate-snapshot.lock.json"):
        lock = json.loads((ROOT / path).read_text())
        assert lock["workflowSha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    watch = json.loads((ROOT / "agent-ops.watch-snapshot.lock.json").read_text())
    assert watch["commit"] == OPS and watch["repository"] == "markmalina/ops"
    reply = json.loads((ROOT / "agent-ops.gate-snapshot.lock.json").read_text())
    assert reply["commit"] == "b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85"
    for name, job in workflow["jobs"].items():
        if not name.startswith("watch-"):
            continue
        for step in job["steps"]:
            if step.get("uses", "").startswith("markmalina/ops/"):
                assert step["uses"].endswith("@" + OPS)
                assert step["with"]["profile"] == "watch0119"
    terminal = workflow["jobs"]["watch-snapshot-final-verify"]
    assert "environment" not in terminal
    assert set(terminal["permissions"].values()) == {"read"}
    assert "RAILWAY" not in json.dumps(terminal)
    assert "PRIVATE_KEY" not in json.dumps(terminal)


@pytest.mark.skipif(os.name != "posix", reason="workflow transport uses Bash")
def test_watch_snapshot_transport_exact_plan_apply_and_drift(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/gate-snapshot-cutover.yml").read_text())
    steps = workflow["jobs"]["watch-snapshot-cutover"]["steps"]
    script = next(s["run"] for s in steps if s.get("id") == "snapshot-transport")
    sha = "a" * 40
    plan = (
        "/agent-ops v1 plan operation=gate-snapshot-cutover target=gate-production "
        f"profile=watch0119 expected-client-sha={sha} fence-run=111 fence-artifact=222 "
        + "fence-digest=sha256:" + "b" * 64
    )
    apply = (
        "/agent-ops v1 apply operation=gate-snapshot-cutover target=gate-production "
        "profile=watch0119 plan-run=123 plan-artifact=456 plan-digest=sha256:" + "c" * 64
    )
    for index, (body, accepted) in enumerate(
        [(plan, True), (apply, True), (plan.replace(sha, "d" * 40), False),
         (apply + " extra=unsafe", False), (apply.replace("watch0119", "reply745"), False)]
    ):
        result = subprocess.run(
            ["bash", "-c", script],
            env={"PATH": os.environ["PATH"], "COMMENT_BODY": body, "GITHUB_SHA": sha,
                 "GITHUB_OUTPUT": str(tmp_path / str(index))},
            capture_output=True, check=False, timeout=5,
        )
        assert (result.returncode == 0) is accepted

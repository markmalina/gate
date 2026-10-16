"""Keep the Gate-only read-only caller bound to its independently accepted source."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS_PIN = "45e60c3d93d09c978633ac80b73e0a281d5ccea6"
PROFILE_DIGEST = "sha256:7808485398176a79fcc05c2934ac311cf4763b4dce56508b1af77d243ffd7f56"
WORKFLOW = ROOT / ".github/workflows/ops-runtime-observer.yml"


def test_observer_lock_binds_exact_accepted_executor_and_raw_workflow():
    lock = json.loads((ROOT / "agent-ops.runtime-observer.lock.json").read_text())
    assert lock == {
        "schemaVersion": "agent-ops.gate-runtime-observer-lock/v1",
        "repository": "markmalina/ops",
        "commit": OPS_PIN,
        "actionPath": "agent-ops/actions/gate-runtime-observer",
        "workflowSha256": "sha256:" + hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
        "profileSha256": PROFILE_DIGEST,
    }


def test_observer_is_owner_labeled_exact_pr_without_a_default_branch_effect_trigger():
    text = WORKFLOW.read_text()
    assert "on:\n  pull_request:\n    types: [labeled]\n    branches: [main]\n" in text
    assert "github.event.pull_request.number == 766" in text
    assert "github.event.sender.id == 23397294" in text
    assert "github.event.pull_request.head.repo.full_name == 'markmalina/gate'" in text
    assert "github.event.pull_request.head.ref == 'agent/736-autonomous-watch-queue'" in text
    assert "format('o{0}-', github.event.pull_request.head.sha)" in text
    assert "group: gate736-runtime-observation\n  cancel-in-progress: false" in text
    for forbidden in ("pull_request_target", "issue_comment:", "push:", "schedule:"):
        assert forbidden not in text


def test_provider_credential_has_one_protected_job_and_never_reaches_verifier():
    text = WORKFLOW.read_text()
    admission, tail = text.split("  admission:\n", 1)[1].split("  observe:\n", 1)
    observe, verify = tail.split("  verify:\n", 1)
    assert "environment:" not in admission + verify
    assert "RAILWAY" not in admission + verify
    assert observe.count("environment: gate-production") == 1
    assert observe.count("RAILWAY_PROJECT_TOKEN: ${{ secrets.RAILWAY_TOKEN }}") == 1
    assert text.count("secrets.") == 1
    assert "if: needs.admission.outputs.admitted == 'true'" in observe
    assert "if: always() && needs.observe.result == 'success'" in verify
    assert "actions: read" in text and "contents: read" in text
    assert not re.search(r"\b(?:actions|contents|issues|pull-requests): write", text)
    assert not re.search(r"^\s*(?:run|services|container|environment):", admission + verify, re.M)
    assert not re.search(r"^\s*run:", observe, re.M)
    assert "actions/checkout" not in text and "cache:" not in text


def test_observer_uses_only_reviewed_immutable_actions_and_exact_raw_artifacts():
    text = WORKFLOW.read_text()
    uses = re.findall(r"\buses: ([^\s]+)", text)
    expected = f"markmalina/ops/agent-ops/actions/gate-runtime-observer@{OPS_PIN}"
    assert uses.count(expected) == 3
    assert set(uses) == {
        expected,
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    }
    assert all(re.fullmatch(r"[^@]+@[a-f0-9]{40}", use) for use in uses)
    assert "artifact-ids: ${{ needs.observe.outputs.receipt-artifact-id }}," in text
    assert "${{ needs.observe.outputs.detached-artifact-id }}" in text
    for required in ("merge-multiple: true", "skip-decompress: true", "digest-mismatch: error"):
        assert required in text
    assert text.count("archive: false") == 3
    assert "expected-digest: ${{ needs.observe.outputs.receipt-digest }}" in text

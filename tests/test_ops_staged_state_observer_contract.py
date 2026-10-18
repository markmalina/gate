"""Protect the one-shot Gate caller's immutable executor and credential boundary."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS_PIN = "86718a1f67ad7a26b7ab402debea1bd5d0bbbaad"
PROFILE_DIGEST = "sha256:3a1757a1b04cd4dda960a43774082a1499bdf16fd9c254966e690dd74f432542"
WORKFLOW = ROOT / ".github/workflows/ops-staged-state-observer.yml"


def test_staged_state_lock_binds_exact_executor_and_raw_workflow():
    assert re.fullmatch(r"[a-f0-9]{40}", OPS_PIN)
    lock = json.loads((ROOT / "agent-ops.staged-state-observer.lock.json").read_text())
    assert lock == {
        "schemaVersion": "agent-ops.gate-staged-state-observer-lock/v1",
        "repository": "markmalina/ops",
        "commit": OPS_PIN,
        "actionPath": "agent-ops/actions/gate-staged-state-observer",
        "workflowSha256": "sha256:" + hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
        "profileSha256": PROFILE_DIGEST,
    }


def test_staged_state_caller_has_only_exact_owner_pr_label_trigger():
    text = WORKFLOW.read_text()
    assert "on:\n  pull_request:\n    types: [labeled]\n    branches: [main]\n" in text
    for required in (
        "github.event.pull_request.number == 766",
        "github.event.sender.id == 23397294",
        "github.event.pull_request.head.repo.full_name == 'markmalina/gate'",
        "github.event.pull_request.head.ref == 'agent/736-autonomous-watch-queue'",
        "format('s{0}-', github.event.pull_request.head.sha)",
        "group: gate736-staged-state-observation\n  cancel-in-progress: false",
    ):
        assert required in text
    for forbidden in ("pull_request_target", "issue_comment:", "push:", "schedule:", "workflow_dispatch:"):
        assert forbidden not in text


def test_staged_state_provider_secret_is_confined_to_protected_observe_job():
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
    assert not re.search(r"\b(?:actions|contents|issues|pull-requests): write", text)
    assert not re.search(r"^\s*(?:run|services|container):", text, re.M)
    assert "actions/checkout" not in text and "cache:" not in text


def test_staged_state_artifacts_and_actions_have_fixed_provenance_paths():
    text = WORKFLOW.read_text()
    action = f"markmalina/ops/agent-ops/actions/gate-staged-state-observer@{OPS_PIN}"
    uses = re.findall(r"\buses: ([^\s]+)", text)
    assert uses.count(action) == 3
    assert set(uses) == {
        action,
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    }
    assert all(re.fullmatch(r"[^@]+@[a-f0-9]{40}", use) for use in uses)
    for required in (
        "path: ops-staged-state-observation.json",
        "path: ops-staged-state-observation.sha256",
        "path: ops-staged-state-verification.json",
        "artifact-ids: ${{ needs.observe.outputs.receipt-artifact-id }},${{ needs.observe.outputs.detached-artifact-id }}",
        "merge-multiple: true",
        "skip-decompress: true",
        "digest-mismatch: error",
        "expected-digest: ${{ needs.observe.outputs.receipt-digest }}",
    ):
        assert required in text
    assert text.count("archive: false") == 3

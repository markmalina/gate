"""Protect the one-shot Gate caller's immutable executor and credential boundary."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS_PIN = "7f9167075bc6e1b60be7490b8454133eb8132058"
PROFILE_DIGEST = "sha256:c8a4a0d4723e5922ef512998edfa448ee46afb503b29dcfac7a16fb19b70e4e6"
WORKFLOW = ROOT / ".github/workflows/ops-cli-state-observer.yml"


def test_cli_state_lock_binds_exact_executor_and_raw_workflow():
    assert re.fullmatch(r"[a-f0-9]{40}", OPS_PIN)
    lock = json.loads((ROOT / "agent-ops.cli-state-observer.lock.json").read_text())
    assert lock == {
        "schemaVersion": "agent-ops.gate-cli-state-observer-lock/v1",
        "repository": "markmalina/ops",
        "commit": OPS_PIN,
        "actionPath": "agent-ops/actions/gate-cli-state-observer",
        "workflowSha256": "sha256:" + hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
        "profileSha256": PROFILE_DIGEST,
    }


def test_cli_state_caller_has_only_exact_owner_pr_label_trigger():
    text = WORKFLOW.read_text()
    assert "on:\n  pull_request:\n    types: [labeled]\n    branches: [main]\n" in text
    for required in (
        "github.event.pull_request.number == 766",
        "github.event.sender.id == 23397294",
        "github.event.pull_request.head.repo.full_name == 'markmalina/gate'",
        "github.event.pull_request.head.ref == 'agent/736-autonomous-watch-queue'",
        "format('c{0}-', github.event.pull_request.head.sha)",
        "cancel-in-progress: false",
    ):
        assert required in text
    predicate = (
        "github.event.pull_request.number == 766 && "
        "github.event.sender.id == 23397294 && "
        "startsWith(github.event.label.name, "
        "format('c{0}-', github.event.pull_request.head.sha)) && "
        "github.event.pull_request.head.repo.full_name == 'markmalina/gate' && "
        "github.event.pull_request.head.ref == 'agent/736-autonomous-watch-queue'"
    )
    group = (
        "concurrency:\n  group: >-\n    ${{ " + predicate
        + " && 'gate736-cli-state-observation'"
        + " || format('gate736-inactive-{0}', github.run_id) }}\n"
        + "  cancel-in-progress: false\n"
    )
    assert group in text
    admission_if = text.split("  admission:\n    if: >-\n", 1)[1]
    admission_if = admission_if.split("    runs-on:", 1)[0]
    assert " ".join(admission_if.split()) == predicate
    for forbidden in (
        "pull_request_target", "issue_comment:", "push:", "schedule:", "workflow_dispatch:"
    ):
        assert forbidden not in text


def test_cli_state_provider_secret_is_confined_to_protected_observe_job():
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


def test_cli_state_artifacts_and_actions_have_fixed_provenance_paths():
    text = WORKFLOW.read_text()
    action = f"markmalina/ops/agent-ops/actions/gate-cli-state-observer@{OPS_PIN}"
    uses = re.findall(r"\buses: ([^\s]+)", text)
    assert uses.count(action) == 3
    assert set(uses) == {
        action,
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    }
    assert all(re.fullmatch(r"[^@]+@[a-f0-9]{40}", use) for use in uses)
    for required in (
        "path: ops-cli-state-observation.json",
        "path: ops-cli-state-observation.sha256",
        "path: ops-cli-state-verification.json",
        "artifact-ids: ${{ needs.observe.outputs.receipt-artifact-id }},"
        "${{ needs.observe.outputs.detached-artifact-id }}",
        "merge-multiple: true",
        "skip-decompress: true",
        "digest-mismatch: error",
        "expected-digest: ${{ needs.observe.outputs.receipt-digest }}",
    ):
        assert required in text
    assert text.count("archive: false") == 3

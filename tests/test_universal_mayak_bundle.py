"""Release guard for the reviewed Universal Mayak instruction bundle."""

import hashlib
from pathlib import Path

from app.application.agent_kernel import AgentResultKind
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.application.universal_mayak_demo import UNIVERSAL_MAYAK_PROMPT_SHA256
from app.application.work_ownership import AgentKey


def test_deployed_universal_mayak_bundle_is_reviewed_and_digest_pinned() -> None:
    repository_root = Path(__file__).parents[1]
    bundle_path = repository_root / "agent_bundles" / "universal_mayak_v1"
    bundle = DeployedInstructionBundleLoader(
        repository_root / "agent_bundles"
    ).load("universal_mayak_v1")

    assert bundle.agent_key is AgentKey.OKO
    assert bundle.revision.revision_key == "universal-mayak-v1"
    assert bundle.revision.source_path == "universal_mayak_v1"
    assert bundle.allowed_capabilities == frozenset(
        {"reply", "handoff", "no_reply"}
    )
    assert len(bundle.evaluations) == 8
    assert {fixture.result.kind for fixture in bundle.evaluations} == {
        AgentResultKind.REPLY,
        AgentResultKind.HANDOFF,
        AgentResultKind.NO_REPLY,
    }
    assert "одном личном диалоге" in bundle.system_instructions
    assert "Не выбирай аккаунт, получателя" in bundle.system_instructions
    assert "Команды паузы и передачи человеку обрабатывает Gate" in (
        bundle.system_instructions
    )
    assert hashlib.sha256(
        (bundle_path / "system.md").read_bytes()
    ).hexdigest() == UNIVERSAL_MAYAK_PROMPT_SHA256

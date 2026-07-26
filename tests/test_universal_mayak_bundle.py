"""Release guard for the reviewed Oko LMS instruction bundle."""

from pathlib import Path

from app.application.agent_kernel import AgentResultKind
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.application.universal_mayak_demo import UNIVERSAL_MAYAK_PROMPT_SHA256
from app.application.work_ownership import AgentKey


def test_deployed_universal_mayak_bundle_is_reviewed_and_digest_pinned() -> None:
    repository_root = Path(__file__).parents[1]
    bundle = DeployedInstructionBundleLoader(
        repository_root / "agent_bundles"
    ).load("universal_mayak_oko_v2")

    assert bundle.agent_key is AgentKey.OKO
    assert bundle.revision.revision_key == "universal-mayak-oko-v2"
    assert bundle.revision.source_repository == "markmalina/mayaki"
    assert bundle.revision.source_path == "lms/oko-vozrozhdeniya"
    assert bundle.revision.source_commit_sha == (
        "0a638b580fc514f73cfb166c60c57531875e4887"
    )
    assert bundle.revision.content_sha256 == UNIVERSAL_MAYAK_PROMPT_SHA256
    assert bundle.allowed_capabilities == frozenset(
        {"reply", "handoff", "no_reply"}
    )
    assert len(bundle.evaluations) == 8
    assert {fixture.result.kind for fixture in bundle.evaluations} == {
        AgentResultKind.REPLY,
        AgentResultKind.HANDOFF,
    }
    for required in (
        "хочу заниматься возрождением",
        "Упражнение 1",
        "Упражнение 2",
        "Упражнение 3",
        "Упражнение 4",
        "Упражнение 5",
        "Всего",
        "после перезапуска",
        "боль",
        "головокружение",
    ):
        assert required in bundle.system_instructions

"""Acceptance guards for the exact Oko LMS v2 release."""

import importlib.util
from pathlib import Path

from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.application.universal_mayak_demo import (
    UNIVERSAL_MAYAK_PROMPT_REF,
    UNIVERSAL_MAYAK_PROMPT_SHA256,
)


def test_prompt_identity_uses_the_bundle_digest_seen_by_the_model() -> None:
    repository_root = Path(__file__).parents[1]
    bundle = DeployedInstructionBundleLoader(
        repository_root / "agent_bundles"
    ).load("universal_mayak_oko_v2")

    assert UNIVERSAL_MAYAK_PROMPT_REF == bundle.revision.revision_key
    assert UNIVERSAL_MAYAK_PROMPT_SHA256 == bundle.revision.content_sha256


def test_oko_v2_migration_is_exact_quiescent_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0062_mayak_oko_v2.py"
    )
    spec = importlib.util.spec_from_file_location("mayak_oko_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0062_mayak_oko_v2"
    assert module.down_revision == "0061_voice_dead_letter_reconciliation"
    source = path.read_text(encoding="utf-8")
    for guard in (
        "universal-mayak-v1-owner-private",
        "universal-mayak-v1-openai",
        "universal-mayak-oko-v2-openai",
        "universal-mayak-oko-v2",
        "markmalina/mayaki",
        "lms/oko-vozrozhdeniya",
        "0a638b580fc514f73cfb166c60c57531875e4887",
        "AND installation_key = :installation_key",
        "AND state = 'active'",
        "turn.status IN ('queued', 'running')",
        "used_at IS NULL AND revoked_at IS NULL",
        "state = 'paused'",
    ):
        assert guard in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)

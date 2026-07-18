"""Guards for durable Alpha rate-limit deferral and bounded backlog recovery."""

import importlib.util
from pathlib import Path


def test_alpha_rate_limit_deferral_requeues_only_missing_v2_commands() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0035_alpha_rate_limit_deferral.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_rate_limit_deferral", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0035_alpha_rate_limit_deferral"
    assert module.down_revision == "0034_alpha_model_v2_release"
    assert module._DEPLOYMENT_KEY == "alpha-model-v2-openai"
    assert module._CONNECTOR_KEY == "telegram_alpha_group"

    source = path.read_text(encoding="utf-8")
    assert "receipt.received_at >= deployment.created_at" in source
    assert "usage.trigger_message_id = message.id" in source
    assert "turn.trigger_message_id = message.id" in source
    assert "message.direction = 'inbound'" in source
    assert "status = 'retry_scheduled'" in source
    assert "status = 'received'" in source
    assert "interval '2 minutes'" in source
    assert "delivery_grants" not in source
    assert "agent_send_attempts" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)

"""AQUB production code must not regress to rollout-phase naming."""

from __future__ import annotations

from pathlib import Path

AQUB_PRODUCTION_FILES = (
    Path("app/api/aqub_telegram.py"),
    Path("app/application/aqub_live.py"),
    Path("app/application/aqub_transport.py"),
    Path("app/infrastructure/aqub_installation.py"),
    Path("app/infrastructure/aqub_live.py"),
    Path("app/models/aqub.py"),
    Path("app/models/aqub_live.py"),
    Path("app/services/telegram_aqub_webhook.py"),
)

FORBIDDEN_SYMBOL_FRAGMENTS = (
    "AQUBS0",
    "AQUBPreflight",
    "gate_aqub_s0",
    "GATE_AQUB_S0",
    "AQUB_RELEASE_",
    "legacy_dispatch",
    "canary_dispatch",
)


def test_aqub_production_modules_use_product_names_not_rollout_phase_names() -> None:
    for path in AQUB_PRODUCTION_FILES:
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_SYMBOL_FRAGMENTS:
            assert forbidden not in source, (path, forbidden)


def test_only_compatibility_identifiers_keep_preflight_wording() -> None:
    transport = Path("app/application/aqub_transport.py").read_text(encoding="utf-8")
    installation = Path("app/infrastructure/aqub_installation.py").read_text(
        encoding="utf-8"
    )
    assert 'AQUB_TELEGRAM_WEBHOOK_PATH = "/telegram/aqub/preflight"' in transport
    assert 'AQUB_RECEIPT_BOT_KEY = "telegram_aqub_preflight"' in installation

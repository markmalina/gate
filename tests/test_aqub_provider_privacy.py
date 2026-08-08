"""Regression guard: AQUB production provider IDs stay out of mutable source."""

from __future__ import annotations

from pathlib import Path

MUTABLE_AQUB_PATHS = (
    Path("app/api/aqub_telegram_preflight.py"),
    Path("app/application/aqub_live.py"),
    Path("app/infrastructure/aqub_live.py"),
    Path("app/infrastructure/aqub_s0_preflight.py"),
    Path("app/models/aqub.py"),
    Path("app/services/telegram_aqub_webhook.py"),
    Path("tests/test_aqub_live.py"),
    Path("tests/test_aqub_live_delivery_postgres.py"),
    Path("tests/test_aqub_telegram_preflight.py"),
    Path("tests/test_aqub_installation_binding_postgres.py"),
)


def test_aqub_mutable_source_contains_no_release_provider_binding() -> None:
    # Build the old production identifiers only at runtime so the guard itself
    # never reintroduces them as contiguous tracked-source literals.
    former_chat_id = "-" + "5052" + "983997"
    former_alpha_id = "7819" + "816320"
    forbidden_symbols = (
        "AQUB_RELEASE_GROUP_CHAT_ID",
        "AQUB_RELEASE_ALPHA_USER_ID",
        "AQUB_RELEASE_ACE_USER_ID",
    )

    for path in MUTABLE_AQUB_PATHS:
        source = path.read_text(encoding="utf-8")
        assert former_chat_id not in source, path
        assert former_alpha_id not in source, path
        for symbol in forbidden_symbols:
            assert symbol not in source, (path, symbol)

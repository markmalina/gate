"""Secret-safe readiness report for the dormant Instagram transport."""

from __future__ import annotations

from typing import Any

from app.instagram.config import InstagramRuntimeConfig


def diagnose_instagram_transport(config: InstagramRuntimeConfig) -> dict[str, Any]:
    checks = [
        _check("tools_enabled", config.enabled, "chatgpt_tools"),
        _check("graph_version", bool(config.graph_version), "publishing"),
        _check("signing_secret", bool(config.signing_secret), "publishing"),
        _check("account_registry", bool(config.accounts), "publishing"),
        _check("publish_delivery", config.delivery_enabled, "external_publish"),
        _check("webhook_enabled", config.webhook_enabled, "webhook_verification"),
        _check("meta_app_secret", bool(config.app_secret), "webhook_verification"),
        _check(
            "webhook_verify_token",
            bool(config.webhook_verify_token),
            "webhook_verification",
        ),
        _check(
            "webhook_delivery",
            config.webhook_delivery_enabled,
            "mayaki_delivery",
        ),
        _check("mayaki_intake_url", bool(config.mayaki_intake_url), "mayaki_delivery"),
        _check(
            "mayaki_intake_bearer",
            bool(config.mayaki_intake_bearer_token),
            "mayaki_delivery",
        ),
        _check(
            "mayaki_intake_hmac",
            bool(config.mayaki_intake_hmac_secret),
            "mayaki_delivery",
        ),
    ]
    capabilities = {
        "chatgpt_tools": config.enabled,
        "publishing": config.ready,
        "external_publish": config.ready and config.delivery_enabled,
        "webhook_verification": config.webhook_verification_ready,
        "mayaki_delivery": config.webhook_delivery_ready,
    }
    missing = [item["key"] for item in checks if not item["ok"]]
    return {
        "status": "ready" if not missing else "configuration_required",
        "external_delivery": False,
        "configured_account_aliases": sorted(config.accounts),
        "capabilities": capabilities,
        "checks": checks,
        "missing": missing,
        "activation_order": [
            "configure_accounts_and_graph_version",
            "enable_chatgpt_tools",
            "run_prepare_only_dry_run",
            "enable_one_publish_canary",
            "configure_webhook_verification",
            "configure_signed_mayaki_delivery",
        ],
    }


def _check(key: str, ok: bool, required_for: str) -> dict[str, str | bool]:
    return {"key": key, "ok": ok, "required_for": required_for}

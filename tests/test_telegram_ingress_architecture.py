"""Characterization guard for the transitional Telegram webhook architecture."""

from typing import Any

from app.api.telegram_ingress import (
    LEGACY_TELEGRAM_INGRESS_ROUTERS,
    LEGACY_TELEGRAM_WEBHOOK_CHAIN,
    include_legacy_telegram_ingress,
)
from app.main import create_app
from fastapi import FastAPI


def _is_webhook_layer(route: Any) -> bool:
    endpoint = getattr(route, "endpoint", None)
    endpoint_name = getattr(endpoint, "__name__", "")
    return endpoint_name == "telegram_webhook" or endpoint_name.endswith(
        "_telegram_webhook"
    )


def _endpoint_fingerprint(route: Any) -> str:
    endpoint = route.endpoint
    return f"{endpoint.__module__}:{endpoint.__name__}"


def _registered_webhook_chain(app: FastAPI) -> tuple[str, ...]:
    return tuple(
        _endpoint_fingerprint(route)
        for route in app.routes
        if _is_webhook_layer(route)
    )


def test_production_webhook_chain_matches_the_frozen_characterization() -> None:
    assert _registered_webhook_chain(create_app()) == LEGACY_TELEGRAM_WEBHOOK_CHAIN


def test_every_transitional_router_owns_exactly_one_webhook_layer() -> None:
    fingerprints = []
    for router in LEGACY_TELEGRAM_INGRESS_ROUTERS:
        webhook_routes = [route for route in router.routes if _is_webhook_layer(route)]
        assert len(webhook_routes) == 1
        fingerprints.append(_endpoint_fingerprint(webhook_routes[0]))

    assert tuple(fingerprints) == LEGACY_TELEGRAM_WEBHOOK_CHAIN[:-1]


def test_registration_boundary_preserves_declared_layer_order() -> None:
    app = FastAPI()
    include_legacy_telegram_ingress(app)

    assert _registered_webhook_chain(app) == LEGACY_TELEGRAM_WEBHOOK_CHAIN[:-1]

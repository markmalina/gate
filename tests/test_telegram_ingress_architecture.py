"""Characterization guard for the transitional Telegram webhook architecture."""

from collections.abc import Iterable, Iterator
from typing import Any

from app.api.telegram_ingress import (
    LEGACY_TELEGRAM_INGRESS_ROUTERS,
    LEGACY_TELEGRAM_WEBHOOK_CHAIN,
    TELEGRAM_WEBHOOK_CHAIN,
    TELEGRAM_WEBHOOK_PATH,
    durable_ingress_router,
    include_legacy_telegram_ingress,
    include_telegram_ingress,
)
from app.main import create_app
from fastapi import FastAPI


def _flatten_routes(routes: Iterable[Any]) -> Iterator[Any]:
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _flatten_routes(original_router.routes)
        else:
            yield route


def _original_endpoint(route: Any) -> Any:
    dependant = getattr(route, "dependant", None)
    return getattr(dependant, "call", None) or route.endpoint


def _is_webhook_layer(route: Any) -> bool:
    endpoint_name = getattr(_original_endpoint(route), "__name__", "")
    has_webhook_name = endpoint_name == "telegram_webhook" or endpoint_name.endswith(
        "_telegram_webhook"
    )
    return has_webhook_name and route.path == TELEGRAM_WEBHOOK_PATH and "POST" in route.methods


def _endpoint_fingerprint(route: Any) -> str:
    endpoint = _original_endpoint(route)
    return f"{endpoint.__module__}:{endpoint.__name__}"


def _registered_webhook_chain(app: FastAPI) -> tuple[str, ...]:
    return tuple(
        _endpoint_fingerprint(route)
        for route in _flatten_routes(app.routes)
        if _is_webhook_layer(route)
    )


def test_production_webhook_chain_matches_the_frozen_characterization() -> None:
    assert _registered_webhook_chain(create_app()) == TELEGRAM_WEBHOOK_CHAIN


def test_public_durable_seam_owns_exactly_one_first_webhook_layer() -> None:
    webhook_routes = [route for route in durable_ingress_router.routes if _is_webhook_layer(route)]

    assert len(webhook_routes) == 1
    assert _endpoint_fingerprint(webhook_routes[0]) == TELEGRAM_WEBHOOK_CHAIN[0]
    assert TELEGRAM_WEBHOOK_CHAIN[1:] == LEGACY_TELEGRAM_WEBHOOK_CHAIN


def test_every_transitional_router_owns_exactly_one_webhook_layer() -> None:
    fingerprints = []
    for router in LEGACY_TELEGRAM_INGRESS_ROUTERS:
        webhook_routes = [route for route in router.routes if _is_webhook_layer(route)]
        assert len(webhook_routes) == 1
        fingerprints.append(_endpoint_fingerprint(webhook_routes[0]))

    assert tuple(fingerprints) == LEGACY_TELEGRAM_WEBHOOK_CHAIN[:-1]


def test_registration_boundary_preserves_declared_layer_order() -> None:
    app = FastAPI()
    include_telegram_ingress(app)

    assert _registered_webhook_chain(app) == TELEGRAM_WEBHOOK_CHAIN[:-1]


def test_legacy_registration_compatibility_preserves_only_the_frozen_stack() -> None:
    app = FastAPI()
    include_legacy_telegram_ingress(app)

    assert _registered_webhook_chain(app) == LEGACY_TELEGRAM_WEBHOOK_CHAIN[:-1]

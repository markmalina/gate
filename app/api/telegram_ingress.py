"""Transitional registration boundary for the legacy Telegram webhook stack.

The individual sprint routers intentionally remain unchanged in this sprint. Keeping their
registration in one place freezes the production order and gives the canonical ingress
cutover one explicit seam.
"""

from fastapi import FastAPI
from fastapi.routing import APIRouter

from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router
from app.api.sprint11b_routes import router as sprint11b_router
from app.api.sprint14_routes import router as sprint14_router
from app.api.sprint14b_routes import router as sprint14b_router
from app.api.sprint15b_routes import router as sprint15b_router
from app.api.sprint16_raw_routes import router as sprint16_raw_router
from app.api.sprint16d_clean_inbox_routes import router as sprint16d_clean_inbox_router
from app.api.sprint17_send_draft_routes import router as sprint17_send_draft_router
from app.api.sprint18b_draft_ux_routes import router as sprint18b_draft_ux_router

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"

# FastAPI dispatches duplicate path/method registrations in registration order. The first
# matching sprint layer handles its command or delegates directly to the next layer.
LEGACY_TELEGRAM_INGRESS_ROUTERS: tuple[APIRouter, ...] = (
    sprint18b_draft_ux_router,
    sprint17_send_draft_router,
    sprint16d_clean_inbox_router,
    sprint16_raw_router,
    sprint15b_router,
    sprint14b_router,
    sprint14_router,
    sprint11b_router,
    sprint10_router,
    sprint9_router,
)

# Characterization fingerprint. The base webhook in app.api.routes is registered after the
# transitional sprint layers and terminates the delegation chain.
LEGACY_TELEGRAM_WEBHOOK_CHAIN: tuple[str, ...] = (
    "app.api.sprint18b_draft_ux_routes:sprint18b_telegram_webhook",
    "app.api.sprint17_send_draft_routes:sprint17_telegram_webhook",
    "app.api.sprint16d_clean_inbox_routes:sprint16d_telegram_webhook",
    "app.api.sprint16_raw_routes:sprint16_telegram_webhook",
    "app.api.sprint15b_routes:sprint15b_telegram_webhook",
    "app.api.sprint14b_routes:sprint14b_telegram_webhook",
    "app.api.sprint14_routes:sprint14_telegram_webhook",
    "app.api.sprint11b_routes:sprint11b_telegram_webhook",
    "app.api.sprint10_routes:sprint10_telegram_webhook",
    "app.api.sprint9_routes:sprint9_telegram_webhook",
    "app.api.routes:telegram_webhook",
)


def include_legacy_telegram_ingress(app: FastAPI) -> None:
    """Register the frozen webhook delegation stack in production order."""

    for router in LEGACY_TELEGRAM_INGRESS_ROUTERS:
        app.include_router(router)

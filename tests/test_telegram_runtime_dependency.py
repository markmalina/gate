from typing import cast

import httpx
import pytest
from app.api.routes import get_telegram_client
from app.services.telegram import TelegramClient
from fastapi import FastAPI, HTTPException, Request, status


def _request_for(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/",
            "headers": [],
        }
    )


def test_telegram_client_dependency_returns_process_runtime() -> None:
    app = FastAPI()
    telegram = TelegramClient(
        "secret-token",
        http_client=cast(httpx.AsyncClient, object()),
    )
    app.state.telegram_client = telegram

    assert get_telegram_client(_request_for(app)) is telegram


def test_telegram_client_dependency_fails_closed_outside_lifespan() -> None:
    app = FastAPI()

    with pytest.raises(HTTPException) as exc_info:
        get_telegram_client(_request_for(app))

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == "Telegram client runtime is not initialized"

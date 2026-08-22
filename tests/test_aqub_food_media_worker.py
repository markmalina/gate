from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.application.aqub_transport import AQUBTransportBinding
from app.worker import aqub_food_media as worker

NOW = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
RECEIPT_ID = UUID("22222222-2222-4222-8222-222222222222")
CHAT_ID = "-1001234567890"
ACTOR_ID = "123456789"


def _payload() -> dict[str, object]:
    return {
        "update_id": 501,
        "message": {
            "message_id": 601,
            "from": {"id": int(ACTOR_ID), "is_bot": False},
            "date": int((NOW - timedelta(seconds=1)).timestamp()),
            "chat": {"id": int(CHAT_ID), "type": "supergroup"},
            "photo": [
                {
                    "file_id": "synthetic-file",
                    "file_unique_id": "synthetic-unique",
                    "width": 640,
                    "height": 480,
                    "file_size": 1234,
                }
            ],
        },
    }


def _job() -> worker._MediaJob:
    return worker._MediaJob(
        receipt_id=RECEIPT_ID,
        status="queued",
        error_code=None,
        result_json=None,
        progress_sent=False,
        payload=_payload(),
        received_at=NOW,
    )


def _binding() -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id=CHAT_ID,
        ace_user_id="__unbound_ace__",
        alpha_user_id=ACTOR_ID,
        ready_at=NOW - timedelta(days=1),
        receipt_signing_key=b"k" * 32,
    )


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _Sessions:
    def __call__(self) -> _Session:
        return _Session()


class _Telegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_plain_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        assert chat_id == CHAT_ID
        self.messages.append(text)
        return {"ok": True}


async def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    *,
    processing_delay: float,
) -> tuple[_Telegram, list[str]]:
    telegram = _Telegram()
    markers: list[str] = []

    async def discover(*_args: object, **_kwargs: object) -> worker._MediaJob:
        return _job()

    async def load_binding(*_args: object, **_kwargs: object) -> AQUBTransportBinding:
        return _binding()

    async def process(*_args: object, **_kwargs: object) -> dict[str, object]:
        await asyncio.sleep(processing_delay)
        return {
            "kind": "meal",
            "confidence": "high",
            "transcript": None,
            "meal": {
                "label": "омлет",
                "kcal": 350,
                "protein_g": 24,
                "fat_g": 25,
                "carbs_g": 6,
                "items": [
                    {
                        "label": "омлет",
                        "kcal": 350,
                        "protein_g": 24,
                        "fat_g": 25,
                        "carbs_g": 6,
                    }
                ],
            },
            "receipt": None,
        }

    async def forward(**_kwargs: object) -> dict[str, object]:
        return {"result": {"reply_text": "Записано: омлет — 350 ккал."}}

    async def mark_final(**_kwargs: object) -> None:
        markers.append("final")

    class Store:
        def __init__(self, _session: object) -> None:
            pass

        async def mark_progress_sent(self, **_kwargs: object) -> bool:
            markers.append("progress")
            return True

    monkeypatch.setattr(worker, "_discover_job", discover)
    monkeypatch.setattr(worker, "load_aqub_production_binding", load_binding)
    monkeypatch.setattr(worker, "_process_job", process)
    monkeypatch.setattr(worker, "forward_aqub_media_result", forward)
    monkeypatch.setattr(worker, "_mark_final", mark_final)
    monkeypatch.setattr(worker, "SqlAQUBFoodMediaStore", Store)
    monkeypatch.setenv("GATE_AQUB_RUNTIME_URL", "https://aqub.example")
    monkeypatch.setattr(worker, "AQUB_FOOD_MEDIA_PROGRESS_SECONDS", 0.01)

    settled = await worker.run_aqub_food_media_once(
        settings=SimpleNamespace(),
        session_factory=_Sessions(),
        telegram=telegram,
        http_client=SimpleNamespace(),
        now=NOW,
    )
    assert settled == 1
    return telegram, markers


@pytest.mark.anyio
async def test_fast_media_job_sends_only_final_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram, markers = await _prepare(monkeypatch, processing_delay=0)

    assert telegram.messages == ["Записано: омлет — 350 ккал."]
    assert markers == ["final"]


@pytest.mark.anyio
async def test_slow_media_job_sends_one_progress_then_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram, markers = await _prepare(monkeypatch, processing_delay=0.03)

    assert telegram.messages == [
        "Нужно больше времени, продолжаю разбираться.",
        "Записано: омлет — 350 ккал.",
    ]
    assert markers == ["progress", "final"]

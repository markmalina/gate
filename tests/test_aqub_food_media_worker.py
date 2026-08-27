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


def _payload(
    *,
    update_id: int = 501,
    message_id: int = 601,
    actor_id: str = ACTOR_ID,
    media_group_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": int(actor_id), "is_bot": False},
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
    if media_group_id is not None:
        message = payload["message"]
        assert isinstance(message, dict)
        message["media_group_id"] = media_group_id
    return payload


def _job(
    *,
    receipt_id: UUID = RECEIPT_ID,
    payload: dict[str, object] | None = None,
    received_at: datetime = NOW,
) -> worker._MediaJob:
    return worker._MediaJob(
        receipt_id=receipt_id,
        status="queued",
        error_code=None,
        result_json=None,
        progress_sent=False,
        payload=payload or _payload(),
        received_at=received_at,
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
    routed_chat_ids: list[str] = []

    async def discover(*_args: object, **_kwargs: object) -> worker._MediaJob:
        return _job()

    async def load_binding(*_args: object, **_kwargs: object) -> AQUBTransportBinding:
        return _binding()

    async def select_binding(
        *_args: object,
        fallback_binding: AQUBTransportBinding,
        chat_id: str,
        **_kwargs: object,
    ) -> AQUBTransportBinding:
        routed_chat_ids.append(chat_id)
        return fallback_binding

    async def process(*_args: object, **_kwargs: object) -> dict[str, object]:
        await asyncio.sleep(processing_delay)
        return {
            "kind": "photo",
            "confidence": "high",
            "transcript": None,
            "ocr_text": None,
            "observations": [
                {
                    "label": "омлет",
                    "observation_type": "food",
                    "quantity_text": None,
                    "visible_text": None,
                    "confidence": "high",
                }
            ],
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
    monkeypatch.setattr(worker, "select_aqub_food_inbound_binding", select_binding)
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
    assert routed_chat_ids == [CHAT_ID]
    return telegram, markers


def test_progress_deadline_is_anchored_to_receipt_time() -> None:
    assert (
        worker._progress_timeout_seconds(
            received_at=NOW,
            observed_at=NOW,
        )
        == 21
    )
    assert (
        worker._progress_timeout_seconds(
            received_at=NOW,
            observed_at=NOW + timedelta(seconds=30),
        )
        == 0
    )


def test_album_selection_waits_for_settle_and_preserves_message_order() -> None:
    jobs = [
        _job(
            receipt_id=UUID("22222222-2222-4222-8222-222222222223"),
            payload=_payload(
                update_id=502,
                message_id=602,
                media_group_id="group-1",
            ),
        ),
        _job(
            payload=_payload(
                update_id=501,
                message_id=601,
                media_group_id="group-1",
            ),
        ),
    ]
    assert (
        worker._select_ready_jobs(
            jobs,
            now=NOW + timedelta(seconds=1),
            stale_before=NOW - timedelta(seconds=120),
        )
        == []
    )
    selected = worker._select_ready_jobs(
        jobs,
        now=NOW + timedelta(seconds=3),
        stale_before=NOW - timedelta(seconds=120),
    )
    assert [job.payload["message"]["message_id"] for job in selected] == [601, 602]


def test_album_selection_isolates_actor_and_media_group() -> None:
    jobs = [
        _job(payload=_payload(media_group_id="shared")),
        _job(
            receipt_id=UUID("22222222-2222-4222-8222-222222222223"),
            payload=_payload(
                update_id=502,
                message_id=602,
                actor_id="987654321",
                media_group_id="shared",
            ),
        ),
        _job(
            receipt_id=UUID("22222222-2222-4222-8222-222222222224"),
            payload=_payload(
                update_id=503,
                message_id=603,
                media_group_id="other",
            ),
        ),
    ]
    selected = worker._select_ready_jobs(
        jobs,
        now=NOW + timedelta(seconds=3),
        stale_before=NOW - timedelta(seconds=120),
    )
    assert len(selected) == 1
    assert selected[0].payload["message"]["from"]["id"] == int(ACTOR_ID)
    assert selected[0].payload["message"]["media_group_id"] == "shared"


def test_reply_messages_preserve_semantic_order_and_question_last() -> None:
    assert worker._reply_messages(
        {
            "result": {
                "reply_messages": [
                    "Что понял",
                    "Предварительный расчёт",
                    "Сколько было семечек?",
                ],
                "reply_text": "Сколько было семечек?",
            }
        }
    ) == ["Что понял", "Предварительный расчёт", "Сколько было семечек?"]


@pytest.mark.anyio
async def test_fast_media_job_sends_only_final_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram, markers = await _prepare(monkeypatch, processing_delay=0)

    assert telegram.messages == ["Записано: омлет — 350 ккал."]
    assert markers == ["final"]


@pytest.mark.anyio
async def test_slow_media_job_still_sends_only_the_final_meal_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram, markers = await _prepare(monkeypatch, processing_delay=0.03)

    assert telegram.messages == ["Записано: омлет — 350 ккал."]
    assert markers == ["final"]


def test_waiting_session_can_suppress_a_media_reply() -> None:
    assert worker._reply_messages(
        {"result": {"kind": "food_waiting_for_context", "suppress_reply": True}}
    ) == []


def test_runtime_failure_preserves_the_allowlisted_diagnostic_code() -> None:
    error = worker.AQUBRuntimeUnavailable("aqub_runtime_transport_unavailable")
    assert worker._safe_error_code(error) == "aqub_runtime_transport_unavailable"


@pytest.mark.anyio
async def test_album_worker_forwards_linked_text_and_finalizes_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_two = UUID("22222222-2222-4222-8222-222222222223")
    linked_receipt = "22222222-2222-4222-8222-222222222224"
    album_payloads = [
        _payload(update_id=501, message_id=601, media_group_id="group-linked"),
        _payload(update_id=502, message_id=602, media_group_id="group-linked"),
    ]
    text_payload = {
        "update_id": 503,
        "message": {
            "message_id": 603,
            "from": {"id": int(ACTOR_ID), "is_bot": False},
            "date": int(NOW.timestamp()),
            "chat": {"id": int(CHAT_ID), "type": "supergroup"},
            "text": "Вес 120 г",
        },
    }
    analysis = {
        "schema_version": "aqub.food-media-evidence.v1",
        "source_kind": "photo",
        "source_sha256": "a" * 64,
        "kind": "photo",
        "confidence": "medium",
        "transcript": None,
        "ocr_text": None,
        "observations": [
            {
                "label": "яблоко",
                "observation_type": "food",
                "quantity_text": None,
                "visible_text": None,
                "confidence": "medium",
            }
        ],
        "receipt": None,
    }
    jobs = [
        worker._MediaJob(
            receipt_id=RECEIPT_ID,
            status="processed",
            error_code=None,
            result_json=analysis,
            progress_sent=False,
            payload=album_payloads[0],
            received_at=NOW,
        ),
        worker._MediaJob(
            receipt_id=receipt_two,
            status="processed",
            error_code=None,
            result_json=analysis,
            progress_sent=False,
            payload=album_payloads[1],
            received_at=NOW + timedelta(milliseconds=100),
        ),
    ]
    inbounds = [
        worker._project_inbound(
            payload,
            bot_username="@AQubAgentBot",
            received_at=job.received_at,
        )
        for payload, job in zip(album_payloads, jobs, strict=True)
    ]
    assert all(item is not None for item in inbounds)
    projected = [item for item in inbounds if item is not None]
    forwarded: dict[str, object] = {}
    finalized: list[tuple[list[str], list[str]]] = []

    class Store:
        def __init__(self, _session: object) -> None:
            pass

        async def linked_texts_for_album(self, **_kwargs: object):
            return [(linked_receipt, text_payload, NOW + timedelta(milliseconds=700))]

        async def finalize_album(
            self,
            *,
            media_receipt_ids: list[str],
            linked_text_receipt_ids: list[str],
            now: datetime,
        ) -> tuple[int, int]:
            assert now >= NOW
            finalized.append((media_receipt_ids, linked_text_receipt_ids))
            return (2, 1)

    async def forward(**kwargs: object) -> dict[str, object]:
        forwarded.update(kwargs)
        return {"result": {"reply_messages": ["готово"], "reply_text": "готово"}}

    async def deliver(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(worker, "SqlAQUBFoodMediaStore", Store)
    monkeypatch.setattr(worker, "forward_aqub_album_result", forward)
    monkeypatch.setattr(worker, "_deliver_reply_messages", deliver)
    monkeypatch.setenv("GATE_AQUB_RUNTIME_URL", "https://aqub.example")

    result = await worker._run_album(
        settings=SimpleNamespace(),
        session_factory=_Sessions(),
        telegram=_Telegram(),
        http_client=SimpleNamespace(),
        binding=_binding(),
        jobs=jobs,
        inbounds=projected,
        observed_at=NOW + timedelta(seconds=3),
    )

    assert result == 1
    linked = forwarded["linked_text_inbounds"]
    assert isinstance(linked, list)
    assert [item.command for item in linked] == ["Вес 120 г"]
    assert forwarded["linked_text_receipt_ids"] == [linked_receipt]
    assert finalized == [([str(RECEIPT_ID), str(receipt_two)], [linked_receipt])]
